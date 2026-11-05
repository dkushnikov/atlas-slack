"""`required_checks` in the context policy (0.7.3, contract v1.12): a policy refusal before any human decision.

Level 1: fake transport, no secrets, no network. Every test runs under both synthetic profiles of
test_v2_slice (`env` fixture). A check is satisfied by a truthy `provenance.checks[name]`; `cicero` also
by the door's own ledger lookup (audit: pass, the text equal to the last `## Draft`).
"""

import os
import time
from unittest.mock import MagicMock, patch

import pytest
import yaml
from fastmcp.exceptions import ToolError

from atlas_slack import core, decision as decision_mod, ledger, policy as policy_mod
from atlas_slack.door import Door
from atlas_slack.formatters import ERROR_HINTS, format_error
from atlas_slack.gate import rights
from test_v2_slice import CHANNEL, Env, PROFILES, post, refused

POLICY_REQ = """
version: "2026-11-05.1"
ratified_by: test
required_checks:
  A: [cicero]
"""

POLICY_TWO = """
version: "2026-11-05.2"
ratified_by: test
required_checks:
  A: [cicero, recipient]
  B-h: [cicero]
"""


@pytest.fixture(params=sorted(PROFILES))
def env(request, isolated_door_dirs, monkeypatch):
    return Env(request.param, isolated_door_dirs, monkeypatch)


def person(text="hello team", channel=CHANNEL, **prov):
    return post(text=text, as_bot=False, channel=channel, **prov)


def ledger_file(d, name, comm_id, text="hello team", audit="pass", **fm):
    p = d / name
    body = "---\n" + f"comm_id: {comm_id}\naudit: {audit}\n" + "".join(f"{k}: {v}\n" for k, v in fm.items())
    body += f"---\n## Draft v1\n{text}\n## Final sent\n\n"
    p.write_text(body, encoding="utf-8")
    os.utime(p, (time.time(), time.time()))
    return p


def hook_deny(env, ti):
    out = env.hook(ti)
    assert out["permissionDecision"] == "deny", out
    return out["permissionDecisionReason"]


# ------------------------------------------------------------------ loader

def _load(text):
    return policy_mod.validate_policy(yaml.safe_load(text))


def test_loader_accepts_the_key_and_defaults_to_empty():
    pol = _load(POLICY_TWO)
    assert pol["required_checks"] == {"A": ["cicero", "recipient"], "B-h": ["cicero"]}
    assert _load('version: "1"\n')["required_checks"] == {}
    assert _load('version: "1"\nrequired_checks:\n')["required_checks"] == {}           # null → none
    assert _load('version: "1"\nrequired_checks: {C2: []}\n')["required_checks"] == {"C2": []}
    assert _load('version: "1"\nrequired_checks: {A: [cicero, cicero]}\n')["required_checks"] == {"A": ["cicero"]}
    # the built-in policy is unchanged: no key, the same digest 0.7.2 recorded on every journal row
    assert "required_checks" not in policy_mod.STRICT_POLICY
    assert policy_mod.builtin_policy().digest == "da85de8751bccac48fa4e0ce0bba30d209b8e1af8e51aecdb07cc269dcad3d98"


@pytest.mark.parametrize("body,why", [
    ("required_checks: [cicero]", "must map sender class"),
    ("required_checks: {X: [cicero]}", "unknown class"),
    ("required_checks: {B: [cicero]}", "unknown class"),
    ("required_checks: {A: cicero}", "list of check names"),
    ("required_checks: {A: [1]}", "list of check names"),
    ("required_checks: {A: ['']}", "list of check names"),
    ("required_checks: {A: ['a, b']}", "list of check names"),
    ("required_checks: {A: ['two words']}", "list of check names"),
    ("required_checks: {A: [{cicero: true}]}", "list of check names"),
])
def test_loader_rejects_bad_shapes(body, why):
    with pytest.raises(ValueError, match=why):
        _load(f'version: "1"\n{body}\n')


def test_bad_required_checks_make_the_policy_invalid_for_every_write(env):
    env.policy('version: "1"\nrequired_checks: {X: [cicero]}\n')
    assert "policy_invalid" in hook_deny(env, person())
    assert env.transport.calls == []


# ------------------------------------------------------------------ refusal on every face

def test_missing_check_refuses_on_the_hook_before_any_decision(env):
    env.policy(POLICY_REQ)
    reason = hook_deny(env, person())
    assert reason.startswith("atlas-slack refused — check_missing: needed: cicero — policy 2026-11-05.1 requires "
                             "check(s) cicero for class A to others"), reason
    assert ERROR_HINTS["check_missing"] in reason
    assert env.journal.ops() == []
    rows = [c for c in env.journal.calls() if c["error_code"] == "check_missing"]
    assert len(rows) == 1 and rows[0]["status"] == "refused" and rows[0]["caller"] == "hook"
    assert env.transport.calls == []


def test_missing_check_refuses_on_the_server_with_needed_and_provided(env):
    env.policy(POLICY_TWO)
    e = refused(env, person(checks={"voice": "mine", "recipient": "Pat, platform lead", "mark": ""}), "check_missing")
    assert e.verdict.details == {"needed": ["cicero"], "provided": ["recipient", "voice"]}
    assert e.fields == "needed: cicero · provided: recipient, voice"
    assert "requires check(s) cicero for class A" in e.message
    assert "no provenance.comm_id" in e.message           # what the ledger could not do, said in the message
    assert env.transport.calls == [] and env.journal.ops() == []
    rows = env.journal.calls()
    assert [(r["status"], r["error_code"]) for r in rows] == [("refused", "check_missing")]


def test_missing_check_through_the_mcp_server(env):
    from atlas_slack import gateway
    env.policy(POLICY_REQ)
    mock = MagicMock()
    with patch("atlas_slack.gateway.get_client", return_value=mock):
        with pytest.raises(ToolError) as e:
            gateway.slack_api("chat.postMessage", {"channel": CHANNEL, "text": "x"}, as_bot=False,
                              provenance={"cls": "A", "checks": {"voice": "mine"}})
    text = str(e.value)
    assert text.startswith("Error: check_missing — needed: cicero · provided: voice — policy 2026-11-05.1 requires"), text
    mock.api_call.assert_not_called()
    assert env.journal.ops() == []


def test_missing_check_on_the_library_face(env):
    env.policy(POLICY_REQ)
    door = Door(transport=env.transport)
    res = door.call("chat.postMessage", {"channel": CHANNEL, "text": "x"}, as_bot=False,
                    provenance={"cls": "A", "checks": {"voice": "mine"}})
    assert res.verdict.decision == "refuse" and res.verdict.reason == "check_missing"
    assert res.error == "check_missing (refused) — needed: cicero · provided: voice"
    assert res.verdict.details == {"needed": ["cicero"], "provided": ["voice"]} and not res.ok
    assert env.transport.calls == []
    # a refusal without detail fields keeps `error` None, as before
    env.write(freeze=True)
    res = door.call("chat.postMessage", {"channel": CHANNEL, "text": "x"}, as_bot=False, provenance={"cls": "A"})
    assert res.verdict.reason == "frozen" and res.error is None


def test_formatter_catalogue_line():
    assert format_error("check_missing") == f"Error: check_missing — {ERROR_HINTS['check_missing']}"
    assert format_error("check_missing", "custom", fields="needed: cicero") == "Error: check_missing — needed: cicero — custom"


def test_hook_and_server_agree(env):
    env.policy(POLICY_TWO)
    ti = person(checks={"recipient": "Pat"})
    reason = hook_deny(env, ti)
    e = refused(env, ti, "check_missing")
    assert f"check_missing: {e.fields} — {e.message}" in reason
    v = policy_mod.evaluate(ti["method"], ti["params"], False, ti["provenance"], env.profile, journal=env.journal)
    assert v.reason == "check_missing" and v.details == e.verdict.details
    # the gate CLI's dry-run JSON carries the same fields (Verdict.to_dict)
    assert v.to_dict()["details"] == {"needed": ["cicero"], "provided": ["recipient"]}


def test_a_human_yes_does_not_override_a_required_check(env):
    """Invariant iii: approved with the check declared, sent without it — the server refuses before any call."""
    env.policy(POLICY_REQ)
    ti = env.approve(person(checks={"cicero": "audited 2026-11-05"}))
    stripped = {**ti, "provenance": {k: v for k, v in ti["provenance"].items() if k != "checks"}}
    e = refused(env, stripped, "check_missing")          # `checks` is not bound: the decision itself still matches
    assert e.verdict.details["needed"] == ["cicero"]
    assert env.transport.calls == []
    env.send(ti)                                          # the approved operation as approved still goes
    assert len(env.transport.calls) == 1


def test_detail_fields_travel_only_with_the_reported_refusal(env):
    env.policy(POLICY_REQ + "recipients_deny:\n  A: [U0NEVERME]\n")
    e = refused(env, person(target={"channel": CHANNEL, "users": ["U0NEVERME"]}), "recipient_denied")
    assert "check_missing" in e.verdict.reasons
    assert e.verdict.details == {} and e.fields == ""
    assert "needed:" not in hook_deny(env, person(target={"channel": CHANNEL, "users": ["U0NEVERME"]}))


# ------------------------------------------------------------------ what satisfies a check

@pytest.mark.parametrize("value", [True, "audited 2026-11-05", "pass", {"by": "reviewer", "at": "2026-11-05"},
                                   ["draft v2"]])
def test_declared_check_satisfies(env, value):
    env.policy(POLICY_REQ)
    env.send(env.approve(person(checks={"cicero": value})))
    assert len(env.transport.calls) == 1


@pytest.mark.parametrize("value", [False, None, "", "  ", "skip", "skipped", "no", "false", "FAIL", "n/a", 0, 1, {}, []])
def test_declared_value_that_says_not_done_does_not_satisfy(env, value):
    env.policy(POLICY_REQ)
    e = refused(env, person(checks={"cicero": value}), "check_missing")
    assert e.verdict.details["needed"] == ["cicero"] and "cicero" not in e.verdict.details["provided"]


def test_ledger_audit_pass_satisfies_cicero(env, tmp_path, monkeypatch):
    env.policy(POLICY_REQ)
    monkeypatch.setenv(ledger.ENV_LEDGER_DIR, str(tmp_path))
    ledger_file(tmp_path, "c1.md", "20261105-a1a1", text="hello team")
    ti = env.approve(person(comm_id="20261105-a1a1"))
    env.send(ti)
    assert len(env.transport.calls) == 1
    row = env.journal.ops()[0]
    assert row["comm_id"] == "20261105-a1a1"


@pytest.mark.parametrize("case,expect", [
    ("other_text", "was for a different text"),
    ("skip", "audit skipped"),
    ("dropped", "marked dropped"),
    ("not_found", "not found"),
    ("blocks", "blocks-only text, not compared"),
])
def test_ledger_that_does_not_vouch_for_this_text_refuses(env, tmp_path, monkeypatch, case, expect):
    env.policy(POLICY_REQ)
    monkeypatch.setenv(ledger.ENV_LEDGER_DIR, str(tmp_path))
    cid = "20261105-b2b2"
    if case == "other_text":
        ledger_file(tmp_path, "c.md", cid, text="the audited draft")
    elif case == "skip":
        ledger_file(tmp_path, "c.md", cid, audit="skip")
    elif case == "dropped":
        ledger_file(tmp_path, "c.md", cid, status="dropped")
    elif case == "blocks":
        ledger_file(tmp_path, "c.md", cid)
    ti = person(comm_id=cid if case != "not_found" else "20261105-ffff")
    if case == "blocks":
        ti["params"]["blocks"] = [{"type": "section", "text": {"type": "mrkdwn", "text": "hello team"}}]
    reason = hook_deny(env, ti)
    assert "check_missing: needed: cicero" in reason and expect in reason, reason
    e = refused(env, ti, "check_missing")
    assert expect in e.message
    assert env.transport.calls == []


def test_without_a_ledger_folder_the_message_says_so(env, monkeypatch):
    env.policy(POLICY_REQ)
    monkeypatch.delenv(ledger.ENV_LEDGER_DIR, raising=False)
    e = refused(env, person(comm_id="20261105-c3c3"), "check_missing")
    assert "no ledger folder configured" in e.message


def test_ledger_and_declared_checks_combine(env, tmp_path, monkeypatch):
    env.policy(POLICY_TWO)
    monkeypatch.setenv(ledger.ENV_LEDGER_DIR, str(tmp_path))
    ledger_file(tmp_path, "c.md", "20261105-d4d4")
    e = refused(env, person(comm_id="20261105-d4d4"), "check_missing")
    assert e.verdict.details == {"needed": ["recipient"], "provided": ["cicero"]}      # cicero from the ledger
    env.send(env.approve(person(comm_id="20261105-d4d4", checks={"recipient": "Pat"})))
    assert len(env.transport.calls) == 1


def test_ledger_satisfies_an_upload_comment(env, tmp_path, monkeypatch):
    env.policy(POLICY_REQ)
    monkeypatch.setenv(ledger.ENV_LEDGER_DIR, str(tmp_path))
    ledger_file(tmp_path, "c.md", "20261105-e5e5", text="the one-pager")
    f = tmp_path / "one-pager.txt"
    f.write_text("content")
    ti = {"method": "files.uploadV2", "params": {"channel": CHANNEL, "file": str(f), "initial_comment": "the one-pager"},
          "as_bot": False, "provenance": {"cls": "A", "comm_id": "20261105-e5e5"}}
    assert env.hook(ti)["permissionDecision"] == "ask"
    ti["params"]["initial_comment"] = "another comment"
    assert "was for a different text" in hook_deny(env, ti)


# ------------------------------------------------------------------ scope of the rule

def test_the_persons_bot_dm_and_user_id_are_exempt(env):
    env.policy(POLICY_TWO)
    dm, me = env.ids["person_bot_dm_id"], env.ids["person_user_id"]
    env.send(env.approve(post(channel=dm)))                       # B-h to the person's bot DM: no check needed
    env.send(env.approve(post(channel=me, text="first hello")))  # B-h to the person's user id (opens the DM)
    env.send(env.approve(person(channel=me, text="a note to self")))   # A to the person's user id
    assert len(env.transport.calls) == 3
    refused(env, post(channel=CHANNEL), "check_missing")          # B-h to others: cicero needed
    refused(env, post(channel="U0SOMEONE1"), "check_missing")     # another user id is "others"
    refused(env, person(channel="U0SOMEONE1"), "check_missing")
    env.fields.pop("person_bot_dm_id")
    env.write()
    refused(env, post(channel=dm, text="again"), "check_missing")  # no bot DM id in the profile: that id is others
    env.send(env.approve(post(channel=me, text="still me")))      # the user id still exempts
    env.fields.pop("person_user_id")
    env.write()
    refused(env, post(channel=me, text="who"), "check_missing")   # neither id in the profile: nothing is exempt
    assert len(env.transport.calls) == 4


def test_provided_is_bounded_and_lists_only_check_names(env):
    env.policy(POLICY_REQ)
    checks = {f"check{i:02d}": "done" for i in range(20)}
    checks.update({"two words": "x", "a,b": "y", "x" * 70: "z", "ok-name": "pass"})
    e = refused(env, person(checks=checks), "check_missing")
    provided = e.verdict.details["provided"]
    assert len(provided) == policy_mod.PROVIDED_MAX == 12
    assert all(policy_mod._CHECK_NAME_RE.fullmatch(n) for n in provided)
    assert "two words" not in e.fields and "a,b" not in e.fields and "x" * 70 not in e.fields
    assert e.verdict.details["needed"] == ["cicero"]
    # a key a policy could never require never satisfies a required name either
    env.policy('version: "1"\nrequired_checks: {A: [ok-name]}\n')
    env.send(env.approve(person(checks={"ok-name": "done"}, comm_id="20261105-0f0f")))
    assert len(env.transport.calls) == 1


def test_other_classes_and_policies_without_the_key_are_unchanged(env):
    env.policy(POLICY_REQ)
    env.send(env.approve(post(channel=CHANNEL)))                  # B-h: the policy requires nothing for it
    env.policy('version: "2026-11-05.3"\nratified_by: test\n')    # no required_checks at all
    env.send(env.approve(person(comm_id="20261105-0001")))
    assert len(env.transport.calls) == 2


def test_builtin_policy_requires_nothing(env):
    out = env.hook(person())
    assert out["permissionDecision"] == "ask"
    assert "check_missing" not in out["permissionDecisionReason"]


@pytest.mark.parametrize("method,params", [
    ("chat.meMessage", {"channel": CHANNEL, "text": "waves"}),
    ("chat.scheduleMessage", {"channel": CHANNEL, "text": "later", "post_at": None}),
])
def test_every_core_send_is_covered(env, method, params):
    env.policy(POLICY_REQ)
    params = dict(params)
    if "post_at" in params:
        params["post_at"] = int(time.time()) + 3600
    ti = {"method": method, "params": params, "as_bot": False, "provenance": {"cls": "A"}}
    assert "check_missing" in hook_deny(env, ti)
    refused(env, ti, "check_missing")
    assert env.transport.calls == []


def test_rights_table_lists_the_required_checks(env):
    env.policy(POLICY_TWO)
    table = rights(env.profile)
    assert ("required checks (core sends to others; the person's bot DM and user id are exempt): "
            "A: cicero, recipient · B-h: cicero") in table
    env.policy(POLICY_REQ.replace("A: [cicero]", "A: []"))
    assert "required checks" not in rights(env.profile)
