"""Entry point: the MCP server (default), `init` (write a profile from the tokens) and `doctor`.

    python3 -m atlas_slack                      MCP server on stdio (what the plugin's .mcp.json runs)
    python3 -m atlas_slack init [--profile NAME] [--force]
    python3 -m atlas_slack doctor [--profile NAME]
"""

from __future__ import annotations

import argparse
import os
import sys

SUBCOMMANDS = ("init", "doctor")


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
    data["_scopes"] = sorted(s for s in (resp.headers.get("x-oauth-scopes") or "").split(",") if s)
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
    from atlas_slack.profile import config_dir, load_profile
    name = args.profile or os.environ.get("ATLAS_SLACK_PROFILE") or "default"
    path = config_dir() / f"{name}.yaml"
    if path.exists() and not args.force:
        print(f"{path} exists — edit it, or run init --force to rewrite it", file=sys.stderr)
        return 2
    profile = load_profile(name)      # the built-in profile: token variable names
    person = _auth(False, profile)
    bot = _auth(True, profile)
    failed = [f"{profile.token_var(ab)}: {d['error']}" for ab, d in ((False, person), (True, bot)) if not d.get("ok")]
    if failed:
        print("cannot read the identities behind the tokens:\n  " + "\n  ".join(failed), file=sys.stderr)
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
    dm = _bot_dm(profile, person["user_id"])
    lines = [
        f"# atlas-slack profile `{name}` — written by `python3 -m atlas_slack init`; holds no secrets",
        f"workspace_id: {person['team_id']}          # {person.get('team', '')}".rstrip(" #"),
        f"person_user_id: {person['user_id']}        # {person.get('user', '')}".rstrip(" #"),
        f"bot_user_id: {bot['user_id']}              # the bot's user id — the 💙 attribution target",
    ]
    if dm:
        lines.append(f"person_bot_dm_id: {dm}             # the bot's DM with you")
    else:
        lines.append("# person_bot_dm_id: D…                 # the bot's DM with you — open one in Slack, then add its id")
    if profile.token_sources != {"user": "SLACK_USER_TOKEN", "bot": "SLACK_BOT_TOKEN"}:
        lines.append("token_sources:")
        lines.append(f"  user: {profile.token_var(False)}")
        lines.append(f"  bot: {profile.token_var(True)}")
    lines += [
        "language: en                           # approval prompt and card: en | ru",
        "# policy_path: /path/to/atlas-slack.policy.yaml   # optional — see docs/policy-schema.md",
        "# freeze: true                         # emergency stop: every write refuses, reads continue",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {path}")
    print(f"  workspace {person['team_id']} · you {person['user_id']} ({person.get('user', '?')}) · bot {bot['user_id']}"
          f" ({bot.get('user', '?')})" + (f" · bot DM {dm}" if dm else " · bot DM not found (optional)"))
    if name != "default":
        print(f"next: export ATLAS_SLACK_PROFILE={name} in the environment Claude Code runs in "
              f"(a project's .claude/settings.json → env works), then restart the session")
    else:
        print("next: restart the Claude Code session; slack_api(\"_rights\") shows what this host may do")
    return 0


def cmd_doctor(args) -> int:
    from atlas_slack import identity
    from atlas_slack.gate import rights
    from atlas_slack.profile import load_profile
    profile = load_profile(args.profile)
    print(f"profile: {profile.name} ({profile.source}{': ' + profile.path if profile.path else ''})")
    for e in profile.errors:
        print(f"  ✗ {e}")
    rc = 1 if profile.errors else 0
    for as_bot, label in ((False, "person"), (True, "bot")):
        data = _auth(as_bot, profile)
        var = profile.token_var(as_bot)
        if not data.get("ok"):
            print(f"{label} token ({var}): ✗ {data.get('error')}")
            rc = 1
            continue
        found = identity.problems(data, profile, as_bot, advise=True)
        mism = [f for f in found if "is not set" not in f]
        mark = "✗" if mism else "✓"
        print(f"{label} token ({var}): {mark} team {data.get('team_id')} ({data.get('team', '?')}) · "
              f"user {data.get('user_id')} ({data.get('user', '?')})" + (f" · bot_id {data['bot_id']}" if data.get("bot_id") else "")
              + f" · {len(data.get('_scopes', []))} scopes")
        for f in found:
            print(f"  {'✗' if f in mism else '·'} {f}")
        if mism:
            rc = 1
        for s in ("chat:write.customize", "incoming-webhook"):
            if s in data.get("_scopes", []):
                print(f"  · scope {s} is granted — the door refuses what it enables; drop it from the app (hygiene)")
    print()
    print(rights(profile))
    return rc


def main(argv: list[str] | None = None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in SUBCOMMANDS:
        parser = argparse.ArgumentParser(prog="python3 -m atlas_slack", description="atlas-slack profile tools")
        sub = parser.add_subparsers(dest="cmd", required=True)
        p_init = sub.add_parser("init", help="write ~/.config/atlas-slack/<name>.yaml from the tokens in the environment")
        p_init.add_argument("--profile", help="profile name (default: $ATLAS_SLACK_PROFILE or `default`)")
        p_init.add_argument("--force", action="store_true", help="rewrite an existing profile file")
        p_doc = sub.add_parser("doctor", help="check the tokens against the profile and print the rights table")
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
