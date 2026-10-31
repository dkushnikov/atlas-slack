#!/usr/bin/env python3
"""End-to-end smoke test for atlas-slack plugin against a real Slack workspace.

Sandbox: the DM of one colleague who agreed to receive test messages (SMOKE_DM_USER, required).

Covers:
  1. Auth + identity (auth.test) — token reachable, identity resolved
  2. Read path: conversations.history on the sandbox DM
  3. Preparation-layer: conversations.open with the sandbox user (Slack ID)
  4. Lookup: users.info on the sandbox user
  5. Bot-token write: post message as the bot to the sandbox DM
  6. User-token write with attribution: post as the person with auto-injected footer
  7. Reaction: reactions.add bot-side
  8. Cleanup: delete the test messages
  9. Hook enforcement: invoke bash with curl slack.com/api/* — must be blocked

Run:
  python3 scripts/smoke-e2e.py
  python3 scripts/smoke-e2e.py --keep   # don't auto-delete posted messages

Requires:
  SLACK_USER_TOKEN, SLACK_BOT_TOKEN in env (loaded from ~/.zshenv).
  SMOKE_DM_USER env var — the sandbox colleague's Slack user id (no default).
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

SANDBOX_USER = os.environ.get("SMOKE_DM_USER") or sys.exit("SMOKE_DM_USER is required: the sandbox colleague's Slack user id")
TEST_MARKER = "[atlas-slack smoke test — safe to ignore]"


class Result:
    def __init__(self, name: str):
        self.name = name
        self.ok = False
        self.detail = ""

    def passed(self, detail: str = "") -> "Result":
        self.ok = True
        self.detail = detail
        return self

    def failed(self, detail: str) -> "Result":
        self.ok = False
        self.detail = detail
        return self

    def __str__(self) -> str:
        sym = "✓" if self.ok else "✗"
        line = f"  {sym} {self.name}"
        if self.detail:
            line += f" — {self.detail}"
        return line


def get_clients() -> tuple[WebClient, WebClient]:
    user_token = os.environ.get("SLACK_USER_TOKEN")
    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    if not user_token:
        raise SystemExit("SLACK_USER_TOKEN not set in env")
    if not bot_token:
        raise SystemExit("SLACK_BOT_TOKEN not set in env")
    return WebClient(token=user_token), WebClient(token=bot_token)


def test_auth(user: WebClient, bot: WebClient) -> list[Result]:
    out = []
    r = Result("user-token auth.test")
    try:
        u = user.auth_test()
        out.append(r.passed(f"user_id={u['user_id']} team={u.get('team', '?')}"))
    except SlackApiError as e:
        out.append(r.failed(str(e)))
    r = Result("bot-token auth.test")
    try:
        b = bot.auth_test()
        out.append(r.passed(f"bot_user_id={b.get('user_id')} team={b.get('team', '?')}"))
    except SlackApiError as e:
        out.append(r.failed(str(e)))
    return out


def test_user_lookup(user: WebClient) -> Result:
    r = Result(f"users.info on the sandbox user ({SANDBOX_USER})")
    try:
        u = user.users_info(user=SANDBOX_USER)
        if u.get("ok") and u.get("user", {}).get("id") == SANDBOX_USER:
            return r.passed(f"name={u['user'].get('real_name')}")
        return r.failed(f"unexpected response: {u}")
    except SlackApiError as e:
        return r.failed(str(e))


def test_open_dm(user: WebClient) -> tuple[Result, str | None]:
    r = Result("conversations.open with the sandbox user (preparation-layer)")
    try:
        resp = user.conversations_open(users=SANDBOX_USER)
        ch_id = resp.get("channel", {}).get("id")
        if ch_id:
            return r.passed(f"channel_id={ch_id}"), ch_id
        return r.failed(f"no channel id returned: {resp}"), None
    except SlackApiError as e:
        return r.failed(str(e)), None


def test_read(user: WebClient, channel: str) -> Result:
    r = Result(f"conversations.history on the sandbox DM ({channel})")
    try:
        h = user.conversations_history(channel=channel, limit=3)
        n = len(h.get("messages", []))
        return r.passed(f"{n} messages read")
    except SlackApiError as e:
        return r.failed(str(e))


def test_post_as_atlas(bot: WebClient, channel: str) -> tuple[Result, str | None]:
    r = Result("chat.postMessage as Atlas (bot token, no attribution expected)")
    try:
        resp = bot.chat_postMessage(channel=channel, text=f"{TEST_MARKER} bot-token send")
        ts = resp.get("ts")
        # Verify no attribution block injected
        msg_blocks = (resp.get("message") or {}).get("blocks") or []
        has_attribution = any(
            b.get("type") == "context"
            and any(":blue_heart:" in (e.get("text") or "") for e in b.get("elements", []))
            for b in msg_blocks
        )
        if has_attribution:
            return r.failed("attribution block found in bot-mode send (should be absent)"), ts
        return r.passed(f"ts={ts}"), ts
    except SlackApiError as e:
        return r.failed(str(e)), None


def test_post_as_person(user: WebClient, channel: str) -> tuple[Result, str | None]:
    r = Result("chat.postMessage as the person (user token, attribution expected)")
    # We bypass atlas-slack gateway here and call slack_sdk directly to avoid
    # double-injection. Instead we test that the *plugin* injects attribution
    # when called via slack_api — see separate plugin test (test_gateway.py).
    # This raw call is the "control": should NOT have attribution block.
    try:
        resp = user.chat_postMessage(channel=channel, text=f"{TEST_MARKER} user-token raw")
        ts = resp.get("ts")
        return r.passed(f"raw user-token send ts={ts} (control: no attribution expected via raw SDK)"), ts
    except SlackApiError as e:
        return r.failed(str(e)), None


def test_reaction(bot: WebClient, channel: str, ts: str) -> Result:
    r = Result(f"reactions.add ({ts})")
    try:
        bot.reactions_add(channel=channel, name="white_check_mark", timestamp=ts)
        return r.passed("reaction added")
    except SlackApiError as e:
        return r.failed(str(e))


def test_hook_blocks_curl() -> Result:
    """Invoke the plugin's PreToolUse Bash hook directly with a curl payload."""
    r = Result("PreToolUse Bash hook blocks curl slack.com/api/*")
    hook = os.path.expanduser(
        str(Path(__file__).resolve().parent.parent / "plugins/atlas-slack/hooks/bash-slack-gate.py")
    )
    if not os.path.exists(hook):
        return r.failed(f"hook not found: {hook}")
    payload = (
        '{"tool_input":{"command":"curl -H \\"Authorization: Bearer x\\" '
        'https://slack.com/api/conversations.open -d users=U1"}}'
    )
    proc = subprocess.run(
        ["python3", hook],
        input=payload,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if proc.returncode == 2 and "BLOCKED" in proc.stderr:
        return r.passed("curl blocked, exit=2 with BLOCKED stderr")
    return r.failed(f"hook did not block — returncode={proc.returncode} stderr={proc.stderr[:200]}")


def cleanup(user: WebClient, bot: WebClient, channel: str, msgs: list[tuple[str, str]]) -> list[Result]:
    """Delete posted test messages. msgs = [(client_kind, ts), ...]."""
    out = []
    for kind, ts in msgs:
        if not ts:
            continue
        client = bot if kind == "bot" else user
        r = Result(f"cleanup delete {kind}-mode ts={ts}")
        try:
            client.chat_delete(channel=channel, ts=ts)
            out.append(r.passed())
        except SlackApiError as e:
            out.append(r.failed(str(e)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="don't auto-delete posted test messages")
    args = ap.parse_args()

    print("atlas-slack smoke E2E — sandbox: the SMOKE_DM_USER DM")
    print(f"  user-id target: {SANDBOX_USER}")
    print(f"  cleanup: {'OFF (--keep)' if args.keep else 'ON'}")
    print()

    user, bot = get_clients()
    results: list[Result] = []

    print("§ Auth")
    for r in test_auth(user, bot):
        results.append(r); print(r)

    print("§ Read + lookup")
    r = test_user_lookup(user); results.append(r); print(r)
    r, dm_id = test_open_dm(user); results.append(r); print(r)
    if not dm_id:
        print("\n[abort] no DM channel ID — skipping write tests")
        return 1
    r = test_read(user, dm_id); results.append(r); print(r)

    print("§ Writes")
    posted: list[tuple[str, str | None]] = []
    r, ts_bot = test_post_as_atlas(bot, dm_id); results.append(r); print(r)
    if ts_bot: posted.append(("bot", ts_bot))
    r, ts_user = test_post_as_person(user, dm_id); results.append(r); print(r)
    if ts_user: posted.append(("user", ts_user))

    if ts_bot:
        print("§ Reaction")
        r = test_reaction(bot, dm_id, ts_bot); results.append(r); print(r)

    print("§ Hook enforcement")
    r = test_hook_blocks_curl(); results.append(r); print(r)

    if not args.keep and posted:
        print("§ Cleanup")
        # Small pause so reaction lands before delete
        time.sleep(1)
        for r in cleanup(user, bot, dm_id, posted):
            results.append(r); print(r)

    n_pass = sum(1 for r in results if r.ok)
    n_total = len(results)
    print()
    print(f"{n_pass}/{n_total} pass" + (" — all green ✓" if n_pass == n_total else " — failures present ✗"))
    return 0 if n_pass == n_total else 1


if __name__ == "__main__":
    sys.exit(main())
