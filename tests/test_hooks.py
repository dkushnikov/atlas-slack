"""Tests for the plugin's PreToolUse gate hook (mcp-slack-gate.py).

The hook is a standalone stdin/stdout script — tested via subprocess, exactly
as Claude Code invokes it. Regression context: the CC 2.1.195 matcher change
(commit 1248ff6) showed gate behavior needs pinning in tests.
"""

import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "plugins" / "atlas-slack" / "hooks" / "mcp-slack-gate.py"


def run_gate(tool_name: str, tool_input: dict) -> dict:
    payload = {"tool_name": tool_name, "tool_input": tool_input}
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["hookSpecificOutput"]


def api(method: str, params: dict | None = None) -> dict:
    tool_input = {"method": method}
    if params is not None:
        tool_input["params"] = params
    return run_gate("mcp__plugin_atlas-slack_atlas-slack__slack_api", tool_input)


class TestSlackApiGate:
    def test_reads_allowed(self):
        for method in ("conversations.history", "users.info", "search.messages", "auth.test"):
            assert api(method)["permissionDecision"] == "allow"

    def test_writes_ask(self):
        for method in ("chat.postMessage", "conversations.create", "chat.delete"):
            out = api(method)
            assert out["permissionDecision"] == "ask"
            assert method in out["permissionDecisionReason"]

    def test_stream_start_asks(self):
        """The send decision is the stream open — gate it like any send."""
        assert api("chat.startStream")["permissionDecision"] == "ask"

    def test_stream_continuations_allowed(self):
        """append/stop only continue an already-approved stream."""
        assert api("chat.appendStream")["permissionDecision"] == "allow"
        assert api("chat.stopStream")["permissionDecision"] == "allow"

    def test_agent_session_chrome_allowed(self):
        """setStatus/rename mutate session chrome only — no recipient-facing content."""
        assert api("agents.sessions.setStatus")["permissionDecision"] == "allow"
        assert api("agents.sessions.rename")["permissionDecision"] == "allow"

    def test_unknown_agent_sessions_method_asks(self):
        """Future agents.sessions.* additions default to ask, not allow."""
        assert api("agents.sessions.invite")["permissionDecision"] == "ask"

    def test_blocks_validate_allowed(self):
        assert api("blocks.validate")["permissionDecision"] == "allow"

    def test_users_conversations_allowed(self):
        """The conversations.open hint points here — it must actually be a read."""
        assert api("users.conversations")["permissionDecision"] == "allow"

    def test_upload_v2_asks(self):
        """files.uploadV2 (UPL-002, SDK-mapped) is a write — gated like completeUploadExternal."""
        out = api("files.uploadV2", {"channel": "C1", "file": "/tmp/x.txt"})
        assert out["permissionDecision"] == "ask"
        assert "files.uploadV2" in out["permissionDecisionReason"]


class TestConversationsOpenHint:
    """v1.2 fix #6 — conversations.open creates a DM/MPIM (incident 08-28:
    accidental empty mpdm). The ask-text must say so and name the read path."""

    def test_single_user_dm_hint(self):
        out = api("conversations.open", {"users": "U123"})
        assert out["permissionDecision"] == "ask"
        reason = out["permissionDecisionReason"]
        assert "1:1 DM with U123" in reason
        assert "users.conversations" in reason

    def test_multi_party_lists_members(self):
        out = api("conversations.open", {"users": "U1, U2,U3"})
        assert out["permissionDecision"] == "ask"
        reason = out["permissionDecisionReason"]
        assert "group DM (MPIM)" in reason
        assert "3 users" in reason
        assert "U1, U2, U3" in reason
        assert "users.conversations" in reason

    def test_channel_reopen_hint(self):
        out = api("conversations.open", {"channel": "D2SH1V09K"})
        assert out["permissionDecision"] == "ask"
        assert "reopens conversation D2SH1V09K" in out["permissionDecisionReason"]

    def test_no_params_still_warns(self):
        out = api("conversations.open")
        assert out["permissionDecision"] == "ask"
        assert "creates a DM/MPIM" in out["permissionDecisionReason"]


class TestOtherToolGate:
    def test_unknown_tool_asks(self):
        """Removed convenience tools / future ungated tools fall through to ask."""
        out = run_gate("mcp__plugin_atlas-slack_atlas-slack__slack_react", {"channel": "C1"})
        assert out["permissionDecision"] == "ask"

    def test_download_allowed(self):
        out = run_gate("mcp__plugin_atlas-slack_atlas-slack__slack_download", {"file_id": "F1"})
        assert out["permissionDecision"] == "allow"

    def test_malformed_stdin_asks(self):
        proc = subprocess.run(
            [sys.executable, str(HOOK)],
            input="not json", capture_output=True, text=True, timeout=10,
        )
        out = json.loads(proc.stdout)["hookSpecificOutput"]
        assert out["permissionDecision"] == "ask"
