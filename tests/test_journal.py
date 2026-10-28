"""Door journal (SQLite, WAL, write-ahead) — v2 core slice."""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from atlas_slack.journal import (
    DecisionRejected, DuplicateOp, Journal, JournalUnavailable, OpIntent, token_hash,
)

SRC = Path(__file__).resolve().parent.parent / "src"


def intent(op_id="op-1", **kw):
    base = dict(op_id=op_id, method="chat.postMessage", payload_hash="h1",
                payload={"params": {"channel": "C1", "text": "hi"}}, comm_id=kw.pop("comm_id", None),
                cls="B-h", voice="bot", context="test", target={"channel": "C1"})
    base.update(kw)
    return OpIntent(**base)


def test_wal_mode_and_permissions(tmp_path):
    j = Journal(tmp_path / "j")
    mode = sqlite3.connect(str(j.path)).execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    assert (j.path.stat().st_mode & 0o777) == 0o600


def test_reserve_then_outcome(tmp_path):
    j = Journal(tmp_path)
    j.reserve(intent())
    assert j.get_op("op-1")["outcome"] == "pending"
    j.record_outcome("op-1", "ok", slack_ts="1700000000.0001")
    row = j.get_op("op-1")
    assert row["outcome"] == "ok" and row["slack_ts"] == "1700000000.0001"
    assert json.loads(row["payload_json"])["params"]["text"] == "hi"


def test_duplicate_op_id_refused(tmp_path):
    j = Journal(tmp_path)
    j.reserve(intent())
    with pytest.raises(DuplicateOp):
        j.reserve(intent())
    assert len(j.ops()) == 1


def test_two_connections_race_one_wins(tmp_path):
    """Acceptance 11 (threads): two Journal instances = two SQLite connections."""
    j1, j2 = Journal(tmp_path), Journal(tmp_path)
    barrier = threading.Barrier(2)
    results = []

    def go(j):
        barrier.wait()
        try:
            j.reserve(intent("op-race"))
            results.append("ok")
        except DuplicateOp:
            results.append("dup")

    ts = [threading.Thread(target=go, args=(j,)) for j in (j1, j2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == ["dup", "ok"]
    assert len(Journal(tmp_path).ops()) == 1


RACE_SCRIPT = """
import sys, time
sys.path.insert(0, sys.argv[1])
from atlas_slack.journal import Journal, OpIntent, DuplicateOp
j = Journal(sys.argv[2])
while time.time() < float(sys.argv[3]):
    time.sleep(0.001)
try:
    j.reserve(OpIntent(op_id="op-proc", method="chat.postMessage", payload_hash="h", payload={}))
    print("ok")
except DuplicateOp:
    print("dup")
"""


def test_two_processes_race_one_wins(tmp_path):
    """Acceptance 11 (processes): two OS processes reserve the same op_id."""
    Journal(tmp_path)  # create schema first so both children only race on reserve
    start_at = str(time.time() + 1.0)
    procs = [subprocess.Popen([sys.executable, "-c", RACE_SCRIPT, str(SRC), str(tmp_path), start_at],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    outs = [p.communicate(timeout=30) for p in procs]
    assert all(p.returncode == 0 for p in procs), [o[1] for o in outs]
    assert sorted(o[0].strip() for o in outs) == ["dup", "ok"]


def test_stale_pending_becomes_unknown_never_retried(tmp_path):
    """Acceptance 10 (journal side): pending survives the 'crash'; a new instance marks it unknown."""
    j = Journal(tmp_path)
    j.reserve(intent("op-crash"))
    j.close()
    j2 = Journal(tmp_path, stale_after=3600)
    assert j2.get_op("op-crash")["outcome"] == "pending"
    j3 = Journal(tmp_path, stale_after=0)
    assert j3.stale_marked == 1
    row = j3.get_op("op-crash")
    assert row["outcome"] == "unknown" and row["error_code"] == "stale_pending"


def test_unwritable_dir_is_unavailable(tmp_path):
    d = tmp_path / "ro"
    d.mkdir()
    os.chmod(d, 0o500)
    try:
        with pytest.raises(JournalUnavailable):
            Journal(d)
    finally:
        os.chmod(d, 0o700)


def test_decision_consumed_once_with_reserve(tmp_path):
    j = Journal(tmp_path)
    j.store_decision("tok-1", "ophash", "cc-hook")
    j.reserve(intent("op-a"), consume_token_hash=token_hash("tok-1"))
    with pytest.raises(DecisionRejected) as e:
        j.reserve(intent("op-b"), consume_token_hash=token_hash("tok-1"))
    assert e.value.code == "decision_already_used"
    assert j.get_op("op-b") is None  # rolled back with the decision check


def test_unknown_and_expired_decisions(tmp_path):
    j = Journal(tmp_path)
    with pytest.raises(DecisionRejected) as e:
        j.reserve(intent("op-a"), consume_token_hash=token_hash("never-issued"))
    assert e.value.code == "decision_unknown"
    j.store_decision("tok-old", "ophash", "cc-hook")
    j._conn.execute("UPDATE decisions SET issued_at=issued_at-3600")
    with pytest.raises(DecisionRejected) as e:
        j.reserve(intent("op-b"), consume_token_hash=token_hash("tok-old"), decision_max_age_s=600)
    assert e.value.code == "decision_expired"


def test_fallback_consumes_by_op_hash(tmp_path):
    j = Journal(tmp_path)
    with pytest.raises(DecisionRejected) as e:
        j.reserve(intent("op-a"), consume_op_hash="H")
    assert e.value.code == "no_decision"
    j.store_decision("tok-f", "H", "cc-hook")
    j.reserve(intent("op-a"), consume_op_hash="H")
    with pytest.raises(DecisionRejected):
        j.reserve(intent("op-b"), consume_op_hash="H")


def test_comm_first_seen_is_sticky(tmp_path):
    j = Journal(tmp_path)
    first = j.note_comm_seen("comm-1", at=1000.0)
    assert first == 1000.0
    assert j.note_comm_seen("comm-1") == 1000.0  # later sightings never move prepared_at


def test_calls_never_carry_content(tmp_path):
    j = Journal(tmp_path)
    j.record_call("chat.postMessage", "ok", token_kind="bot", caller="test", latency_ms=5, op_id="op-1")
    row = j.calls()[0]
    assert set(row) == {"id", "at", "method", "token_kind", "caller", "status", "error_code", "latency_ms", "op_id"}


def test_find_by_comm_and_export_envelope(tmp_path):
    j = Journal(tmp_path)
    j.reserve(intent("op-1", comm_id="comm-9"))
    j.record_outcome("op-1", "ok", slack_ts="1.2")
    assert [r["op_id"] for r in j.find_by_comm_id("comm-9")] == ["op-1"]
    lines = j.export_envelope()
    rec = json.loads(lines[0])
    assert rec["kind"] == "outbound_message" and rec["sent_by"] == "atlas-slack"
    assert rec["comm_id"] == "comm-9" and rec["ts"] == "1.2" and rec["payload"]["params"]["text"] == "hi"
