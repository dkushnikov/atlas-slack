"""Identity at connect (0.7.0): the tokens behind a profile are the identities the profile names.

Level 1: no Slack. `auth.test` is answered by fakes; the conftest switch
`ATLAS_SLACK_IDENTITY_CHECK=0` is lifted here so the check actually runs.
"""

from __future__ import annotations

import os
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
        cli.main(["init", "--user-var", "SLACK_USER_TOKEN", "--bot-var", "SLACK_BOT_TOKEN"])   # a named profile says its variables
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "wrote" in out and "acme.yaml" in out
    p = load_profile("acme")
    assert not p.errors and p.workspace_id == WS and p.person_user_id == PERSON and p.bot_user_id == BOT
    assert p.person_bot_dm_id == "D0BOTDM001" and p.language == "en"


def test_init_refuses_to_overwrite_and_refuses_bad_tokens(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    monkeypatch.setenv("ATLAS_SLACK_PROFILE", "acme")
    V = ["--user-var", "SLACK_USER_TOKEN", "--bot-var", "SLACK_BOT_TOKEN"]
    swapped = {False: _auth(False, bot_id="B9"), True: _auth(True)}
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: swapped[as_bot])
    with pytest.raises(SystemExit) as e:
        cli.main(["init", *V])
    assert e.value.code == 1 and "bot token" in capsys.readouterr().err
    good = {False: _auth(False), True: _auth(True)}
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: good[as_bot])
    monkeypatch.setattr(cli, "_bot_dm", lambda profile, uid: None)
    with pytest.raises(SystemExit) as e:
        cli.main(["init", *V])
    assert e.value.code == 0
    with pytest.raises(SystemExit) as e:
        cli.main(["init"])                                   # the file exists now: no variables needed to be refused
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


# ------------------------------------------------ Codex code review 2026-10-31 — regressions pinned

def test_transitional_read_by_legacy_heuristic_is_not_identity_checked(check_on, monkeypatch, isolated_door_dirs):
    """An unregistered read-like name falls to the transitional path with write=False: reads are not attributed."""
    from atlas_slack.gateway import slack_api
    from atlas_slack.policy import classify
    eff = classify("team.accessLogs", {})
    assert eff.route == "transitional" and eff.write is False
    env = _env("atlas-like", isolated_door_dirs, monkeypatch)
    client = MagicMock()
    client.api_call.side_effect = lambda method, params=None, **kw: (
        _ids_auth(False, team_id="T0OTHER") if method == "auth.test" else {"ok": True, "logins": []})
    with patch("atlas_slack.gateway.get_client", return_value=client):
        slack_api("team.accessLogs", {}, as_bot=False)
    assert [c.args[0] for c in client.api_call.call_args_list] == ["team.accessLogs"]


def test_transitional_identity_refusal_writes_one_calls_row(check_on, monkeypatch, isolated_door_dirs):
    from atlas_slack.gateway import slack_api
    env = _env("atlas-like", isolated_door_dirs, monkeypatch)
    client = MagicMock()
    client.api_call.side_effect = lambda method, params=None, **kw: (
        _ids_auth(env.ids, True, team_id="T0OTHER") if method == "auth.test" else {"ok": True})
    with patch("atlas_slack.gateway.get_client", return_value=client):
        with pytest.raises(Exception):
            slack_api("reactions.add", {"channel": "C1", "timestamp": "1.2", "name": "ok"}, as_bot=True)
    rows = [r for r in env.journal.calls() if r["method"] == "reactions.add"]
    assert [r["error_code"] for r in rows] == ["identity_mismatch"]       # exactly one row, the refusal


def test_init_refuses_an_invalid_profile_name(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: pytest.fail("auth.test must not run for a bad name"))
    for bad in ("bad name", "../escaped", ""):
        with pytest.raises(SystemExit) as e:
            cli.main(["init", "--profile", bad] if bad else ["init", "--profile", "-x"])
        assert e.value.code == 2
    assert "not valid" in capsys.readouterr().err


# ------------------------------------------ init: a second workspace's variables; --force keeps other keys

def test_init_second_workspace_uses_its_own_variables_and_force_keeps_keys(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    seen = []

    def fake_auth(as_bot, profile):
        seen.append(profile.token_var(as_bot))
        return {**_auth(as_bot), "team_id": "T0ACME0001", "team": "acme", "user": "bot" if as_bot else "ann"}
    monkeypatch.setattr(cli, "_auth", fake_auth)
    monkeypatch.setattr(cli, "_bot_dm", lambda profile, uid: None)
    with pytest.raises(SystemExit) as e:
        cli.main(["init", "--profile", "acme", "--user-var", "ACME_SLACK_USER", "--bot-var", "ACME_SLACK_BOT"])
    assert e.value.code == 0 and seen == ["ACME_SLACK_USER", "ACME_SLACK_BOT"]      # not SLACK_USER_TOKEN
    p = load_profile("acme")
    assert not p.errors and p.workspace_id == "T0ACME0001" and p.token_sources == {"user": "ACME_SLACK_USER", "bot": "ACME_SLACK_BOT"}
    assert p.person_bot_dm_id is None
    # the person adds keys by hand; --force must keep them and the token sources
    path = p.path
    with open(path, "a", encoding="utf-8") as f:
        f.write("freeze: true\nlanguage: ru\nledger_dir: /tmp/ledger\nnames:\n  U0PERSON01: Ann\n")
    seen.clear()
    with pytest.raises(SystemExit) as e:
        cli.main(["init", "--profile", "acme", "--force"])
    assert e.value.code == 0 and seen == ["ACME_SLACK_USER", "ACME_SLACK_BOT"]
    q = load_profile("acme")
    assert not q.errors and q.freeze is True and q.language == "ru" and q.ledger_dir == "/tmp/ledger" and q.names == {"U0PERSON01": "Ann"}
    assert q.token_sources == {"user": "ACME_SLACK_USER", "bot": "ACME_SLACK_BOT"} and q.workspace_id == "T0ACME0001"
    assert "other keys kept" in capsys.readouterr().out
    # a stale DM id never survives --force when the DM is not found (verifier): write one, force again without a DM
    with open(path, "a", encoding="utf-8") as f:
        f.write("person_bot_dm_id: D0STALE99\n")
    assert load_profile("acme").person_bot_dm_id == "D0STALE99"
    with pytest.raises(SystemExit):
        cli.main(["init", "--profile", "acme", "--force"])
    assert load_profile("acme").person_bot_dm_id is None


# ----------------------------------------------- round 2: init guards; a bot DM opened by posting to the user id

def test_init_new_non_default_profile_needs_its_own_variables(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: pytest.fail("auth.test must not run"))
    monkeypatch.setenv("ATLAS_SLACK_PROFILE", "acme")
    with pytest.raises(SystemExit) as e:
        cli.main(["init"])                                   # the env var selects acme; no variables named
    assert e.value.code == 2 and "names no token variables" in capsys.readouterr().err
    with pytest.raises(SystemExit) as e:
        cli.main(["init", "--profile", "acme", "--user-var", "acme_user", "--bot-var", "ACME_BOT"])
    assert e.value.code == 2 and "not an environment variable name" in capsys.readouterr().err
    # the explicit default pair is accepted for a profile that really shares the tokens
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: {**_auth(as_bot), "team": "t", "user": "u"})
    monkeypatch.setattr(cli, "_bot_dm", lambda profile, uid: None)
    with pytest.raises(SystemExit) as e:
        cli.main(["init", "--profile", "acme", "--user-var", "SLACK_USER_TOKEN", "--bot-var", "SLACK_BOT_TOKEN"])
    assert e.value.code == 0 and load_profile("acme").token_sources == {"user": "SLACK_USER_TOKEN", "bot": "SLACK_BOT_TOKEN"}


def test_bot_send_to_the_person_by_user_id_is_a_human_decision_not_a_refusal(isolated_door_dirs, monkeypatch):
    """A profile without person_bot_dm_id: the first bot send targets the person's user id (Slack opens the DM)."""
    env = _env("atlas-like", isolated_door_dirs, monkeypatch)
    env.write(person_bot_dm_id=None)
    out = env.hook({"method": "chat.postMessage", "params": {"channel": env.ids["person_user_id"], "text": "hello from the door"},
                    "as_bot": True, "provenance": {"cls": "B-h"}})
    assert out["permissionDecision"] == "ask", out


# ------------------------------------------------------------- doctor: one `next:` line, first cause wins

def _doctor(cli, capsys, args=("doctor",)):
    with pytest.raises(SystemExit) as e:
        cli.main(list(args))
    out = capsys.readouterr().out
    nxt = [l for l in out.splitlines() if l.startswith("next: ")]
    assert len(nxt) == 1, out
    return e.value.code, nxt[0], out


def test_doctor_next_init_when_no_profile_file(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    monkeypatch.delenv("ATLAS_BOT_USER_ID", raising=False)       # a first install: no file, no ids in the environment
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: {**_auth(as_bot), "_scopes": []})
    rc, nxt, out = _doctor(cli, capsys)
    assert rc == 1 and nxt.startswith("next: init — no profile file for 'default'") and "profile: default (builtin)" in out
    monkeypatch.setenv("ATLAS_SLACK_PROFILE", "acme")
    rc, nxt, _ = _doctor(cli, capsys)
    assert rc == 1 and nxt.startswith("next: init — profile_missing") and "--user-var <VAR>" in nxt


def test_doctor_next_set_token_new_token_retry(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    env = _env("atlas-like", isolated_door_dirs, monkeypatch)
    answers = {False: {"ok": False, "error": "SLACK_USER_TOKEN is not set in the environment"}, True: _ids_auth(env.ids, True)}
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: answers[as_bot])
    rc, nxt, _ = _doctor(cli, capsys)
    assert rc == 1 and nxt.startswith("next: set-token — SLACK_USER_TOKEN is not set")
    answers[False] = {"ok": False, "error": "invalid_auth"}
    rc, nxt, _ = _doctor(cli, capsys)
    assert nxt.startswith("next: new-token — SLACK_USER_TOKEN: Slack answered invalid_auth")
    answers[False] = {"ok": False, "error": "URLError: <urlopen error [Errno 8] nodename nor servname provided>"}
    rc, nxt, _ = _doctor(cli, capsys)
    assert nxt.startswith("next: retry — SLACK_USER_TOKEN: URLError")


def test_doctor_next_swap_decide_frozen_policy_send(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    env = _env("atlas-like", isolated_door_dirs, monkeypatch)
    answers = {False: _ids_auth(env.ids, False, bot_id="B9"), True: _ids_auth(env.ids, True)}
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: answers[as_bot])
    rc, nxt, _ = _doctor(cli, capsys)
    assert nxt.startswith("next: swap-tokens — SLACK_USER_TOKEN: person: auth.test reports a bot_id")
    answers[False] = _ids_auth(env.ids, False, team_id="T0OTHER")
    rc, nxt, _ = _doctor(cli, capsys)
    assert nxt.startswith("next: decide — SLACK_USER_TOKEN: workspace:") and "--force" in nxt
    answers[False] = _ids_auth(env.ids, False)
    env.write(freeze=True)
    rc, nxt, out = _doctor(cli, capsys)
    assert nxt.startswith("next: frozen —") and "freeze: YES" in out
    env.write(freeze=False, policy_path="/nonexistent/p.yaml", policy_sha256="0" * 64)
    rc, nxt, _ = _doctor(cli, capsys)
    assert nxt.startswith("next: fix-policy — policy invalid")
    env.write(policy_path=None, policy_sha256=None)
    env.fields.pop("policy_path", None); env.fields.pop("policy_sha256", None); env.write()
    rc, nxt, out = _doctor(cli, capsys)
    assert rc == 0 and nxt.startswith("next: send — consistent") and env.ids["person_bot_dm_id"] in nxt
    env.write(person_bot_dm_id=None)
    env.fields.pop("person_bot_dm_id", None); env.write()
    rc, nxt, _ = _doctor(cli, capsys)
    assert rc == 0 and "your user id" in nxt and env.ids["person_user_id"] in nxt


def test_doctor_env_only_builtin_profile_can_reach_send(monkeypatch, isolated_door_dirs, capsys):
    """An env-only profile (no file, ATLAS_BOT_USER_ID and friends set — policy-schema § Profile) is not told to init."""
    from atlas_slack import __main__ as cli
    monkeypatch.setenv("ATLAS_SLACK_WORKSPACE_ID", WS)
    monkeypatch.setenv("ATLAS_SLACK_PERSON_USER_ID", PERSON)          # ATLAS_BOT_USER_ID = TEST_BOT_USER_ID from conftest
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: {**_auth(as_bot), "_scopes": []})
    rc, nxt, out = _doctor(cli, capsys)
    assert rc == 0 and nxt.startswith("next: send") and "profile: default (builtin)" in out
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: {"ok": False, "error": "internal_error"} if not as_bot else {**_auth(True), "_scopes": []})
    rc, nxt, _ = _doctor(cli, capsys)
    assert nxt.startswith("next: retry — SLACK_USER_TOKEN: internal_error")


def test_doctor_same_token_in_both_variables_and_fix_profile_never_offers_force(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    env = _env("atlas-like", isolated_door_dirs, monkeypatch)
    same = {**_ids_auth(env.ids, True), "_scopes": []}                       # a bot token pasted into both variables
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: same)
    rc, nxt, _ = _doctor(cli, capsys)
    assert nxt.startswith("next: swap-tokens — SLACK_USER_TOKEN and SLACK_BOT_TOKEN hold the SAME token")
    env.write(bogus="x")
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: {**_ids_auth(env.ids, as_bot), "_scopes": []})
    rc, nxt, _ = _doctor(cli, capsys)
    assert nxt.startswith("next: fix-profile — unknown profile keys") and "--force" not in nxt.split("(init --force would keep it)")[0]


def test_init_note_is_printed_only_when_a_file_is_written(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    from atlas_slack.profile import config_dir
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: {**_auth(as_bot), "team": "t", "user": "u"})
    monkeypatch.setattr(cli, "_bot_dm", lambda profile, uid: None)
    bad = config_dir() / "acme.yaml"; bad.parent.mkdir(parents=True, exist_ok=True); bad.write_text(": : not yaml [\n")
    with pytest.raises(SystemExit) as e:
        cli.main(["init", "--profile", "acme", "--force"])              # the guard refuses: no variables named
    err = capsys.readouterr().err
    assert e.value.code == 2 and "names no token variables" in err and "note:" not in err
    with pytest.raises(SystemExit) as e:
        cli.main(["init", "--profile", "acme", "--force", "--user-var", "SLACK_USER_TOKEN", "--bot-var", "SLACK_BOT_TOKEN"])
    err = capsys.readouterr().err
    assert e.value.code == 0 and "note:" in err and "could not be parsed" in err


def test_first_bot_post_to_a_user_id_records_the_dm_slack_opened(isolated_door_dirs, monkeypatch):
    """The row's target becomes the D… channel from the response, so an edit by ts + channel finds the post."""
    from atlas_slack import core
    from test_v2_slice import post
    env = _env("atlas-like", isolated_door_dirs, monkeypatch)
    env.write(person_bot_dm_id=None)
    env.fields.pop("person_bot_dm_id", None); env.write()
    ti = env.approve(post(comm_id="dm-1", channel=env.ids["person_user_id"], text="hello from the door"))

    class DmTransport:
        calls = []

        def __call__(self, method, params, as_bot):
            self.calls.append(method)
            return {"ok": True, "ts": "1700000000.000100", "channel": "D0OPENED01"} if method != "auth.test" else _ids_auth(env.ids, as_bot)
    core.execute_send(ti["method"], ti["params"], True, ti["provenance"], profile=env.profile, transport=DmTransport(),
                      identity_check=False)
    row = env.journal.find_by_slack_ts("1700000000.000100", "D0OPENED01")
    assert row is not None and row["comm_id"] == "dm-1"
    import json
    tgt = json.loads(row["target_json"])
    assert tgt["channel"] == "D0OPENED01" and tgt["addressed_to"] == env.ids["person_user_id"]


def test_doctor_file_profile_without_identity_fields_is_fix_profile_not_send(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    from atlas_slack.profile import config_dir
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: {**_auth(as_bot), "_scopes": []})
    (config_dir()).mkdir(parents=True, exist_ok=True)
    (config_dir() / "default.yaml").write_text("freeze: false\n")
    rc, nxt, _ = _doctor(cli, capsys)
    assert rc == 1 and nxt.startswith("next: fix-profile — ") and "has no workspace_id, person_user_id, bot_user_id" in nxt and "--force" in nxt


def test_init_force_on_a_hand_written_second_profile_without_token_sources_is_refused(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    from atlas_slack.profile import config_dir
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: pytest.fail("auth.test must not run"))
    config_dir().mkdir(parents=True, exist_ok=True)
    (config_dir() / "acme.yaml").write_text("freeze: false\n")
    with pytest.raises(SystemExit) as e:
        cli.main(["init", "--profile", "acme", "--force"])
    assert e.value.code == 2 and "names no token variables (neither the file nor the options)" in capsys.readouterr().err
    (config_dir() / "acme.yaml").write_text("freeze: false\ntoken_sources: {user: ACME_U, bot: ACME_B}\n")
    seen = []

    def fake_auth(as_bot, profile):
        seen.append(profile.token_var(as_bot))
        return {**_auth(as_bot), "team_id": "T0ACME0001", "team": "acme", "user": "x"}
    monkeypatch.setattr(cli, "_auth", fake_auth)
    monkeypatch.setattr(cli, "_bot_dm", lambda profile, uid: None)
    with pytest.raises(SystemExit) as e:
        cli.main(["init", "--profile", "acme", "--force"])
    assert e.value.code == 0 and seen == ["ACME_U", "ACME_B"]


def test_rights_table_names_the_identities_and_an_edit_by_user_id_finds_the_post(isolated_door_dirs, monkeypatch):
    from atlas_slack import core
    from atlas_slack.gate import rights
    from test_v2_slice import post
    env = _env("atlas-like", isolated_door_dirs, monkeypatch)
    assert f"identities: person {env.ids['person_user_id']} · bot {env.ids['bot_user_id']} · bot DM {env.ids['person_bot_dm_id']}" in rights(env.profile)
    env.fields.pop("person_bot_dm_id", None); env.write()
    assert "bot DM none yet" in rights(env.profile)
    ti = env.approve(post(comm_id="dm-2", channel=env.ids["person_user_id"], text="hi"))
    core.execute_send(ti["method"], ti["params"], True, ti["provenance"], profile=env.profile, identity_check=False,
                      transport=lambda m, p, b: {"ok": True, "ts": "1700000000.000200", "channel": "D0OPENED02"})
    assert env.journal.find_by_slack_ts("1700000000.000200", env.ids["person_user_id"])["comm_id"] == "dm-2"
    assert env.journal.find_by_slack_ts("1700000000.000200", "D0OPENED02")["comm_id"] == "dm-2"


def test_doctor_retry_for_transport_shaped_failures(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    env = _env("atlas-like", isolated_door_dirs, monkeypatch)
    for msg in ("Received a response in a non-JSON format: <html>503", "IncompleteRead(0 bytes read)", "RemoteDisconnected: Remote end closed connection"):
        answers = {False: {"ok": False, "error": msg}, True: _ids_auth(env.ids, True)}
        monkeypatch.setattr(cli, "_auth", lambda as_bot, profile, a=answers: a[as_bot])
        rc, nxt, _ = _doctor(cli, capsys)
        assert nxt.startswith("next: retry"), (msg, nxt)


def test_doctor_fix_profile_for_a_second_profile_names_the_variable_flags(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    from atlas_slack.profile import config_dir
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: {**_auth(as_bot), "_scopes": []})
    config_dir().mkdir(parents=True, exist_ok=True)
    (config_dir() / "acme.yaml").write_text("freeze: false\n")
    rc, nxt, _ = _doctor(cli, capsys, ["doctor", "--profile", "acme"])
    assert "init --profile acme --force --user-var <VAR> --bot-var <VAR>" in nxt
    (config_dir() / "acme.yaml").write_text("freeze: false\ntoken_sources: {user: ACME_U, bot: ACME_B}\n")
    rc, nxt, _ = _doctor(cli, capsys, ["doctor", "--profile", "acme"])
    assert "init --profile acme --force fills" in nxt


def test_init_named_profile_with_default_variables_declares_them(monkeypatch, isolated_door_dirs, capsys):
    """A named profile that shares the default tokens still writes token_sources, so a later --force needs no flags."""
    from atlas_slack import __main__ as cli
    from atlas_slack.profile import config_dir
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: {**_auth(as_bot), "team": "t", "user": "u"})
    monkeypatch.setattr(cli, "_bot_dm", lambda profile, uid: None)
    with pytest.raises(SystemExit):
        cli.main(["init", "--profile", "acme", "--user-var", "SLACK_USER_TOKEN", "--bot-var", "SLACK_BOT_TOKEN"])
    assert "token_sources" in (config_dir() / "acme.yaml").read_text()
    monkeypatch.setattr(cli, "_bot_dm", lambda profile, uid: "D0NOWOPEN1")
    with pytest.raises(SystemExit) as e:
        cli.main(["init", "--profile", "acme", "--force"])                 # no flags: the file declares them
    assert e.value.code == 0 and load_profile("acme").person_bot_dm_id == "D0NOWOPEN1"


def test_doctor_placeholders_only_when_the_file_declares_no_token_sources(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    from atlas_slack.profile import config_dir
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: {**_auth(as_bot), "_scopes": []})
    config_dir().mkdir(parents=True, exist_ok=True)
    (config_dir() / "acme.yaml").write_text("freeze: false\ntoken_sources: {user: SLACK_USER_TOKEN, bot: SLACK_BOT_TOKEN}\n")
    rc, nxt, _ = _doctor(cli, capsys, ["doctor", "--profile", "acme"])
    assert "init --profile acme --force fills" in nxt and "<VAR>" not in nxt


def test_init_one_variable_option_alone_is_refused_for_a_named_profile(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: pytest.fail("auth.test must not run"))
    with pytest.raises(SystemExit) as e:
        cli.main(["init", "--profile", "acme", "--user-var", "ACME_U"])
    assert e.value.code == 2 and "name both variables" in capsys.readouterr().err


def test_doctor_decide_and_send_lines_carry_the_variable_flags_for_an_undeclared_second_profile(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    from atlas_slack.profile import config_dir
    config_dir().mkdir(parents=True, exist_ok=True)
    (config_dir() / "acme.yaml").write_text(f"workspace_id: {WS}\nperson_user_id: {PERSON}\nbot_user_id: {BOT}\n")
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: {**_auth(as_bot, team_id="T0OTHER"), "_scopes": []})
    rc, nxt, _ = _doctor(cli, capsys, ["doctor", "--profile", "acme"])
    assert nxt.startswith("next: decide") and "init --profile acme --force --user-var <VAR> --bot-var <VAR>" in nxt
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: {**_auth(as_bot), "_scopes": []})
    rc, nxt, _ = _doctor(cli, capsys, ["doctor", "--profile", "acme"])
    assert nxt.startswith("next: send") and "init --profile acme --force --user-var <VAR> --bot-var <VAR> records it" in nxt


def test_doctor_checks_the_first_session_scopes_after_identity(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    monkeypatch.setenv("ATLAS_SLACK_WORKSPACE_ID", WS)
    monkeypatch.setenv("ATLAS_SLACK_PERSON_USER_ID", PERSON)
    full = {False: ["channels:history", "chat:write"], True: ["chat:write", "im:write"]}
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: {**_auth(as_bot), "_scopes": full[as_bot]})
    rc, nxt, out = _doctor(cli, capsys)
    assert rc == 0 and nxt.startswith("next: send") and "2 scopes" in out and "missing for the first session" not in out
    # the person token lacks chat:write → add-scope names the variable and the scope
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: {**_auth(as_bot), "_scopes": ["channels:history"] if not as_bot else full[True]})
    rc, nxt, out = _doctor(cli, capsys)
    assert rc == 1 and nxt.startswith("next: add-scope — SLACK_USER_TOKEN: the app lacks chat:write") and "✗ missing for the first session: chat:write" in out
    # the bot token lacks im:write (no DM can be opened)
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: {**_auth(as_bot), "_scopes": full[False] if not as_bot else ["chat:write"]})
    rc, nxt, _ = _doctor(cli, capsys)
    assert nxt.startswith("next: add-scope — SLACK_BOT_TOKEN: the app lacks im:write")
    # no x-oauth-scopes header: unknown is said, never claimed as checked, and does not block send
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: {**_auth(as_bot), "_scopes": []})
    rc, nxt, out = _doctor(cli, capsys)
    assert rc == 0 and nxt.startswith("next: send") and "scopes unknown" in out and "scopes: unknown" in out
    # a wrong app outranks a missing scope: the bot token of another workspace → decide, not add-scope
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: {**_auth(as_bot, team_id="T0OTHER" if as_bot else WS), "_scopes": [] if as_bot else ["channels:history"]})
    rc, nxt, _ = _doctor(cli, capsys)
    assert nxt.startswith("next: decide — SLACK_BOT_TOKEN")


def test_doctor_names_every_scope_gap_and_outranks_it_with_token_verdicts(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    monkeypatch.setenv("ATLAS_SLACK_WORKSPACE_ID", WS)
    monkeypatch.setenv("ATLAS_SLACK_PERSON_USER_ID", PERSON)
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: {**_auth(as_bot), "_scopes": ["chat:write"]})
    rc, nxt, _ = _doctor(cli, capsys)
    assert nxt.startswith("next: add-scope — SLACK_USER_TOKEN: the app lacks channels:history; SLACK_BOT_TOKEN: the app lacks im:write")
    assert "put the new token(s) into SLACK_USER_TOKEN, SLACK_BOT_TOKEN" in nxt
    # an unset bot variable outranks the person token's scope gap
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: {"ok": False, "error": "SLACK_BOT_TOKEN is not set in the environment"} if as_bot else {**_auth(False), "_scopes": ["chat:write"]})
    rc, nxt, _ = _doctor(cli, capsys)
    assert nxt.startswith("next: set-token — SLACK_BOT_TOKEN")
    # a user token in the bot slot outranks it too
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: {**_auth(False), "_scopes": ["chat:write"]})
    rc, nxt, _ = _doctor(cli, capsys)
    assert nxt.startswith("next: swap-tokens")


def test_auth_reads_the_scope_header_case_insensitively(monkeypatch):
    import slack_sdk
    from atlas_slack import __main__ as cli
    from atlas_slack.profile import load_profile

    class Resp:
        def __init__(self, headers):
            self.data = {"ok": True, "team_id": WS, "user_id": PERSON}
            self.headers = headers

    class Client:
        headers = {}

        def __init__(self, token, retry_handlers=None):
            assert token == "xoxp-test"

        def auth_test(self):
            return Resp(Client.headers)
    monkeypatch.setattr(slack_sdk, "WebClient", Client)
    monkeypatch.setenv("SLACK_USER_TOKEN", "xoxp-test")
    profile = load_profile("default")
    Client.headers = {"X-OAuth-Scopes": "chat:write, channels:history"}
    assert cli._auth(False, profile)["_scopes"] == ["channels:history", "chat:write"]
    Client.headers = {}
    assert cli._auth(False, profile)["_scopes"] == []


def test_doctor_for_a_missing_named_profile_does_not_show_the_default_tokens_as_its_own(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: pytest.fail("the default variables are not acme's"))
    rc, nxt, out = _doctor(cli, capsys, ["doctor", "--profile", "acme"])
    assert rc == 1 and nxt.startswith("next: init — profile_missing") and "tokens: not checked" in out and "✓ team" not in out


def test_init_file_token_sources_must_name_both(monkeypatch, isolated_door_dirs, capsys):
    from atlas_slack import __main__ as cli
    from atlas_slack.profile import config_dir
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: pytest.fail("auth.test must not run"))
    config_dir().mkdir(parents=True, exist_ok=True)
    (config_dir() / "acme.yaml").write_text("token_sources: {user: ACME_U}\n")
    with pytest.raises(SystemExit) as e:
        cli.main(["init", "--profile", "acme", "--force"])
    assert e.value.code == 2 and "names no token variables" in capsys.readouterr().err


def test_identity_cache_is_bound_to_the_token_value_not_only_the_variable(check_on, monkeypatch):
    """Codex review 2026-10-31: a token replaced in a long-lived process must be verified again."""
    p = _profile()
    calls = []

    def call(method, params, as_bot):
        calls.append(os.environ.get("SLACK_BOT_TOKEN"))
        return _auth(as_bot)

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-first")
    assert identity.verify(p, True, call) == [] and identity.verify(p, True, call) == []
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-rotated")
    assert identity.verify(p, True, call) == []
    assert calls == ["xoxb-first", "xoxb-rotated"]
