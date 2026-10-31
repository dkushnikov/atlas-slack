"""Entry point: the MCP server (default), `init` (write a profile from the tokens) and `doctor`.

    python3 -m atlas_slack                      MCP server on stdio (what the plugin's .mcp.json runs)
    python3 -m atlas_slack init [--profile NAME] [--user-var VAR --bot-var VAR] [--force]
    python3 -m atlas_slack doctor [--profile NAME]   # ends with one `next: <code> — …` line; exit 0 only on `send`
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

SUBCOMMANDS = ("init", "doctor")
_VAR_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")       # the same rule profile.py applies to token_sources


# The scopes docs/setup.md § 6 needs (a channel read, a reply in the person's voice, a bot post to the person
# that opens the DM). doctor checks these and no more: a method outside them fails with missing_scope.
FIRST_SCOPES = {False: ("channels:history", "chat:write"), True: ("chat:write", "im:write")}


def _auth(as_bot: bool, profile) -> dict:
    """`auth.test` under the profile's token variable for a voice; the dict answer or {ok: False, error}."""
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    var = profile.token_var(as_bot)
    token = os.environ.get(var)
    if not token:
        return {"ok": False, "error": f"{var} is not set in the environment"}
    try:
        resp = WebClient(token=token, retry_handlers=[]).auth_test()
    except SlackApiError as e:
        return {"ok": False, "error": e.response.get("error", str(e)) if e.response is not None else str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    data = resp.data if hasattr(resp, "data") else dict(resp)
    headers = {str(k).lower(): v for k, v in (resp.headers or {}).items()}   # header names are case-insensitive
    data["_scopes"] = sorted(s.strip() for s in str(headers.get("x-oauth-scopes") or "").split(",") if s.strip())
    return data


def _bot_dm(profile, person_user_id: str) -> str | None:
    """The bot's DM with the person, looked up without creating it (`prevent_creation`)."""
    from slack_sdk import WebClient
    token = os.environ.get(profile.token_var(True))
    if not token:
        return None
    try:
        resp = WebClient(token=token, retry_handlers=[]).conversations_open(users=person_user_id, prevent_creation=True)
        data = resp.data if hasattr(resp, "data") else dict(resp)
        ch = (data.get("channel") or {}).get("id")
        return ch if isinstance(ch, str) else None
    except Exception:  # noqa: BLE001 — optional field; the profile works without it
        return None


def cmd_init(args) -> int:
    import yaml

    from atlas_slack.profile import DEFAULT_TOKEN_SOURCES, _NAME_RE, config_dir, load_profile
    name = args.profile or os.environ.get("ATLAS_SLACK_PROFILE") or "default"
    if not _NAME_RE.match(name):
        print(f"profile name {name!r} is not valid: letters, digits, `_ . -`, up to 64 chars, no path parts "
              "(the same rule load_profile applies)", file=sys.stderr)
        return 2
    path = config_dir() / f"{name}.yaml"
    pflag = f" --profile {name}" if name != "default" else ""
    existing: dict = {}
    notes: list[str] = []
    if path.exists():
        if not args.force:
            print(f"{path} exists — edit it, or run init --force to rewrite its identity fields", file=sys.stderr)
            return 2
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            existing = loaded if isinstance(loaded, dict) else {}
            if loaded is not None and not isinstance(loaded, dict):
                notes.append(f"note: {path} is not a mapping; its content is not kept")
        except Exception as e:  # noqa: BLE001 — an unparsable file is replaced, and we say so (after the guards below)
            notes.append(f"note: {path} could not be parsed ({type(e).__name__}); its other keys are not kept")
    # token variable NAMES: --user-var/--bot-var > the existing profile's token_sources > the defaults
    sources = dict(DEFAULT_TOKEN_SOURCES)
    if isinstance(existing.get("token_sources"), dict):
        sources.update({k: v for k, v in existing["token_sources"].items() if k in ("user", "bot") and isinstance(v, str)})
    for opt, key in ((args.user_var, "user"), (args.bot_var, "bot")):
        if opt is not None:
            if not _VAR_RE.match(opt):
                print(f"--{key}-var {opt!r} is not an environment variable name (A-Z, 0-9, _ ; the profile loader "
                      "rejects anything else)", file=sys.stderr)
                return 2
            sources[key] = opt
    named = args.user_var is not None or args.bot_var is not None
    ts_file = existing.get("token_sources") if isinstance(existing.get("token_sources"), dict) else {}
    file_named = all(isinstance(ts_file.get(k), str) and ts_file.get(k) for k in ("user", "bot"))
    if name != "default" and named and not file_named and (args.user_var is None or args.bot_var is None):
        # one option alone would pair a second app's token with the FIRST app's other variable (spec-verifier)
        print(f"profile {name!r}: name both variables — --user-var <VAR> --bot-var <VAR> (one alone would read the "
              f"other token from the default variable, the first app's)", file=sys.stderr)
        return 2
    if name != "default" and not named and not file_named:
        # a second profile that silently reads the default variables writes the FIRST workspace's ids under the
        # new name (spec-verifier, 2026-10-31) — the person says which variables, every time
        print(f"profile {name!r} names no token variables (neither the file nor the options): a second workspace has its own — "
              f"init --profile {name} --user-var <VAR> --bot-var <VAR>; if this profile really shares the default "
              f"tokens, say so explicitly: --user-var SLACK_USER_TOKEN --bot-var SLACK_BOT_TOKEN", file=sys.stderr)
        return 2
    profile = load_profile(name)      # the built-in profile for a new name; the file's for an existing one
    profile.token_sources = sources
    person = _auth(False, profile)
    bot = _auth(True, profile)
    failed = [f"{profile.token_var(ab)}: {d['error']}" for ab, d in ((False, person), (True, bot)) if not d.get("ok")]
    if failed:
        print("cannot read the identities behind the tokens:\n  " + "\n  ".join(failed), file=sys.stderr)
        print("set the variable(s) in the shell Claude Code starts from, open a new terminal, run init again "
              "(a second workspace: init --profile <name> --user-var <VAR> --bot-var <VAR>)", file=sys.stderr)
        return 1
    problems = []
    if person.get("bot_id"):
        problems.append(f"{profile.token_var(False)} is a bot token (auth.test reports bot_id) — the user token goes there")
    if not bot.get("bot_id"):
        problems.append(f"{profile.token_var(True)} is not a bot token (no bot_id in auth.test)")
    if person.get("team_id") != bot.get("team_id"):
        problems.append(f"the two tokens belong to different workspaces: {person.get('team_id')} vs {bot.get('team_id')}")
    if problems:
        print("the tokens do not form one person + one bot in one workspace:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    for n in notes:                                       # printed only when a file is actually written
        print(n, file=sys.stderr)
    dm = _bot_dm(profile, person["user_id"])
    out = dict(existing)                                  # --force keeps every other key (freeze, policy, journal, names …)
    out["workspace_id"] = person["team_id"]
    out["person_user_id"] = person["user_id"]
    out["bot_user_id"] = bot["user_id"]
    if dm:
        out["person_bot_dm_id"] = dm
    else:
        out.pop("person_bot_dm_id", None)          # never keep a DM id from another workspace or app (verifier)
    if sources != dict(DEFAULT_TOKEN_SOURCES) or name != "default":
        out["token_sources"] = sources          # a named profile always declares its variables (the guard reads them back)
    else:
        out.pop("token_sources", None)
    out.setdefault("language", "en")
    one_line = lambda v: str(v or "").replace("\n", " ").replace("\r", " ")      # a name never breaks the header comment
    header = (f"# atlas-slack profile `{name}` — written by `python3 -m atlas_slack init`; holds no secrets "
              f"(token variable names only)\n# workspace {one_line(person.get('team'))} · person {one_line(person.get('user'))} · "
              f"bot {one_line(bot.get('user'))}" + ("" if dm else f" · person_bot_dm_id: none yet — a bot send with channel = your user id opens the DM; init{pflag} --force then records it") + "\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + yaml.safe_dump(out, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"wrote {path}" + (" (identity fields rewritten; other keys kept, comments not)" if existing else "")
          + ("" if dm else f" — bot DM not found: the first bot send to you (channel = your user id) opens it; init{pflag} --force then records it"))
    print(f"  workspace {person['team_id']} · you {person['user_id']} ({person.get('user', '?')}) · bot {bot['user_id']}"
          f" ({bot.get('user', '?')})" + (f" · bot DM {dm}" if dm else " · bot DM not found (optional)"))
    if name != "default":
        print(f"then: export ATLAS_SLACK_PROFILE={name} in the environment Claude Code runs in (a project's "
              f".claude/settings.json → env works); doctor --profile {name}; then restart the session")
    else:
        print("then: doctor, and a restart of the Claude Code session before the first send")
    return 0


NEXT_CODES = ("init", "fix-profile", "set-token", "new-token", "retry", "swap-tokens", "decide", "add-scope", "frozen",
              "fix-policy", "fix-journal", "send")


def cmd_doctor(args) -> int:
    """Print the state and ONE `next:` line — the setup skill follows that line, nothing else.

    Order of the verdict (first that applies wins): no profile file for the name (for `default`: and no
    ATLAS_BOT_USER_ID in the environment) → init ·
    identity fields missing in a file → fix-profile · profile errors → fix-profile · the same token in both
    variables → swap-tokens · then per token slot, the person's first and the bot's second: variable unset →
    set-token · Slack rejects the token → new-token · a transient failure (an exception type, a rate limit, Slack
    internal_error) → retry · the wrong kind of token in the slot → swap-tokens · ids disagree with the profile →
    decide · after both slots, a first-session scope missing on either token (FIRST_SCOPES; skipped and said when
    Slack sent no scope header) → add-scope · then freeze → frozen · policy invalid → fix-policy · journal unavailable or no decision adapter →
    fix-journal · else send. Exit 0 only on send.
    """
    from atlas_slack import identity
    from atlas_slack.gate import rights
    from atlas_slack.journal import JournalUnavailable, open_journal
    from atlas_slack.policy import load_policy
    from atlas_slack.profile import load_profile
    profile = load_profile(args.profile)
    name = profile.name
    print(f"profile: {name} ({profile.source}{': ' + profile.path if profile.path else ''})")
    for e in profile.errors:
        print(f"  ✗ {e}")
    nxt: tuple[str, str] | None = None

    def first(code: str, why: str) -> None:
        nonlocal nxt
        if nxt is None:
            nxt = (code, why)

    pflag = f" --profile {name}" if name != "default" else ""
    if profile.source == "builtin" and not profile.errors and not profile.bot_user_id:
        # no file and no ids in the environment (an env-only profile sets ATLAS_BOT_USER_ID at least — policy-schema § Profile)
        first("init", f"no profile file for {name!r}: python3 -m atlas_slack init{pflag}"
                      + (" --user-var <VAR> --bot-var <VAR>" if name != "default" else ""))
    declared = False                                           # does the FILE declare token_sources? (init's guard reads the file)
    if profile.source == "file":
        try:
            import yaml
            raw = yaml.safe_load(Path(profile.path).read_text(encoding="utf-8")) if profile.path else None
            ts_raw = raw.get("token_sources") if isinstance(raw, dict) and isinstance(raw.get("token_sources"), dict) else {}
            declared = all(isinstance(ts_raw.get(k), str) and ts_raw.get(k) for k in ("user", "bot"))
        except Exception:  # noqa: BLE001
            declared = False
    vflags = "" if name == "default" or declared else " --user-var <VAR> --bot-var <VAR>"   # a second profile names its own variables
    if profile.source == "file" and not profile.errors:
        missing = [k for k in ("workspace_id", "person_user_id", "bot_user_id") if not getattr(profile, k)]
        if missing:
            first("fix-profile", f"{profile.path} has no {', '.join(missing)} — python3 -m atlas_slack init{pflag} --force{vflags} "
                                 "fills the identity fields from the tokens and keeps the other keys")
    if profile.errors:
        e0 = profile.errors[0]
        if "invalid profile name" in e0:
            first("fix-profile", f"{e0} — choose a valid name (letters, digits, _ . -) or fix ATLAS_SLACK_PROFILE")
        elif "profile_missing" in e0:
            first("init", f"{e0} — python3 -m atlas_slack init{pflag} --user-var <VAR> --bot-var <VAR>")
        else:
            one = " ".join(e0.split())[:220]
            hint = ("remove or correct that key (init --force would keep it)" if "unknown profile keys" in e0 else
                    f"edit the file by hand (init{pflag} --force{vflags} rewrites only the identity fields and token_sources; "
                    "an unparsable file it replaces)")
            first("fix-profile", f"{one} — {profile.path or 'the profile'}: {hint}")
    if any("profile_missing" in e for e in profile.errors):
        print("tokens: not checked — the missing profile names no variables; init writes them")
        print()
        print(rights(profile))
        code, why = nxt
        print(f"\nnext: {code} — {why}")
        return 1
    auth: dict[bool, dict] = {as_bot: _auth(as_bot, profile) for as_bot in (False, True)}
    lacking: list[tuple[str, list[str]]] = []
    if all(d.get("ok") for d in auth.values()) and auth[False].get("user_id") == auth[True].get("user_id") \
            and auth[False].get("bot_id") == auth[True].get("bot_id"):
        first("swap-tokens", f"{profile.token_var(False)} and {profile.token_var(True)} hold the SAME token — the user token "
                             f"(xoxp-) goes in {profile.token_var(False)}, the bot token (xoxb-) in {profile.token_var(True)}")
    for as_bot, label in ((False, "person"), (True, "bot")):
        data = auth[as_bot]
        var = profile.token_var(as_bot)
        if not data.get("ok"):
            err = str(data.get("error"))
            print(f"{label} token ({var}): ✗ {err}")
            if "is not set in the environment" in err:
                first("set-token", f"{var} is not set: export it in the start-up file of the shell Claude Code starts from, "
                                   "open a new terminal, restart Claude Code")
            elif re.search(r"\b[A-Za-z]*(Error|Exception|Disconnected|IncompleteRead)\b", err) or "non-JSON" in err or err.lower() in ("ratelimited", "service_unavailable", "internal_error", "fatal_error", "request_timeout"):
                # a Python exception type (URLError, ConnectionError, TimeoutError …) or a transient Slack code: not the token
                first("retry", f"{var}: {err} — transient (network, rate limit): try again in a minute; if it persists, "
                               "check the variable's value for stray characters (quotes, a newline) — then doctor again")
            else:
                first("new-token", f"{var}: Slack answered {err} — first check the variable's value (the whole token, no quotes, "
                                   "no newline); if it is clean, reinstall the app for a fresh token and update the variable")
            continue
        found = identity.problems(data, profile, as_bot, advise=True)
        mism = [f for f in found if "is not set" not in f]
        mark = "✗" if mism else "✓"
        print(f"{label} token ({var}): {mark} team {data.get('team_id')} ({data.get('team', '?')}) · "
              f"user {data.get('user_id')} ({data.get('user', '?')})" + (f" · bot_id {data['bot_id']}" if data.get("bot_id") else "")
              + (f" · {len(data['_scopes'])} scopes" if data.get("_scopes") else " · scopes unknown"))
        for f in found:
            print(f"  {'✗' if f in mism else '·'} {f}")
        kind = [f for f in mism if "bot_id" in f and ("sits in" in f or "not a bot token" in f)]
        if kind:
            first("swap-tokens", f"{var}: {kind[0]} — the two variables hold the wrong kind of token; fix the variables")
        elif mism:
            first("decide", f"{var}: {'; '.join(mism)} — if the TOKEN is right: python3 -m atlas_slack init{pflag} --force{vflags} "
                            f"(identity fields only); if the PROFILE is right: the variable holds another app's token")
        for sc in ("chat:write.customize", "incoming-webhook"):
            if sc in data.get("_scopes", []):
                print(f"  · scope {sc} is granted — the door refuses what it enables; drop it from the app (hygiene)")
        if not data.get("_scopes"):
            print("  · scopes: unknown (no x-oauth-scopes header in Slack's answer) — the first-session scopes are not checked")
        else:
            need = [sc for sc in FIRST_SCOPES[as_bot] if sc not in data["_scopes"]]
            if need:
                print(f"  ✗ missing for the first session: {', '.join(need)}")
                lacking.append((var, need))
    if lacking:
        # after both slots: a wrong app (swap-tokens / decide) outranks a scope the right app lacks; one line names every gap
        gaps = "; ".join(f"{var}: the app lacks {', '.join(need)}" for var, need in lacking)
        first("add-scope", f"{gaps} — add under OAuth & Permissions → Scopes, Reinstall to Workspace, "
                           f"put the new token(s) into {', '.join(var for var, _ in lacking)}, doctor again")
    if profile.freeze:
        first("frozen", f"freeze: true in {profile.path or 'the profile'} — every write refuses until it is false (the person's call)")
    pol = load_policy(profile)
    if pol.state == "invalid":
        first("fix-policy", f"policy invalid: {pol.error} — every write refuses policy_invalid until the file or the pin is fixed")
    try:
        open_journal(profile.journal_dir)
    except JournalUnavailable as e:
        first("fix-journal", f"journal unavailable: {e} — nothing can be sent; fix permissions on {profile.journal_dir}")
    except Exception as e:  # noqa: BLE001
        first("fix-journal", f"journal: {type(e).__name__}: {e}")
    if not profile.decision_adapters:
        first("fix-journal", "decision_adapters is empty in the profile — human-decided sends have no adapter; put [cc-hook] back")
    print()
    print(rights(profile))
    if nxt is None:
        target = profile.person_bot_dm_id or (auth[False].get("user_id") if auth.get(False) else None) or profile.person_user_id
        record = (f"then set ATLAS_SLACK_PERSON_BOT_DM_ID to the DM id the result reports, restart, doctor again" if profile.source == "builtin"
                  else f"then init{pflag} --force{vflags} records it")
        how = (f"the bot's DM {target}" if profile.person_bot_dm_id else
               f"your user id {target} as the channel (Slack opens the bot's DM; {record})")
        nxt = ("send", f"consistent — restart the session if anything changed, then one message to yourself in the bot's voice: "
                       f"chat.postMessage, as_bot=True, channel = {how}")
    code, why = nxt
    print(f"\nnext: {code} — {why}")
    return 0 if code == "send" else 1


def main(argv: list[str] | None = None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in SUBCOMMANDS:
        parser = argparse.ArgumentParser(prog="python3 -m atlas_slack", description="atlas-slack profile tools")
        sub = parser.add_subparsers(dest="cmd", required=True)
        p_init = sub.add_parser("init", help="write ~/.config/atlas-slack/<name>.yaml from the tokens in the environment")
        p_init.add_argument("--profile", help="profile name (default: $ATLAS_SLACK_PROFILE or `default`)")
        p_init.add_argument("--force", action="store_true", help="rewrite the identity fields of an existing profile (other keys kept)")
        p_init.add_argument("--user-var", metavar="VAR", help="env var holding the user token (default SLACK_USER_TOKEN; a second workspace)")
        p_init.add_argument("--bot-var", metavar="VAR", help="env var holding the bot token (default SLACK_BOT_TOKEN)")
        p_doc = sub.add_parser("doctor", help="check the tokens against the profile, print the rights table and ONE next step")
        p_doc.add_argument("--profile", help="profile name (default: $ATLAS_SLACK_PROFILE or `default`)")
        args = parser.parse_args(argv)
        sys.exit(cmd_init(args) if args.cmd == "init" else cmd_doctor(args))

    parser = argparse.ArgumentParser(description="atlas-slack MCP server (also: init, doctor)")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    args = parser.parse_args(argv)

    from atlas_slack.gateway import mcp
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
