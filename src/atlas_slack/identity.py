"""Identity check at connect — the tokens behind a profile are the identities the profile names.

A profile says which workspace, which person and which bot a send is attributed to. The
tokens come from the environment; nothing stops a bot token of another workspace, or a user
token of another person, from sitting in the configured variables. Before the first write
under each token in a process (core sends in `core.execute_send` after policy and the
decision check; transitional writes in the MCP face and the Door; reads never), the face asks
Slack `auth.test` once and compares the answer with the profile (`problems`). A positive mismatch refuses `identity_mismatch`; an
answer that cannot be obtained refuses `identity_unverified` — nothing is sent and no op row
is written in either case. Fields the profile leaves unset are not checked (the `doctor`
command lists them as advice). The result is cached per process and token variable.

`ATLAS_SLACK_IDENTITY_CHECK=0` switches the check off (the level-1 test suite sets it; a host
never should). `verify` takes a `call(method, params, as_bot) -> dict` — the face's transport — so the
check runs under exactly the client the send would use.
"""

from __future__ import annotations

import hashlib
import os

ENV_SKIP = "ATLAS_SLACK_IDENTITY_CHECK"   # "0" switches the check off — the test suite's level 1

_VERIFIED: dict[tuple[str, str, str], dict] = {}


def _key(profile, as_bot: bool) -> tuple[str, str, str]:
    """Profile name, token variable, and a fingerprint of the token VALUE — a token replaced in a
    long-lived process (a new Door after a rotation) is verified again (Codex review 2026-10-31).
    The fingerprint is a truncated sha256; the token itself is never stored."""
    var = profile.token_var(as_bot)
    tok = os.environ.get(var) or ""
    return (profile.name, var, hashlib.sha256(tok.encode("utf-8")).hexdigest()[:16])


def reset_cache() -> None:
    _VERIFIED.clear()


def problems(auth_test: dict, profile, as_bot: bool, *, advise: bool = False) -> list[str]:
    """Mismatches between an `auth.test` answer and the profile; empty = consistent.

    With `advise=True` the unset profile fields are reported too (for `doctor`); a send
    refuses only on a positive mismatch.
    """
    p = profile
    out: list[str] = []
    if not isinstance(auth_test, dict) or not auth_test.get("ok", True):
        err = auth_test.get("error", "no response") if isinstance(auth_test, dict) else "no response"
        return [f"auth.test failed: {err}"]
    team = auth_test.get("team_id")
    uid = auth_test.get("user_id")
    slot = "bot" if as_bot else "person"
    if p.workspace_id and team != p.workspace_id:
        out.append(f"workspace: the {slot} token belongs to {team}, the profile says {p.workspace_id}")
    elif not p.workspace_id and advise:
        out.append("workspace_id is not set in the profile — the token's workspace is not checked")
    if as_bot:
        if p.bot_user_id and uid != p.bot_user_id:
            out.append(f"bot: the token's user_id {uid} ≠ profile bot_user_id {p.bot_user_id}")
        if not auth_test.get("bot_id"):
            out.append("bot: auth.test has no bot_id — this is not a bot token")
        if not p.bot_user_id and advise:
            out.append("bot_user_id is not set — person-voice sends cannot be attributed")
    else:
        if p.person_user_id and uid != p.person_user_id:
            out.append(f"person: the token's user_id {uid} ≠ profile person_user_id {p.person_user_id}")
        if auth_test.get("bot_id"):
            out.append("person: auth.test reports a bot_id — a bot token sits in the user-token slot")
        if not p.person_user_id and advise:
            out.append("person_user_id is not set — whose user token this is is not checked")
    return out


def verify(profile, as_bot: bool, call) -> list[str]:
    """Check the token of a voice once per process; cached by (profile name, token variable, token fingerprint).

    Returns the problems; `[]` means verified. A failed `auth.test` (exception, non-dict,
    `ok: false`) is reported as one problem and is not cached, so the next write tries again.
    """
    if os.environ.get(ENV_SKIP) == "0":
        return []                       # level-1 tests (no Slack); never set in production
    key = _key(profile, as_bot)
    cached = _VERIFIED.get(key)
    if cached is not None:
        return problems(cached, profile, as_bot)
    try:
        data = call("auth.test", {}, as_bot)
    except Exception as e:  # noqa: BLE001 — any transport failure: nothing is sent
        return [f"auth.test unavailable: {type(e).__name__}: {e}"]
    if not isinstance(data, dict):
        return ["auth.test returned no data"]
    found = problems(data, profile, as_bot)
    if not found and data.get("ok", True):
        _VERIFIED[key] = data
    return found


def code_for(problems: list[str]) -> str:
    """The refusal code for a non-empty problem list: no usable answer → unverified, else mismatch."""
    return "identity_unverified" if problems[0].startswith("auth.test") else "identity_mismatch"


def verified(profile, as_bot: bool) -> dict | None:
    """The cached `auth.test` answer for a voice, when this process has verified it."""
    return _VERIFIED.get(_key(profile, as_bot))
