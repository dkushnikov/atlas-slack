"""Acceptance tests for the v2 core slice (plan 2026-10-28 § Tests, items 1–17).

Level 1: fake transport with a call recorder, no secrets, no network. Every
test runs under two independent profiles (item 16): an Atlas-like one and a
synthetic `acme` one with different ids and different token env var names.

The decision a human gives in Claude Code is simulated exactly as production
produces it: `gate.hook_output` (the PreToolUse hook) returns `ask` with
`updatedInput`; the tool then runs with that input.
"""

import os
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import yaml
from fastmcp.exceptions import ToolError

from atlas_slack import core, decision as decision_mod, policy as policy_mod
from atlas_slack.door import Door
from atlas_slack.gate import hook_output, rights
from atlas_slack.journal import Journal, open_journal, reset_cache
from atlas_slack.profile import load_profile

TOOL = "mcp__plugin_atlas-slack_atlas-slack__slack_api"

PROFILES = {
    "atlas-like": {
        "workspace_id": "T0WORKSPC1", "person_user_id": "U0PERSON01", "bot_user_id": "U0BOTUSR01",
        "person_bot_dm_id": "D0BOTDM001",
        "token_sources": {"user": "SLACK_USER_TOKEN", "bot": "SLACK_BOT_TOKEN"},
    },
    "acme": {
        "workspace_id": "TACME00001", "person_user_id": "UACMEPRSN", "bot_user_id": "UACMEBOT1",
        "person_bot_dm_id": "DACMEBOTDM",
        "token_sources": {"user": "ACME_SLACK_USER", "bot": "ACME_SLACK_BOT"},
    },
}
CHANNEL = "C0TEAMCHAN"


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.exc = None
        self.lock = threading.Lock()

    def __call__(self, method, params, as_bot):
        with self.lock:
            self.calls.append((method, dict(params), as_bot))
        if self.exc is not None:
            raise self.exc
        ts = f"1700000000.{len(self.calls):06d}"
        return {"ok": True, "channel": params.get("channel"), "ts": ts,
                "message": {"ts": ts, "text": params.get("text", "")}}


class ProcessDied(BaseException):
    """Simulates the process dying mid-call (not an Exception: nothing handles it)."""


class Env:
    def __init__(self, name, root, monkeypatch):
        self.name = name
        self.ids = PROFILES[name]
        self.cfg_dir = root / "config"
        self.cfg_dir.mkdir(parents=True, exist_ok=True)
        self.journal_dir = root / "data" / name
        self.fields = dict(self.ids)
        self.monkeypatch = monkeypatch
        monkeypatch.setenv("ATLAS_SLACK_PROFILE", name)
        ts = self.ids["token_sources"]
        monkeypatch.setenv(ts["user"], "xoxp-fake-user")
        monkeypatch.setenv(ts["bot"], "xoxb-fake-bot")
        self.write()
        self.transport = FakeTransport()

    def write(self, **extra):
        self.fields.update(extra)
        (self.cfg_dir / f"{self.name}.yaml").write_text(yaml.safe_dump(self.fields))

    def policy(self, text: str, pin: str | None = None):
        p = self.cfg_dir / f"{self.name}.policy.yaml"
        p.write_text(text)
        extra = {"policy_path": str(p)}
        if pin is not None:
            extra["policy_sha256"] = pin
        self.write(**extra)
        return p

    @property
    def profile(self):
        return load_profile()

    @property
    def journal(self):
        return open_journal(self.journal_dir)

    def hook(self, tool_input):
        return hook_output({"tool_name": TOOL, "tool_input": tool_input, "session_id": "s-test"})["hookSpecificOutput"]

    def approve(self, tool_input):
        out = self.hook(tool_input)
        assert out["permissionDecision"] == "ask", out
        return out["updatedInput"]

    def send(self, ti, face="mcp"):
        return core.execute_send(ti["method"], ti.get("params", {}), ti.get("as_bot", False),
                                 ti.get("provenance"), profile=self.profile, transport=self.transport, face=face)


@pytest.fixture(params=sorted(PROFILES))
def env(request, isolated_door_dirs, monkeypatch):
    return Env(request.param, isolated_door_dirs, monkeypatch)


def op_of(env, ti):
    """The op_id the server derives for a hook-approved send (it is not on the wire)."""
    tok = ti["provenance"]["decision"]["t"]
    return decision_mod.derive_op_id(env.journal.get_decision(tok)["op_hash"], tok)


def post(text="hello team", as_bot=True, channel=CHANNEL, **prov):
    p = {"cls": "B-h" if as_bot else "A", **prov}
    return {"method": "chat.postMessage", "params": {"channel": channel, "text": text}, "as_bot": as_bot,
            "provenance": p}


def refused(env, ti, code):
    with pytest.raises(core.Refused) as e:
        env.send(ti)
    assert e.value.code == code, (e.value.code, e.value.message)
    return e.value


# ---------------------------------------------------------------- 1, 2 happy paths

def test_01_happy_path_bot_voice_human_approved(env):
    ti = env.approve(post(comm_id="comm-1"))
    assert ti["provenance"]["decision"]["p"] == env.name
    res = env.send(ti)
    assert len(env.transport.calls) == 1
    row = env.journal.get_op(res.op_id)
    assert row["outcome"] == "ok" and row["slack_ts"] == "1700000000.000001"
    assert row["cls"] == "B-h" and row["voice"] == "bot" and row["context"] == env.name
    assert row["policy_version"] == "builtin-strict-1"
    calls = [c for c in env.journal.calls() if c["op_id"] == res.op_id]
    assert len(calls) == 1 and calls[0]["status"] == "ok" and calls[0]["token_kind"] == "bot"


def test_01b_happy_path_through_the_mcp_server(env):
    """Same flow through gateway.slack_api (the MCP face) with a mocked WebClient."""
    from atlas_slack import gateway
    mock = MagicMock()
    mock.api_call.return_value = {"ok": True, "channel": CHANNEL, "ts": "1.1", "message": {"ts": "1.1", "text": "x"}}
    ti = env.approve(post())
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        out = gateway.slack_api(**ti)
    assert mock.api_call.call_count == 1
    assert "journal: op" in out and op_of(env, ti) in out


def test_02_happy_path_person_voice_keeps_attribution(env):
    ti = env.approve(post(as_bot=False))
    env.send(ti)
    assert len(env.transport.calls) == 1
    _, final, as_bot = env.transport.calls[0]
    assert as_bot is False
    assert final["blocks"][-1] == {"type": "context", "elements": [
        {"type": "mrkdwn", "text": f":blue_heart: <@{env.ids['bot_user_id']}>"}]}


# ---------------------------------------------------------------- 3 no decision

def test_03_no_decision_means_no_call_and_no_op_row(env):
    """Choice documented in the plan: refusals leave no `ops` row; they are a `calls` row (status refused)."""
    refused(env, post(), "no_decision")
    assert env.transport.calls == []
    assert env.journal.ops() == []
    assert [c["status"] for c in env.journal.calls()] == ["refused"]
    assert env.journal.calls()[0]["error_code"] == "no_decision"


def test_03b_hook_without_provenance_asks_with_migration_warning(env):
    ti = {"method": "chat.postMessage", "params": {"channel": CHANNEL, "text": "hi"}, "as_bot": True}
    out = env.hook(ti)
    assert out["permissionDecision"] == "ask"
    assert set(out["updatedInput"]["provenance"]) == {"decision"}      # compact wire: nothing echoed
    assert "provenance missing" not in out["permissionDecisionReason"]  # kept in the verdict/journal only
    env.send(out["updatedInput"])
    assert len(env.transport.calls) == 1


# ---------------------------------------------------------------- 4 freeze

def test_04_freeze_blocks_every_send_reads_continue(env):
    ti = env.approve(post())        # approved before the freeze
    env.write(freeze=True)
    refused(env, ti, "frozen")
    assert env.hook(post())["permissionDecision"] == "deny"
    assert env.hook({"method": "reactions.add", "params": {"channel": CHANNEL}})["permissionDecision"] == "deny"
    assert env.hook(post(channel=env.ids["person_bot_dm_id"]))["permissionDecision"] == "deny"
    assert env.hook({"method": "conversations.history", "params": {"channel": CHANNEL}})["permissionDecision"] == "allow"
    door = Door(transport=env.transport)
    assert door.call("conversations.history", {"channel": CHANNEL}).verdict.decision == "allow"
    assert [c[0] for c in env.transport.calls] == ["conversations.history"]


# ---------------------------------------------------------------- 5 deny lists per class

POLICY_DENY = f"""
version: "2026-10-28.1"
ratified_by: test
ratified_at: "2026-10-28"
channels:
  {CHANNEL}:
    class: working
    voices: [person, bot]
    approval: explicit-greenlight
    modes: {{A: human, B-h: human, B-a: stamp, C1: stamp, C2: deny}}
recipients_deny_autonomous: [U0VIPUSER]
recipients_deny:
  A: [U0NEVERME]
"""


def test_05_deny_list_applies_per_class_and_approval_does_not_override(env):
    env.policy(POLICY_DENY)
    # autonomous class → refused, and the deny list is among the reasons (not only the missing notary)
    auto = post(cls="B-a", target={"channel": CHANNEL, "users": ["U0VIPUSER"]})
    e = refused(env, auto, "user_token_in_process")
    assert "recipient_denied" in e.verdict.reasons and "no_notary" in e.verdict.reasons
    # same recipient, class A + human decision → allowed (deny_autonomous is for B-a/C1 only)
    ok = env.approve(post(as_bot=False, target={"channel": CHANNEL, "users": ["U0VIPUSER"]}))
    env.send(ok)
    assert len(env.transport.calls) == 1
    # a deny list that names class A holds even against a valid human decision
    blocked = post(as_bot=False, target={"channel": CHANNEL, "users": ["U0NEVERME"]})
    assert env.hook(blocked)["permissionDecision"] == "deny"
    v = policy_mod.evaluate("chat.postMessage", blocked["params"], False, blocked["provenance"], env.profile,
                            journal=env.journal)
    secret = decision_mod.load_secret(env.journal_dir)
    tok = decision_mod.issue_token(secret, v.op_hash)
    env.journal.store_decision(tok, v.op_hash, "cc-hook")
    blocked["provenance"]["decision"] = {"adapter": "cc-hook", "token": tok}
    e = refused(env, blocked, "recipient_denied")
    assert len(env.transport.calls) == 1


# ---------------------------------------------------------------- 6 ttl / prepared_at

def test_06_ttl_from_the_journal_and_future_prepared_at(env):
    env.journal.note_comm_seen("comm-old", at=time.time() - 7200)
    ti = post(comm_id="comm-old", ttl_s=3600)
    assert env.hook(ti)["permissionDecision"] == "deny"
    refused(env, ti, "ttl_expired")
    # caller claims a fresh prepared_at — the journal's first sighting wins (invariant x)
    refused(env, post(comm_id="comm-old", ttl_s=3600, prepared_at=time.time()), "ttl_expired")
    refused(env, post(comm_id="comm-new", prepared_at=time.time() + 120), "prepared_at_future")
    env.journal.note_comm_seen("comm-skew", at=time.time() + 600)
    refused(env, post(comm_id="comm-skew"), "prepared_at_future")
    assert env.transport.calls == []


def test_06b_prepared_as_human_needs_ttl_and_person_voice(env):
    refused(env, post(as_bot=False, origin="prepared-as-human"), "ttl_required")
    refused(env, post(as_bot=True, origin="prepared-as-human", ttl_s=60), "prepared_as_human_bot_voice")
    assert env.transport.calls == []


# ---------------------------------------------------------------- 7 changed after decision

def test_07_payload_or_voice_changed_after_decision(env):
    ti = env.approve(post(text="approved text"))
    changed = {**ti, "params": {**ti["params"], "text": "something else"}}
    refused(env, changed, "decision_mismatch")
    retarget = {**ti, "params": {**ti["params"], "channel": "C0OTHERCH1"}}
    refused(env, retarget, "decision_mismatch")
    broadcast = {**ti, "params": {**ti["params"], "thread_ts": "1.1", "reply_broadcast": True}}
    refused(env, broadcast, "decision_mismatch")
    revoice = {**ti, "as_bot": False, "provenance": {**ti["provenance"], "cls": "A", "voice": "person"}}
    refused(env, revoice, "decision_mismatch")
    assert env.transport.calls == []
    env.send(ti)   # the untouched approved operation still goes
    assert len(env.transport.calls) == 1


# ---------------------------------------------------------------- 8 journal unavailable

def test_08_journal_unwritable_refuses_every_send(env):
    reset_cache()
    env.journal_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(env.journal_dir, 0o500)
    try:
        for ti in (post(), post(as_bot=False), post(channel=env.ids["person_bot_dm_id"])):
            refused(env, ti, "journal_unavailable")
            out = env.hook(ti)
            assert out["permissionDecision"] == "deny" and "journal_unavailable" in out["permissionDecisionReason"]
        assert env.transport.calls == []
        # reads are not blocked by a missing journal
        assert env.hook({"method": "conversations.history", "params": {"channel": CHANNEL}})["permissionDecision"] == "allow"
    finally:
        os.chmod(env.journal_dir, 0o700)


# ---------------------------------------------------------------- 9 decision reuse

def test_09_repeat_with_the_same_decision_is_refused(env):
    ti = env.approve(post())
    env.send(ti)
    refused(env, ti, "decision_already_used")
    fresh_op = {**ti, "provenance": {**ti["provenance"], "op_id": "another-op"}}   # caller op_id wins
    refused(env, fresh_op, "decision_already_used")
    assert len(env.transport.calls) == 1


# ---------------------------------------------------------------- 10 crash

def test_10_crash_between_call_and_outcome_leaves_pending_then_unknown(env):
    ti = env.approve(post())
    env.transport.exc = ProcessDied()
    with pytest.raises(ProcessDied):
        env.send(ti)
    op_id = op_of(env, ti)
    assert env.journal.get_op(op_id)["outcome"] == "pending"
    reset_cache()
    restarted = Journal(env.journal_dir, stale_after=0)
    assert restarted.get_op(op_id)["outcome"] == "unknown"
    assert len(env.transport.calls) == 1          # no automatic retry
    env.transport.exc = None
    refused(env, ti, "decision_already_used")     # the same approval cannot re-send it
    assert len(env.transport.calls) == 1


def test_10b_ambiguous_transport_error_is_unknown_not_retried(env):
    ti = env.approve(post())
    env.transport.exc = TimeoutError("read timed out")
    with pytest.raises(core.SendFailed) as e:
        env.send(ti)
    assert e.value.outcome == "unknown"
    assert env.journal.get_op(op_of(env, ti))["outcome"] == "unknown"
    assert len(env.transport.calls) == 1


def test_10c_slack_error_is_error_outcome(env):
    from slack_sdk.errors import SlackApiError
    ti = env.approve(post())
    env.transport.exc = SlackApiError("x", {"ok": False, "error": "channel_not_found"})
    with pytest.raises(core.SendFailed) as e:
        env.send(ti)
    assert e.value.code == "channel_not_found" and e.value.outcome == "error"
    assert env.journal.get_op(op_of(env, ti))["error_code"] == "channel_not_found"


# ---------------------------------------------------------------- 11 concurrency

def test_11_two_threads_one_approved_op_one_send(env):
    ti = env.approve(post())
    barrier = threading.Barrier(2)
    results = []

    def go():
        barrier.wait()
        try:
            env.send(ti)
            results.append("sent")
        except core.Refused as e:
            results.append(e.code)

    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(env.transport.calls) == 1
    assert results.count("sent") == 1
    assert set(results) - {"sent"} <= {"decision_already_used", "duplicate_op_id"}


# ---------------------------------------------------------------- 12 comm_id

def test_12_same_comm_id_needs_resend_and_a_fresh_decision(env):
    env.send(env.approve(post(comm_id="comm-7")))
    out = env.hook(post(comm_id="comm-7"))
    assert out["permissionDecision"] == "deny" and "duplicate_comm_id" in out["permissionDecisionReason"]
    again = env.approve(post(comm_id="comm-7", resend=True))
    assert "Resend." in env.hook(post(comm_id="comm-7", resend=True))["permissionDecisionReason"]
    env.send(again)
    assert len(env.transport.calls) == 2
    assert [r["outcome"] for r in env.journal.find_by_comm_id("comm-7")] == ["ok", "ok"]


# ---------------------------------------------------------------- 13 policy states

def test_13_invalid_policy_refuses_writes_allows_reads(env):
    ti = env.approve(post())
    env.policy("version: [unclosed\n")
    refused(env, ti, "policy_invalid")
    assert env.hook(post())["permissionDecision"] == "deny"
    assert env.hook({"method": "conversations.history", "params": {"channel": CHANNEL}})["permissionDecision"] == "allow"
    # valid yaml, wrong pin
    env.policy(POLICY_DENY, pin="0" * 64)
    refused(env, ti, "policy_invalid")
    # valid yaml, schema error
    env.policy("version: 1\nchannels:\n  C1:\n    modes: {B-h: auto}\n")
    assert "policy_invalid" in env.hook(post())["permissionDecisionReason"]
    assert env.transport.calls == []


def test_13b_pinned_valid_policy_and_missing_policy(env):
    import hashlib
    env.policy(POLICY_DENY, pin=hashlib.sha256(POLICY_DENY.encode()).hexdigest())
    v = policy_mod.evaluate("chat.postMessage", {"channel": CHANNEL, "text": "x"}, True, {"cls": "B-h"},
                            env.profile, journal=env.journal)
    assert v.decision == "needs-human" and v.policy_state == "file" and v.policy_version == "2026-10-28.1"
    assert v.approval == "explicit-greenlight"
    env.write(policy_path=str(env.cfg_dir / "nope.yaml"), policy_sha256=None)
    fields = dict(env.fields)
    fields.pop("policy_sha256")
    (env.cfg_dir / f"{env.name}.yaml").write_text(yaml.safe_dump(fields))
    v = policy_mod.evaluate("chat.postMessage", {"channel": CHANNEL, "text": "x"}, True, {"cls": "B-h"},
                            env.profile, journal=env.journal)
    assert v.decision == "needs-human" and v.policy_state == "builtin"
    assert any("policy file missing" in w for w in v.warnings)


# ---------------------------------------------------------------- 14 conversations.open

def test_14_conversations_open_split_by_effect(env):
    look = env.hook({"method": "conversations.open", "params": {"users": "U0PEER0001", "prevent_creation": True}})
    assert look["permissionDecision"] == "allow"
    assert policy_mod.classify("conversations.open", {"users": "U1", "prevent_creation": "true"}).row == 0
    dm = env.hook({"method": "conversations.open", "params": {"users": "U0PEER0001"}})
    assert dm["permissionDecision"] == "ask" and "1:1 DM with U0PEER0001" in dm["permissionDecisionReason"]
    assert "transitional" in dm["permissionDecisionReason"]
    mpim = env.hook({"method": "conversations.open", "params": {"users": "U1,U2", "prevent_creation": True}})
    assert mpim["permissionDecision"] == "ask" and "group DM (MPIM)" in mpim["permissionDecisionReason"]
    ch = env.hook({"method": "conversations.open", "params": {"channel": "D0X", "prevent_creation": True}})
    assert ch["permissionDecision"] == "ask"
    assert policy_mod.classify("conversations.open", {"users": "U1,U2"}).row == 4


# ---------------------------------------------------------------- 15 identity + rights

@pytest.mark.parametrize("param", ["username", "icon_emoji", "icon_url"])
def test_15_identity_overrides_never_reach_slack(env, param):
    from atlas_slack import gateway
    ti = post()
    ti["params"][param] = "x"
    out = env.hook(ti)
    assert out["permissionDecision"] == "deny" and "identity_override" in out["permissionDecisionReason"]
    refused(env, ti, "identity_override")
    mock = MagicMock()
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        for method in ("chat.postMessage", "chat.update", "chat.scheduleMessage", "chat.postEphemeral"):
            with pytest.raises(ToolError, match="identity_override"):
                gateway.slack_api(method, {"channel": CHANNEL, "text": "x", "ts": "1.1", param: "x"}, as_bot=True)
    mock.api_call.assert_not_called()
    assert env.transport.calls == []


def test_15b_rights_table_shows_autonomous_classes_disabled(env):
    text = rights()
    assert f"profile {env.name} (file)" in text and "builtin-strict-1" in text
    row1 = next(line for line in text.splitlines() if line.startswith("1 message to others"))
    assert row1.count("disabled") == 2 and "not enabled by policy" in row1 and "no notary" in row1
    assert row1.count("human decision") == 2
    from atlas_slack import gateway
    assert gateway.slack_api("_rights") == text
    assert env.hook({"method": "_rights"})["permissionDecision"] == "allow"
    env.write(freeze=True)
    assert "YES — every write refuses" in rights()


# ---------------------------------------------------------------- 17 library face

def test_17_library_door_without_adapter(env):
    door = Door(transport=env.transport)
    res = door.call("chat.postMessage", {"channel": CHANNEL, "text": "x"}, as_bot=True, provenance={"cls": "B-h"})
    assert res.verdict.decision == "refuse" and res.verdict.reason == "no_decision_adapter"
    # an approval token minted for the hook does not help the library either
    ti = env.approve(post())
    res = door.call(ti["method"], ti["params"], True, ti["provenance"])
    assert res.verdict.reason == "no_decision_adapter"
    assert env.transport.calls == []
    assert door.call("conversations.history", {"channel": CHANNEL}).ok
    assert door.call("reactions.add", {"channel": CHANNEL}).verdict.reason == "not_in_slice"
    assert len(env.transport.calls) == 1


def test_17b_manifest_read_scope_and_allowed_methods(env):
    door = Door(transport=env.transport, manifest={
        "cls": "C2", "allowed_methods": ["conversations.history", "users.info", "search.messages",
                                         "chat.postMessage"],
        "allowed_targets": [env.ids["person_bot_dm_id"]], "read_scope": {"channels": [CHANNEL], "methods": []}})
    assert door.call("conversations.history", {"channel": CHANNEL}).ok
    assert door.call("conversations.history", {"channel": "C0SECRET01"}).verdict.reason == "read_scope_violation"
    assert door.call("search.messages", {"query": "x"}).verdict.reason == "read_scope_violation"
    assert door.call("users.info", {"user": "U1"}).verdict.reason == "read_scope_violation"
    assert door.call("conversations.replies", {"channel": CHANNEL}).verdict.reason == "method_not_in_manifest"
    r = door.call("chat.postMessage", {"channel": CHANNEL, "text": "x"}, as_bot=True, provenance={"cls": "B-h"})
    assert r.verdict.decision == "refuse"
    assert "class_not_in_manifest" in r.verdict.reasons and "target_not_in_manifest" in r.verdict.reasons
    assert len(env.transport.calls) == 1


def test_17c_doctor_checks_auth_test_against_profile(env):
    door = Door(transport=env.transport)
    ids = env.ids
    ok_bot = {"ok": True, "team_id": ids["workspace_id"], "user_id": ids["bot_user_id"], "bot_id": "B1"}
    ok_user = {"ok": True, "team_id": ids["workspace_id"], "user_id": ids["person_user_id"]}
    assert door.doctor(ok_bot, as_bot=True) == []
    assert door.doctor(ok_user, as_bot=False) == []
    other_ws = {**ok_bot, "team_id": "T0SOMEOTHER"}
    assert any("workspace" in p for p in door.doctor(other_ws, as_bot=True))
    assert any("bot_id" in p for p in door.doctor({**ok_user, "bot_id": "B9"}, as_bot=False))
    assert any("user_id" in p for p in door.doctor({**ok_bot, "user_id": "U0STRANGER"}, as_bot=True))
    assert env.transport.calls == []


# ---------------------------------------------------------------- profile edge cases

def test_profile_missing_named_file_breaks_writes_only(isolated_door_dirs, monkeypatch):
    monkeypatch.setenv("ATLAS_SLACK_PROFILE", "ghost")
    p = load_profile()
    assert p.broken and p.source == "builtin"
    v = policy_mod.evaluate("chat.postMessage", {"channel": "C1", "text": "x"}, True, {"cls": "B-h"}, p)
    assert v.reason == "profile_missing"
    assert policy_mod.evaluate("conversations.history", {"channel": "C1"}, False, None, p).decision == "allow"


def test_profile_rejects_secrets_and_unknown_keys(isolated_door_dirs, monkeypatch):
    cfg = isolated_door_dirs / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "leaky.yaml").write_text("bot_user_id: xoxb-123-abc\ncolour: blue\n")
    monkeypatch.setenv("ATLAS_SLACK_PROFILE", "leaky")
    p = load_profile()
    assert p.broken and any("token" in e for e in p.errors) and any("unknown" in e for e in p.errors)


def test_builtin_profile_without_bot_id_refuses_person_voice(isolated_door_dirs, monkeypatch):
    monkeypatch.delenv("ATLAS_BOT_USER_ID", raising=False)
    monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-x")
    p = load_profile()
    assert p.source == "builtin" and p.bot_user_id is None
    e = Env.__new__(Env)
    e.transport, e.journal_dir = FakeTransport(), isolated_door_dirs / "data" / "default"
    ti = hook_output({"tool_name": TOOL, "tool_input": post(as_bot=False)})["hookSpecificOutput"]["updatedInput"]
    with pytest.raises(core.Refused) as err:
        core.execute_send(ti["method"], ti["params"], False, ti["provenance"], profile=p, transport=e.transport)
    assert err.value.code == "attribution_unavailable"
    assert e.transport.calls == []
    assert open_journal(e.journal_dir).ops() == []


def test_17d_door_send_failure_keeps_the_original_verdict(env):
    """A library C2 report under a policy that enables it: Slack fails → the verdict stays the core's."""
    env.policy(f"""
version: "r1"
channels:
  {env.ids['person_bot_dm_id']}:
    class: dm
    voices: [bot]
    modes: {{C2: auto}}
""")
    from slack_sdk.errors import SlackApiError
    env.transport.exc = SlackApiError("x", {"ok": False, "error": "not_in_channel"})
    door = Door(transport=env.transport)
    res = door.call("chat.postMessage", {"channel": env.ids["person_bot_dm_id"], "text": "report"}, as_bot=True,
                    provenance={"cls": "C2"})
    assert res.error == "not_in_channel (error)" and res.verdict.reason == "policy_auto"
    assert len(env.transport.calls) == 1
    env.transport.exc = None
    ok = door.call("chat.postMessage", {"channel": env.ids["person_bot_dm_id"], "text": "report 2"}, as_bot=True,
                   provenance={"cls": "C2"})
    assert ok.ok and env.journal.get_op(ok.op_id)["outcome"] == "ok"


def test_17e_door_never_runs_transitional_writes(env):
    door = Door(transport=env.transport)
    for method in ("chat.appendStream", "agents.sessions.setStatus", "chat.update", "conversations.open"):
        assert door.call(method, {"channel": CHANNEL}, as_bot=True).verdict.reason == "not_in_slice", method
    assert env.transport.calls == []


# ---------------------------------------------------------------- verifier findings (2026-10-28 night)

def _mcp_mock():
    mock = MagicMock()
    mock.api_call.return_value = {"ok": True, "channel": CHANNEL, "ts": "2.2", "message": {"ts": "2.2", "text": "x"}}
    return mock


@pytest.mark.parametrize("name", ["chat.postMessage ", " chat.postMessage", "chat.PostMessage", "CHAT.POSTMESSAGE",
                                  "chat.postMessage\n", "chat..postMessage"])
def test_v1_noncanonical_method_names_never_reach_slack(env, name):
    from atlas_slack import gateway
    ti = {**post(), "method": name}
    out = env.hook(ti)
    assert out["permissionDecision"] == "deny" and "method_noncanonical" in out["permissionDecisionReason"]
    mock = _mcp_mock()
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        with pytest.raises(ToolError, match="method_noncanonical"):
            gateway.slack_api(name, ti["params"], as_bot=True)
    mock.api_call.assert_not_called()
    assert Door(transport=env.transport).call(name, ti["params"], True, ti["provenance"]).verdict.reason \
        == "method_noncanonical"
    assert env.transport.calls == []


def test_v2_library_refuses_unregistered_reads_and_runs_rights_locally(env):
    door = Door(transport=env.transport)
    for name in ("auth.futureMutation", "team.somethingNew", "widgets.get"):
        assert door.call(name, {}).verdict.reason == "not_in_slice", name
    r = door.call("_rights")
    assert r.ok and "atlas-slack rights" in r.data["rights"]
    assert env.transport.calls == []
    # the MCP face keeps the legacy heuristic on the transitional path, flagged
    assert env.hook({"method": "auth.futureMutation"})["permissionDecision"] == "allow"


def test_v3_sent_but_outcome_not_journaled_is_reported_not_failed(env, monkeypatch):
    from atlas_slack import gateway
    from atlas_slack.journal import Journal, JournalUnavailable
    ti = env.approve(post())

    def boom(self, *a, **k):
        raise JournalUnavailable("disk full (simulated)")

    monkeypatch.setattr(Journal, "record_outcome", boom)
    res = env.send(ti)
    assert res.journal_error and "disk full" in res.journal_error and len(env.transport.calls) == 1
    ti2 = env.approve(post(text="second"))
    mock = _mcp_mock()
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        out = gateway.slack_api(**ti2)
    assert "outcome_not_journaled" in out and "do not resend" in out and mock.api_call.call_count == 1
    monkeypatch.undo()
    assert env.journal.get_op(op_of(env, ti2))["outcome"] == "pending"


def test_v4_recipients_comm_id_and_resend_are_bound(env):
    ti = env.approve(post(target={"channel": CHANNEL, "users": ["U0ALICE01"]}, comm_id="comm-b"))
    other_users = {**ti, "provenance": {**ti["provenance"], "target": {"channel": CHANNEL, "users": ["U0MALLORY"]}}}
    refused(env, other_users, "decision_mismatch")
    other_comm = {**ti, "provenance": {**ti["provenance"], "comm_id": "comm-c"}}
    refused(env, other_comm, "decision_mismatch")
    flipped = {**ti, "provenance": {**ti["provenance"], "resend": True}}
    refused(env, flipped, "decision_mismatch")
    assert env.transport.calls == []


@pytest.mark.parametrize("prov", [None, {"cls": "B-h"},
                                  {"cls": "B-h", "decision": {"adapter": "cc-hook", "token": "deadbeef.00"}},
                                  {"cls": "B-h", "decision": {"adapter": "cc-hook", "token": ""}}],
                         ids=["no-provenance", "no-token", "forged-token", "empty-token"])
def test_v5_gateway_zero_calls_without_a_real_decision(env, prov):
    from atlas_slack import gateway
    mock = _mcp_mock()
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        with pytest.raises(ToolError) as e:
            gateway.slack_api("chat.postMessage", {"channel": CHANNEL, "text": "x"}, as_bot=True, provenance=prov)
    assert any(c in str(e.value) for c in ("no_decision", "decision_mismatch")), str(e.value)
    mock.api_call.assert_not_called()
    assert env.journal.ops() == []


@pytest.mark.parametrize("mode,decision", [("default", "ask"), ("auto", "ask"), ("acceptEdits", "ask"),
                                           ("plan", "ask"), (None, "ask"), ("bypassPermissions", "deny"),
                                           ("dontAsk", "deny"), ("somethingNew", "deny")])
def test_v6_decisions_only_where_ask_reaches_a_human(env, mode, decision):
    data = {"tool_name": TOOL, "tool_input": post()}
    if mode is not None:
        data["permission_mode"] = mode
    out = hook_output(data)["hookSpecificOutput"]
    assert out["permissionDecision"] == decision
    if decision == "deny":
        assert "permission_mode_unsafe" in out["permissionDecisionReason"] and "updatedInput" not in out


def test_v6b_profile_can_narrow_decision_modes(env):
    env.write(decision_permission_modes=["default"])
    out = hook_output({"tool_name": TOOL, "tool_input": post(), "permission_mode": "auto"})["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"


def test_v7_hook_and_server_on_different_profiles(env, monkeypatch):
    ti = env.approve(post())
    monkeypatch.delenv("ATLAS_SLACK_PROFILE")
    with pytest.raises(core.Refused) as e:
        core.execute_send(ti["method"], ti["params"], True, ti["provenance"], profile=load_profile(),
                          transport=env.transport)
    assert e.value.code == "profile_mismatch" and env.name in e.value.message
    assert env.transport.calls == []


def test_v8_unconsumed_decisions_are_pruned(env):
    j = env.journal
    j.store_decision("old.1", "h-old", "cc-hook")
    j._conn.execute("UPDATE decisions SET issued_at = issued_at - 90000")   # a day + (prune horizon 24 h since 0.5.1)
    j.store_decision("new.1", "h-new", "cc-hook")
    assert j.get_decision("old.1") is None and j.get_decision("new.1") is not None


def test_v9_gate_cli_is_a_dry_run(env):
    import subprocess, sys, json as _json
    ti = {"tool_input": post(comm_id="comm-dry")}
    proc = subprocess.run([sys.executable, "-m", "atlas_slack.gate"], input=_json.dumps(ti), capture_output=True,
                          text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
    assert _json.loads(proc.stdout)["decision"] == "needs-human"
    assert env.journal.comm_first_seen("comm-dry") is None


def test_v10_unexpanded_env_placeholder_is_unset(isolated_door_dirs, monkeypatch):
    monkeypatch.setenv("ATLAS_SLACK_PROFILE", "${ATLAS_SLACK_PROFILE}")
    monkeypatch.setenv("ATLAS_BOT_USER_ID", "${ATLAS_BOT_USER_ID}")
    p = load_profile()
    assert p.name == "default" and not p.broken and p.bot_user_id is None


def test_v11_every_emitted_error_code_is_in_the_contract():
    """docs/output-contract.md §10b lists every code the door can refuse with (verifier MISMATCH 3)."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    src = "\n".join((root / "src" / "atlas_slack" / f).read_text()
                    for f in ("policy.py", "core.py", "decision.py", "journal.py", "gate.py", "gateway.py"))
    codes = set(re.findall(r'(?:\.refuse\(|_refuse\(\s*verdict,|_base\(ctx, REFUSE,|DecisionCheck\(False,|'
                           r'DecisionRejected\(|_slack_error\()\s*"([a-z_]+)"', src))
    codes |= {"profile_missing", "profile_invalid",            # via _profile_error_code()
              "permission_mode_unsafe", "outcome_not_journaled", "outcome_unknown"}
    contract = (root / "docs" / "output-contract.md").read_text()
    section = contract[contract.index("## 10. Error policy"):contract.index("### How hints work")]
    missing = sorted(c for c in codes if f"`{c}`" not in section)
    assert len(codes) > 25
    assert missing == [], missing


# ---------------------------------------------------------------- slice 2, part 1: chat.scheduleMessage
class ScheduleTransport(FakeTransport):
    def __call__(self, method, params, as_bot):
        with self.lock:
            self.calls.append((method, dict(params), as_bot))
        return {"ok": True, "channel": params.get("channel"), "scheduled_message_id": f"Q{len(self.calls):06d}",
                "post_at": params.get("post_at"), "message": {"text": params.get("text", "")}}


def schedule(text="later", as_bot=True, channel=CHANNEL, lead=3600, **prov):
    p = {"cls": "B-h" if as_bot else "A", **prov}
    return {"method": "chat.scheduleMessage", "params": {"channel": channel, "text": text, "post_at": int(time.time()) + lead},
            "as_bot": as_bot, "provenance": p}


def test_s1_schedule_runs_through_the_core_and_binds_post_at(env):
    env.transport = ScheduleTransport()
    out = env.hook(schedule(comm_id="20261031-prompt"))
    assert out["permissionDecision"] == "ask"
    assert "scheduled for" in out["permissionDecisionReason"] or "отложено на" in out["permissionDecisionReason"]
    ti = env.approve(schedule(comm_id="20261031-sched"))
    res = env.send(ti)
    assert res.data["ok"] and len(env.transport.calls) == 1
    row = next(r for r in env.journal.ops() if r["op_id"] == res.op_id)
    assert row["method"] == "chat.scheduleMessage" and row["outcome"] == "ok"
    assert row["slack_ts"].startswith("Q")                                  # scheduled_message_id is the outcome id
    assert '"scheduled_for"' in row["provenance_json"]
    assert any("scheduled send" in w for w in res.verdict.warnings)
    ti2 = env.approve(schedule(comm_id="20261031-sched2"))
    ti2["params"]["post_at"] += 600                                        # a later time = a different operation
    refused(env, ti2, "decision_mismatch")
    assert len(env.transport.calls) == 1


def test_s2_schedule_refusals_before_any_call(env):
    env.transport = ScheduleTransport()
    missing = schedule(comm_id="s-missing"); del missing["params"]["post_at"]
    assert "missing_params" in env.hook(missing)["permissionDecisionReason"]
    assert "schedule_in_past" in env.hook(schedule(comm_id="s-past", lead=10))["permissionDecisionReason"]
    assert "schedule_too_far" in env.hook(schedule(comm_id="s-far", lead=200 * 86400))["permissionDecisionReason"]
    ti = schedule(comm_id="s-mismatch", scheduled_for=int(time.time()) + 7200)
    assert "schedule_mismatch" in env.hook(ti)["permissionDecisionReason"]
    for bad in ("nan", float("nan"), "inf", float("inf"), "-inf"):
        t = schedule(comm_id=f"s-{bad}"); t["params"]["post_at"] = bad
        out = env.hook(t)
        assert out["permissionDecision"] == "deny" and "missing_params" in out["permissionDecisionReason"], (bad, out)
    assert env.transport.calls == []


def test_s3_schedule_through_the_mcp_server_keeps_attribution(env):
    from atlas_slack import gateway
    import json as _json
    mock = MagicMock()
    mock.api_call.return_value = {"ok": True, "channel": CHANNEL, "scheduled_message_id": "Q1", "post_at": 1,
                                  "message": {"text": "x"}}
    ti = env.approve(schedule(comm_id="20261031-mcp", as_bot=False))
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        out = gateway.slack_api(**ti)
    assert mock.api_call.call_count == 1
    assert "journal: op" in out and "· comm 20261031-mcp" in out
    sent = mock.api_call.call_args.kwargs["params"]
    assert _json.loads(sent["blocks"])[-1]["type"] == "context"             # the person-voice footer survives the core
