"""Approval prompt UX + compact wire (live-check finding 2026-10-28, «сейчас это бесполезный запрос»)."""

import json
import time

import pytest
import yaml

from atlas_slack import core, decision as decision_mod
from atlas_slack.gate import describe_send, hook_output
from atlas_slack.journal import open_journal
from atlas_slack.names import Names
from atlas_slack.policy import evaluate
from atlas_slack.profile import load_profile

TOOL = "mcp__plugin_atlas-slack_atlas-slack__slack_api"
IDS = {"workspace_id": "T0WS000001", "person_user_id": "U0PERSON01", "bot_user_id": "U0BOTUSR01",
       "person_bot_dm_id": "D0BOTDM001"}


class FakeSlack:
    """users.info / conversations.info only; records calls."""

    USERS = {"U0PERSON01": {"profile": {"display_name": "Аня"}}, "U0BOTUSR01": {"profile": {"real_name": "Atlas"}},
             "U0ALICE001": {"name": "alice"}}
    CHANNELS = {"C0PLATFORM": {"name": "team-platform"}, "D0BOTDM001": {"is_im": True, "user": "U0PERSON01"},
                "D0ALICEDM1": {"is_im": True, "user": "U0ALICE001"}}

    def __init__(self, calls):
        self.calls = calls

    def api_call(self, method, params=None):
        self.calls.append((method, dict(params or {})))
        if method == "users.info" and params["user"] in self.USERS:
            return {"ok": True, "user": self.USERS[params["user"]]}
        if method == "conversations.info" and params["channel"] in self.CHANNELS:
            return {"ok": True, "channel": self.CHANNELS[params["channel"]]}
        return {"ok": False, "error": "not_found"}


class Broken:
    def api_call(self, method, params=None):
        raise TimeoutError("slack down")


@pytest.fixture
def prof(isolated_door_dirs, monkeypatch):
    cfg = isolated_door_dirs / "config"
    cfg.mkdir(exist_ok=True)

    def make(**extra):
        (cfg / "p.yaml").write_text(yaml.safe_dump({**IDS, **extra}))
        monkeypatch.setenv("ATLAS_SLACK_PROFILE", "p")
        return load_profile()
    return make


def verdict(profile, channel, text, as_bot=True, **prov):
    p = {"cls": "B-h" if as_bot else "A", **prov}
    return evaluate("chat.postMessage", {"channel": channel, "text": text}, as_bot, p, profile,
                    journal=open_journal(profile.journal_dir))


def live_names(profile, monkeypatch, calls):
    monkeypatch.setenv("ATLAS_SLACK_RESOLVE_NAMES", "1")
    return Names(profile, client_factory=lambda as_bot: FakeSlack(calls))


def test_prompt_snapshot_ru(prof, monkeypatch):
    p = prof(language="ru")
    calls = []
    v = verdict(p, "D0BOTDM001", "v2 check 1", comm_id="v2check-1")
    text = describe_send(v, {"channel": "D0BOTDM001", "text": "v2 check 1"}, True, p,
                         live_names(p, monkeypatch, calls), op_short="20e2ea86")
    assert text == (
        "Atlas → DM Аня↔Atlas: “v2 check 1”. Голос бота, по твоему запросу. Отправить?\n"
        "⚠ политики контекста нет — действует встроенная строгая\n"
        "class B-h · policy builtin-strict-1 · comm v2check-1 · op 20e2ea86")


def test_prompt_snapshot_en_channel_and_person_voice(prof, monkeypatch):
    p = prof()
    calls = []
    names = live_names(p, monkeypatch, calls)
    v = verdict(p, "C0PLATFORM", "Deploy is done.", as_bot=False)
    text = describe_send(v, {"channel": "C0PLATFORM", "text": "Deploy is done.", "thread_ts": "1.1"}, False, p, names,
                         op_short="abcd1234")
    assert text.splitlines()[0] == ("Аня → #team-platform (in thread): “Deploy is done.”. "
                                    "In your voice, marked 💙 Atlas. Send?")
    assert text.splitlines()[-1] == "class A · policy builtin-strict-1 · op abcd1234"
    for noise in ("row", "mode human", "profile p", "checks declared", "origin live", "op hash"):
        assert noise not in text


def test_prompt_long_text_and_extras(prof, monkeypatch):
    p = prof()
    names = live_names(p, monkeypatch, [])
    long = "x" * 450
    v = verdict(p, "C0PLATFORM", long, checks={"recipient": "team-platform members"})
    text = describe_send(v, {"channel": "C0PLATFORM", "text": long, "reply_broadcast": True}, True, p, names)
    lines = text.splitlines()
    assert lines[0] == "Atlas → #team-platform + also to the channel: message below (450 chars). " \
                       "Bot voice, on your request. Send?"
    assert lines[1] == "x" * 400 + "… (450 chars)"
    assert "checks: recipient=team-platform members" in lines
    assert "params reply_broadcast" in lines[-1]


def test_names_are_cached_24h_and_profile_override_wins(prof, monkeypatch):
    p = prof(names={"U0PERSON01": "Ann"})
    calls = []
    n = live_names(p, monkeypatch, calls)
    assert n.channel("D0ALICEDM1", True) == ("DM alice", True)
    n.save()
    assert json.loads(n.path.read_text())["chan:D0ALICEDM1"]["kind"] == "im"
    assert n.user("U0PERSON01") == "Ann" and ("users.info", {"user": "U0PERSON01"}) not in calls
    before = len(calls)
    n2 = Names(p, client_factory=lambda b: FakeSlack(calls))
    assert n2.channel("D0ALICEDM1", True) == ("DM alice", True) and len(calls) == before   # cache hit
    stale = Names(p, client_factory=lambda b: FakeSlack(calls), now=time.time() + 25 * 3600)
    stale.channel("D0ALICEDM1", True)
    assert len(calls) > before                                                              # TTL expired


def test_failing_resolution_falls_back_to_ids(prof, monkeypatch):
    p = prof()
    monkeypatch.setenv("ATLAS_SLACK_RESOLVE_NAMES", "1")
    names = Names(p, client_factory=lambda b: Broken())
    v = verdict(p, "C0PLATFORM", "hi")
    first = describe_send(v, {"channel": "C0PLATFORM", "text": "hi"}, True, p, names).splitlines()[0]
    assert first == "the bot → C0PLATFORM (unresolved): “hi”. Bot voice, on your request. Send?"
    assert not names.path.exists()   # failures are never cached


def test_resolution_off_makes_no_calls(prof):
    p = prof()
    calls = []
    n = Names(p, client_factory=lambda b: FakeSlack(calls))   # env ATLAS_SLACK_RESOLVE_NAMES=0 (conftest)
    assert n.channel("C0PLATFORM", True) == ("C0PLATFORM (unresolved)", False) and calls == []


def test_wire_payload_is_compact_and_token_short(prof, monkeypatch):
    prof()
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fake")
    caller = {"cls": "B-h", "comm_id": "c-9"}
    out = hook_output({"tool_name": TOOL, "session_id": "sess-1234",
                       "tool_input": {"method": "chat.postMessage", "params": {"channel": "C0PLATFORM", "text": "x"},
                                      "as_bot": True, "provenance": caller}})["hookSpecificOutput"]
    wire = out["updatedInput"]["provenance"]
    assert set(wire) == {"cls", "comm_id", "decision"} and set(wire["decision"]) == {"t", "p", "v"}
    tok = wire["decision"]["t"]
    nonce, mac = tok.split(".")
    assert len(nonce) == 16 and len(mac) == 32 and len(tok) == 49
    assert out["permissionDecisionReason"].splitlines()[-1].endswith(
        "op " + decision_mod.derive_op_id(
            open_journal(load_profile().journal_dir).get_decision(tok)["op_hash"], tok)[:8])


def test_server_derives_op_id_and_records_the_session(prof, monkeypatch):
    p = prof()
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fake")
    ti = {"method": "chat.postMessage", "params": {"channel": "C0PLATFORM", "text": "x"}, "as_bot": True}
    out = hook_output({"tool_name": TOOL, "session_id": "sess-abcd", "tool_input": ti})["hookSpecificOutput"]
    up = out["updatedInput"]
    assert set(up["provenance"]) == {"decision"}
    res = core.execute_send(up["method"], up["params"], True, up["provenance"], profile=p,
                            transport=lambda m, pr, b: {"ok": True, "ts": "1.1"})
    tok = up["provenance"]["decision"]["t"]
    row = open_journal(p.journal_dir).get_op(res.op_id)
    assert res.op_id == decision_mod.derive_op_id(row["payload_hash"], tok)
    assert row["prepared_by"] == "cc-session:sess-abcd" and row["cls"] == "B-h"
    assert tok not in json.dumps(row)


def test_profile_language_validated(prof):
    assert prof(language="de").broken


def test_resolution_reads_are_journalled_and_a_timeout_stops_further_reads(prof, monkeypatch):
    p = prof()
    calls = []
    names = live_names(p, monkeypatch, calls)
    names.channel("C0PLATFORM", True)
    rows = open_journal(p.journal_dir).calls()
    assert [(r["method"], r["caller"], r["status"]) for r in rows] == [("conversations.info", "names", "ok")]
    attempts = []

    class Slow:
        def api_call(self, method, params=None):
            attempts.append(method)
            raise TimeoutError("slow")

    n2 = Names(p, client_factory=lambda b: Slow(), now=time.time() + 30 * 3600)
    n2.channel("C0OTHER001", True)
    n2.user("U0ALICE001")
    assert attempts == ["conversations.info"]
