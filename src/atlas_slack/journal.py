"""Door journal — SQLite, WAL, write-ahead (Architecture v2 A4, invariant viii).

One file per host and profile: `<journal_dir>/journal.sqlite`. The door is the
only writer; several processes on one host (two sessions + a job) serialise
through SQLite's own locking (WAL + `BEGIN IMMEDIATE`).

Tables
- `ops`       one row per write attempt that passed the gate (`op_id` = primary
              key → atomic reservation). Intent is written BEFORE the Slack call
              (`outcome='pending'`), the outcome after. Full outgoing payload is
              kept (A4: stored verbatim) — retention is the owner's (README).
- `calls`     every Slack call and every gate refusal — method, token kind,
              caller, status, error code, latency. Never content.
- `decisions` one-time decision tokens issued by a decision adapter (hashes only).
- `state`     small key/value store (first sighting of a `comm_id`, ...).

Crash semantics: a row left `pending` (process died between the call and the
outcome) becomes `unknown` on the next open once it is older than
`stale_after` seconds. `unknown` is resolved by reconciliation against Slack,
never by a retry — reconciliation itself is out of this slice.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_STALE_AFTER_S = 600
DECISION_PRUNE_AFTER_S = 3600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ops (
    op_id TEXT PRIMARY KEY,
    comm_id TEXT,
    cls TEXT,
    voice TEXT,
    method TEXT NOT NULL,
    context TEXT,
    prepared_by TEXT,
    target_json TEXT,
    payload_hash TEXT,
    payload_json TEXT,
    provenance_json TEXT,
    policy_version TEXT,
    policy_digest TEXT,
    decision_json TEXT,
    intent_at REAL NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('pending','ok','error','unknown')),
    outcome_at REAL,
    slack_ts TEXT,
    error_code TEXT,
    parent_op_id TEXT
);
CREATE INDEX IF NOT EXISTS ops_comm ON ops(comm_id);
CREATE INDEX IF NOT EXISTS ops_outcome ON ops(outcome);
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    method TEXT NOT NULL,
    token_kind TEXT,
    caller TEXT,
    status TEXT NOT NULL,
    error_code TEXT,
    latency_ms INTEGER,
    op_id TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    token_hash TEXT PRIMARY KEY,
    op_hash TEXT NOT NULL,
    adapter TEXT NOT NULL,
    issued_at REAL NOT NULL,
    consumed_at REAL
);
CREATE INDEX IF NOT EXISTS decisions_op ON decisions(op_hash);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value_json TEXT,
    updated_at REAL NOT NULL
);
"""


class JournalUnavailable(Exception):
    """The journal cannot durably record — the door must not send (A4, A7 #6)."""


class DuplicateOp(Exception):
    """`op_id` already reserved — idempotency by `op_id` (invariant ix)."""


class DuplicateComm(Exception):
    """`comm_id` already has a write (ok/pending/unknown) and no `resend` (invariant ix)."""


class DecisionRejected(Exception):
    """The one-time decision could not be consumed (used, unknown, expired)."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class OpIntent:
    """What `reserve` writes before the network call."""

    op_id: str
    method: str
    payload_hash: str
    payload: dict
    comm_id: str | None = None
    cls: str | None = None
    voice: str | None = None
    context: str | None = None
    prepared_by: str | None = None
    target: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)
    policy_version: str | None = None
    policy_digest: str | None = None
    decision: dict | None = None
    parent_op_id: str | None = None


class _Fetched:
    """Materialised cursor result — safe to read after the lock is released."""

    def __init__(self, rows, rowcount):
        self._rows = rows
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)


class Journal:
    """SQLite journal for one profile on one host.

    Opening is cheap but not free; callers in a long-lived process cache the
    instance (see `open_journal`). Every public method raises
    `JournalUnavailable` on any SQLite/OS failure — the caller turns that into
    a refusal for writes and ignores it for read bookkeeping.
    """

    def __init__(self, journal_dir: str | os.PathLike, stale_after: float = DEFAULT_STALE_AFTER_S):
        self.dir = Path(journal_dir).expanduser()
        self.path = self.dir / "journal.sqlite"
        self.stale_after = stale_after
        # One connection per instance; the MCP server may run tools on worker
        # threads, so every statement/transaction on it is serialised here.
        self._lock = threading.RLock()
        try:
            self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._conn = sqlite3.connect(str(self.path), timeout=10, isolation_level=None,
                                         check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            mode = self._conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                raise JournalUnavailable(f"journal_mode={mode}, WAL required")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.executescript(_SCHEMA)
            row = self._conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                self._conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
            elif row[0] != SCHEMA_VERSION:
                raise JournalUnavailable(f"schema_version {row[0]} != {SCHEMA_VERSION}")
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            self.stale_marked = self.mark_stale_unknown()
        except JournalUnavailable:
            raise
        except (sqlite3.Error, OSError) as e:
            raise JournalUnavailable(f"{type(e).__name__}: {e} ({self.path})") from e

    # ------------------------------------------------------------------ util
    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def _exec(self, sql: str, args: tuple = ()):
        with self._lock:
            try:
                cur = self._conn.execute(sql, args)
                if cur.description is not None:
                    return _Fetched(cur.fetchall(), cur.rowcount)
                return _Fetched([], cur.rowcount)
            except sqlite3.Error as e:
                raise JournalUnavailable(f"{type(e).__name__}: {e}") from e

    # -------------------------------------------------------------- ops (W-A)
    def reserve(self, op: OpIntent, consume_token_hash: str | None = None,
                consume_op_hash: str | None = None, decision_max_age_s: float | None = None,
                forbid_comm_duplicate: bool = False) -> None:
        """Insert the intent row atomically, optionally consuming a decision.

        One `BEGIN IMMEDIATE` transaction: the decision row (if any) is checked
        and consumed and the `ops` row inserted together, so two processes can
        neither both consume one decision nor both reserve one `op_id`.

        `consume_token_hash` → the token path; `consume_op_hash` alone → the
        fallback path (newest unconsumed decision row for that op hash).
        `forbid_comm_duplicate` → the `comm_id` duplicate rule is re-checked
        inside the same transaction (two concurrent sends of one comm_id).
        Raises DuplicateOp, DuplicateComm, DecisionRejected, JournalUnavailable.
        """
        with self._lock:
            self._reserve_locked(op, consume_token_hash, consume_op_hash, decision_max_age_s,
                                 forbid_comm_duplicate)

    def _reserve_locked(self, op, consume_token_hash, consume_op_hash, decision_max_age_s,
                        forbid_comm_duplicate) -> None:
        now = time.time()
        c = self._conn
        try:
            c.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as e:
            raise JournalUnavailable(f"{type(e).__name__}: {e}") from e
        try:
            if forbid_comm_duplicate and op.comm_id:
                n = c.execute("SELECT COUNT(*) FROM ops WHERE comm_id=? AND outcome IN ('ok','pending','unknown')",
                              (op.comm_id,)).fetchone()[0]
                if n:
                    raise DuplicateComm(op.comm_id)
            if consume_token_hash or consume_op_hash:
                if consume_token_hash:
                    row = c.execute("SELECT * FROM decisions WHERE token_hash=?",
                                    (consume_token_hash,)).fetchone()
                    if row is None:
                        raise DecisionRejected("decision_unknown",
                                               "the decision token was not issued by this door's journal")
                else:
                    row = c.execute(
                        "SELECT * FROM decisions WHERE op_hash=? AND consumed_at IS NULL "
                        "ORDER BY issued_at DESC LIMIT 1", (consume_op_hash,)).fetchone()
                    if row is None:
                        raise DecisionRejected("no_decision",
                                               "no unconsumed decision recorded for this exact operation")
                if row["consumed_at"] is not None:
                    raise DecisionRejected("decision_already_used",
                                           "this decision was already used for a send")
                if consume_op_hash and row["op_hash"] != consume_op_hash:
                    raise DecisionRejected("decision_mismatch",
                                           "the decision was issued for a different operation")
                if decision_max_age_s is not None and now - row["issued_at"] > decision_max_age_s:
                    raise DecisionRejected("decision_expired",
                                           f"decision older than {int(decision_max_age_s)}s")
                cur = c.execute("UPDATE decisions SET consumed_at=? WHERE token_hash=? AND consumed_at IS NULL",
                                (now, row["token_hash"]))
                if cur.rowcount != 1:
                    raise DecisionRejected("decision_already_used", "consumed concurrently")
            c.execute(
                "INSERT INTO ops(op_id, comm_id, cls, voice, method, context, prepared_by, target_json, "
                "payload_hash, payload_json, provenance_json, policy_version, policy_digest, decision_json, "
                "intent_at, outcome, parent_op_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending', ?)",
                (op.op_id, op.comm_id, op.cls, op.voice, op.method, op.context, op.prepared_by,
                 _dumps(op.target), op.payload_hash, _dumps(op.payload), _dumps(op.provenance),
                 op.policy_version, op.policy_digest, _dumps(op.decision) if op.decision else None,
                 now, op.parent_op_id),
            )
            c.execute("DELETE FROM decisions WHERE consumed_at IS NULL AND issued_at < ?",
                      (now - DECISION_PRUNE_AFTER_S,))
            c.execute("COMMIT")
        except sqlite3.IntegrityError as e:
            c.execute("ROLLBACK")
            raise DuplicateOp(op.op_id) from e
        except (DecisionRejected, DuplicateComm):
            c.execute("ROLLBACK")
            raise
        except sqlite3.Error as e:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise JournalUnavailable(f"{type(e).__name__}: {e}") from e

    def record_outcome(self, op_id: str, outcome: str, slack_ts: str | None = None,
                       error_code: str | None = None) -> None:
        if outcome not in ("ok", "error", "unknown"):
            raise ValueError(f"bad outcome {outcome!r}")
        self._exec("UPDATE ops SET outcome=?, outcome_at=?, slack_ts=?, error_code=? WHERE op_id=?",
                   (outcome, time.time(), slack_ts, error_code, op_id))

    def mark_unknown(self, op_id: str, error_code: str | None = None) -> None:
        self.record_outcome(op_id, "unknown", error_code=error_code)

    def mark_stale_unknown(self, stale_after: float | None = None) -> int:
        """`pending` rows older than `stale_after` → `unknown`. Never retries."""
        age = self.stale_after if stale_after is None else stale_after
        cur = self._exec(
            "UPDATE ops SET outcome='unknown', outcome_at=?, error_code=COALESCE(error_code,'stale_pending') "
            "WHERE outcome='pending' AND intent_at <= ?",
            (time.time(), time.time() - age))
        return cur.rowcount

    def get_op(self, op_id: str) -> dict | None:
        row = self._exec("SELECT * FROM ops WHERE op_id=?", (op_id,)).fetchone()
        return dict(row) if row else None

    def find_by_comm_id(self, comm_id: str) -> list[dict]:
        rows = self._exec("SELECT * FROM ops WHERE comm_id=? ORDER BY intent_at", (comm_id,)).fetchall()
        return [dict(r) for r in rows]

    def ops(self, outcome: str | None = None) -> list[dict]:
        if outcome:
            rows = self._exec("SELECT * FROM ops WHERE outcome=? ORDER BY intent_at", (outcome,)).fetchall()
        else:
            rows = self._exec("SELECT * FROM ops ORDER BY intent_at").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------- comm_id
    def note_comm_seen(self, comm_id: str, at: float | None = None) -> float:
        """First sighting of a `comm_id` (= `prepared_at`, invariant x). Idempotent."""
        key = f"comm_first_seen:{comm_id}"
        ts = time.time() if at is None else at
        self._exec("INSERT OR IGNORE INTO state(key, value_json, updated_at) VALUES (?,?,?)",
                   (key, json.dumps(ts), time.time()))
        return self.comm_first_seen(comm_id)

    def comm_first_seen(self, comm_id: str) -> float | None:
        row = self._exec("SELECT value_json FROM state WHERE key=?", (f"comm_first_seen:{comm_id}",)).fetchone()
        return json.loads(row[0]) if row else None

    # ------------------------------------------------------------ decisions
    def store_decision(self, token: str, op_hash: str, adapter: str) -> None:
        self.prune_decisions()
        self._exec("INSERT INTO decisions(token_hash, op_hash, adapter, issued_at) VALUES (?,?,?,?)",
                   (token_hash(token), op_hash, adapter, time.time()))

    def prune_decisions(self, older_than_s: float = DECISION_PRUNE_AFTER_S) -> int:
        """Drop never-used decisions (a human declined, or the call never came) after an hour.

        Consumed rows stay — they are the record of who approved what. Unconsumed
        rows older than the decision TTL can never be used anyway; pruning keeps
        the fallback path ("newest unconsumed row for this op hash") from finding
        stale approvals.
        """
        return self._exec("DELETE FROM decisions WHERE consumed_at IS NULL AND issued_at < ?",
                          (time.time() - older_than_s,)).rowcount

    def get_decision(self, token: str) -> dict | None:
        row = self._exec("SELECT * FROM decisions WHERE token_hash=?", (token_hash(token),)).fetchone()
        return dict(row) if row else None

    # ---------------------------------------------------------------- calls
    def record_call(self, method: str, status: str, token_kind: str | None = None,
                    caller: str | None = None, error_code: str | None = None,
                    latency_ms: int | None = None, op_id: str | None = None) -> None:
        """Call journal row — never content (A4)."""
        self._exec("INSERT INTO calls(at, method, token_kind, caller, status, error_code, latency_ms, op_id) "
                   "VALUES (?,?,?,?,?,?,?,?)",
                   (time.time(), method, token_kind, caller, status, error_code, latency_ms, op_id))

    def calls(self) -> list[dict]:
        return [dict(r) for r in self._exec("SELECT * FROM calls ORDER BY id").fetchall()]

    # -------------------------------------------------------------- export
    def export_envelope(self, since: float = 0.0) -> list[str]:
        """JSONL lines in the Telegram door's envelope shape (`outbound/`).

        `kind: outbound_message`, `payload`, `sent_by`, `comm_id`, `captured_at`
        — plus the door's own fields (op_id, outcome, ts, class, voice, policy).
        Export is a view; the SQLite file stays the primary store.
        """
        rows = self._exec("SELECT * FROM ops WHERE intent_at >= ? ORDER BY intent_at", (since,)).fetchall()
        out = []
        for r in rows:
            rec = {
                "captured_at": datetime.fromtimestamp(r["intent_at"], timezone.utc).isoformat(timespec="seconds"),
                "kind": "outbound_message",
                "sent_by": "atlas-slack",
                "comm_id": r["comm_id"],
                "op_id": r["op_id"],
                "payload": json.loads(r["payload_json"]) if r["payload_json"] else None,
                "outcome": r["outcome"],
                "ts": r["slack_ts"],
                "error_code": r["error_code"],
                "class": r["cls"],
                "voice": r["voice"],
                "context": r["context"],
                "policy_version": r["policy_version"],
                "policy_digest": r["policy_digest"],
                "decision": json.loads(r["decision_json"]) if r["decision_json"] else None,
                "envelope_version": 1,
            }
            out.append(json.dumps(rec, ensure_ascii=False, sort_keys=True))
        return out


_OPEN: dict[str, Journal] = {}


def open_journal(journal_dir: str | os.PathLike) -> Journal:
    """Process-wide cache: one connection per journal file."""
    key = str(Path(journal_dir).expanduser().resolve())
    j = _OPEN.get(key)
    if j is not None and j.dir.exists():
        return j
    j = Journal(journal_dir)
    _OPEN[key] = j
    return j


def reset_cache() -> None:
    for j in _OPEN.values():
        j.close()
    _OPEN.clear()
