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
READ_EXACT = {"dnd.info", "dnd.teamInfo", "reminders.list", "reminders.info",
              "blocks.validate"}  # validator — no workspace mutation

# Writes that are auto-allowed by conscious classification (issue #1):
# - chat.appendStream / chat.stopStream only continue a stream that chat.startStream
#   already opened — and startStream is gated as a send ("ask"). Asking on every
#   appended chunk would make streaming unusable without adding any control: the
#   send decision was the start.
# - agents.sessions.setStatus / rename mutate only agent-session chrome (status
#   line, session title) — no recipient-facing content. Other agents.sessions.*
#   methods (future additions) fall through to "ask" by default.
ALLOWED_WRITES = {
    "chat.appendStream",
    "chat.stopStream",
    "agents.sessions.setStatus",
    "agents.sessions.rename",
}


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
        if is_read(method) or method in ALLOWED_WRITES:
            emit("allow")
            return
        emit("ask", f"Slack write: {method}")
        return

    # slack_download is Slack-read-only (files.info + authenticated GET); its
    # only local effect is writing the file into out_dir.
    if short == "slack_download":
        emit("allow")
        return

    # Convenience tools (slack_react, slack_pin, slack_upload, slack_edit,
    # slack_delete, slack_create_channel, slack_invite, slack_archive,
    # slack_usergroup, slack_bookmark, slack_remind, slack_set_status, slack_dnd)
    # — always writes.
    emit("ask", f"Slack write: {short}")


if __name__ == "__main__":
    main()
