"""Identity at connect (0.7.0): the tokens behind a profile are the identities the profile names.

Level 1: no Slack. `auth.test` is answered by fakes; the conftest switch
`ATLAS_SLACK_IDENTITY_CHECK=0` is lifted here so the check actually runs.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from atlas_slack import identity
from atlas_slack.door import Door
from atlas_slack.profile import Profile, load_profile

WS, PERSON, BOT = "T0WS000001", "U0PERSON01", "U0BOTTEST01"


def _profile(**kw) -> Profile:
    p = Profile(name="idtest", source="file", workspace_id=WS, person_user_id=PERSON, bot_user_id=BOT,
                journal_dir=kw.pop("journal_dir", None) or "/tmp/atlas-slack-idtest")
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def _auth(as_bot: bool, **over) -> dict:
    d = {"ok": True, "team_id": WS, "user_id": BOT if as_bot else PERSON, "bot_id": "B0TEST" if as_bot else None}
    d.update(over)
    return d


@pytest.fixture
def check_on(monkeypatch):
    monkeypatch.delenv("ATLAS_SLACK_IDENTITY_CHECK", raising=False)
    identity.reset_cache()
    yield
    identity.reset_cache()


# ------------------------------------------------------------------ problems()

def test_consistent_tokens_have_no_problems():
    p = _profile()
    assert identity.problems(_auth(True), p, True) == []
    assert identity.problems(_auth(False), p, False) == []


def test_other_workspace_other_person_swapped_slots_are_mismatches():
    p = _profile()
    assert any("workspace" in x for x in identity.problems(_auth(True, team_id="T0OTHER"), p, True))
    assert any("person_user_id" in x for x in identity.problems(_auth(False, user_id="U0STRANGER"), p, False))
    assert any("bot token sits" in x for x in identity.problems(_auth(False, bot_id="B9"), p, False))
    assert any("not a bot token" in x for x in identity.problems(_auth(True, bot_id=None), p, True))


def test_unset_profile_fields_are_advice_not_mismatch():
    p = _profile(workspace_id=None, person_user_id=None)
    assert identity.problems(_auth(False, team_id="T0ANY", user_id="U0ANY"), p, False) == []
    advice = identity.problems(_auth(False, team_id="T0ANY", user_id="U0ANY"), p, False, advise=True)
    assert any("workspace_id is not set" in x for x in advice) and any("person_user_id is not set" in x for x in advice)


def test_failed_auth_test_is_one_problem():
    assert identity.problems({"ok": False, "error": "invalid_auth"}, _profile(), True) == ["auth.test failed: invalid_auth"]
    assert identity.problems(None, _profile(), True) == ["auth.test failed: no response"]


# -------------------------------------------------------------------- verify()

def test_verify_asks_once_per_token_and_caches_only_success(check_on):
    p = _profile()
    calls = []

    def call(method, params, as_bot):
        calls.append((method, as_bot))
        return _auth(as_bot)
    assert identity.verify(p, True, call) == []
    assert identity.verify(p, True, call) == []
    assert identity.verify(p, False, call) == []
    assert calls == [("auth.test", True), ("auth.test", False)]
    assert identity.verified(p, True)["user_id"] == BOT

    def failing(method, params, as_bot):
        raise ConnectionError("down")
    q = _profile(name="idtest2")
    q.name = "idtest2"
    assert identity.verify(q, True, failing) == ["auth.test unavailable: ConnectionError: down"]
    assert identity.verify(q, True, lambda *_: _auth(True)) == []       # not cached: the next write tries again


def test_verify_switch_off_for_level_one(monkeypatch):
    monkeypatch.setenv("ATLAS_SLACK_IDENTITY_CHECK", "0")
    assert identity.verify(_profile(), True, lambda *_: (_ for _ in ()).throw(RuntimeError("never called"))) == []


# ------------------------------------------- placement: after policy + decision, before the intent

def _env(name, isolated_door_dirs, monkeypatch):
    from test_v2_slice import Env
    return Env(name, isolated_door_dirs, monkeypatch)


class AuthTransport:
    """A fake transport that answers auth.test with `answer` and everything else with ok."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, method, params, as_bot):
        self.calls.append(method)
        return self.answer if method == "auth.test" else {"ok": True, "ts": "1.2"}


def _ids_auth(ids, as_bot, **over):
    d = {"ok": True, "team_id": ids["workspace_id"], "user_id": ids["bot_user_id"] if as_bot else ids["person_user_id"],
         "bot_id": "B0TEST" if as_bot else None}
    d.update(over)
    return d


def test_core_refuses_identity_mismatch_after_the_decision_before_the_intent(check_on, isolated_door_dirs, monkeypatch):
    from atlas_slack import core
    from test_v2_slice import post
    env = _env("atlas-like", isolated_door_dirs, monkeypatch)
    ti = env.approve(post(comm_id="id-1"))
    tr = AuthTransport(_ids_auth(env.ids, True, team_id="T0OTHER"))
    with pytest.raises(core.Refused) as e:
        core.execute_send(ti["method"], ti["params"], True, ti["provenance"], profile=env.profile, transport=tr)
    assert e.value.code == "identity_mismatch" and "workspace" in e.value.message
    assert tr.calls == ["auth.test"]                     # nothing else reached Slack
    assert env.journal.ops() == []                        # no intent row
    assert [c["error_code"] for c in env.journal.calls()][-1] == "identity_mismatch"
    # the one-time decision was not consumed: the same approved input goes through once the token is right
    tr2 = AuthTransport(_ids_auth(env.ids, True))
    res = core.execute_send(ti["method"], ti["params"], True, ti["provenance"], profile=env.profile, transport=tr2)
    assert res.op_id and tr2.calls == ["auth.test", "chat.postMessage"]


def test_core_refuses_identity_unverified_when_auth_test_fails(check_on, isolated_door_dirs, monkeypatch):
    from atlas_slack import core
    from test_v2_slice import post
    env = _env("acme", isolated_door_dirs, monkeypatch)
    ti = env.approve(post(comm_id="id-2"))
    tr = AuthTransport({"ok": False, "error": "invalid_auth"})
    with pytest.raises(core.Refused) as e:
        core.execute_send(ti["method"], ti["params"], True, ti["provenance"], profile=env.profile, transport=tr)
    assert e.value.code == "identity_unverified" and "invalid_auth" in e.value.message
    assert tr.calls == ["auth.test"] and env.journal.ops() == []


def test_core_checks_policy_before_identity(check_on, isolated_door_dirs, monkeypatch):
    """A frozen profile refuses locally — no auth.test, no network (verifier finding, 2026-10-31)."""
    from atlas_slack import core
    from test_v2_slice import post
    env = _env("atlas-like", isolated_door_dirs, monkeypatch)
    ti = env.approve(post(comm_id="id-3"))
    env.write(freeze=True)
    tr = AuthTransport(_ids_auth(env.ids, True, team_id="T0OTHER"))
    with pytest.raises(core.Refused) as e:
        core.execute_send(ti["method"], ti["params"], True, ti["provenance"], profile=env.profile, transport=tr)
    assert e.value.code == "frozen" and tr.calls == []


def test_core_identity_is_asked_once_per_token(check_on, isolated_door_dirs, monkeypatch):
    from atlas_slack import core
    from test_v2_slice import post
    env = _env("atlas-like", isolated_door_dirs, monkeypatch)
    tr = AuthTransport(_ids_auth(env.ids, True))
    for cid in ("id-4", "id-5"):
        ti = env.approve(post(comm_id=cid))
        core.execute_send(ti["method"], ti["params"], True, ti["provenance"], profile=env.profile, transport=tr)
    assert tr.calls == ["auth.test", "chat.postMessage", "chat.postMessage"]


def test_gateway_face_passes_the_check_through(check_on, isolated_door_dirs, monkeypatch):
    """The MCP face: the same refusal surfaces as a tool error, and only auth.test reached the client."""
    from atlas_slack import gateway
    from test_v2_slice import post
    env = _env("atlas-like", isolated_door_dirs, monkeypatch)
    ti = env.approve(post(comm_id="id-6"))
    client = MagicMock()
    client.api_call.side_effect = lambda method, params=None, **kw: (
        _ids_auth(env.ids, True, user_id="U0STRANGER") if method == "auth.test" else {"ok": True, "ts": "1.2"})
    with patch("atlas_slack.gateway.get_client", return_value=client):
        with pytest.raises(Exception) as e:
            gateway._core_send(ti["method"], ti["params"], True, ti["provenance"], env.profile)
    assert "identity_mismatch" in str(e.value) and "bot_user_id" in str(e.value)
    assert [c.args[0] for c in client.api_call.call_args_list] == ["auth.test"]


def test_gateway_checks_identity_on_transitional_writes_too(check_on, monkeypatch, isolated_door_dirs):
    """reactions.add (transitional) under a bot token of another workspace refuses identity_mismatch."""
    from atlas_slack.gateway import slack_api
    env = _env("atlas-like", isolated_door_dirs, monkeypatch)
    client = MagicMock()
    client.api_call.side_effect = lambda method, params=None, **kw: (
        _ids_auth(env.ids, True, team_id="T0OTHER") if method == "auth.test" else {"ok": True})
    with patch("atlas_slack.gateway.get_client", return_value=client):
        with pytest.raises(Exception) as e:
            slack_api("reactions.add", {"channel": "C1", "timestamp": "1.2", "name": "thumbsup"}, as_bot=True)
    assert "identity_mismatch" in str(e.value)
    assert [c.args[0] for c in client.api_call.call_args_list] == ["auth.test"]
    # a read under the same token is not checked (reads are not attributed)
    client.api_call.reset_mock()
    client.api_call.side_effect = lambda method, params=None, **kw: {"ok": True, "messages": []}
    with patch("atlas_slack.gateway.get_client", return_value=client):
        slack_api("conversations.history", {"channel": "C1"}, as_bot=True)
    assert [c.args[0] for c in client.api_call.call_args_list] == ["conversations.history"]


# ------------------------------------------------------------------ Door face

def test_door_with_its_own_transport_checks_identity(check_on, isolated_door_dirs, monkeypatch):
    """A library C2 send under a policy that enables it, with the default transport: identity is checked."""
    from atlas_slack import door as door_mod
    env = _env("atlas-like", isolated_door_dirs, monkeypatch)
    env.policy(f"""
version: "r1"
channels:
  {env.ids['person_bot_dm_id']}:
    class: dm
    voices: [bot]
    modes: {{C2: auto}}
""")
    seen = []

    class FakeSdk:
        def __init__(self, profile):
            self.profile = profile

        def __call__(self, method, params, as_bot):
            seen.append(method)
            return _ids_auth(env.ids, as_bot, user_id="U0STRANGER") if method == "auth.test" else {"ok": True, "ts": "1.2"}
    monkeypatch.setattr(door_mod, "_SdkTransport", FakeSdk)
    door = Door()
    r = door.call("chat.postMessage", {"channel": env.ids["person_bot_dm_id"], "text": "report"}, as_bot=True,
                  provenance={"cls": "C2"})
    assert r.verdict.decision == "refuse" and r.verdict.reason == "identity_mismatch"
    assert seen == ["auth.test"]


def test_door_with_an_explicit_transport_is_the_callers_contract(check_on, isolated_door_dirs, monkeypatch):
    env = _env("atlas-like", isolated_door_dirs, monkeypatch)
    env.policy(f"""
version: "r1"
channels:
  {env.ids['person_bot_dm_id']}:
    class: dm
    voices: [bot]
    modes: {{C2: auto}}
""")
    tr = AuthTransport(_ids_auth(env.ids, True, team_id="T0OTHER"))     # would mismatch — but it is the caller's
    door = Door(transport=tr)
    r = door.call("chat.postMessage", {"channel": env.ids["person_bot_dm_id"], "text": "report"}, as_bot=True,
                  provenance={"cls": "C2"})
    assert r.ok and tr.calls == ["chat.postMessage"]


def test_door_doctor_delegates_with_advice(isolated_door_dirs):
    door = Door(profile=_profile(workspace_id=None, journal_dir=str(isolated_door_dirs / "j")), transport=lambda *a: {})
    assert any("workspace_id is not set" in x for x in door.doctor(_auth(True), as_bot=True))


# ------------------------------------------------------------------ CLI init / doctor

def test_init_writes_a_profile_the_loader_reads(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    answers = {False: {**_auth(False), "team": "acme", "user": "ann"}, True: {**_auth(True), "team": "acme", "user": "bot"}}
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: answers[as_bot])
    monkeypatch.setattr(cli, "_bot_dm", lambda profile, uid: "D0BOTDM001")
    monkeypatch.setenv("ATLAS_SLACK_PROFILE", "acme")
    with pytest.raises(SystemExit) as e:
        cli.main(["init"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "wrote" in out and "acme.yaml" in out
    p = load_profile("acme")
    assert not p.errors and p.workspace_id == WS and p.person_user_id == PERSON and p.bot_user_id == BOT
    assert p.person_bot_dm_id == "D0BOTDM001" and p.language == "en"


def test_init_refuses_to_overwrite_and_refuses_bad_tokens(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    monkeypatch.setenv("ATLAS_SLACK_PROFILE", "acme")
    swapped = {False: _auth(False, bot_id="B9"), True: _auth(True)}
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: swapped[as_bot])
    with pytest.raises(SystemExit) as e:
        cli.main(["init"])
    assert e.value.code == 1 and "bot token" in capsys.readouterr().err
    good = {False: _auth(False), True: _auth(True)}
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: good[as_bot])
    monkeypatch.setattr(cli, "_bot_dm", lambda profile, uid: None)
    with pytest.raises(SystemExit) as e:
        cli.main(["init"])
    assert e.value.code == 0
    with pytest.raises(SystemExit) as e:
        cli.main(["init"])
    assert e.value.code == 2 and "exists" in capsys.readouterr().err


def test_doctor_reports_both_tokens_and_the_rights_table(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    monkeypatch.setenv("ATLAS_SLACK_WORKSPACE_ID", WS)
    monkeypatch.setenv("ATLAS_SLACK_PERSON_USER_ID", PERSON)
    answers = {False: {**_auth(False), "_scopes": ["chat:write"]},
               True: {**_auth(True, team_id="T0OTHER"), "_scopes": ["chat:write", "chat:write.customize"]}}
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: answers[as_bot])
    with pytest.raises(SystemExit) as e:
        cli.main(["doctor"])
    out = capsys.readouterr().out
    assert e.value.code == 1
    assert "person token (SLACK_USER_TOKEN): ✓" in out
    assert "bot token (SLACK_BOT_TOKEN): ✗" in out and "workspace" in out
    assert "chat:write.customize is granted" in out
    assert "rights" in out.lower() or "class" in out.lower()


# --------------------------------------------- the transitional ask carries the profile language

def test_transitional_ask_carries_a_non_default_language(isolated_door_dirs, monkeypatch):
    """A ru profile's transitional ask ends with `lang: ru`; an en profile's reason has no lang line."""
    env = _env("atlas-like", isolated_door_dirs, monkeypatch)
    react = {"method": "reactions.add", "params": {"channel": "C1", "timestamp": "1.2", "name": "ok"}, "as_bot": True}
    out = env.hook(react)
    assert out["permissionDecision"] == "ask" and "lang:" not in out["permissionDecisionReason"]
    env.write(language="ru")
    out = env.hook(react)
    assert out["permissionDecision"] == "ask" and out["permissionDecisionReason"].endswith("\nlang: ru")
