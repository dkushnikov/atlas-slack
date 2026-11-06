"""0.8.1 — the bot-voice cc on a thread reply only while the person does not follow the thread; an edit keeps what the
original had. `follow.follows_thread`: the journal first (an earlier cc of ours in the thread), then one read of the
parent (author · reply_users · mentions); a parent that cannot be read → the cc goes.

Level 1: fake transport, no network, both profiles (test_v2_slice.Env)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from atlas_slack import core, follow, gateway, shaping
from atlas_slack.journal import OpIntent
from test_v2_slice import CHANNEL, Env, PROFILES, post  # noqa: F401
from test_v2_slice2 import Slack, update, upload, writes

PARENT_TS = "1700000000.100000"


@pytest.fixture(params=sorted(PROFILES))
def env(request, isolated_door_dirs, monkeypatch):
    e = Env(request.param, isolated_door_dirs, monkeypatch)
    e.write(cc_person=True)
    return e


def cc(env):
    return f"cc <@{env.ids['person_user_id']}>"


def person(env):
    return env.ids["person_user_id"]


class Thread(Slack):
    """A transport whose read of the parent (`ts == PARENT_TS`) answers with a configurable parent message."""

    def __init__(self, parent=None, fail_read=False, not_ok=False, **kw):
        super().__init__(**kw)
        self.parent = parent
        self.fail_read = fail_read
        self.not_ok = not_ok

    def __call__(self, method, params, as_bot):
        if method == "conversations.replies" and params.get("ts") == PARENT_TS:
            with self.lock:
                self.calls.append((method, dict(params), as_bot))
            if self.fail_read:
                raise RuntimeError("read failed")
            if self.not_ok:
                return {"ok": False, "error": "thread_not_found"}
            return {"ok": True, "messages": [self.parent] if self.parent else []}
        return super().__call__(method, params, as_bot)


def reply(text="in the thread", **extra):
    ti = post(text=text)
    ti["params"]["thread_ts"] = PARENT_TS
    ti["params"].update(extra)
    return ti


def parent_by(user, text="parent text", **extra):
    return {"ts": PARENT_TS, "user": user, "text": text, **extra}


def reads(tr):
    return [c for c in tr.calls if c[0] == "conversations.replies"]


# ---------------------------------------------------------------- the person does not follow → cc, after one read

def test_reply_in_a_strangers_thread_gets_the_cc_after_one_read_of_the_parent(env):
    env.transport = Thread(parent=parent_by("U0STRANGER", reply_users=["U0OTHER"]))
    env.send(env.approve(reply()))
    assert writes(env.transport)[-1][1]["text"] == f"in the thread\n\n{cc(env)}"
    r = reads(env.transport)
    assert len(r) == 1 and r[0][1] == {"channel": CHANNEL, "ts": PARENT_TS, "limit": 1, "inclusive": True} and r[0][2] is True
    assert env.transport.calls.index(r[0]) < len(env.transport.calls) - 1          # the read comes before the post


@pytest.mark.parametrize("why", ["read_fails", "parent_missing", "not_ok"])
def test_a_parent_that_cannot_be_read_means_the_cc_goes(env, why):
    env.transport = {"read_fails": Thread(fail_read=True), "parent_missing": Thread(parent=None),
                     "not_ok": Thread(not_ok=True)}[why]
    env.send(env.approve(reply()))
    assert len(reads(env.transport)) == 1
    assert writes(env.transport)[-1][1]["text"] == f"in the thread\n\n{cc(env)}"


# ---------------------------------------------------------------- the person follows → no cc

@pytest.mark.parametrize("how", ["author", "reply_users", "mention_in_text", "mention_in_blocks", "mention_in_attachments"])
def test_no_cc_when_the_person_already_follows_the_thread(env, how):
    p = person(env)
    parent = {
        "author": parent_by(p),
        "reply_users": parent_by("U0STRANGER", reply_users=["U0OTHER", p]),
        "mention_in_text": parent_by("U0STRANGER", text=f"digest\n\ncc <@{p}>"),
        "mention_in_blocks": parent_by("U0STRANGER", blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": f"cc <@{p}|d>"}}]),
        "mention_in_attachments": parent_by("U0STRANGER", attachments=[{"text": f"for <@{p}>"}]),
    }[how]
    env.transport = Thread(parent=parent)
    env.send(env.approve(reply()))
    sent = writes(env.transport)[-1][1]
    assert sent["text"] == "in the thread" and "blocks" not in sent
    assert len(reads(env.transport)) == 1


def test_no_read_when_the_text_already_mentions_the_person(env):
    env.transport = Thread(parent=parent_by("U0STRANGER"))
    env.send(env.approve(reply(text=f"<@{person(env)}> look")))
    assert writes(env.transport)[-1][1]["text"] == f"<@{person(env)}> look" and reads(env.transport) == []


def test_journal_check_counts_real_sends_only(env):
    """An ephemeral mentioning the person in the thread subscribes nobody; a scheduled message has not posted yet."""
    j = env.journal
    for op, method in (("t-eph", "chat.postEphemeral"), ("t-sched", "chat.scheduleMessage")):
        params = {"channel": CHANNEL, "thread_ts": PARENT_TS, "text": f"x\n\n{cc(env)}", "user": "U0VIEWER01"}
        j.reserve(OpIntent(op_id=op, method=method, payload_hash="h", comm_id=op,
                           payload={"as_bot": True, "params": params, "final_params": params}))
        j.record_outcome(op, "ok", slack_ts="9.9")
    assert j.thread_sends(CHANNEL, PARENT_TS) == []
    env.transport = Thread(parent=parent_by("U0STRANGER"))
    env.send(env.approve(reply()))
    assert writes(env.transport)[-1][1]["text"].endswith(cc(env)) and len(reads(env.transport)) == 1


def test_a_me_message_is_never_a_thread_reply(env):
    """chat.meMessage takes no thread_ts: a caller's thread_ts is ignored by the rule — base rule, no read, no journal match."""
    from test_v2_slice2 import me
    env.transport = Thread(parent=parent_by(person(env)))                 # a followed thread would have removed the cc
    ti = me(as_bot=True)
    ti["params"]["thread_ts"] = PARENT_TS
    env.send(env.approve(ti))
    sent = writes(env.transport)[-1][1]
    assert sent["text"] == f"shrugs\n\n{cc(env)}" and reads(env.transport) == []
    assert env.journal.thread_sends(CHANNEL, PARENT_TS) == []              # … and it never counts as our cc in the thread


def test_a_longer_id_that_starts_with_the_persons_id_is_not_the_person(env):
    p = person(env)
    env.transport = Thread(parent=parent_by("U0STRANGER", text=f"hi <@{p}X>", reply_users=[f"{p}X"]))
    env.send(env.approve(reply()))
    assert writes(env.transport)[-1][1]["text"].endswith(cc(env))


# ---------------------------------------------------------------- check 4: the journal, no Slack read

def test_a_second_reply_in_the_same_thread_needs_no_read_the_journal_remembers_our_cc(env):
    env.transport = Thread(parent=parent_by("U0STRANGER"))
    env.send(env.approve(reply(text="first", comm_id="20261105-t1")))
    assert writes(env.transport)[-1][1]["text"].endswith(cc(env)) and len(reads(env.transport)) == 1
    env.send(env.approve(reply(text="second", comm_id="20261105-t2")))
    assert writes(env.transport)[-1][1]["text"] == "second"                       # no cc …
    assert len(reads(env.transport)) == 1                                           # … and no second read
    # another thread in the same channel: the journal knows nothing → one read, cc
    other = post(text="elsewhere", comm_id="20261105-t3")
    other["params"]["thread_ts"] = "1700000000.200000"
    env.transport.calls.clear()
    env.transport.parent = None
    env.send(env.approve(other))
    assert writes(env.transport)[-1][1]["text"].endswith(cc(env))


def test_journal_thread_sends_filters_by_channel_and_outcome(env):
    j = env.journal
    base = {"as_bot": True, "params": {"channel": CHANNEL, "thread_ts": PARENT_TS, "text": "x"}}
    j.reserve(OpIntent(op_id="t-ok", method="chat.postMessage", payload_hash="h", comm_id="c1",
                       payload={**base, "final_params": {**base["params"], "text": f"x\n\n{cc(env)}"}}))
    j.record_outcome("t-ok", "ok", slack_ts="1.1")
    j.reserve(OpIntent(op_id="t-err", method="chat.postMessage", payload_hash="h", comm_id="c2",
                       payload={**base, "final_params": base["params"]}))
    j.record_outcome("t-err", "error", error_code="x")
    j.reserve(OpIntent(op_id="t-other", method="chat.postMessage", payload_hash="h", comm_id="c3",
                       payload={**base, "params": {**base["params"], "channel": "C0OTHER"}}))
    j.record_outcome("t-other", "ok", slack_ts="1.2")
    got = j.thread_sends(CHANNEL, PARENT_TS)
    assert len(got) == 1 and got[0]["final_params"]["text"].endswith(cc(env))
    assert len(j.thread_sends(None, PARENT_TS)) == 2
    assert follow.follows_thread(lambda *a: (_ for _ in ()).throw(AssertionError("no read")), j, CHANNEL, PARENT_TS, True,
                                 person(env)) is True
    assert follow.follows_thread(lambda *a: {"ok": True, "messages": [parent_by("U0STRANGER")]}, j, "C0OTHER", PARENT_TS,
                                 True, person(env)) is False


# ---------------------------------------------------------------- an edit keeps what the original had

def test_an_edit_keeps_the_cc_the_original_had_and_adds_none_otherwise(env):
    p = person(env)
    bot = env.ids["bot_user_id"]
    # the original had the cc (a 0.8.0 top-level post): the edit keeps it
    env.transport = Slack(author=bot, prior_text=f"digest\n\ncc <@{p}>")
    env.send(env.approve(update(text="digest v2", comm_id="20261105-e1")))
    assert writes(env.transport)[-1][1]["text"] == f"digest v2\n\n{cc(env)}"
    # the original had none (a reply the person followed, or a pre-0.8.0 post): the edit adds none
    env.transport = Slack(author=bot, prior_text="plain reply")
    env.send(env.approve(update(text="plain reply v2", comm_id="20261105-e2")))
    assert writes(env.transport)[-1][1]["text"] == "plain reply v2"
    # the original's cc sat in a section block (a blocks send): fetch_prior reads the mentions from the blocks
    tr = Slack(author=bot, prior_text="fallback")
    orig = tr.__call__

    def with_blocks(method, params, as_bot):
        out = orig(method, params, as_bot)
        if method == "conversations.replies":
            out["messages"][0]["blocks"] = [{"type": "section", "text": {"type": "mrkdwn", "text": f"cc <@{p}>"}}]
        return out
    env.transport = with_blocks
    env.transport.calls = tr.calls
    env.send(env.approve(update(text="v3", comm_id="20261105-e3")))
    assert [c for c in tr.calls if c[0] == "chat.update"][-1][1]["text"] == f"v3\n\n{cc(env)}"


def test_an_edit_whose_prior_could_not_be_read_falls_back_to_the_base_rule(env):
    env.transport = Slack(author=env.ids["bot_user_id"], fail_on={"conversations.replies": RuntimeError("down")})
    env.send(env.approve(update(text="edited blind", comm_id="20261105-e4")))
    assert writes(env.transport)[-1][1]["text"] == f"edited blind\n\n{cc(env)}"


def test_prior_carries_the_mentions(env):
    p = person(env)
    env.transport = Slack(author=env.ids["bot_user_id"], prior_text=f"a <@{p}> and <@U0OTHER|o>")
    res = env.send(env.approve(update(comm_id="20261105-e5")))
    assert res.prior["mentions"] == sorted([p, "U0OTHER"])
    row = env.journal.get_op(res.op_id)
    assert json.loads(row["provenance_json"])["prior"]["mentions"] == sorted([p, "U0OTHER"])


# ---------------------------------------------------------------- uploads with thread_ts, the two faces

def test_an_upload_into_a_thread_follows_the_same_rule(env, tmp_path):
    env.transport = Thread(parent=parent_by("U0STRANGER"))
    env.send(env.approve(upload(tmp_path, as_bot=True, comment="see", thread_ts=PARENT_TS)))
    assert writes(env.transport)[-1][1]["initial_comment"] == f"see\n\n{cc(env)}"
    env.transport = Thread(parent=parent_by(person(env)))
    env.send(env.approve(upload(tmp_path, as_bot=True, comment="see again", thread_ts=PARENT_TS)))
    assert writes(env.transport)[-1][1]["initial_comment"] == "see again"


def test_complete_upload_external_into_a_thread_on_the_transitional_path(env):
    p = person(env)
    mock = MagicMock()
    parent = {"followed": parent_by(p), "not": parent_by("U0STRANGER")}
    state = {"parent": parent["not"]}

    def api_call(method, params=None):
        if method == "conversations.replies":
            return {"ok": True, "messages": [state["parent"]]}
        return {"ok": True}
    mock.api_call.side_effect = api_call
    files = [{"id": "F1", "title": "doc"}]
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        gateway.slack_api("files.completeUploadExternal",
                          {"files": files, "channel_id": CHANNEL, "thread_ts": PARENT_TS, "initial_comment": "see"}, as_bot=True)
        assert mock.api_call.call_args.kwargs["params"]["initial_comment"] == f"see\n\n{cc(env)}"
        state["parent"] = parent["followed"]
        gateway.slack_api("files.completeUploadExternal",
                          {"files": files, "channel_id": CHANNEL, "thread_ts": PARENT_TS, "initial_comment": "see"}, as_bot=True)
        assert mock.api_call.call_args.kwargs["params"]["initial_comment"] == "see"
        read = [c for c in mock.api_call.call_args_list if c.args[0] == "conversations.replies"]
        assert len(read) == 2 and read[0].kwargs["params"]["channel"] == CHANNEL


# ---------------------------------------------------------------- the pure pieces

def test_follow_mentions_reads_text_blocks_and_attachments():
    assert follow.mentions({"text": "a <@U1> b <@U2|x>"}) == {"U1", "U2"}
    assert follow.mentions({"text": "", "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "<@U3>"}}]}) == {"U3"}
    assert follow.mentions({"attachments": [{"text": "<@U4>"}]}) == {"U4"}
    assert follow.mentions({"text": "<@U5X> <@U5"}) == {"U5X"}          # an unterminated mention is not one
    assert follow.payload_mentions({"params": {"text": "<@U6>"}}) == {"U6"}
    assert follow.payload_mentions({"params": {"text": "x"}, "final_params": {"initial_comment": "x\n\ncc <@U7>"}}) == {"U7"}


def test_cc_decision_leaves_the_base_rule_alone_outside_threads_and_edits(env):
    m = shaping.Marks.from_profile(env.profile)
    boom = lambda *a: (_ for _ in ()).throw(AssertionError("no read for a top-level send"))   # noqa: E731
    assert core.cc_decision("chat.postMessage", {"channel": CHANNEL, "text": "x"}, True, m, boom, None) is None
    assert core.cc_decision("chat.postMessage", {"channel": CHANNEL, "text": "x", "thread_ts": PARENT_TS}, False, m, boom, None) is None
    off = shaping.Marks.from_profile(env.profile)
    off = shaping.Marks(**{**off.__dict__, "cc_person": False})
    assert core.cc_decision("chat.postMessage", {"channel": CHANNEL, "text": "x", "thread_ts": PARENT_TS}, True, off, boom, None) is None
    dm = {"channel": env.ids["person_bot_dm_id"], "text": "x", "thread_ts": PARENT_TS}
    assert core.cc_decision("chat.postMessage", dm, True, m, boom, None) is None
    assert shaping.shape_send("chat.postMessage", {"channel": CHANNEL, "text": "x"}, True, m, cc=False)["text"] == "x"
    assert shaping.shape_send("chat.postMessage", {"channel": CHANNEL, "text": "x"}, True, m, cc=True)["text"] == f"x\n\n{cc(env)}"
