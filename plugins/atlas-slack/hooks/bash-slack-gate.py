#!/usr/bin/env python3
"""PreToolUse hook — block bash shortcuts to Slack when the MCP tool exists.

Fires on every Bash tool use. If the command is doing raw HTTP against
slack.com/api/* or pulling a Slack token out of keychain, block it with
a pointer to `slack_api`. Legitimate file-upload PUTs (to files.slack.com)
are allowed — those are binary uploads, not Web API calls.
"""

import json
import re
import sys


SLACK_API_CURL = re.compile(
    r"\bcurl\b[^|&;]*?\bslack\.com/api/",
    re.IGNORECASE | re.DOTALL,
)
KEYCHAIN_SLACK_TOKEN = re.compile(
    r"security\s+find-generic-password[^|&;]*slack",
    re.IGNORECASE | re.DOTALL,
)


BLOCK_MESSAGE_API = (
    "BLOCKED: raw curl to slack.com/api/*.\n"
    "Use the atlas-slack MCP tool instead — it covers all 200+ Slack Web API\n"
    "methods and handles token routing, attribution, and error formatting:\n\n"
    "  slack_api(method, params, as_bot=?)\n\n"
    "Examples:\n"
    '  slack_api("conversations.open", {"users": "U..."})\n'
    '  slack_api("users.lookupByEmail", {"email": "..."})\n'
    '  slack_api("chat.postMessage", {...}, as_bot=True)\n\n'
    "See the `atlas-slack` skill for the intent→tool table.\n"
    "(Legitimate binary uploads to files.slack.com are not blocked.)"
)

BLOCK_MESSAGE_KEYCHAIN = (
    "BLOCKED: manual keychain lookup for Slack token is unnecessary.\n"
    "Tokens are resolved via environment (SLACK_USER_TOKEN / SLACK_BOT_TOKEN),\n"
    "set once in ~/.zshenv. The slack_api MCP tool handles token selection.\n\n"
    "Use slack_api(method, params, as_bot=?) — no manual token handling needed."
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    command = payload.get("tool_input", {}).get("command", "")
    if not command:
        return 0

    if SLACK_API_CURL.search(command):
        print(BLOCK_MESSAGE_API, file=sys.stderr)
        return 2

    if KEYCHAIN_SLACK_TOKEN.search(command):
        print(BLOCK_MESSAGE_KEYCHAIN, file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
