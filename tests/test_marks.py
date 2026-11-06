"""0.8.0 — the marks come from the profile: the person-voice footer label (`attribution_label`, default «Sent via»)
and the bot-voice cc to the person (`cc_person`, `cc_label`); `init --set KEY=VALUE` writes them; doctor prints them.

Level 1: fake transport, no network, both profiles (test_v2_slice.Env)."""

import json
from unittest.mock import MagicMock, patch

import pytest
import yaml

from atlas_slack import __main__ as cli, gateway, shaping
from atlas_slack.profile import DEFAULT_ATTRIBUTION_LABEL, DEFAULT_CC_LABEL, SETTINGS, load_profile
from test_v2_slice import CHANNEL, Env, PROFILES, post  # noqa: F401
from test_v2_slice2 import Slack, ephemeral, me, update, upload, writes


@pytest.fixture(params=sorted(PROFILES))
def env(request, isolated_door_dirs, monkeypatch):
    return Env(request.param, isolated_door_dirs, monkeypatch)


def footer(env, label=DEFAULT_ATTRIBUTION_LABEL):
    bot = env.ids["bot_user_id"]
    return f":blue_heart: {label} <@{bot}>" if label else f":blue_heart: <@{bot}>"


def cc(env, label=DEFAULT_CC_LABEL):
    person = env.ids["person_user_id"]
    return f"{label} <@{person}>" if label else f"<@{person}>"


def final_of(env):
    return writes(env.transport)[-1][1]


# ---------------------------------------------------------------- profile: defaults and validation

def test_profile_defaults_and_summary_carry_the_marks(env):
    p = env.profile
    assert not p.errors
    assert (p.attribution_label, p.cc_person, p.cc_label) == ("Sent via", False, "cc")
    s = p.summary()
    assert (s["attribution_label"], s["cc_person"], s["cc_label"]) == ("Sent via", False, "cc")
    assert SETTINGS == ("attribution_label", "cc_person", "cc_label")


@pytest.mark.parametrize("key,value,fragment", [
    ("attribution_label", 7, "one line"),
    ("attribution_label", "two\nlines", "one line"),
    ("attribution_label", " padded", "no surrounding whitespace"),
    ("attribution_label", "x" * 61, "at most 60"),
    ("cc_label", ["cc"], "one line"),
    ("cc_person", "true", "true/false"),
])
def test_profile_refuses_a_bad_mark_value(env, key, value, fragment):
    env.write(**{key: value})
    p = env.profile
    assert p.broken and any(key in e and fragment in e for e in p.errors), p.errors


def test_profile_cc_person_needs_the_person_id(env):
    env.fields.pop("person_user_id")
    env.write(cc_person=True)
    p = env.profile
    assert p.broken and any("cc_person: true needs person_user_id" in e for e in p.errors)
    env.write(person_user_id=env.ids["person_user_id"])
    assert not env.profile.errors


def test_profile_empty_labels_are_allowed(env):
    env.write(attribution_label="", cc_label="")
    p = env.profile
    assert not p.errors and p.attribution_label == "" and p.cc_label == ""


# ---------------------------------------------------------------- person voice: the label

def test_attribution_text_label_variants():
    m = shaping.Marks(bot_user_id="U0BOT")
    assert shaping.attribution_text(m) == ":blue_heart: Sent via <@U0BOT>"
    assert shaping.attribution_text(shaping.Marks(bot_user_id="U0BOT", attribution_label="via")) == ":blue_heart: via <@U0BOT>"
    assert shaping.attribution_text(shaping.Marks(bot_user_id="U0BOT", attribution_label="")) == ":blue_heart: <@U0BOT>"
    with pytest.raises(shaping.ShapingError) as e:
        shaping.attribution_text(shaping.Marks(bot_user_id=None))
    assert e.value.code == "attribution_unavailable"


def test_marks_of_coerces_the_pre_080_argument():
    """Callers that still pass the bot user id alone get the defaults for everything else."""
    final = shaping.shape_send("chat.postMessage", {"channel": "C1", "text": "hi"}, False, "U0BOT")
    assert final["blocks"][-1]["elements"][0]["text"] == ":blue_heart: Sent via <@U0BOT>"
    assert "blocks" not in shaping.shape_send("chat.postMessage", {"channel": "C1", "text": "hi"}, True, None)


def test_person_voice_footer_through_the_core_uses_the_profile_label(env):
    env.write(attribution_label="via")
    env.send(env.approve(post(as_bot=False, text="a")))
    assert final_of(env)["blocks"][-1]["elements"][0]["text"] == footer(env, "via")
    env.write(attribution_label="")
    env.send(env.approve(post(as_bot=False, text="b")))
    assert final_of(env)["blocks"][-1]["elements"][0]["text"] == footer(env, "")


def test_person_voice_never_gets_a_cc(env):
    env.write(cc_person=True)
    env.send(env.approve(post(as_bot=False)))
    final = final_of(env)
    assert final["text"] == "hello team" and final["blocks"][-1]["type"] == "context"
    assert f"<@{env.ids['person_user_id']}>" not in json.dumps(final)


# ---------------------------------------------------------------- bot voice: the cc

def test_cc_is_off_by_default(env):
    env.send(env.approve(post()))
    final = final_of(env)
    assert final["text"] == "hello team" and "blocks" not in final
    assert cli.marks_line(env.profile).endswith("· bot voice cc off")


def test_cc_on_a_bot_post_to_others_is_the_last_line(env):
    env.write(cc_person=True)
    env.send(env.approve(post()))
    final = final_of(env)
    assert final["text"] == f"hello team\n\n{cc(env)}" and "blocks" not in final
    assert final["metadata"]["event_type"] == "atlas_slack_op"        # the reconciliation handle is untouched
    line = cli.marks_line(env.profile)
    assert f"bot voice cc {cc(env)} on bot-voice sends to others" in line and footer(env) in line


def test_cc_label_from_the_profile(env):
    env.write(cc_person=True, cc_label="fyi")
    env.send(env.approve(post(text="a")))
    assert final_of(env)["text"] == f"a\n\n{cc(env, 'fyi')}"
    env.write(cc_label="")
    env.send(env.approve(post(text="b")))
    assert final_of(env)["text"] == f"b\n\n{cc(env, '')}"


def test_cc_never_in_the_persons_own_conversations(env):
    env.write(cc_person=True)
    env.send(env.approve(post(channel=env.ids["person_bot_dm_id"], text="report")))
    assert final_of(env)["text"] == "report"
    env.send(env.approve(post(channel=env.ids["person_user_id"], text="first post opens the DM")))
    assert final_of(env)["text"] == "first post opens the DM"


def test_cc_not_on_an_ephemeral(env):
    env.write(cc_person=True)
    env.transport = Slack()
    env.send(env.approve(ephemeral(as_bot=True, text="only you")))
    assert final_of(env)["text"] == "only you"


def test_cc_skipped_when_the_text_already_mentions_the_person(env):
    env.write(cc_person=True)
    person = env.ids["person_user_id"]
    env.send(env.approve(post(text=f"<@{person}> please look")))
    assert final_of(env)["text"] == f"<@{person}> please look"
    env.send(env.approve(post(text=f"see <@{person}|pat>")))
    assert final_of(env)["text"] == f"see <@{person}|pat>"


def test_cc_as_a_section_when_the_send_carries_blocks():
    m = shaping.Marks(bot_user_id="U0BOT", cc_person=True, person_user_id="U0PERSON")
    own = [{"type": "section", "text": {"type": "mrkdwn", "text": "mine"}}]
    final = shaping.shape_send("chat.postMessage", {"channel": "C1", "text": "fallback", "blocks": own}, True, m)
    assert final["blocks"] == own + [{"type": "section", "text": {"type": "mrkdwn", "text": "cc <@U0PERSON>"}}]
    assert final["text"] == "fallback"                               # the notification fallback is the caller's
    # blocks that already mention the person: left alone
    mention = [{"type": "section", "text": {"type": "mrkdwn", "text": "hi <@U0PERSON>"}}]
    assert shaping.shape_send("chat.postMessage", {"channel": "C1", "blocks": mention}, True, m)["blocks"] == mention
    # the bot auto-split: the sections, then the cc section — never a 3001-char section
    long = shaping.shape_send("chat.postMessage", {"channel": "C1", "text": "x" * 5000}, True, m)
    assert [b["type"] for b in long["blocks"]] == ["section", "section", "section"]
    assert long["blocks"][-1]["text"]["text"] == "cc <@U0PERSON>" and long["text"] == "x" * 5000
    assert all(len(b["text"]["text"]) <= shaping.SECTION_TEXT_LIMIT for b in long["blocks"])


def test_cc_on_a_bot_edit_is_reapplied_and_idempotent(env):
    env.write(cc_person=True)
    env.transport = Slack(author=env.ids["bot_user_id"])
    env.send(env.approve(update(text="new text", comm_id="20261105-cc1")))
    assert final_of(env)["text"] == f"new text\n\n{cc(env)}" and "metadata" not in final_of(env)
    # the agent copied the whole prior message, cc line included: one cc, not two
    env.send(env.approve(update(text=f"newer\n\n{cc(env)}", comm_id="20261105-cc2")))
    assert final_of(env)["text"] == f"newer\n\n{cc(env)}"


def test_cc_on_a_bot_upload_comment_and_a_me_message(env, tmp_path):
    env.write(cc_person=True)
    env.transport = Slack()
    env.send(env.approve(upload(tmp_path, as_bot=True, comment="see attached")))
    assert final_of(env)["initial_comment"] == f"see attached\n\n{cc(env)}"
    env.send(env.approve(upload(tmp_path, as_bot=True, comment="")))          # no comment → nothing to append
    assert "initial_comment" not in final_of(env)
    env.send(env.approve(me(as_bot=True)))
    assert final_of(env)["text"] == f"shrugs\n\n{cc(env)}"


def test_cc_on_the_transitional_complete_upload_external(env):
    """files.completeUploadExternal is transitional, bot voice, addresses `channel_id` — cc'd like a core send."""
    env.write(cc_person=True)
    mock = MagicMock()
    mock.api_call.return_value = {"ok": True}
    files = [{"id": "F1", "title": "doc"}]
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        gateway.slack_api("files.completeUploadExternal", {"files": files, "channel_id": CHANNEL, "initial_comment": "see"},
                          as_bot=True)
        assert mock.api_call.call_args.kwargs["params"]["initial_comment"] == f"see\n\n{cc(env)}"
        gateway.slack_api("files.completeUploadExternal",
                          {"files": files, "channel_id": env.ids["person_bot_dm_id"], "initial_comment": "see"}, as_bot=True)
        assert mock.api_call.call_args.kwargs["params"]["initial_comment"] == "see"


def test_cc_mention_match_is_exact_and_a_full_message_gets_no_section():
    m = shaping.Marks(bot_user_id="U0BOT", cc_person=True, person_user_id="U0P")
    # another user whose id begins with the person's id is not the person
    assert shaping.shape_send("chat.postMessage", {"channel": "C1", "text": "hi <@U0P2>"}, True, m)["text"] == "hi <@U0P2>\n\ncc <@U0P>"
    assert shaping.shape_send("chat.postMessage", {"channel": "C1", "text": "hi <@U0P|p>"}, True, m)["text"] == "hi <@U0P|p>"
    full = [{"type": "divider"}] * shaping.MAX_BLOCKS
    assert shaping.shape_send("chat.postMessage", {"channel": "C1", "text": "t", "blocks": full}, True, m)["blocks"] == full
    almost = full[:-1]
    assert len(shaping.shape_send("chat.postMessage", {"channel": "C1", "text": "t", "blocks": almost}, True, m)["blocks"]) == shaping.MAX_BLOCKS


def test_cc_applies_names_the_cases():
    m = shaping.Marks(bot_user_id="U0BOT", cc_person=True, person_user_id="U0P", person_channels=frozenset({"D0DM", "U0P"}))
    assert shaping.cc_applies("chat.postMessage", {"channel": "C1"}, m)
    assert shaping.cc_applies("chat.scheduleMessage", {"channel": "C1"}, m)
    assert not shaping.cc_applies("chat.postMessage", {"channel": "D0DM"}, m)
    assert not shaping.cc_applies("chat.postEphemeral", {"channel": "C1", "user": "U1"}, m)
    assert not shaping.cc_applies("chat.startStream", {"channel": "C1"}, m)
    assert not shaping.cc_applies("reactions.add", {"channel": "C1"}, m)
    assert not shaping.cc_applies("chat.postMessage", {"channel": "C1"}, shaping.Marks(bot_user_id="U0BOT", cc_person=True))
    assert shaping.Marks.from_profile(load_profile()).person_channels == frozenset()      # the built-in profile: nothing set


# ---------------------------------------------------------------- init --set

WS, PERSON, BOT = "TACME00001", "UACMEPRSN", "UACMEBOT1"
HEADER = "# atlas-slack profile `acme` — written by init; holds no secrets\n# workspace acme · person pat · bot atlas\n"
IDS = {"workspace_id": WS, "person_user_id": PERSON, "bot_user_id": BOT, "person_bot_dm_id": "DACMEBOTDM",
       "token_sources": {"user": "ACME_SLACK_USER", "bot": "ACME_SLACK_BOT"}, "language": "en", "freeze": False}


@pytest.fixture
def cfg(isolated_door_dirs, monkeypatch):
    d = isolated_door_dirs / "config"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("ATLAS_SLACK_PROFILE", raising=False)

    def no_network(*a, **k):
        raise AssertionError("init --set must not call Slack")
    monkeypatch.setattr(cli, "_auth", no_network)
    monkeypatch.setattr(cli, "_bot_dm", no_network)
    return d


def profile_file(cfg, name="acme", body=None, **extra):
    p = cfg / f"{name}.yaml"
    p.write_text(HEADER + yaml.safe_dump({**IDS, **extra}, sort_keys=False) if body is None else body, encoding="utf-8")
    return p


def run(capsys, *argv):
    with pytest.raises(SystemExit) as e:
        cli.main(["init", *argv])
    out = capsys.readouterr()
    return e.value.code, out.out, out.err


def test_set_writes_the_setting_and_keeps_every_other_line(cfg, capsys):
    prof = profile_file(cfg)
    before = prof.read_text()
    rc, out, err = run(capsys, "--profile", "acme", "--set", "cc_person=true")
    assert rc == 0, err
    assert out == f"marks: person voice :blue_heart: Sent via <@{BOT}> · bot voice cc cc <@{PERSON}> on bot-voice sends to others (not in the person's own DM)\n"
    after = prof.read_text()
    assert after.startswith(before) and yaml.safe_load(after) == {**IDS, "cc_person": True}
    p = load_profile("acme")
    assert not p.errors and p.cc_person is True
    # a rerun changes nothing
    rc, out2, _ = run(capsys, "--profile", "acme", "--set", "cc_person=true")
    assert rc == 0 and out2 == out and prof.read_text() == after


def test_set_several_keys_labels_as_given_and_the_empty_label(cfg, capsys):
    prof = profile_file(cfg, cc_person=True)
    rc, out, err = run(capsys, "--profile", "acme", "--set", "attribution_label=via", "--set", "cc_label=fyi")
    assert rc == 0, err
    assert out == f"marks: person voice :blue_heart: via <@{BOT}> · bot voice cc fyi <@{PERSON}> on bot-voice sends to others (not in the person's own DM)\n"
    rc, out, err = run(capsys, "--profile", "acme", "--set", "attribution_label=", "--set", "cc_person=false")
    assert rc == 0, err
    assert out == f"marks: person voice :blue_heart: <@{BOT}> · bot voice cc off\n"
    data = yaml.safe_load(prof.read_text())
    assert data["attribution_label"] == "" and data["cc_label"] == "fyi" and data["cc_person"] is False
    assert prof.read_text().startswith(HEADER)


def test_set_replaces_an_existing_line_in_place(cfg, capsys):
    prof = profile_file(cfg, body=HEADER + "workspace_id: TACME00001\nperson_user_id: UACMEPRSN\nbot_user_id: UACMEBOT1\n"
                        "token_sources: {user: ACME_SLACK_USER, bot: ACME_SLACK_BOT}\ncc_label: cc   # the old label\nfreeze: false\n")
    rc, out, err = run(capsys, "--profile", "acme", "--set", "cc_label=fyi")
    assert rc == 0, err
    lines = prof.read_text().splitlines()
    assert lines[-2] == "cc_label: fyi" and lines[-1] == "freeze: false" and lines[0].startswith("#")   # in place, comment on that line gone


@pytest.mark.parametrize("argv,rc,fragment", [
    (("--set", "language=ru"), 2, "not a setting"),
    (("--set", "cc_person"), 2, "KEY=VALUE"),
    (("--set", "cc_person=yes"), 2, "true or false"),
    (("--set", "attribution_label=x" * 1), 0, ""),
    (("--set", "attribution_label=" + "x" * 61), 1, "at most 60"),
    (("--set", "cc_label=two\nlines"), 1, "one line"),
    (("--set", "cc_person=true", "--force"), 2, "does not combine"),
    (("--set", "cc_person=true", "--policy-path", "/x.yaml"), 2, "does not combine"),
])
def test_set_refusals_write_nothing(cfg, capsys, argv, rc, fragment):
    prof = profile_file(cfg)
    before = prof.read_bytes()
    got, out, err = run(capsys, "--profile", "acme", *argv)
    assert got == rc, (out, err)
    if rc:
        assert fragment in err and out == "" and prof.read_bytes() == before
        assert not load_profile("acme").errors


def test_set_cc_person_without_a_person_id_is_refused_before_writing(cfg, capsys):
    prof = profile_file(cfg, body=HEADER + "workspace_id: TACME00001\nbot_user_id: UACMEBOT1\n"
                        "token_sources: {user: ACME_SLACK_USER, bot: ACME_SLACK_BOT}\n")
    before = prof.read_bytes()
    rc, out, err = run(capsys, "--profile", "acme", "--set", "cc_person=true")
    assert rc == 1 and "would not load" in err and "person_user_id" in err and prof.read_bytes() == before


def test_set_needs_an_existing_profile(cfg, capsys):
    rc, out, err = run(capsys, "--profile", "acme", "--set", "cc_person=true")
    assert rc == 2 and "profile not found" in err and not (cfg / "acme.yaml").exists()


def test_set_rewrites_a_flow_mapping_as_a_whole_and_says_so(cfg, capsys):
    prof = profile_file(cfg, body=HEADER + "{workspace_id: TACME00001, person_user_id: UACMEPRSN, bot_user_id: UACMEBOT1, "
                        "token_sources: {user: ACME_SLACK_USER, bot: ACME_SLACK_BOT}}\n")
    rc, out, err = run(capsys, "--profile", "acme", "--set", "cc_person=true")
    assert rc == 0 and "rewritten as a whole" in err
    text = prof.read_text()
    assert text.startswith(HEADER) and yaml.safe_load(text)["cc_person"] is True and load_profile("acme").cc_person is True


# ---------------------------------------------------------------- doctor

def test_doctor_prints_the_marks_line(cfg, capsys, monkeypatch):
    profile_file(cfg, cc_person=True)
    answers = {False: {"ok": True, "team_id": WS, "user_id": PERSON, "team": "acme", "user": "pat", "_scopes": ["channels:history", "chat:write"]},
               True: {"ok": True, "team_id": WS, "user_id": BOT, "bot_id": "B1", "team": "acme", "user": "atlas", "_scopes": ["chat:write", "im:write"]}}
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: answers[as_bot])
    with pytest.raises(SystemExit):
        cli.main(["doctor", "--profile", "acme"])
    out = capsys.readouterr().out
    assert f"marks: person voice :blue_heart: Sent via <@{BOT}> · bot voice cc cc <@{PERSON}> on bot-voice sends to others" in out
    assert out.index("policy:") < out.index("marks:") < out.index("next:")
