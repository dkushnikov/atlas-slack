"""0.5.1 — the door contract after three days of 0.5.0 in production (plan 2026-10-31).

What the journal and the session logs showed: approvals expiring at 600 s while the
prompt sat in a background session; provenance refused twice per session for a
schema the skill never showed, then dropped (ops without comm_id); hook-side
refusals invisible in the journal; LLM-minted "random" comm_ids repeating.
"""

import re
from unittest.mock import MagicMock, patch

import pytest

from atlas_slack import core, decision as decision_mod
from atlas_slack.journal import DECISION_PRUNE_AFTER_S
from atlas_slack.provenance import COMM_ID_RE, Provenance, mint_comm_id
from test_v2_slice import CHANNEL, Env, PROFILES, post, refused  # noqa: F401  (fixtures reused)


@pytest.fixture(params=sorted(PROFILES))
def env(request, isolated_door_dirs, monkeypatch):
    return Env(request.param, isolated_door_dirs, monkeypatch)


# ------------------------------------------------------------ decision window
def test_window_is_thirty_minutes_and_inside_the_prune_horizon():
    assert decision_mod.DECISION_MAX_AGE_S == 1800
    assert decision_mod.DECISION_MAX_AGE_S <= DECISION_PRUNE_AFTER_S


def test_approval_after_fifteen_minutes_now_sends(env):
    """The 2026-10-30 21:09 case: the owner answered 14m43s after the prompt — expired under 600 s."""
    ti = env.approve(post())
    env.journal._conn.execute("UPDATE decisions SET issued_at=issued_at-883")
    res = env.send(ti)
    assert res.data["ok"] and len(env.transport.calls) == 1


def test_expired_detail_tells_claude_what_to_do(env):
    ti = env.approve(post(comm_id="late-1"))
    env.journal._conn.execute("UPDATE decisions SET issued_at=issued_at-1801")
    e = refused(env, ti, "decision_expired")
    assert "nothing was sent" in e.message and "call again" in e.message and "no new op_id" in e.message
    assert env.transport.calls == []
    # the same input again = a fresh prompt, a fresh token, exactly one send
    ti2 = env.approve(post(comm_id="late-1"))
    assert ti2["provenance"]["decision"]["t"] != ti["provenance"]["decision"]["t"]
    env.send(ti2)
    assert len(env.transport.calls) == 1


# ------------------------------------------------------------- comm_id minting
def test_mint_comm_id_shape_and_taken_filter():
    seen = set()
    for _ in range(50):
        cid = mint_comm_id(taken=lambda c: c in seen)
        assert COMM_ID_RE.match(cid), cid
        assert cid not in seen
        seen.add(cid)


def test_send_without_comm_id_gets_one_from_the_door(env):
    ti = env.approve(post())                       # provenance {cls} only — no comm_id
    res = env.send(ti)
    assert res.comm_minted and COMM_ID_RE.match(res.comm_id)
    row = env.journal.ops()[0]
    assert row["comm_id"] == res.comm_id
    assert '"comm_id_source": "door"' in row["provenance_json"]
    assert env.journal.comm_first_seen(res.comm_id) is not None
    # a second provenance-less send is a different communication, not a duplicate
    res2 = env.send(env.approve(post(text="second")))
    assert res2.comm_minted and res2.comm_id != res.comm_id
    assert len(env.transport.calls) == 2


def test_caller_comm_id_is_kept_not_minted(env):
    res = env.send(env.approve(post(comm_id="20261031-a1b2")))
    assert res.comm_id == "20261031-a1b2" and not res.comm_minted
    assert '"comm_id_source"' not in env.journal.ops()[0]["provenance_json"]


def test_result_line_hands_the_comm_id_to_the_ledger(env):
    from atlas_slack import gateway
    mock = MagicMock()
    mock.api_call.return_value = {"ok": True, "channel": CHANNEL, "ts": "1.1", "message": {"ts": "1.1", "text": "x"}}
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        minted = gateway.slack_api(**env.approve(post()))
        given = gateway.slack_api(**env.approve(post(text="y", comm_id="20261031-c0de")))
    assert re.search(r"journal: op \w+ ok · comm \d{8}-[0-9a-f]{4} \(minted by the door", minted), minted
    assert "journal: op" in given and "· comm 20261031-c0de" in given and "minted" not in given


# ------------------------------------------------------- provenance: cls default
def test_provenance_with_only_comm_id_is_valid(env):
    for as_bot, cls in ((True, "B-h"), (False, "A")):
        ti = {"method": "chat.postMessage", "params": {"channel": CHANNEL, "text": "t"}, "as_bot": as_bot,
              "provenance": {"comm_id": f"20261031-{'b0' if as_bot else 'a0'}01"}}
        approved = env.approve(ti)
        res = env.send(approved)
        assert res.verdict.cls == cls and not res.comm_minted
    assert Provenance.from_dict({"comm_id": "x"}, as_bot=False).cls == "A"
    with pytest.raises(Exception):
        Provenance.from_dict({"comm_id": "x"})            # no voice to derive from → still required


# ------------------------------------------------------ hook refusals journaled
def test_hook_refusal_lands_in_the_call_journal(env):
    out = env.hook({"method": "chat.postMessage", "params": {"channel": CHANNEL, "text": "x", "username": "Boss"},
                    "as_bot": True, "provenance": {"cls": "B-h"}})
    assert out["permissionDecision"] == "deny" and "identity_override" in out["permissionDecisionReason"]
    rows = [c for c in env.journal.calls() if c["status"] == "refused"]
    assert rows and rows[-1]["caller"] == "hook" and rows[-1]["error_code"] == "identity_override"
    assert rows[-1]["method"] == "chat.postMessage" and rows[-1]["token_kind"] == "bot"
    assert env.transport.calls == []


def test_hook_provenance_invalid_is_journaled(env):
    out = env.hook({"method": "chat.postMessage", "params": {"channel": CHANNEL, "text": "x"}, "as_bot": False,
                    "provenance": {"cls": "A", "checks": "recipient ok"}})
    assert out["permissionDecision"] == "deny" and "provenance_invalid" in out["permissionDecisionReason"]
    assert any(c["caller"] == "hook" and c["error_code"] == "provenance_invalid" for c in env.journal.calls())


def test_hook_ask_and_allow_are_not_journaled_as_refusals(env):
    env.approve(post())
    env.hook({"method": "conversations.history", "params": {"channel": CHANNEL}})
    assert not [c for c in env.journal.calls() if c["caller"] == "hook"]


# ------------------------------------------------- round 2 (spec-verifier findings)
def test_late_approval_reads_expired_not_unknown_even_after_other_prompts(env):
    """3 h 47 min with another prompt in between: the row must survive the prune (24 h, not 1 h)."""
    ti = env.approve(post(comm_id="late-2"))
    env.journal._conn.execute("UPDATE decisions SET issued_at=issued_at-13620")
    env.approve(post(text="another prompt meanwhile"))     # store_decision → prune_decisions
    e = refused(env, ti, "decision_expired")
    assert "nothing was sent" in e.message
    assert DECISION_PRUNE_AFTER_S == 86400


def test_unknown_after_a_day_says_call_again(env):
    ti = env.approve(post(comm_id="late-3"))
    env.journal._conn.execute("UPDATE decisions SET issued_at=issued_at-90000")
    env.approve(post(text="prompt after a day"))
    e = refused(env, ti, "decision_unknown")
    assert "nothing was sent" in e.message and "call again" in e.message


def test_comm_id_pseudo_method_mints_and_notes_first_sighting(env):
    from atlas_slack import gateway
    assert env.hook({"method": "_comm_id"})["permissionDecision"] == "allow"
    out = gateway.slack_api("_comm_id")
    cid = out.split()[1]
    assert COMM_ID_RE.match(cid) and env.journal.comm_first_seen(cid) is not None
    res = env.send(env.approve(post(comm_id=cid)))
    assert res.comm_id == cid and not res.comm_minted
    from atlas_slack.door import Door
    r = Door(env.name, transport=env.transport).call("_comm_id")
    assert r.ok and COMM_ID_RE.match(r.comm_id) and r.comm_id != cid


def test_prepared_as_human_needs_comm_id(env):
    ti = {"method": "chat.postMessage", "params": {"channel": CHANNEL, "text": "t"}, "as_bot": False,
          "provenance": {"cls": "A", "origin": "prepared-as-human", "ttl_s": 600}}
    out = env.hook(ti)
    assert out["permissionDecision"] == "deny" and "comm_id_required" in out["permissionDecisionReason"]
    assert any(c["caller"] == "hook" and c["error_code"] == "comm_id_required" for c in env.journal.calls())
    ti["provenance"]["comm_id"] = "20261031-feed"
    env.send(env.approve(ti))
    assert len(env.transport.calls) == 1


def test_outcome_not_journaled_tail_keeps_the_comm_segment(env, monkeypatch):
    from atlas_slack import gateway
    from atlas_slack.journal import Journal, JournalUnavailable
    mock = MagicMock()
    mock.api_call.return_value = {"ok": True, "channel": CHANNEL, "ts": "9.9", "message": {"ts": "9.9", "text": "x"}}
    ti = env.approve(post())
    real = Journal.record_outcome

    def failing(self, op_id, outcome, **kw):
        if outcome == "ok":
            raise JournalUnavailable("disk gone")
        return real(self, op_id, outcome, **kw)
    monkeypatch.setattr(Journal, "record_outcome", failing)
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        out = gateway.slack_api(**ti)
    assert "outcome_not_journaled" in out and re.search(r"· comm \d{8}-[0-9a-f]{4} \(minted by the door", out)


def test_comm_id_refuses_under_freeze_on_both_faces(env):
    from atlas_slack import gateway
    from atlas_slack.door import Door
    from fastmcp.exceptions import ToolError
    env.write(freeze=True)
    assert env.hook({"method": "_comm_id"})["permissionDecision"] == "deny"
    with pytest.raises(ToolError) as e:
        gateway.slack_api("_comm_id")
    assert "frozen" in str(e.value)
    r = Door(env.name, transport=env.transport).call("_comm_id")
    assert not r.ok and r.verdict.reason == "frozen"
    assert env.hook({"method": "_rights"})["permissionDecision"] == "allow"     # read-only stays allowed


def test_journal_open_does_not_stall_behind_a_reader(env):
    """The open-time checkpoint is PASSIVE: a peer holding a read transaction must not block it."""
    import sqlite3, time as _t
    from atlas_slack.journal import Journal, reset_cache
    j = env.journal
    j.store_decision("stall.1", "h-stall", "cc-hook")
    peer = sqlite3.connect(str(j.path), isolation_level=None)
    peer.execute("BEGIN")
    peer.execute("SELECT COUNT(*) FROM decisions").fetchone()      # read transaction stays open
    reset_cache()
    t0 = _t.monotonic()
    j2 = Journal(env.journal_dir)
    assert _t.monotonic() - t0 < 2.0
    assert j2._conn.execute("PRAGMA journal_size_limit").fetchone()[0] == 1048576
    peer.execute("COMMIT"); peer.close(); j2.close()
