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
              "blocks.validate",       # validator — no workspace mutation
              "users.conversations"}   # read: lists conversations for a user


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


def open_reason(params: dict) -> str:
    """Ask-text for conversations.open — it CREATES (or reopens) a DM/MPIM.

    Incident 08-28: an accidental empty multi-party DM was created while
    'looking up' a conversation. The confirmation text now says what the call
    will actually do — including the MPIM member list — and names the read-side
    alternative (users.conversations) for finding existing conversations.
    """
    if not isinstance(params, dict):
        params = {}
    users = [u.strip() for u in str(params.get("users") or "").split(",") if u.strip()]
    tail = "To find existing DMs use users.conversations (read, auto-allowed)."
    if len(users) > 1:
        return (f"Slack write: conversations.open — creates a group DM (MPIM) "
                f"with {len(users)} users: {', '.join(users)}. {tail}")
    if len(users) == 1:
        return f"Slack write: conversations.open — opens a 1:1 DM with {users[0]}. {tail}"
    channel = params.get("channel")
    if channel:
        return f"Slack write: conversations.open — reopens conversation {channel}."
    return f"Slack write: conversations.open — creates a DM/MPIM. {tail}"


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
        if method == "conversations.open":
            emit("ask", open_reason(tool_input.get("params") or {}))
            return
        emit("ask", f"Slack write: {method}")
        return

    # slack_download is Slack-read-only (files.info + authenticated GET); its
    # only local effect is writing the file into out_dir.
    if short == "slack_download":
        emit("allow")
        return

    # Anything else (v1.2 removed the convenience wrappers; this also catches
    # future tools added without a gate rule) → ask. Defense in depth.
    emit("ask", f"Slack write: {short}")


if __name__ == "__main__":
    main()
