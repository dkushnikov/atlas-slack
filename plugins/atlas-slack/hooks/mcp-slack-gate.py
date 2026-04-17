#!/usr/bin/env python3
"""PreToolUse hook for atlas-slack MCP tools — read/write split.

Reads (slack_api with method matching read patterns) → auto-allow.
Writes (everything else, including all convenience tools) → ask user confirmation.

Attribution coverage is no longer hook-checked — universal middleware in
`gateway.py:_maybe_inject_attribution` (introduced 2026-04-14, ATT-001 fix)
applies attribution to whitelisted methods (chat.postMessage / chat.update /
chat.scheduleMessage / chat.postEphemeral / chat.meMessage and
files.completeUploadExternal.initial_comment) when as_bot=False. Hook stays
focused on user-confirmation routing.

Claude Code PreToolUse hook spec — reads JSON from stdin:
  { "tool_name": "mcp__plugin_atlas-slack_atlas-slack__slack_api",
    "tool_input": { "method": "...", "params": {...}, "as_bot": ... } }

Writes JSON to stdout:
  {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                          "permissionDecision": "allow"|"ask"|"deny",
                          "permissionDecisionReason": "..."}}
"""
import json
import sys


READ_SUFFIXES = (".list", ".info", ".history", ".get", ".read", ".members", ".replies", ".test")
READ_PREFIXES = ("search.", "auth.", "emoji.", "team.")
READ_EXACT = {"dnd.info", "dnd.teamInfo", "reminders.list", "reminders.info"}


def emit(decision: str, reason: str = "") -> None:
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
        }
    }
    if reason:
        out["hookSpecificOutput"]["permissionDecisionReason"] = reason
    print(json.dumps(out))
    sys.exit(0)


def is_read(method: str) -> bool:
    if method in READ_EXACT:
        return True
    if any(method.startswith(p) for p in READ_PREFIXES):
        return True
    return any(method.endswith(s) for s in READ_SUFFIXES)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception as e:
        emit("ask", f"slack-gate parse error: {e}")
        return

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    short = tool_name.split("__")[-1]

    if short == "slack_api":
        method = tool_input.get("method", "")
        if is_read(method):
            emit("allow")
            return
        emit("ask", f"Slack write: {method}")
        return

    # Convenience tools (slack_react, slack_pin, slack_upload, slack_edit,
    # slack_delete, slack_create_channel, slack_invite, slack_archive,
    # slack_usergroup, slack_bookmark, slack_remind, slack_set_status, slack_dnd)
    # — always writes.
    emit("ask", f"Slack write: {short}")


if __name__ == "__main__":
    main()
