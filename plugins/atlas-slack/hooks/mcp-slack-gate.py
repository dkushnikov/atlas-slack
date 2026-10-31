#!/usr/bin/env python3
"""PreToolUse hook for atlas-slack MCP tools — thin adapter over the v2 core gate.

All decisions come from `atlas_slack.gate` (the same policy engine the MCP
server and the library `Door` use):

- allow       → permissionDecision "allow"  (reads, row 0; `_rights`; transitional continuations)
- refuse      → "deny" with the reason (freeze, identity overrides, invalid policy, ...)
- needs-human → "ask" with the full provenance as the prompt text; for a
                core send (postMessage, scheduleMessage, update, postEphemeral,
                meMessage, files.uploadV2) the hook also issues a one-time
                decision token bound to the operation hash and returns it in
                `updatedInput` (provenance.decision) — Claude Code runs the
                tool with that input only after the human approves.
- every other write → "ask", flagged "transitional" (0.4.0 semantics).

Import path: when this file sits in a source checkout (`<repo>/plugins/atlas-slack/hooks/`)
the checkout's `src/` is preferred — so `claude --plugin-dir <worktree>/plugins/atlas-slack`
gates with the worktree's code, not the installed package. In the plugin
cache (no `src/` next to it) the installed package is used.

Claude Code PreToolUse hook spec — reads JSON from stdin:
  { "tool_name": "mcp__plugin_atlas-slack_atlas-slack__slack_api",
    "tool_input": { "method": "...", "params": {...}, "as_bot": ..., "provenance": {...} } }
"""
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3] / "src"
if (_SRC / "atlas_slack" / "gate.py").is_file():
    sys.path.insert(0, str(_SRC))

try:
    from atlas_slack.gate import run_cc_hook
except Exception as e:  # core not importable → never silently allow
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "ask",
        "permissionDecisionReason": f"atlas-slack gate unavailable ({type(e).__name__}: {e}) — confirm manually"}}))
    sys.exit(0)

if __name__ == "__main__":
    run_cc_hook()
