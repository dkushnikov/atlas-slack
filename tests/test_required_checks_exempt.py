"""`required_checks_exempt` in the context policy (0.8.2): recipients with no human reader, named by id.

A send whose every recipient — the channel plus `provenance.target.users`, the set the deny lists match — is on
the list skips the policy's required checks; one recipient outside the list keeps them all. Only DM and user ids
are accepted (a channel or group has human readers). No is_bot lookup: the id list is the whole rule.
Level 1: fake transport, no secrets, no network.
"""

import pytest
import yaml

from atlas_slack import policy as policy_mod
from atlas_slack.gate import rights
from test_required_checks import POLICY_REQ, env, hook_deny, person  # noqa: F401  (env is a fixture)
from test_v2_slice import CHANNEL, post, refused

BOT_DM, BOT_USER = "D0MANYDEV1", "U0MANYDEV1"
POLICY_EXEMPT = POLICY_REQ.replace('"2026-11-05.1"', '"2026-11-06.1"') + \
    f"required_checks_exempt: [{BOT_DM}, {BOT_USER}]\n"


def _load(text):
    return policy_mod.validate_policy(yaml.safe_load(text))


# ------------------------------------------------------------------ loader

def test_loader_defaults_and_normalises():
    assert _load('version: "1"\n')["required_checks_exempt"] == []
    assert _load('version: "1"\nrequired_checks_exempt:\n')["required_checks_exempt"] == []
    pol = _load(f'version: "1"\nrequired_checks_exempt: [{BOT_DM}, {BOT_USER}, {BOT_DM}, W0ENTUSER1]\n')
    assert pol["required_checks_exempt"] == [BOT_DM, BOT_USER, "W0ENTUSER1"]
    assert "required_checks_exempt" not in policy_mod.STRICT_POLICY       # the built-in digest is unchanged


@pytest.mark.parametrize("body", [
    "required_checks_exempt: D0MANYDEV1",              # a string, not a list
    "required_checks_exempt: {A: [D0MANYDEV1]}",       # not per class
    "required_checks_exempt: [C0TEAMCHAN]",            # a channel has human readers
    "required_checks_exempt: [G0PRIVGRP1]",            # so does a group
    "required_checks_exempt: [d0manydev1]",            # ids are upper case
    "required_checks_exempt: ['D0 MANYDEV']",
    "required_checks_exempt: ['']",
    "required_checks_exempt: [1]",
    "required_checks_exempt: [Many - Dev]",            # a name, not an id
])
def test_loader_rejects_anything_but_dm_and_user_ids(body):
    with pytest.raises(ValueError, match="required_checks_exempt must be a list of DM or user ids"):
        _load(f'version: "1"\n{body}\n')


def test_a_bad_exempt_list_makes_the_policy_invalid_for_every_write(env):
    env.policy('version: "1"\nrequired_checks_exempt: [C0TEAMCHAN]\n')
    assert "policy_invalid" in hook_deny(env, person(channel=BOT_DM))
    assert env.transport.calls == []


# ------------------------------------------------------------------ the waiver

def test_a_send_to_the_exempt_dm_needs_no_check(env):
    env.policy(POLICY_EXEMPT)
    ti = person("Сколько у меня осталось дней отпуска?", channel=BOT_DM,
                target={"channel": BOT_DM, "users": [BOT_USER]}, comm_id="20261106-0b07")
    out = env.hook(ti)
    assert out["permissionDecision"] == "ask", out
    card = out["permissionDecisionReason"]
    tech = card.splitlines()[-1].split(" · ")              # the gate's own last line: a text cannot forge it
    assert tech[:3] == ["class A", "policy 2026-11-06.1", "checks waived"], tech
    assert card.count("checks waived") == 1
    env.send(env.approve(ti))
    assert len(env.transport.calls) == 1


def test_the_bot_user_id_as_channel_is_exempt_too(env):
    env.policy(POLICY_EXEMPT)
    env.send(env.approve(person(channel=BOT_USER, comm_id="20261106-0b08")))
    assert len(env.transport.calls) == 1


def test_the_waiver_is_a_verdict_warning(env):
    env.policy(POLICY_EXEMPT)
    v = policy_mod.evaluate("chat.postMessage", {"channel": BOT_DM, "text": "x"}, False, {"cls": "A"}, env.profile,
                            journal=env.journal)
    assert v.reason != "check_missing" and "check_missing" not in v.reasons
    assert [w for w in v.warnings if w.startswith(policy_mod.WAIVER_PREFIX)] == [
        f"required checks waived — {BOT_DM} on required_checks_exempt of policy 2026-11-06.1 (no human reader)"]


def test_one_recipient_outside_the_list_keeps_every_check(env):
    """All, not any: a human named in target.users, or a channel with the bot in it, is not exempt."""
    env.policy(POLICY_EXEMPT)
    e = refused(env, person(channel=BOT_DM, target={"channel": BOT_DM, "users": [BOT_USER, "U0HUMAN001"]}),
                "check_missing")
    assert e.verdict.details["needed"] == ["cicero"]
    e = refused(env, person(channel=CHANNEL, target={"channel": CHANNEL, "users": [BOT_USER]}), "check_missing")
    assert e.verdict.details["needed"] == ["cicero"]
    assert env.transport.calls == []


def test_the_waiver_does_not_lift_a_deny_list(env):
    env.policy(POLICY_EXEMPT + f"recipients_deny:\n  A: [{BOT_DM}]\n")
    e = refused(env, person(channel=BOT_DM), "recipient_denied")
    assert "check_missing" not in e.verdict.reasons
    assert env.transport.calls == []


def test_the_waiver_does_not_replace_the_human_decision(env):
    env.policy(POLICY_EXEMPT)
    refused(env, person(channel=BOT_DM), "no_decision")
    assert env.transport.calls == []


def test_no_waiver_mark_without_required_checks_or_off_the_list(env):
    env.policy(f'version: "2026-11-06.2"\nrequired_checks_exempt: [{BOT_DM}]\n')
    out = env.hook(person(channel=BOT_DM))
    assert out["permissionDecision"] == "ask"
    assert "waived" not in out["permissionDecisionReason"]
    env.policy(POLICY_EXEMPT)
    out = env.hook(person(channel=CHANNEL, checks={"cicero": "audited"}))
    assert out["permissionDecision"] == "ask" and "waived" not in out["permissionDecisionReason"]


def test_bot_voice_to_the_exempt_dm(env):
    env.policy(POLICY_EXEMPT.replace("  A: [cicero]\n", "  A: [cicero]\n  B-h: [cicero]\n"))
    env.send(env.approve(post(channel=BOT_DM, comm_id="20261106-0b09")))
    refused(env, post(channel=CHANNEL), "check_missing")
    assert len(env.transport.calls) == 1


# ------------------------------------------------------------------ rights

def test_rights_table_lists_the_exempt_recipients(env):
    env.policy(POLICY_EXEMPT)
    assert f"exempt recipients (every recipient of the send on this list): {BOT_DM}, {BOT_USER}" in rights(env.profile)
    env.policy(POLICY_REQ)
    assert "exempt recipients" not in rights(env.profile)
