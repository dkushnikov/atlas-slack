"""Slice 2, part 2 (2026-10-31): every row-1 send through the core — chat.update, files.uploadV2,
chat.postEphemeral, chat.meMessage — plus bot-send metadata, `reconcile_required` and `_reconcile`.

Level 1: fake transport, no network, both profiles (see test_v2_slice.Env)."""

import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError
from slack_sdk.errors import SlackApiError

from atlas_slack import core, gateway, policy as policy_mod, prior as prior_mod, reconcile as rec, shaping
from atlas_slack.door import Door
from atlas_slack.gate import describe_send
from atlas_slack.journal import OpIntent
from test_v2_slice import CHANNEL, Env, FakeTransport, PROFILES, POLICY_DENY, post, refused  # noqa: F401

TS = "1700000000.000042"


@pytest.fixture(params=sorted(PROFILES))
def env(request, isolated_door_dirs, monkeypatch):
    return Env(request.param, isolated_door_dirs, monkeypatch)


class Slack(FakeTransport):
    """A transport that also answers the reads the core and the reconciler make."""

    def __init__(self, author=None, prior_text="old text", fail_on=None, upload_name="doc.txt"):
        super().__init__()
        self.author = author
        self.prior_text = prior_text
        self.fail_on = fail_on or {}          # method → exception
        self.upload_name = upload_name

    def __call__(self, method, params, as_bot):
        with self.lock:
            self.calls.append((method, dict(params), as_bot))
        if method in self.fail_on:
            raise self.fail_on[method]
        if self.exc is not None and method not in ("conversations.replies",):
            raise self.exc
        if method == "conversations.replies":
            return {"ok": True, "messages": [{"ts": params["ts"], "user": self.author, "text": self.prior_text,
                                               **({"bot_id": "B1"} if as_bot else {})}]}
        if method == "files.uploadV2":
            return {"ok": True, "file": {"id": "F0001", "name": self.upload_name, "title": params.get("title") or self.upload_name,
                                         "permalink": "https://x.slack.com/files/F0001"}}
        if method == "chat.postEphemeral":
            return {"ok": True, "message_ts": "1700000001.000001"}
        n = len(self.calls)
        ts = params.get("ts") if method == "chat.update" else f"1700000000.{n:06d}"
        return {"ok": True, "channel": params.get("channel"), "ts": ts,
                "message": {"ts": ts, "text": params.get("text", "")}}


def writes(tr):
    return [c for c in tr.calls if c[0] != "conversations.replies"]


def update(text="new text", as_bot=True, ts=TS, channel=CHANNEL, **prov):
    return {"method": "chat.update", "params": {"channel": channel, "ts": ts, "text": text}, "as_bot": as_bot,
            "provenance": {"cls": "B-h" if as_bot else "A", **prov}}


def upload(tmp_path, as_bot=False, comment="see attached", content=None, **extra):
    params = {"channel": CHANNEL}
    if content is not None:
        params["content"] = content
        params["filename"] = "snippet.txt"
    else:
        f = tmp_path / "doc.txt"
        if not f.exists():
            f.write_text("hello")
        params["file"] = str(f)
    if comment:
        params["initial_comment"] = comment
    params.update(extra)
    return {"method": "files.uploadV2", "params": params, "as_bot": as_bot, "provenance": {"cls": "B-h" if as_bot else "A"}}


def ephemeral(user="U0VIEWER01", as_bot=False, text="only you", **prov):
    return {"method": "chat.postEphemeral", "params": {"channel": CHANNEL, "user": user, "text": text}, "as_bot": as_bot,
            "provenance": {"cls": "B-h" if as_bot else "A", **prov}}


def me(text="shrugs", as_bot=False):
    return {"method": "chat.meMessage", "params": {"channel": CHANNEL, "text": text}, "as_bot": as_bot,
            "provenance": {"cls": "B-h" if as_bot else "A"}}


def row(env, op_id):
    return env.journal.get_op(op_id)


# ================================================================ chat.update

def test_u1_update_runs_through_the_core_binds_ts_and_journals_the_prior(env):
    env.transport = Slack(author=env.ids["bot_user_id"])
    ti = env.approve(update(comm_id="20261031-upd1"))
    res = env.send(ti)
    assert [c[0] for c in env.transport.calls] == ["conversations.replies", "chat.update"]
    assert res.data["ok"] and res.prior["text"] == "old text" and res.prior["user"] == env.ids["bot_user_id"]
    r = row(env, res.op_id)
    assert r["method"] == "chat.update" and r["outcome"] == "ok" and r["slack_ts"] == TS
    prov = json.loads(r["provenance_json"])
    assert prov["prior"]["text"] == "old text" and prov["prior"]["ts"] == TS
    # an edit never carries metadata (it would overwrite the original post's handle); a person edit carries the attribution block
    sent = writes(env.transport)[0][1]
    assert "metadata" not in sent
    # another ts after approval = another operation
    ti2 = env.approve(update(comm_id="20261031-upd2"))
    ti2["params"]["ts"] = "1700000000.000099"
    refused(env, ti2, "decision_mismatch")
    assert len(writes(env.transport)) == 1


def test_u2_update_person_voice_keeps_attribution_and_checks_the_author(env):
    env.transport = Slack(author=env.ids["person_user_id"])
    ti = env.approve(update(as_bot=False, comm_id="20261031-upd3"))
    env.send(ti)
    sent = writes(env.transport)[0][1]
    assert sent["blocks"][-1]["type"] == "context" and "metadata" not in sent
    # a message somebody else posted: refused before any write, after the one read
    env.transport = Slack(author="U0STRANGER")
    e = refused(env, env.approve(update(as_bot=False, comm_id="20261031-upd4")), "author_mismatch")
    assert "U0STRANGER" in e.message and writes(env.transport) == []
    env.transport = Slack(author="U0STRANGER")
    refused(env, env.approve(update(as_bot=True, comm_id="20261031-upd5")), "author_mismatch")
    assert writes(env.transport) == []
    # the read fails → warning, Slack decides; the edit still goes
    env.transport = Slack(fail_on={"conversations.replies": TimeoutError("slow")})
    res = env.send(env.approve(update(comm_id="20261031-upd6")))
    assert res.prior["error"].startswith("TimeoutError") and len(writes(env.transport)) == 1
    assert any("prior text not read" in w for w in res.verdict.warnings)
    assert json.loads(row(env, res.op_id)["provenance_json"])["prior"]["error"]


def test_u3_update_refusals_before_any_call(env):
    env.transport = Slack()
    for bad in (None, "", "abc", "1700000000", 1700000000.5):
        t = update(comm_id=f"u-{bad}")
        if bad is None:
            del t["params"]["ts"]
        else:
            t["params"]["ts"] = bad
        out = env.hook(t)
        assert out["permissionDecision"] == "deny" and "missing_params" in out["permissionDecisionReason"], bad
    t = update(comm_id="u-notext"); del t["params"]["text"]
    assert "missing_params" in env.hook(t)["permissionDecisionReason"]
    big = update(comm_id="u-blocks"); big["params"]["blocks"] = [{"type": "section", "text": {"type": "mrkdwn", "text": "y" * 4000}}]
    out = env.hook(big)
    assert out["permissionDecision"] == "deny" and "invalid_blocks" in out["permissionDecisionReason"]
    assert env.transport.calls == []


def test_u4_update_prompt_shows_edit_time_and_prior(env):
    v = policy_mod.evaluate("chat.update", update()["params"], True, {"cls": "B-h"}, env.profile, journal=env.journal)
    text = describe_send(v, update()["params"], True, env.profile, op_short="abcd1234",
                         prior={"ts": TS, "user": "U", "text": "the   old\nline"})
    first = text.splitlines()[0]
    assert ("edit of the message from" in first) or ("правка сообщения от" in first)
    assert "“new text”" in first
    assert any(line in ("was: the old line", "было: the old line") for line in text.splitlines())
    assert "params ts" not in text
    unread = describe_send(v, update()["params"], True, env.profile, prior={"error": "message_not_found"})
    assert "⚠" in unread and "message_not_found" in unread


def test_u5_author_problem_rules(env):
    p = env.profile
    assert prior_mod.author_problem(None, True, p) is None
    assert prior_mod.author_problem({"error": "x"}, False, p) is None
    assert prior_mod.author_problem({"user": p.bot_user_id, "bot_id": "B1"}, True, p) is None
    assert prior_mod.author_problem({"user": p.person_user_id}, False, p) is None
    assert "not by the bot" in prior_mod.author_problem({"user": p.person_user_id}, True, p)
    assert "not by you" in prior_mod.author_problem({"user": p.bot_user_id}, False, p)


def test_u6_update_through_the_mcp_server(env):
    mock = MagicMock()

    def api_call(method, params=None):
        if method == "conversations.replies":
            return {"ok": True, "messages": [{"ts": params["ts"], "user": env.ids["bot_user_id"], "bot_id": "B1", "text": "old"}]}
        return {"ok": True, "channel": CHANNEL, "ts": params["ts"], "message": {"ts": params["ts"], "text": params["text"]}}

    mock.api_call.side_effect = api_call
    ti = env.approve(update(comm_id="20261031-mcp1"))
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        out = gateway.slack_api(**ti)
    assert mock.api_call.call_count == 2
    assert "journal: op" in out and "· comm 20261031-mcp1" in out and "was: old" in out
    sent = mock.api_call.call_args.kwargs["params"]
    assert "metadata" not in sent


# ================================================================ files.uploadV2

def test_f1_upload_runs_through_the_core_and_binds_the_bytes(env, tmp_path):
    env.transport = Slack()
    ti = env.approve(upload(tmp_path))
    assert ti["provenance"]["decision"]
    res = env.send(ti)
    method, sent, as_bot = env.transport.calls[0]
    assert method == "files.uploadV2" and as_bot is False
    assert sent["initial_comment"].startswith("see attached") and sent["initial_comment"].endswith(f":blue_heart: <@{env.ids['bot_user_id']}>")
    assert "attribution" not in sent and sent["file"] == b"hello" and sent["filename"] == "doc.txt"   # bytes on the wire
    assert json.loads(row(env, res.op_id)["payload_json"])["final_params"]["file"] == str(tmp_path / "doc.txt")  # path in the journal
    r = row(env, res.op_id)
    assert r["slack_ts"] == "F0001" and r["outcome"] == "ok"
    prov = json.loads(r["provenance_json"])
    assert len(prov["file_sha256"]) == 1 and len(prov["file_sha256"][0]) == 64
    # the file is rewritten after approval: the bytes are in the binding → the decision does not cover it
    ti2 = env.approve(upload(tmp_path, comment="v2"))
    (tmp_path / "doc.txt").write_text("hello, changed")
    refused(env, ti2, "decision_mismatch")
    assert len(env.transport.calls) == 1
    # a snippet upload (content) is bound by its params alone
    res3 = env.send(env.approve(upload(tmp_path, content="snippet body", comment=None)))
    assert env.transport.calls[-1][1]["content"] == "snippet body"
    assert "file_sha256" not in json.loads(row(env, res3.op_id)["provenance_json"])


def test_f2_upload_refusals_before_any_call(env, tmp_path):
    env.transport = Slack()
    ok = tmp_path / "doc.txt"
    ok.write_text("hello")
    cases = [
        ({"channel": CHANNEL, "content": "x", "channels": ["C1"]}, "invalid_arguments"),
        ({"content": "x", "channels": "C1"}, "invalid_arguments"),
        ({"channel": CHANNEL, "file": "rel.txt"}, "invalid_arguments"),
        ({"channel": CHANNEL, "file": str(tmp_path / "nope.txt")}, "file_not_found"),
        ({"channel": CHANNEL}, "missing_params"),
        ({"content": "x"}, "missing_params"),
        ({"channel": CHANNEL, "content": "x", "initial_comment": "c", "attribution": "footer"}, "invalid_arguments"),
        ({"channel": CHANNEL, "file_uploads": [{"file": str(ok)}, {"file": "rel.txt"}]}, "invalid_arguments"),
        ({"channel": CHANNEL, "file": str(ok), "username": "x"}, "identity_override"),
    ]
    for params, code in cases:
        out = env.hook({"method": "files.uploadV2", "params": params, "as_bot": True, "provenance": {"cls": "B-h"}})
        assert out["permissionDecision"] == "deny" and code in out["permissionDecisionReason"], (params, out)
    wrong = {"method": "files.uploadV2", "params": {"channel": CHANNEL, "file": str(ok)}, "as_bot": True,
             "provenance": {"cls": "B-h", "file_sha256": ["0" * 64]}}
    assert "file_mismatch" in env.hook(wrong)["permissionDecisionReason"]
    assert env.transport.calls == []


def test_f3_upload_block_mode_is_a_composite_with_a_child_op(env, tmp_path):
    env.transport = Slack()
    ti = env.approve(upload(tmp_path, comment="here it is", attribution="block", thread_ts="1.1", title="Report"))
    assert "scheduled" not in env.hook(upload(tmp_path, comment="here it is", attribution="block"))["permissionDecisionReason"]
    res = env.send(ti)
    calls = env.transport.calls
    assert [c[0] for c in calls] == ["files.uploadV2", "chat.postMessage"]
    assert "initial_comment" not in calls[0][1] and "attribution" not in calls[0][1]
    child = calls[1][1]
    assert child["text"] == "here it is\n<https://x.slack.com/files/F0001|Report>" and child["thread_ts"] == "1.1"
    assert child["blocks"][-1]["type"] == "context" and calls[1][2] is False
    assert res.child_op_id == f"{res.op_id}.c1" and res.child_error is None and res.child_data["ok"]
    parent, kid = row(env, res.op_id), row(env, res.child_op_id)
    assert parent["outcome"] == "ok" and kid["outcome"] == "ok" and kid["parent_op_id"] == res.op_id
    assert kid["comm_id"] == parent["comm_id"] and kid["method"] == "chat.postMessage"
    assert json.loads(kid["provenance_json"])["composite"] == "comment"
    # the same comm_id again, no resend → duplicate (the composite counts as a write)
    out = env.hook({**upload(tmp_path, comment="again"), "provenance": {"cls": "A", "comm_id": parent["comm_id"]}})
    assert "duplicate_comm_id" in out["permissionDecisionReason"]


def test_f4_upload_child_failure_keeps_the_file_and_forbids_a_re_upload(env, tmp_path):
    env.transport = Slack(fail_on={"chat.postMessage": SlackApiError("x", {"ok": False, "error": "not_in_channel"})})
    res = env.send(env.approve(upload(tmp_path, comment="c", attribution="block")))
    assert [c[0] for c in env.transport.calls] == ["files.uploadV2", "chat.postMessage"]
    assert row(env, res.op_id)["outcome"] == "ok"
    assert row(env, res.child_op_id)["outcome"] == "error" and row(env, res.child_op_id)["error_code"] == "not_in_channel"
    assert res.child_error.startswith("not_in_channel")
    # the MCP result says what to do; an ambiguous child failure is an unknown child, not a failed upload
    mock = MagicMock()
    mock.files_upload_v2.return_value = {"ok": True, "file": {"id": "F2", "name": "doc.txt", "permalink": "https://x/F2"}}
    mock.api_call.side_effect = TimeoutError("read timed out")
    ti = env.approve(upload(tmp_path, comment="c2", attribution="block"))
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        out = gateway.slack_api(**ti)
    assert "comment_outcome_unknown" in out and "Do not upload again" in out and "journal: op" in out
    assert mock.files_upload_v2.call_count == 1
    kid = [r for r in env.journal.ops() if r["op_id"].endswith(".c1") and r["outcome"] == "unknown"]
    assert len(kid) == 1


def test_f5_upload_through_the_mcp_server_dispatches_to_the_sdk(env, tmp_path):
    mock = MagicMock()
    mock.files_upload_v2.return_value = {"ok": True, "files": [{"id": "F1", "name": "doc.txt", "permalink": "https://x.slack.com/F1"}]}
    ti = env.approve(upload(tmp_path, as_bot=True, comment="from the bot"))
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        out = gateway.slack_api(**ti)
    mock.api_call.assert_not_called()
    kw = mock.files_upload_v2.call_args.kwargs
    assert kw["channel"] == CHANNEL and kw["initial_comment"] == "from the bot" and "attribution" not in kw
    assert "https://x.slack.com/F1" in out and "journal: op" in out
    # file_uploads: paths resolved, other items untouched
    ok = tmp_path / "a.txt"
    ok.write_text("a")
    mock.files_upload_v2.return_value = {"ok": True, "files": [{"id": "F1", "name": "a.txt"}, {"id": "F2", "name": "b"}]}
    ti = env.approve({"method": "files.uploadV2", "as_bot": True, "provenance": {"cls": "B-h"},
                      "params": {"channel": CHANNEL, "file_uploads": [{"file": str(ok), "title": "A"}, {"content": "b", "filename": "b"}]}})
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        out = gateway.slack_api(**ti)
    kw = mock.files_upload_v2.call_args.kwargs
    assert kw["file_uploads"][0]["title"] == "A" and kw["file_uploads"][1] == {"content": "b", "filename": "b"}
    assert "2 files" in out


def test_f6_upload_prompt_names_the_file(env, tmp_path):
    t = upload(tmp_path, comment="see attached")
    out = env.hook(t)
    reason = out["permissionDecisionReason"]
    assert out["permissionDecision"] == "ask"
    assert "📎 doc.txt (5 B) + “see attached”" in reason and "sha256 " in reason
    long = upload(tmp_path, comment="long comment " * 30)
    reason = env.hook(long)["permissionDecisionReason"]
    assert ("комментарий ниже" in reason) or ("comment below" in reason)
    assert reason.splitlines()[1].startswith("long comment")


# ================================================================ chat.postEphemeral · chat.meMessage

def test_e1_ephemeral_binds_the_viewer(env):
    env.transport = Slack()
    ti = env.approve(ephemeral(comm_id="20261031-eph1"))
    res = env.send(ti)
    method, sent, as_bot = env.transport.calls[0]
    assert method == "chat.postEphemeral" and sent["user"] == "U0VIEWER01" and sent["blocks"][-1]["type"] == "context"
    r = row(env, res.op_id)
    assert r["slack_ts"] == "1700000001.000001" and "U0VIEWER01" in json.loads(r["target_json"])["users"]
    assert any("ephemeral" in w for w in res.verdict.warnings)
    ti2 = env.approve(ephemeral(comm_id="20261031-eph2"))
    ti2["params"]["user"] = "U0OTHER001"
    refused(env, ti2, "decision_mismatch")
    bad = ephemeral(comm_id="e-nouser"); del bad["params"]["user"]
    assert "missing_params" in env.hook(bad)["permissionDecisionReason"]
    assert len(env.transport.calls) == 1


def test_e2_ephemeral_viewer_is_a_recipient_for_the_deny_list(env):
    env.policy(POLICY_DENY)
    out = env.hook(ephemeral(user="U0NEVERME", comm_id="e-deny"))
    assert out["permissionDecision"] == "deny" and "recipient_denied" in out["permissionDecisionReason"]
    ok = env.hook(ephemeral(user="U0FRIEND01", comm_id="e-ok"))
    assert ok["permissionDecision"] == "ask"
    assert ("visible only to" in ok["permissionDecisionReason"]) or ("видно только" in ok["permissionDecisionReason"])


def test_e3_ephemeral_through_the_mcp_server_renders_the_shape(env):
    mock = MagicMock()
    mock.api_call.return_value = {"ok": True, "message_ts": "1700000002.000001"}
    ti = env.approve(ephemeral(as_bot=True, comm_id="20261031-eph3"))
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        out = gateway.slack_api(**ti)
    assert out.startswith("Ephemeral message (message_ts=1700000002.000001)") and "journal: op" in out
    assert "metadata" not in mock.api_call.call_args.kwargs["params"]     # metadata only where it reconciles


def test_m1_me_message_suffix_and_done(env):
    env.transport = Slack()
    res = env.send(env.approve(me()))
    method, sent, _ = env.transport.calls[0]
    assert method == "chat.meMessage" and sent["text"].endswith(f":blue_heart: <@{env.ids['bot_user_id']}>")
    assert row(env, res.op_id)["outcome"] == "ok"
    bad = me(); del bad["params"]["text"]
    assert "missing_params" in env.hook(bad)["permissionDecisionReason"]
    mock = MagicMock()
    mock.api_call.return_value = {"ok": True, "channel": CHANNEL, "ts": "1.9"}
    ti = env.approve(me(as_bot=True, text="waves"))
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        out = gateway.slack_api(**ti)
    assert out.startswith("Done ✓") and "journal: op" in out
    assert mock.api_call.call_args.kwargs["params"]["text"] == "waves"
    reason = env.hook(me())["permissionDecisionReason"]
    assert " · /me" in reason


# ================================================================ metadata · reconcile_required · _reconcile

def test_r1_bot_sends_carry_the_op_in_metadata_unless_the_caller_set_it(env):
    res = env.send(env.approve(post(comm_id="20261031-meta")))
    sent = env.transport.calls[0][1]
    assert sent["metadata"]["event_payload"] == {"op_id": res.op_id, "comm_id": "20261031-meta"}
    env.send(env.approve(post(as_bot=False, comm_id="20261031-meta2")))
    assert "metadata" not in env.transport.calls[1][1]
    own = post(comm_id="20261031-meta3")
    own["params"]["metadata"] = {"event_type": "mine", "event_payload": {"k": "v"}}
    env.send(env.approve(own))
    assert env.transport.calls[2][1]["metadata"]["event_type"] == "mine"


def test_r2_an_unknown_outcome_blocks_the_comm_until_reconciled(env):
    env.transport.exc = TimeoutError("read timed out")
    with pytest.raises(core.SendFailed) as e:
        env.send(env.approve(post(comm_id="20261031-unk")))
    assert e.value.outcome == "unknown"
    env.transport.exc = None
    for resend in (False, True):
        out = env.hook(post(comm_id="20261031-unk", resend=resend))
        assert out["permissionDecision"] == "deny" and "reconcile_required" in out["permissionDecisionReason"], resend
    refused(env, {**post(comm_id="20261031-unk", resend=True)}, "reconcile_required")
    assert len(env.transport.calls) == 1
    # reconcile finds it (bot send: by metadata) → ok; a resend then needs the usual fresh decision
    op_id = env.journal.ops("unknown")[0]["op_id"]
    found = {"ok": True, "messages": [{"ts": "1700000000.777777", "text": "other", "user": "U?",
                                       "metadata": {"event_type": "atlas_slack_op", "event_payload": {"op_id": op_id}}}]}
    res = rec.reconcile(env.journal, lambda m, p, b: found, env.profile)
    assert [r.outcome for r in res] == ["ok"] and env.journal.get_op(op_id)["slack_ts"] == "1700000000.777777"
    assert "duplicate_comm_id" in env.hook(post(comm_id="20261031-unk"))["permissionDecisionReason"]
    env.send(env.approve(post(comm_id="20261031-unk", resend=True)))
    assert len(env.transport.calls) == 2


def _unknown_row(env, method, params, as_bot, intent_at, op_id=None):
    op_id = op_id or f"{method}-{intent_at}"
    final = shaping.shape_send(method, params, as_bot, env.ids["bot_user_id"])
    env.journal.reserve(OpIntent(op_id=op_id, method=method, payload_hash="h", comm_id=f"c-{op_id}",
                                 payload={"params": params, "final_params": final, "as_bot": as_bot}))
    env.journal._conn.execute("UPDATE ops SET intent_at=? WHERE op_id=?", (intent_at, op_id))
    env.journal.mark_unknown(op_id, error_code="exception")
    return op_id


def test_r3_reconcile_resolves_each_method_by_the_fact_in_slack(env):
    now = 1_800_000_000.0
    j = env.journal
    p_person = _unknown_row(env, "chat.postMessage", {"channel": CHANNEL, "text": "hello  there"}, False, now - 600, "p-person")
    p_thread = _unknown_row(env, "chat.postMessage", {"channel": CHANNEL, "text": "in thread", "thread_ts": "1.0"}, True, now - 600, "p-thread")
    p_young = _unknown_row(env, "chat.postMessage", {"channel": CHANNEL, "text": "young"}, True, now - 30, "p-young")
    p_absent = _unknown_row(env, "chat.postMessage", {"channel": CHANNEL, "text": "gone"}, True, now - 600, "p-absent")
    u_ok = _unknown_row(env, "chat.update", {"channel": CHANNEL, "ts": TS, "text": "edited"}, True, now - 600, "u-ok")
    u_old = _unknown_row(env, "chat.update", {"channel": CHANNEL, "ts": "1700000000.000043", "text": "edited"}, True, now - 600, "u-old")
    s_list = _unknown_row(env, "chat.scheduleMessage", {"channel": CHANNEL, "text": "later", "post_at": int(now) + 3600}, True, now - 600, "s-list")
    f_ok = _unknown_row(env, "files.uploadV2", {"channel": CHANNEL, "file": "/abs/report.pdf"}, True, now - 600, "f-ok")
    e_row = _unknown_row(env, "chat.postEphemeral", {"channel": CHANNEL, "user": "U0VIEWER01", "text": "x"}, True, now - 600, "e-row")

    def reader(method, params, as_bot):
        if method == "conversations.history":
            return {"ok": True, "messages": [
                {"ts": str(now - 599), "text": "hello there", "user": env.ids["person_user_id"]},     # ours: after the intent
                {"ts": str(now - 598), "text": "hello there", "user": "U0IMPOSTER"},
            ]}
        if method == "conversations.replies":
            if params.get("ts") == "1.0":
                return {"ok": True, "messages": [{"ts": "1.0", "text": "parent"},
                                                 {"ts": "1.7", "text": "x", "metadata": {"event_type": "atlas_slack_op",
                                                                                         "event_payload": {"op_id": "p-thread"}}}]}
            if params.get("ts") == TS:
                return {"ok": True, "messages": [{"ts": TS, "text": "edited"}]}
            return {"ok": True, "messages": [{"ts": params.get("ts"), "text": "still the old text"}]}
        if method == "chat.scheduledMessages.list":
            return {"ok": True, "scheduled_messages": [{"id": "Q77", "text": "later", "post_at": int(now) + 3600}]}
        if method == "files.list":
            return {"ok": True, "files": [{"id": "F77", "name": "report.pdf", "created": int(now - 590)}], "paging": {"page": 1, "pages": 1}}
        raise AssertionError(method)

    res = {r.op_id: r for r in rec.reconcile(j, reader, env.profile, now=now)}
    assert res[p_person].outcome == "ok" and res[p_person].slack_ts == str(now - 599)   # text + author + after the intent
    assert res[p_thread].outcome == "ok" and res[p_thread].slack_ts == "1.7"          # metadata in the thread
    assert res[p_young].outcome == "unknown" and "too early" not in res[p_young].detail and "wait" in res[p_young].detail
    assert res[p_absent].outcome == "error" and res[p_absent].error_code == "reconciled_absent"
    assert res[u_ok].outcome == "ok" and res[u_ok].slack_ts == TS
    assert res[u_old].outcome == "error" and res[u_old].error_code == "reconciled_absent"
    assert res[s_list].outcome == "ok" and res[s_list].slack_ts == "Q77"
    assert res[f_ok].outcome == "ok" and res[f_ok].slack_ts == "F77"
    assert res[e_row].outcome == "error" and res[e_row].error_code == "reconciled_unobservable"
    assert {r["op_id"] for r in j.ops("unknown")} == {p_young}
    text = rec.report(list(res.values()))
    assert text.startswith("reconcile: 9 unknown → 5 ok · 3 error · 1 still unknown")
    # a read failure leaves the row unknown and says so
    again = rec.reconcile(j, lambda m, p, b: {"ok": False, "error": "ratelimited"}, env.profile, now=now + 600)
    assert [r.outcome for r in again] == ["unknown"] and "ratelimited" in again[0].detail
    assert rec.report([]) == "reconcile: no unknown outcomes in the journal"


def test_r4_reconcile_faces_gateway_door_cli_and_freeze(env):
    mock = MagicMock()
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        assert gateway.slack_api("_reconcile") == "reconcile: no unknown outcomes in the journal"
    mock.api_call.assert_not_called()
    env.transport.exc = TimeoutError("boom")
    with pytest.raises(core.SendFailed):
        env.send(env.approve(post(comm_id="20261031-door")))
    env.transport.exc = None
    env.write(freeze=True)                                          # frozen: reads and resolution still run
    door = Door(transport=lambda m, p, b: {"ok": True, "messages": []})
    r = door.call("_reconcile")
    assert r.ok and "1 still unknown" in r.data["report"]
    assert env.hook({"method": "_reconcile"})["permissionDecision"] == "allow"
    assert env.hook(post())["permissionDecision"] == "deny"        # the freeze still holds for sends
    env.write(freeze=False)
    import subprocess, sys
    proc = subprocess.run([sys.executable, "-m", "atlas_slack.gate", "--reconcile"], capture_output=True, text=True, timeout=30,
                          env={**__import__("os").environ, "ATLAS_SLACK_RESOLVE_NAMES": "0"})
    assert proc.returncode == 0 and proc.stdout.startswith("reconcile: 1 unknown"), (proc.stdout, proc.stderr)


def test_r5_broken_profile_cannot_reconcile(isolated_door_dirs, monkeypatch):
    monkeypatch.setenv("ATLAS_SLACK_PROFILE", "ghost")
    from atlas_slack.profile import load_profile
    v = policy_mod.evaluate("_reconcile", {}, False, None, load_profile())
    assert v.decision == "refuse" and v.reason == "profile_missing"
    with pytest.raises(ToolError, match="profile_missing"):
        gateway.slack_api("_reconcile")


# ================================================================ library face · rights · registry

def test_l1_library_runs_core_methods_only_with_a_policy_that_allows_them(env):
    env.policy(f"""
version: "r2"
channels:
  {env.ids['person_bot_dm_id']}:
    class: dm
    voices: [bot]
    modes: {{C2: auto}}
""")
    door = Door(transport=Slack(author=env.ids["bot_user_id"]))
    dm = env.ids["person_bot_dm_id"]
    assert door.call("chat.update", {"channel": dm, "ts": TS, "text": "fixed"}, as_bot=True, provenance={"cls": "C2"}).ok
    assert door.call("chat.meMessage", {"channel": dm, "text": "x"}, as_bot=True, provenance={"cls": "C2"}).ok
    r = door.call("chat.postEphemeral", {"channel": dm, "user": env.ids["person_user_id"], "text": "x"}, as_bot=True,
                  provenance={"cls": "C2"})
    assert r.ok
    assert door.call("chat.delete", {"channel": dm, "ts": TS}, as_bot=True).verdict.reason == "not_in_slice"


def test_l2_registry_and_rights_text(env):
    for m in ("chat.update", "chat.postEphemeral", "chat.meMessage", "files.uploadV2"):
        assert policy_mod.classify(m, {}).route == "core", m
        assert m not in policy_mod.KNOWN_WRITE_ROWS
    assert policy_mod.classify("chat.delete", {}).route == "transitional"
    assert policy_mod.classify("_reconcile", {}).route == "local"
    v = policy_mod.evaluate("chat.delete", {"channel": CHANNEL, "ts": TS}, True, None, env.profile, face="library")
    assert "chat.update" in v.message and "files.uploadV2" in v.message       # not_in_slice names the core set


# ================================================================ spec-verifier round 1 (2026-10-31)

def test_sv1_child_unknown_says_reconcile_never_post_again(env, tmp_path):
    env.transport = Slack(fail_on={"chat.postMessage": TimeoutError("read timed out")})
    res = env.send(env.approve(upload(tmp_path, comment="c", attribution="block")))
    assert res.child_outcome == "unknown" and row(env, res.child_op_id)["outcome"] == "unknown"
    assert row(env, res.op_id)["outcome"] == "ok"
    # the comm is now blocked until reconciled — a plain re-post or an edit refuses reconcile_required
    comm = row(env, res.op_id)["comm_id"]
    out = env.hook(post(as_bot=False, comm_id=comm))
    assert "reconcile_required" in out["permissionDecisionReason"]
    env.transport = Slack(fail_on={"chat.postMessage": SlackApiError("x", {"ok": False, "error": "not_in_channel"})})
    res2 = env.send(env.approve(upload(tmp_path, comment="c2", attribution="block")))
    assert res2.child_outcome == "error"
    mock = MagicMock()
    mock.files_upload_v2.return_value = {"ok": True, "file": {"id": "F2", "name": "doc.txt", "permalink": "https://x/F2"}}
    mock.api_call.side_effect = TimeoutError("read timed out")
    ti = env.approve(upload(tmp_path, comment="c3", attribution="block"))
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        out = gateway.slack_api(**ti)
    assert "comment_outcome_unknown" in out and "_reconcile" in out and "comment_not_posted" not in out
    assert "post the comment with chat.postMessage" not in out


def test_sv2_hook_checks_the_author_before_issuing_a_token(env):
    from atlas_slack.gate import hook_output
    from test_v2_slice import TOOL
    stranger = lambda m, p, b: {"ok": True, "messages": [{"ts": p["ts"], "user": "U0STRANGER", "text": "theirs"}]}
    before = len(env.journal._conn.execute("SELECT * FROM decisions").fetchall())
    out = hook_output({"tool_name": TOOL, "tool_input": update(as_bot=True)}, prior_reader=stranger)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "author_mismatch" in out["permissionDecisionReason"]
    assert "updatedInput" not in out
    assert len(env.journal._conn.execute("SELECT * FROM decisions").fetchall()) == before   # no token issued
    assert env.journal.calls()[-1]["error_code"] == "author_mismatch" and env.journal.calls()[-1]["caller"] == "hook"
    # a bot post (bot_id, no user) is never edited in the person's voice; the bot's own user id is not the person's
    bot_msg = lambda m, p, b: {"ok": True, "messages": [{"ts": p["ts"], "user": env.ids["bot_user_id"], "bot_id": "B1", "text": "x"}]}
    out = hook_output({"tool_name": TOOL, "tool_input": update(as_bot=False)}, prior_reader=bot_msg)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "not by you" in out["permissionDecisionReason"]
    assert prior_mod.author_problem({"user": None, "bot_id": "B9"}, False, type("P", (), {"person_user_id": None, "bot_user_id": None})()) is not None
    # the right author: the prompt carries the prior text, a token is issued
    mine = lambda m, p, b: {"ok": True, "messages": [{"ts": p["ts"], "user": env.ids["bot_user_id"], "bot_id": "B1", "text": "the old one"}]}
    out = hook_output({"tool_name": TOOL, "tool_input": update(as_bot=True)}, prior_reader=mine)["hookSpecificOutput"]
    assert out["permissionDecision"] == "ask" and "the old one" in out["permissionDecisionReason"] and "updatedInput" in out


def test_sv3_an_edit_carries_the_original_comm_id_without_resend(env):
    res = env.send(env.approve(post(comm_id="20261031-orig")))
    env.transport = Slack(author=env.ids["bot_user_id"])
    out = env.hook(update(comm_id="20261031-orig", ts=res.data["ts"]))
    assert out["permissionDecision"] == "ask" and "Resend" not in out["permissionDecisionReason"] \
        and "Повторная" not in out["permissionDecisionReason"]
    res2 = env.send(env.approve(update(comm_id="20261031-orig", ts=res.data["ts"])))
    assert [r["method"] for r in env.journal.find_by_comm_id("20261031-orig")] == ["chat.postMessage", "chat.update"]
    assert row(env, res2.op_id)["outcome"] == "ok"
    # a second *post* under that comm still needs resend
    assert "duplicate_comm_id" in env.hook(post(comm_id="20261031-orig"))["permissionDecisionReason"]


def test_sv4_upload_reconcile_matches_name_and_uploader_and_needs_a_name(env):
    now = 1_800_000_000.0
    named = _unknown_row(env, "files.uploadV2", {"channel": CHANNEL, "file": "/abs/report.pdf"}, False, now - 600, "f-named")
    nameless = _unknown_row(env, "files.uploadV2", {"channel": CHANNEL, "content": "x"}, True, now - 600, "f-nameless")
    seen = []

    def reader(method, params, as_bot):
        seen.append(params)
        return {"ok": True, "files": [{"id": "F1", "name": "report.pdf", "user": "U0SOMEONE", "created": int(now - 590)},
                                      {"id": "F2", "name": "report.pdf", "user": env.ids["person_user_id"], "created": int(now - 590)}],
                "paging": {"page": 1, "pages": 1}}

    res = {r.op_id: r for r in rec.reconcile(env.journal, reader, env.profile, now=now)}
    assert res[named].outcome == "ok" and res[named].slack_ts == "F2"
    assert seen[0]["user"] == env.ids["person_user_id"]
    assert res[nameless].outcome == "unknown" and "without a name" in res[nameless].detail
    assert "resend under a new comm_id" in rec.report(list(res.values()))


# ================================================================ verifier round 1 (2026-10-31)

def test_vf1_file_swapped_after_reserve_is_an_error_not_an_upload(env, tmp_path, monkeypatch):
    env.transport = Slack()
    f = tmp_path / "doc.txt"
    ti = env.approve(upload(tmp_path, as_bot=True, comment=None))
    real_reserve = env.journal.reserve

    def swap_then_reserve(*a, **k):
        real_reserve(*a, **k)
        f.write_text("swapped after the intent row")
    monkeypatch.setattr(type(env.journal), "reserve", lambda self, *a, **k: swap_then_reserve(*a, **k))
    with pytest.raises(core.SendFailed) as e:
        env.send(ti)
    assert e.value.code == "file_changed" and e.value.outcome == "error"
    assert env.transport.calls == []
    r = env.journal.ops()[0]
    assert r["outcome"] == "error" and r["error_code"] == "file_changed"


def test_vf2_reconcile_reads_history_with_metadata(env):
    now = 1_800_000_000.0
    _unknown_row(env, "chat.postMessage", {"channel": CHANNEL, "text": "x"}, True, now - 600, "p-meta")
    _unknown_row(env, "chat.postMessage", {"channel": CHANNEL, "text": "y", "thread_ts": "1.0"}, True, now - 600, "p-meta-thread")
    seen = []

    def reader(method, params, as_bot):
        seen.append((method, params))
        return {"ok": True, "messages": []}
    rec.reconcile(env.journal, reader, env.profile, now=now)
    assert all(p.get("include_all_metadata") is True for m, p in seen if m in ("conversations.history", "conversations.replies"))
    assert "chat.update" not in shaping.METADATA_METHODS
    assert len(seen) == 2


def test_vf3_parent_outcome_journal_failure_reports_the_missing_comment(env, tmp_path, monkeypatch):
    from atlas_slack.journal import Journal, JournalUnavailable
    env.transport = Slack()
    ti = env.approve(upload(tmp_path, comment="c", attribution="block"))
    monkeypatch.setattr(Journal, "record_outcome", lambda self, *a, **k: (_ for _ in ()).throw(JournalUnavailable("disk full")))
    res = env.send(ti)
    assert res.journal_error and res.child_outcome == "skipped" and "NOT posted" in res.child_error
    assert [c[0] for c in env.transport.calls] == ["files.uploadV2"]       # no comment post without a journal


# ================================================================ Codex implementation review (2026-10-31)

def test_cx1_a_write_is_one_http_attempt_no_sdk_retry(env, monkeypatch):
    """Codex P1: the SDK's ConnectionErrorRetryHandler would repeat a request Slack may have accepted.
    Probe below WebClient.api_call: the core's client makes one HTTP attempt and the op is `unknown`."""
    from slack_sdk.web.base_client import BaseClient
    from slack_sdk import WebClient
    from atlas_slack import client as client_mod
    monkeypatch.setattr(WebClient, "api_call", BaseClient.__dict__["api_call"])        # undo conftest's block
    attempts = []

    def boom(self, url, req):
        attempts.append(url)
        raise ConnectionResetError("peer reset")
    monkeypatch.setattr(BaseClient, "_perform_urllib_http_request_internal", boom)
    client_mod._clients.clear()
    ti = env.approve(post(comm_id="20261031-cx1"))
    with pytest.raises(ToolError, match="outcome_unknown"):
        gateway.slack_api(**ti)
    assert len(attempts) == 1
    assert env.journal.ops()[0]["outcome"] == "unknown"
    # the read client keeps the SDK retry (reads are safe to repeat)
    assert client_mod.get_client(as_bot=False).retry_handlers and not client_mod.get_client(as_bot=False, retries=False).retry_handlers
    from atlas_slack.door import _SdkTransport
    monkeypatch.setattr(BaseClient, "_perform_urllib_http_request_internal", boom)
    attempts.clear()
    tr = _SdkTransport(env.profile)
    with pytest.raises(ConnectionResetError):
        tr("chat.postMessage", {"channel": CHANNEL, "text": "x"}, True)
    assert len(attempts) == 1


def test_cx2_internal_error_is_unknown_not_error(env):
    """Codex P1: Slack says internal_error/fatal_error may have partially completed."""
    for code, via_exc in (("internal_error", True), ("fatal_error", False)):
        tr = FakeTransport()
        if via_exc:
            tr.exc = SlackApiError("x", {"ok": False, "error": code})
        else:
            tr.__call__ = None
            tr = type("T", (FakeTransport,), {"__call__": lambda self, m, p, b: {"ok": False, "error": code}})()
        env.transport = tr
        ti = env.approve(post(comm_id=f"20261031-{code}"))
        with pytest.raises(core.SendFailed) as e:
            env.send(ti)
        assert e.value.outcome == "unknown" and e.value.code == code
        r = env.journal.find_by_comm_id(f"20261031-{code}")[0]
        assert r["outcome"] == "unknown" and r["error_code"] == code
        # the comm is blocked until reconciled — not a plain resend
        assert "reconcile_required" in env.hook(post(comm_id=f"20261031-{code}"))["permissionDecisionReason"]
    env.transport = FakeTransport()
    env.transport.exc = SlackApiError("x", {"ok": False, "error": "channel_not_found"})
    with pytest.raises(core.SendFailed) as e:
        env.send(env.approve(post(comm_id="20261031-cnf")))
    assert e.value.outcome == "error"                                           # a definite refusal stays an error


def test_cx3_door_by_name_sees_freeze_between_calls(env):
    env.policy(f"""
version: "r3"
channels:
  {env.ids['person_bot_dm_id']}:
    class: dm
    voices: [bot]
    modes: {{C2: auto}}
""")
    door = Door(transport=env.transport)
    dm = env.ids["person_bot_dm_id"]
    assert door.call("chat.postMessage", {"channel": dm, "text": "r1"}, as_bot=True, provenance={"cls": "C2"}).ok
    env.write(freeze=True)
    r = door.call("chat.postMessage", {"channel": dm, "text": "r2"}, as_bot=True, provenance={"cls": "C2"})
    assert r.verdict.reason == "frozen" and len(env.transport.calls) == 1
    assert door.call("conversations.history", {"channel": dm}).ok                 # reads continue
    # an explicit Profile object is the caller's: used as given
    fixed = Door(transport=env.transport, profile=door.profile)
    env.write(freeze=False)
    assert fixed.call("chat.postMessage", {"channel": dm, "text": "r3"}, as_bot=True, provenance={"cls": "C2"}).verdict.reason == "frozen"


def test_cx4_fallback_mode_passes_the_version_check(env):
    from atlas_slack import __version__
    env.write(decision_fallback=True)
    out = env.hook(post(comm_id="20261031-fb"))
    assert out["permissionDecision"] == "ask" and "updatedInput" not in out
    row = env.journal._conn.execute("SELECT adapter FROM decisions ORDER BY issued_at DESC LIMIT 1").fetchone()[0]
    assert f"v={__version__}" in row and "session=s-test" in row
    res = env.send(post(comm_id="20261031-fb"))                                  # no wire token: the row is consumed
    assert res.data["ok"] and len(env.transport.calls) == 1
    assert env.journal.get_op(res.op_id)["prepared_by"] == "cc-session:s-test"
    # a row issued by a hook of another version: refused before any call
    env.hook(post(comm_id="20261031-fb2"))
    env.journal._conn.execute("UPDATE decisions SET adapter=replace(adapter, 'v=' || ?, 'v=0.5.9') WHERE consumed_at IS NULL", (__version__,))
    refused(env, post(comm_id="20261031-fb2"), "version_skew")
    assert len(env.transport.calls) == 1


def test_cx5_ledger_match_is_not_claimed_over_blocks(env, tmp_path, monkeypatch):
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    (ledger_dir / "2026-10-31--x.md").write_text("---\ncomm_id: 20261031-blk\naudit: pass\naudit_at: 2026-10-31\n---\n## Draft v1\nhello\n")
    monkeypatch.setenv("ATLAS_SLACK_LEDGER_DIR", str(ledger_dir))
    plain = env.hook(post(as_bot=False, text="hello", comm_id="20261031-blk"))["permissionDecisionReason"]
    assert "ledger: ✅" in plain and "не сверен" not in plain and "not compared" not in plain
    t = post(as_bot=False, text="hello", comm_id="20261031-blk")
    t["params"]["blocks"] = [{"type": "section", "text": {"type": "mrkdwn", "text": "something else"}}]
    withblocks = env.hook(t)["permissionDecisionReason"]
    assert "ledger: ✅" in withblocks and ("не сверен" in withblocks or "not compared" in withblocks)
    res = env.send(env.approve(t))
    assert any("blocks" in line for line in json.loads(env.journal.get_op(res.op_id)["provenance_json"])["ledger_at_send"])


# ================================================================ gate round 2 (verifier full list · spec-verifier round 2)

def test_g1_text_match_needs_the_intent_window_and_the_identity(env):
    now = 1_800_000_000.0
    bot = _unknown_row(env, "chat.postMessage", {"channel": CHANNEL, "text": "hello team"}, True, now - 600, "g-bot")
    person = _unknown_row(env, "chat.postMessage", {"channel": CHANNEL, "text": "ok"}, False, now - 600, "g-person")
    older = str(now - 630)      # 30 s before the intent — not ours

    def reader(method, params, as_bot):
        assert params.get("include_all_metadata") is True
        return {"ok": True, "messages": [
            {"ts": older, "text": "hello team", "user": "U0OTHERBOT", "bot_id": "B9"},
            {"ts": older, "text": "ok", "user": env.ids["person_user_id"]},
            {"ts": str(now - 590), "text": "hello team", "user": "U0OTHERBOT", "bot_id": "B9"},   # right time, wrong bot
        ]}
    res = {r.op_id: r for r in rec.reconcile(env.journal, reader, env.profile, now=now)}
    assert res[bot].outcome == "error" and res[person].outcome == "error"           # neither older nor foreign counts
    good = {"ts": str(now - 590), "text": "hello team", "user": env.ids["bot_user_id"], "bot_id": "B1"}
    bot2 = _unknown_row(env, "chat.postMessage", {"channel": CHANNEL, "text": "hello team"}, True, now - 600, "g-bot2")
    res = {r.op_id: r for r in rec.reconcile(env.journal, lambda m, p, b: {"ok": True, "messages": [good]}, env.profile, now=now)}
    assert res[bot2].outcome == "ok"


def test_g2_blocks_only_update_stays_unknown_and_journal_failure_is_reported(env, monkeypatch):
    now = 1_800_000_000.0
    u = _unknown_row(env, "chat.update", {"channel": CHANNEL, "ts": TS, "blocks": [{"type": "divider"}]}, True, now - 600, "g-blocks")
    res = rec.reconcile(env.journal, lambda m, p, b: {"ok": True, "messages": [{"ts": TS, "text": ""}]}, env.profile, now=now)
    assert res[0].outcome == "unknown" and "blocks-only" in res[0].detail
    assert rec.reconcile(env.journal, lambda m, p, b: {"ok": True}, env.profile, now=now, op_ids=[]) == []
    p = _unknown_row(env, "chat.postMessage", {"channel": CHANNEL, "text": "gone"}, True, now - 600, "g-gone")
    from atlas_slack.journal import Journal, JournalUnavailable
    monkeypatch.setattr(Journal, "record_outcome", lambda self, *a, **k: (_ for _ in ()).throw(JournalUnavailable("locked")))
    res = {r.op_id: r for r in rec.reconcile(env.journal, lambda m, p_, b: {"ok": True, "messages": []}, env.profile, now=now, op_ids=[p])}
    assert res[p].outcome == "unknown" and "could not record" in res[p].detail


def test_g3_upload_preflight_refuses_what_the_sdk_would(env, tmp_path):
    ok = tmp_path / "a.txt"
    ok.write_text("a")
    for params in ({"file": str(ok), "content": "x"}, {"file": str(ok), "file_uploads": [{"content": "b"}]},
                   {"file_uploads": []}, {"file_uploads": ["x"]}, {"file_uploads": [5]}, {"file_uploads": [{"title": "no source"}]},
                   {"file_uploads": [{"file": str(ok), "content": "b"}]}, {"content": 5}):
        out = env.hook({"method": "files.uploadV2", "params": {"channel": CHANNEL, **params}, "as_bot": True, "provenance": {"cls": "B-h"}})
        assert out["permissionDecision"] == "deny" and "invalid_arguments" in out["permissionDecisionReason"], params
    assert env.transport.calls == []


def test_g4_trailing_newline_never_reaches_slack(env):
    env.transport = Slack(author=env.ids["bot_user_id"])
    t = update(comm_id="g-nl"); t["params"]["ts"] = TS + "\n"
    assert "missing_params" in env.hook(t)["permissionDecisionReason"]
    e = ephemeral(comm_id="g-nl2"); e["params"]["user"] = "U0VIEWER01\n"
    assert "missing_params" in env.hook(e)["permissionDecisionReason"]
    assert env.transport.calls == []


def test_g5_update_keeps_foreign_metadata_and_warns_on_caller_metadata(env):
    class WithMeta(Slack):
        def __call__(self, method, params, as_bot):
            if method == "conversations.replies":
                self.calls.append((method, dict(params), as_bot))
                return {"ok": True, "messages": [{"ts": params["ts"], "user": self.author, "bot_id": "B1", "text": "old",
                                                   "metadata": {"event_type": "other_app_event", "event_payload": {"k": 1}}}]}
            return super().__call__(method, params, as_bot)
    env.transport = WithMeta(author=env.ids["bot_user_id"])
    res = env.send(env.approve(update(comm_id="g-meta")))
    assert "metadata" not in writes(env.transport)[0][1]           # an edit never touches metadata
    own = post(comm_id="g-meta2"); own["params"]["metadata"] = {"event_type": "mine", "event_payload": {}}
    assert any("caller metadata kept" in w for w in policy_mod.evaluate("chat.postMessage", own["params"], True, own["provenance"],
                                                                        env.profile, journal=env.journal).warnings)


def test_g6_person_voice_edit_of_a_user_token_post_is_allowed(env):
    """A user-token app post keeps `user` = the person; a bot_id there must not refuse the person's own edit."""
    p = env.profile
    assert prior_mod.author_problem({"user": p.person_user_id, "bot_id": "B1"}, False, p) is None
    assert prior_mod.author_problem({"user": None, "bot_id": "B1"}, False, p) is not None
    assert "not by you" in prior_mod.author_problem({"user": "U0OTHER", "bot_id": "B1"}, False, p)


def test_g7_child_reserve_failure_is_an_error_not_a_journaled_post(env, tmp_path, monkeypatch):
    from atlas_slack.journal import Journal, JournalUnavailable
    env.transport = Slack()
    ti = env.approve(upload(tmp_path, comment="c", attribution="block"))
    real = Journal.reserve
    def flaky(self, op, *a, **k):
        if op.op_id.endswith(".c1"):
            raise JournalUnavailable("locked")
        return real(self, op, *a, **k)
    monkeypatch.setattr(Journal, "reserve", flaky)
    res = env.send(ti)
    assert res.child_outcome == "skipped" and "not posted" in res.child_error
    assert [c[0] for c in env.transport.calls] == ["files.uploadV2"]


def test_g8_block_comment_gets_its_permalink_from_files_info(env, tmp_path):
    class NoPermalink(Slack):
        def __call__(self, method, params, as_bot):
            if method == "files.uploadV2":
                self.calls.append((method, dict(params), as_bot))
                return {"ok": True, "files": [{"id": "F0009", "title": "Report"}]}
            if method == "files.info":
                self.calls.append((method, dict(params), as_bot))
                return {"ok": True, "file": {"id": "F0009", "title": "Report", "permalink": "https://x.slack.com/files/F0009"}}
            return super().__call__(method, params, as_bot)
    env.transport = NoPermalink()
    res = env.send(env.approve(upload(tmp_path, comment="here", attribution="block")))
    assert [c[0] for c in env.transport.calls] == ["files.uploadV2", "files.info", "chat.postMessage"]
    assert env.transport.calls[2][1]["text"] == "here\n<https://x.slack.com/files/F0009|Report>"
    assert res.child_outcome == "ok"


# ================================================================ gate round 3 (spec-verifier)

def test_h1_manual_resolution_is_a_decision_asked_by_the_hook_and_recorded_by_the_server(env):
    now = 1_800_000_000.0
    u = _unknown_row(env, "chat.update", {"channel": CHANNEL, "ts": TS, "blocks": [{"type": "divider"}]}, True, now - 600, "h-blocks")
    other = _unknown_row(env, "chat.postMessage", {"channel": CHANNEL, "text": "x"}, True, now - 600, "h-other")
    # the hook: a read is allowed, the person's word is asked for
    assert env.hook({"method": "_reconcile"})["permissionDecision"] == "allow"
    ask = env.hook({"method": "_reconcile", "params": {"op_ids": [u], "resolve": "ok"}})
    assert ask["permissionDecision"] == "ask" and "by your word" in ask["permissionDecisionReason"] and u in ask["permissionDecisionReason"]
    from atlas_slack.gate import hook_output
    from test_v2_slice import TOOL
    unsafe = hook_output({"tool_name": TOOL, "tool_input": {"method": "_reconcile", "params": {"op_ids": [u], "resolve": "ok"}},
                          "permission_mode": "bypassPermissions"})["hookSpecificOutput"]
    assert unsafe["permissionDecision"] == "deny" and "permission_mode_unsafe" in unsafe["permissionDecisionReason"]
    assert "chat.update" in ask["permissionDecisionReason"] and CHANNEL in ask["permissionDecisionReason"]   # what to look for
    assert ask["updatedInput"]["params"]["decision"]["t"]
    mock = MagicMock()
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        with pytest.raises(ToolError, match="no_decision"):                       # a call that never met the hook
            gateway.slack_api("_reconcile", {"op_ids": [u], "resolve": "ok"})
        assert env.journal.get_op(u)["outcome"] == "unknown"
        out = gateway.slack_api(**ask["updatedInput"])
        with pytest.raises(ToolError, match="decision_already_used"):             # one word, once
            gateway.slack_api(**ask["updatedInput"])
        swapped = env.hook({"method": "_reconcile", "params": {"op_ids": [other], "resolve": "absent"}})["updatedInput"]
        swapped["params"]["resolve"] = "ok"
        with pytest.raises(ToolError, match="decision_mismatch"):                 # the token covers these ops + this word
            gateway.slack_api(**swapped)
        with pytest.raises(ToolError, match="invalid_arguments"):
            gateway.slack_api("_reconcile", {"op_ids": [], "resolve": "ok"})
        with pytest.raises(ToolError, match="invalid_arguments"):
            gateway.slack_api("_reconcile", {"op_ids": [other], "resolve": "maybe"})
        nothing = gateway.slack_api("_reconcile", {"op_ids": ["nope"]})
        absent = gateway.slack_api(**env.hook({"method": "_reconcile", "params": {"op_ids": [other], "resolve": "absent"}})["updatedInput"])
    mock.api_call.assert_not_called()
    assert "resolved by hand" in out and env.journal.get_op(u)["outcome"] == "ok" and env.journal.get_op(u)["error_code"] == "resolved_by_hand"
    assert nothing.startswith("reconcile: no unknown op among")
    assert env.journal.get_op(other)["outcome"] == "error" and env.journal.get_op(other)["error_code"] == "resolved_by_hand_absent"
    assert env.journal.ops("unknown") == []
    # the library has no one to ask
    r = Door(transport=lambda m, p, b: {"ok": True}).call("_reconcile", {"op_ids": [u], "resolve": "ok"})
    assert r.verdict.reason == "no_decision_adapter" and not r.ok


def test_h2_person_voice_never_edits_the_bots_posts(env):
    p = env.profile
    assert prior_mod.author_problem({"user": p.bot_user_id, "bot_id": "B1"}, False, p) is not None   # ≠ person: refused
    bare = type("P", (), {"person_user_id": None, "bot_user_id": p.bot_user_id})()
    assert "posted by the bot" in prior_mod.author_problem({"user": p.bot_user_id, "bot_id": "B1"}, False, bare)
    assert prior_mod.author_problem({"user": "U0SOMEONE"}, False, bare) is None        # unknown person id: Slack decides


# ================================================================ verifier round 2

def test_i1_our_metadata_with_another_op_never_text_matches(env):
    now = 1_800_000_000.0
    b = _unknown_row(env, "chat.postMessage", {"channel": CHANNEL, "text": "Done"}, True, now - 600, "i-b")
    a_msg = {"ts": str(now - 598), "text": "Done", "user": env.ids["bot_user_id"],
             "metadata": {"event_type": "atlas_slack_op", "event_payload": {"op_id": "i-a"}}}
    res = rec.reconcile(env.journal, lambda m, p, bot: {"ok": True, "messages": [a_msg]}, env.profile, now=now)
    assert res[0].outcome == "error" and res[0].error_code == "reconciled_absent"      # A's message is not B's


def test_i2_op_ids_must_be_a_list(env):
    mock = MagicMock()
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        with pytest.raises(ToolError, match="invalid_arguments"):
            gateway.slack_api("_reconcile", {"op_ids": "abc"})
    mock.api_call.assert_not_called()
    assert Door(transport=lambda m, p, b: {"ok": True}).call("_reconcile", {"op_ids": "abc"}).verdict.reason == "invalid_arguments"


def test_i3_unreadable_prior_means_no_metadata_on_the_edit(env):
    env.transport = Slack(fail_on={"conversations.replies": TimeoutError("slow")})
    res = env.send(env.approve(update(comm_id="i-unread")))
    assert "metadata" not in writes(env.transport)[0][1]


def test_j1_no_comm_id_blind_repeat_is_refused_while_an_unknown_row_exists(env):
    env.policy(f"""
version: "r4"
channels:
  {env.ids['person_bot_dm_id']}:
    class: dm
    voices: [bot]
    modes: {{C2: auto}}
""")
    dm = env.ids["person_bot_dm_id"]
    env.transport.exc = ConnectionResetError("peer reset")
    door = Door(transport=env.transport)
    first = door.call("chat.postMessage", {"channel": dm, "text": "nightly  report"}, as_bot=True, provenance={"cls": "C2"})
    assert not first.ok and env.journal.ops("unknown")
    env.transport.exc = None
    again = door.call("chat.postMessage", {"channel": dm, "text": "nightly report"}, as_bot=True, provenance={"cls": "C2"})
    assert again.verdict.reason == "reconcile_required" and len(env.transport.calls) == 1
    other = door.call("chat.postMessage", {"channel": dm, "text": "different"}, as_bot=True, provenance={"cls": "C2"})
    assert other.ok                                                               # another text is another communication
    # the interactive face too: a provenance-less send of that text asks nothing, it is refused
    out = env.hook({"method": "chat.postMessage", "params": {"channel": dm, "text": "nightly report"}, "as_bot": True})
    assert out["permissionDecision"] == "deny" and "reconcile_required" in out["permissionDecisionReason"]
    # reconciled → clear
    rec.reconcile(env.journal, lambda m, p, b: {"ok": True, "messages": []}, env.profile, now=time.time() + 600)
    assert door.call("chat.postMessage", {"channel": dm, "text": "nightly report"}, as_bot=True, provenance={"cls": "C2"}).ok


def test_j2_guard_keys_on_thread_and_post_at_and_sees_pending(env):
    env.policy(f"""
version: "r5"
channels:
  {env.ids['person_bot_dm_id']}:
    class: dm
    voices: [bot]
    modes: {{C2: auto}}
""")
    dm = env.ids["person_bot_dm_id"]
    env.transport.exc = TimeoutError("x")
    door = Door(transport=env.transport)
    assert not door.call("chat.postMessage", {"channel": dm, "text": "same", "thread_ts": "1.0"}, as_bot=True, provenance={"cls": "C2"}).ok
    env.transport.exc = None
    assert door.call("chat.postMessage", {"channel": dm, "text": "same", "thread_ts": "2.0"}, as_bot=True, provenance={"cls": "C2"}).ok   # another thread
    assert door.call("chat.postMessage", {"channel": dm, "text": "same", "thread_ts": "1.0"}, as_bot=True,
                     provenance={"cls": "C2"}).verdict.reason == "reconcile_required"
    # a pending row (a process that died seconds ago) guards too
    env.journal.reserve(OpIntent(op_id="pend-1", method="chat.postMessage", payload_hash="h", comm_id="c-pend",
                                 payload={"params": {"channel": dm, "text": "still in flight"}, "final_params": {}, "as_bot": True},
                                 target={"channel": dm}))
    assert door.call("chat.postMessage", {"channel": dm, "text": "still in flight"}, as_bot=True,
                     provenance={"cls": "C2"}).verdict.reason == "reconcile_required"


def test_k1_caller_may_not_forge_the_doors_metadata(env):
    forged = {"event_type": "atlas_slack_op", "event_payload": {"op_id": "deadbeefdeadbeef"}}
    for method, params in (("chat.postMessage", {"channel": CHANNEL, "text": "x"}),
                           ("chat.update", {"channel": CHANNEL, "ts": TS, "text": "x"})):
        for meta in (forged, json.dumps(forged)):
            out = env.hook({"method": method, "params": {**params, "metadata": meta}, "as_bot": True, "provenance": {"cls": "B-h"}})
            assert out["permissionDecision"] == "deny" and "metadata_reserved" in out["permissionDecisionReason"], (method, meta)
    own = {"event_type": "mine", "event_payload": {"k": "v"}}
    v = policy_mod.evaluate("chat.update", {"channel": CHANNEL, "ts": TS, "text": "x", "metadata": own}, True, {"cls": "B-h"},
                            env.profile, journal=env.journal)
    assert v.decision == "needs-human" and any("replaces the message's existing metadata" in w for w in v.warnings)
    assert env.transport.calls == []


def test_k2_manual_resolution_on_the_fallback_path_and_pending_wording(env):
    now = 1_800_000_000.0
    u = _unknown_row(env, "chat.update", {"channel": CHANNEL, "ts": TS, "blocks": [{"type": "divider"}]}, True, now - 600, "k-fb")
    env.write(decision_fallback=True)
    ask = env.hook({"method": "_reconcile", "params": {"op_ids": [u], "resolve": "absent"}})
    assert ask["permissionDecision"] == "ask" and "updatedInput" not in ask
    mock = MagicMock()
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        with pytest.raises(ToolError, match="decision_mismatch|no_decision"):
            gateway.slack_api("_reconcile", {"op_ids": [u], "resolve": "ok"})          # another word than the one asked
        out = gateway.slack_api("_reconcile", {"op_ids": [u], "resolve": "absent"})
        with pytest.raises(ToolError, match="no_decision"):
            gateway.slack_api("_reconcile", {"op_ids": [u], "resolve": "absent"})       # the row is consumed
    assert "resolved by hand" in out and env.journal.get_op(u)["error_code"] == "resolved_by_hand_absent"
    # pending wording + promotion by _reconcile
    env.journal.reserve(OpIntent(op_id="k-pend", method="chat.postMessage", payload_hash="h", comm_id="c-k",
                                 payload={"params": {"channel": CHANNEL, "text": "in flight"}, "final_params": {}, "as_bot": True},
                                 target={"channel": CHANNEL}))
    v = policy_mod.evaluate("chat.postMessage", {"channel": CHANNEL, "text": "in flight"}, True, None, env.profile, journal=env.journal)
    assert v.reason == "reconcile_required" and "wait" in v.message and "pending" in v.message
    env.journal._conn.execute("UPDATE ops SET intent_at=intent_at-700 WHERE op_id='k-pend'")
    res = rec.reconcile(env.journal, lambda m, p, b: {"ok": True, "messages": []}, env.profile)
    assert [r.op_id for r in res] == ["k-pend"]                                           # stale pending → unknown → resolved


def test_k3_fallback_profile_unapproved_send_is_no_decision_not_version_skew(env):
    env.write(decision_fallback=True)
    refused(env, post(comm_id="k3-never-asked"), "no_decision")
    assert env.transport.calls == []


# ================================================================ MC live check 2026-10-31 (0.6.0 installed)

def test_l1_an_edit_without_comm_id_inherits_the_posts_comm_and_parent(env):
    res = env.send(env.approve(post(comm_id="20261031-orig2")))
    env.transport = Slack(author=env.ids["bot_user_id"])
    t = {"method": "chat.update", "params": {"channel": CHANNEL, "ts": res.data["ts"], "text": "v2"}, "as_bot": True}
    out = env.hook(t)
    assert out["permissionDecision"] == "ask" and "comm 20261031-orig2" in out["permissionDecisionReason"]
    res2 = env.send(out["updatedInput"])
    r = row(env, res2.op_id)
    assert r["comm_id"] == "20261031-orig2" and r["parent_op_id"] == res.op_id and not res2.comm_minted
    assert [x["method"] for x in env.journal.find_by_comm_id("20261031-orig2")] == ["chat.postMessage", "chat.update"]
    # a second edit still points at the post, not at the first edit (an edit row stores the same ts)
    env.transport = Slack(author=env.ids["bot_user_id"])
    res2b = env.send(env.hook({"method": "chat.update", "params": {"channel": CHANNEL, "ts": res.data["ts"], "text": "v3"}, "as_bot": True})["updatedInput"])
    assert row(env, res2b.op_id)["parent_op_id"] == res.op_id and row(env, res2b.op_id)["comm_id"] == "20261031-orig2"
    # a caller comm_id wins over the inherited one
    res2c = env.send(env.approve({"method": "chat.update", "params": {"channel": CHANNEL, "ts": res.data["ts"], "text": "v4"},
                                  "as_bot": True, "provenance": {"comm_id": "20261031-mine"}}))
    assert row(env, res2c.op_id)["comm_id"] == "20261031-mine" and row(env, res2c.op_id)["parent_op_id"] == res.op_id
    # the same ts in another channel is another post
    env.journal.reserve(OpIntent(op_id="other-chan", method="chat.postMessage", payload_hash="h", comm_id="c-other",
                                 payload={}, target={"channel": "C0OTHERCH1"}))
    env.journal.record_outcome("other-chan", "ok", slack_ts=res.data["ts"])
    assert env.journal.find_by_slack_ts(res.data["ts"], CHANNEL)["op_id"] == res.op_id
    assert env.journal.find_by_slack_ts(res.data["ts"], "C0OTHERCH1")["op_id"] == "other-chan"
    # an edit of a message the door never posted still mints (nothing to inherit)
    res3 = env.send(env.approve({"method": "chat.update", "params": {"channel": CHANNEL, "ts": "1700000000.999999", "text": "x"}, "as_bot": True}))
    assert res3.comm_minted and row(env, res3.op_id)["parent_op_id"] is None


def test_l2_update_result_line_carries_the_ts(env):
    from atlas_slack.formatters import format_response
    out = format_response("chat.update", {"ok": True, "channel": CHANNEL, "ts": TS, "text": "new", "message": {"text": "new", "user": "U1"}})
    assert out.startswith(f"Message (ts={TS}):")


# ================================================================ Codex addendum (31.10): A1 scheduled metadata · A2 pagination · A3 upload proof

def test_a1_scheduled_sends_never_carry_metadata(env):
    assert "chat.scheduleMessage" not in shaping.METADATA_METHODS
    from test_v2_slice import schedule, ScheduleTransport
    env.transport = ScheduleTransport()
    env.send(env.approve(schedule(comm_id="20261031-a1")))
    assert "metadata" not in env.transport.calls[0][1]
    t = schedule(comm_id="20261031-a1b"); t["params"]["metadata"] = {"event_type": "mine", "event_payload": {}}
    out = env.hook(t)
    assert out["permissionDecision"] == "deny" and "invalid_arguments" in out["permissionDecisionReason"] \
        and "never posted" in out["permissionDecisionReason"]
    assert len(env.transport.calls) == 1


def test_a2_an_incomplete_scan_is_no_proof_of_absence(env):
    now = 1_800_000_000.0
    p2 = _unknown_row(env, "chat.postMessage", {"channel": CHANNEL, "text": "on page two"}, True, now - 600, "a2-p2")
    calls = []

    def paged(method, params, as_bot):
        calls.append(dict(params))
        if params.get("cursor") == "PAGE_TWO":
            return {"ok": True, "messages": [{"ts": str(now - 590), "text": "x", "user": env.ids["bot_user_id"],
                                              "metadata": {"event_type": "atlas_slack_op", "event_payload": {"op_id": "a2-p2"}}}]}
        return {"ok": True, "messages": [{"ts": str(now - 595), "text": "someone else"}],
                "has_more": True, "response_metadata": {"next_cursor": "PAGE_TWO"}}
    res = rec.reconcile(env.journal, paged, env.profile, now=now, op_ids=[p2])
    assert res[0].outcome == "ok" and len(calls) == 2 and calls[1]["cursor"] == "PAGE_TWO"
    # a cursor that never ends: unknown after MAX_PAGES, never absent
    endless = _unknown_row(env, "chat.postMessage", {"channel": CHANNEL, "text": "never found"}, True, now - 600, "a2-endless")
    res = rec.reconcile(env.journal, lambda m, p, b: {"ok": True, "messages": [], "response_metadata": {"next_cursor": "MORE"}},
                        env.profile, now=now, op_ids=[endless])
    assert res[0].outcome == "unknown" and "incomplete" in res[0].detail
    # has_more without a cursor, and a failing second page: unknown too
    broken = _unknown_row(env, "chat.postMessage", {"channel": CHANNEL, "text": "broken"}, True, now - 600, "a2-broken")
    res = rec.reconcile(env.journal, lambda m, p, b: {"ok": True, "messages": [], "has_more": True}, env.profile, now=now, op_ids=[broken])
    assert res[0].outcome == "unknown"
    # scheduled list paginates the same way
    s = _unknown_row(env, "chat.scheduleMessage", {"channel": CHANNEL, "text": "later", "post_at": int(now) + 3600}, True, now - 600, "a2-sched")
    res = rec.reconcile(env.journal, lambda m, p, b: {"ok": True, "scheduled_messages": [], "response_metadata": {"next_cursor": "N"}},
                        env.profile, now=now, op_ids=[s])
    assert res[0].outcome == "unknown"


def test_a3_an_upload_is_proven_by_files_created_after_the_intent_one_per_name(env):
    now = 1_800_000_000.0
    two = _unknown_row(env, "files.uploadV2", {"channel": CHANNEL, "file_uploads": [{"file": "/abs/a.txt"}, {"file": "/abs/b.txt"}]},
                       False, now - 600, "a3-two")
    me = env.ids["person_user_id"]
    old_a = {"id": "F_OLD", "name": "a.txt", "user": me, "created": int(now - 630)}            # 30 s before the intent
    new_a = {"id": "F_A", "name": "a.txt", "user": me, "created": int(now - 590)}
    new_b = {"id": "F_B", "name": "b.txt", "user": me, "created": int(now - 589)}
    # only the old namesake → not ours → absent (window passed, scan complete)
    res = rec.reconcile(env.journal, lambda m, p, b: {"ok": True, "files": [old_a], "paging": {"page": 1, "pages": 1}}, env.profile, now=now, op_ids=[two])
    assert res[0].outcome == "error" and res[0].error_code == "reconciled_absent"
    # without a paging object the scan is not provably complete → unknown, never absent
    two0 = _unknown_row(env, "files.uploadV2", {"channel": CHANNEL, "file": "/abs/z.txt"}, False, now - 600, "a3-nopaging")
    res = rec.reconcile(env.journal, lambda m, p, b: {"ok": True, "files": []}, env.profile, now=now, op_ids=[two0])
    assert res[0].outcome == "unknown" and "paging" in res[0].detail
    # a non-finite created is undatable
    res = rec.reconcile(env.journal, lambda m, p, b: {"ok": True, "files": [{"id": "F_N", "name": "z.txt", "user": me, "created": "nan"}],
                                                   "paging": {"page": 1, "pages": 1}}, env.profile, now=now, op_ids=[two0])
    assert res[0].outcome == "unknown" and "creation time" in res[0].detail
    two2 = _unknown_row(env, "files.uploadV2", {"channel": CHANNEL, "file_uploads": [{"file": "/abs/a.txt"}, {"file": "/abs/b.txt"}]},
                        False, now - 600, "a3-two2")
    # one of two → partial → unknown
    res = rec.reconcile(env.journal, lambda m, p, b: {"ok": True, "files": [new_a], "paging": {"page": 1, "pages": 1}}, env.profile, now=now, op_ids=[two2])
    assert res[0].outcome == "unknown" and "1 of 2" in res[0].detail
    # both → ok, both ids recorded
    res = rec.reconcile(env.journal, lambda m, p, b: {"ok": True, "files": [old_a, new_a, new_b], "paging": {"page": 1, "pages": 1}}, env.profile, now=now, op_ids=[two2])
    assert res[0].outcome == "ok" and res[0].slack_ts == "F_A,F_B"
    # a namesake without a creation time cannot be told from an older one → unknown
    one = _unknown_row(env, "files.uploadV2", {"channel": CHANNEL, "file": "/abs/c.txt"}, False, now - 600, "a3-nodate")
    res = rec.reconcile(env.journal, lambda m, p, b: {"ok": True, "files": [{"id": "F_C", "name": "c.txt", "user": me}]},
                        env.profile, now=now, op_ids=[one])
    assert res[0].outcome == "unknown" and "creation time" in res[0].detail
    # files.list paging: page 1 of 2 without the file → read page 2
    pages = []

    def paged(method, params, as_bot):
        pages.append(params.get("page", 1))
        if params.get("page") == 2:
            return {"ok": True, "files": [{"id": "F_C2", "name": "c.txt", "user": me, "created": int(now - 590)}],
                    "paging": {"page": 2, "pages": 2}}
        return {"ok": True, "files": [], "paging": {"page": 1, "pages": 2}}
    res = rec.reconcile(env.journal, paged, env.profile, now=now, op_ids=[one])
    assert res[0].outcome == "ok" and pages[:2] == [1, 2]          # page 2 was read (other unknown uploads read too)


def test_a3b_two_items_with_the_same_name_need_two_files(env):
    now = 1_800_000_000.0
    me = env.ids["person_user_id"]
    dup = _unknown_row(env, "files.uploadV2", {"channel": CHANNEL, "file_uploads": [{"file": "/x/a.txt"}, {"file": "/y/a.txt"}]},
                       False, now - 600, "a3b-dup")
    one = [{"id": "F1", "name": "a.txt", "user": me, "created": int(now - 590)}]
    res = rec.reconcile(env.journal, lambda m, p, b: {"ok": True, "files": one, "paging": {"page": 1, "pages": 1}}, env.profile, now=now, op_ids=[dup])
    assert res[0].outcome == "unknown" and "1 of 2" in res[0].detail
    two = one + [{"id": "F2", "name": "a.txt", "user": me, "created": int(now - 589)}]
    res = rec.reconcile(env.journal, lambda m, p, b: {"ok": True, "files": two}, env.profile, now=now, op_ids=[dup])
    assert res[0].outcome == "ok" and res[0].slack_ts == "F1,F2"


def test_a3c_one_file_never_proves_two_uploads(env):
    """Codex A3, second pass: two unknown uploads of the same name, overlapping windows, one file in Slack."""
    now = 1_800_000_000.0
    me = env.ids["person_user_id"]
    a = _unknown_row(env, "files.uploadV2", {"channel": CHANNEL, "file": "/x/report.pdf"}, False, now - 600, "a3c-a")
    b = _unknown_row(env, "files.uploadV2", {"channel": CHANNEL, "file": "/y/report.pdf"}, False, now - 580, "a3c-b")
    single = {"ok": True, "files": [{"id": "F_SINGLE", "name": "report.pdf", "user": me, "created": int(now - 570)}],
              "paging": {"page": 1, "pages": 1}}
    res = {r.op_id: r for r in rec.reconcile(env.journal, lambda m, p, bot: single, env.profile, now=now)}
    assert res[a].outcome == "unknown" and res[b].outcome == "unknown" and "ambiguous" in res[a].detail
    assert {r["op_id"] for r in env.journal.ops("unknown")} == {a, b}
    # two files → each op gets its own; a file an ok row already owns is never reused
    two = {"ok": True, "files": [{"id": "F_1", "name": "report.pdf", "user": me, "created": int(now - 570)},
                                 {"id": "F_2", "name": "report.pdf", "user": me, "created": int(now - 560)}],
           "paging": {"page": 1, "pages": 1}}
    res = {r.op_id: r for r in rec.reconcile(env.journal, lambda m, p, bot: two, env.profile, now=now)}
    assert {res[a].outcome, res[b].outcome} == {"ok"} and {res[a].slack_ts, res[b].slack_ts} == {"F_1", "F_2"}
    c = _unknown_row(env, "files.uploadV2", {"channel": CHANNEL, "file": "/z/report.pdf"}, False, now - 550, "a3c-c")
    res = rec.reconcile(env.journal, lambda m, p, bot: two, env.profile, now=now, op_ids=[c])
    assert res[0].outcome == "error" and res[0].error_code == "reconciled_absent"      # both files are owned already


def test_a3d_op_ids_restriction_does_not_bypass_the_ambiguity_rule(env):
    now = 1_800_000_000.0
    me = env.ids["person_user_id"]
    a = _unknown_row(env, "files.uploadV2", {"channel": CHANNEL, "file": "/x/report.pdf"}, False, now - 600, "a3d-a")
    b = _unknown_row(env, "files.uploadV2", {"channel": CHANNEL, "file": "/y/report.pdf"}, False, now - 580, "a3d-b")
    single = {"ok": True, "files": [{"id": "F_ONE", "name": "report.pdf", "user": me, "created": int(now - 570)}],
              "paging": {"page": 1, "pages": 1}}
    res = rec.reconcile(env.journal, lambda m, p, bot: single, env.profile, now=now, op_ids=[a])
    assert [r.op_id for r in res] == [a] and res[0].outcome == "unknown" and "ambiguous" in res[0].detail
    assert env.journal.get_op(b)["outcome"] == "unknown"                       # the rival row is untouched


def test_a3e_an_ok_multi_file_upload_owns_every_file_id(env):
    assert core.outcome_id({"ok": True, "files": [{"id": "FA"}, {"id": "FB"}]}) == "FA,FB"
    assert core.outcome_id({"ok": True, "file": {"id": "F1"}}) == "F1"
    assert core.outcome_id({"ok": True, "ts": "1.2", "message": {}}) == "1.2"
    now = 1_800_000_000.0
    me = env.ids["person_user_id"]
    env.journal.reserve(OpIntent(op_id="m-ok", method="files.uploadV2", payload_hash="h", comm_id="c-m", payload={},
                                 target={"channel": CHANNEL}))
    env.journal.record_outcome("m-ok", "ok", slack_ts="FA,FB")
    n = _unknown_row(env, "files.uploadV2", {"channel": CHANNEL, "file": "/n/b.txt"}, False, now - 600, "a3e-n")
    files = {"ok": True, "files": [{"id": "FB", "name": "b.txt", "user": me, "created": int(now - 590)}], "paging": {"page": 1, "pages": 1}}
    res = rec.reconcile(env.journal, lambda m, p, b: files, env.profile, now=now, op_ids=[n])
    assert res[0].outcome == "error" and res[0].error_code == "reconciled_absent"     # FB belongs to m-ok


def test_a3f_group_capacity_is_per_name_and_a_short_member_voids_the_group(env):
    """Codex, third pass: A expects report.pdf + summary.md, B expects report.pdf; Slack has one report.pdf and two
    summary.md — three files for three slots, yet the report can belong to either op: both stay unknown."""
    now = 1_800_000_000.0
    me = env.ids["person_user_id"]
    a = _unknown_row(env, "files.uploadV2", {"channel": CHANNEL, "file_uploads": [{"file": "/a/report.pdf"}, {"file": "/a/summary.md"}]},
                     False, now - 600, "a3f-bundle")
    b = _unknown_row(env, "files.uploadV2", {"channel": CHANNEL, "file": "/b/report.pdf"}, False, now - 590, "a3f-report")
    files = {"ok": True, "paging": {"page": 1, "pages": 1}, "files": [
        {"id": "F_REPORT", "name": "report.pdf", "user": me, "created": int(now - 580)},
        {"id": "F_SUM1", "name": "summary.md", "user": me, "created": int(now - 579)},
        {"id": "F_SUM2", "name": "summary.md", "user": me, "created": int(now - 578)},
    ]}
    res = {r.op_id: r for r in rec.reconcile(env.journal, lambda m, p, bot: files, env.profile, now=now)}
    assert res[a].outcome == "unknown" and res[b].outcome == "unknown" and "ambiguous" in res[b].detail
    assert {r["op_id"] for r in env.journal.ops("unknown")} == {a, b}
    # a second report makes both provable
    files["files"].append({"id": "F_REPORT2", "name": "report.pdf", "user": me, "created": int(now - 577)})
    res = {r.op_id: r for r in rec.reconcile(env.journal, lambda m, p, bot: files, env.profile, now=now)}
    assert res[a].outcome == "ok" and res[b].outcome == "ok"
    assert set(res[a].slack_ts.split(",")) | set(res[b].slack_ts.split(",")) == {"F_REPORT", "F_SUM1", "F_REPORT2"}
    assert not (set(res[a].slack_ts.split(",")) & set(res[b].slack_ts.split(",")))


def test_a3g_restricted_runs_execute_the_groups_proven_assignment(env):
    """verifier: B (earlier, one x.txt) and A (later, two x.txt); F1,F2 in both windows, F3 only in A's.
    A requested alone must not take B's F1; B requested afterwards must still be ok."""
    now = 1_800_000_000.0
    me = env.ids["person_user_id"]
    b = _unknown_row(env, "files.uploadV2", {"channel": CHANNEL, "file": "/b/x.txt"}, False, now - 3000, "a3g-b")
    a = _unknown_row(env, "files.uploadV2", {"channel": CHANNEL, "file_uploads": [{"file": "/a1/x.txt"}, {"file": "/a2/x.txt"}]},
                     False, now - 1500, "a3g-a")
    allf = [{"id": "F1", "name": "x.txt", "user": me, "created": int(now - 2990)},
            {"id": "F2", "name": "x.txt", "user": me, "created": int(now - 1490)},
            {"id": "F3", "name": "x.txt", "user": me, "created": int(now - 1000)}]     # past B's 1800 s window

    def reader(method, params, as_bot):     # Slack filters by the query window
        lo, hi = params["ts_from"], params["ts_to"]
        return {"ok": True, "files": [f for f in allf if lo <= f["created"] <= hi], "paging": {"page": 1, "pages": 1}}
    res = rec.reconcile(env.journal, reader, env.profile, now=now, op_ids=[a])
    assert res[0].outcome == "ok" and set(res[0].slack_ts.split(",")) == {"F2", "F3"}      # not B's F1
    assert env.journal.get_op(b)["outcome"] == "unknown"
    res = rec.reconcile(env.journal, reader, env.profile, now=now, op_ids=[b])
    assert res[0].outcome == "ok" and res[0].slack_ts == "F1"
