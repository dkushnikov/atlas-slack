"""One-time human decisions bound to an operation (A6, Strategy п. 16).

Adapter contract: on `needs-human` the adapter shows the human the full
provenance and, if the human approves, the operation reaches the door carrying
a decision the door can verify:

- **token path (default)** — the Claude Code hook issues
  `token = <nonce>.<HMAC-SHA256(secret, op_hash + ":" + nonce)>`, records
  `decisions(token_hash, op_hash, issued_at)` and returns `ask` with
  `updatedInput` carrying `provenance.decision = {adapter, token, at}`. Claude
  Code runs the tool with that input only after the human approves. The door
  re-computes the op hash of what actually arrived: any change of text,
  target, voice, class or `scheduled_for` → `decision_mismatch`. A token is
  consumed atomically with the intent row → a repeat is `decision_already_used`.
- **fallback** (`decision_fallback: true` in the profile; for a Claude Code
  build that does not pass `updatedInput` through on `ask`) — the hook only
  records the decision row; the door requires an unconsumed row for the exact
  op hash issued within `DECISION_MAX_AGE_S`.

Deviation from the brief (`token = HMAC(secret, op_hash)`): a per-decision
nonce is mixed in. Without it two separate approvals of the same content
(the `resend` case) yield the same token and collide on the one-time row.

Trust posture (A12.4): tamper-evident, not tamper-proof — the secret lives in
`<journal_dir>/secret` (0600) under the same OS user as the session.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

DECISION_MAX_AGE_S = 600
ADAPTER_CC_HOOK = "cc-hook"
# Token = 16 hex nonce (64 bit) "." 32 hex HMAC-SHA256 prefix (128 bit) = 49 chars.
# 128 bits of MAC is far beyond online guessing (each guess is one tool call, and the
# row must also exist and be unconsumed); the full 256-bit MAC only cost prompt space.
MAC_HEX = 32


def _secret_path(journal_dir: str | os.PathLike) -> Path:
    return Path(journal_dir).expanduser() / "secret"


def load_secret(journal_dir: str | os.PathLike) -> bytes:
    """Read the per-profile secret, creating it once (0600). Raises OSError."""
    p = _secret_path(journal_dir)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        data = p.read_bytes().strip()
        if len(data) < 32:
            raise OSError(f"decision secret {p} is too short or empty")
        return data
    with os.fdopen(fd, "wb") as f:
        f.write(secrets.token_hex(32).encode())
        f.flush()
        os.fsync(f.fileno())
    return p.read_bytes().strip()


def _mac(secret: bytes, op_hash: str, nonce: str) -> str:
    return hmac.new(secret, f"{op_hash}:{nonce}".encode(), hashlib.sha256).hexdigest()[:MAC_HEX]


def token_of(decision) -> str | None:
    """The token from a decision object — compact `{t}` (wire) or `{token}` (long form)."""
    if not isinstance(decision, dict):
        return None
    t = decision.get("t") or decision.get("token")
    return t if isinstance(t, str) and t else None


def derive_op_id(op_hash: str, token: str) -> str:
    """Deterministic op_id for a hook-approved send: the hook can show it, the wire needn't carry it."""
    nonce = token.split(".", 1)[0]
    return hashlib.sha256(f"{op_hash}:{nonce}".encode()).hexdigest()[:16]


def issue_token(secret: bytes, op_hash: str) -> str:
    nonce = secrets.token_hex(8)
    return f"{nonce}.{_mac(secret, op_hash, nonce)}"


def token_matches(secret: bytes, token: str, op_hash: str) -> bool:
    if not isinstance(token, str) or token.count(".") != 1:
        return False
    nonce, mac = token.split(".")
    if len(mac) != MAC_HEX:
        return False
    return hmac.compare_digest(mac, _mac(secret, op_hash, nonce))


@dataclass
class DecisionCheck:
    ok: bool
    code: str            # valid | no_decision | decision_mismatch | decision_already_used | ...
    detail: str = ""
    token: str | None = None
    fallback: bool = False
    adapter: str | None = None       # the decisions row's adapter text (carries the session id)


def check(decision: dict | None, op_hash: str, journal, journal_dir, fallback_enabled: bool,
          now: float | None = None) -> DecisionCheck:
    """Verify (without consuming) the decision attached to an operation.

    Consumption happens inside `Journal.reserve`, atomically with the intent.
    """
    now = time.time() if now is None else now
    token = token_of(decision)
    if not token:
        if fallback_enabled:
            return DecisionCheck(True, "valid", "fallback: pre-registered decision row", fallback=True)
        return DecisionCheck(False, "no_decision",
                             "no decision token — the send was not approved through a decision adapter")
    try:
        secret = load_secret(journal_dir)
    except OSError as e:
        return DecisionCheck(False, "journal_unavailable", f"decision secret unavailable: {e}")
    if not token_matches(secret, token, op_hash):
        return DecisionCheck(False, "decision_mismatch",
                             "the operation changed after the decision (text, target, voice, class or "
                             "scheduled_for) — the decision does not cover what arrived")
    row = journal.get_decision(token)
    if row is None:
        return DecisionCheck(False, "decision_unknown", "token not recorded in this door's journal")
    if row["consumed_at"] is not None:
        return DecisionCheck(False, "decision_already_used", "this decision was already used for a send")
    if now - row["issued_at"] > DECISION_MAX_AGE_S:
        return DecisionCheck(False, "decision_expired", f"decision older than {DECISION_MAX_AGE_S}s")
    return DecisionCheck(True, "valid", token=token, adapter=row.get("adapter"))
