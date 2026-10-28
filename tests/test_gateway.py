"""Tests for the generic gateway tool."""

import json

import pytest
from fastmcp.exceptions import ToolError
from slack_sdk.errors import SlackApiError

from atlas_slack.gate import hook_output
from atlas_slack.gateway import SECTION_TEXT_LIMIT
from atlas_slack.gateway import slack_api as _slack_api_server

TOOL = "mcp__plugin_atlas-slack_atlas-slack__slack_api"


def slack_api(method, params=None, as_bot=False, provenance=None):
    """The production route: PreToolUse hook (verdict + one-time decision) → MCP server.

    Since the v2 core slice chat.postMessage executes only with a decision the
    hook issued for exactly this operation; the 0.4.0 shaping tests below run
    through that route unchanged. Other methods go straight to the server, as
    before (the hook's ask is the human's, not the server's).
    """
    if method != "chat.postMessage":
        return _slack_api_server(method, params, as_bot)
    tool_input = {"method": method, "params": params or {}, "as_bot": as_bot}
    if provenance is not None:
        tool_input["provenance"] = provenance
    out = hook_output({"tool_name": TOOL, "tool_input": tool_input, "session_id": "test"})["hookSpecificOutput"]
    if out["permissionDecision"] == "deny":
        raise ToolError(out["permissionDecisionReason"])
    assert out["permissionDecision"] == "ask", out
    return _slack_api_server(**out["updatedInput"])
from conftest import TEST_BOT_USER_ID as ATLAS_BOT_USER_ID


def test_slack_api_calls_method(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": True}
    result = slack_api("reactions.add", {"channel": "C123", "name": "thumbsup", "timestamp": "1.2"})
    mock_webclient.api_call.assert_called_once_with("reactions.add", params={"channel": "C123", "name": "thumbsup", "timestamp": "1.2"})
    assert "Done" in result or "✓" in result


def test_slack_api_no_params(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": True, "members": [{"id": "U1", "name": "alice"}]}
    result = slack_api("users.list")
    mock_webclient.api_call.assert_called_once_with("users.list", params={})
    assert "alice" in result


def test_slack_api_error_is_structural(mock_webclient):
    """ok=false raises ToolError → FastMCP sets is_error=true on the MCP result."""
    mock_webclient.api_call.return_value = {"ok": False, "error": "not_authed"}
    with pytest.raises(ToolError, match="not_authed"):
        slack_api("chat.postMessage", {"channel": "C1", "text": "hi"})


def test_slack_api_exception_is_structural(mock_webclient):
    mock_webclient.api_call.side_effect = Exception("connection timeout")
    with pytest.raises(ToolError, match="connection timeout"):
        slack_api("users.list")


def test_slack_api_sdk_error_code_extracted_with_hint(mock_webclient):
    """WebClient raises SlackApiError on ok=false — the error code must be
    extracted so the ERROR_HINTS catalogue still applies (not buried in str(e))."""
    mock_webclient.api_call.side_effect = SlackApiError(
        message="The request to the Slack API failed.",
        response={"ok": False, "error": "channel_not_found"},
    )
    with pytest.raises(ToolError) as exc_info:
        slack_api("conversations.history", {"channel": "C_BAD"})
    assert "channel_not_found" in str(exc_info.value)
    assert "DM channel IDs are per-viewer" in str(exc_info.value)


def test_slack_api_sdk_error_without_code_falls_back_to_exception(mock_webclient):
    mock_webclient.api_call.side_effect = SlackApiError(message="boom", response=None)
    with pytest.raises(ToolError, match="exception"):
        slack_api("users.list")


def test_slack_api_default_uses_user_client(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": True}
    slack_api("users.list")
    mock_webclient._gateway_get_client.assert_called_with(as_bot=False)


def test_slack_api_as_bot_uses_bot_client(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": True, "ts": "1.2"}
    slack_api("chat.postMessage", {"channel": "C1", "text": "hi"}, as_bot=True)
    mock_webclient._gateway_get_client.assert_called_with(as_bot=True)


def test_slack_api_post_message_user_mode_injects_attribution(mock_webclient):
    """User-token chat.postMessage must append :blue_heart: <@U0BOTUSR01> context block."""
    mock_webclient.api_call.return_value = {"ok": True, "ts": "1.2"}
    slack_api("chat.postMessage", {"channel": "C1", "text": "hello"})

    call_params = mock_webclient.api_call.call_args.kwargs["params"]
    blocks = json.loads(call_params["blocks"])
    assert len(blocks) == 2
    # First block wraps the original text as section
    assert blocks[0]["type"] == "section"
    assert blocks[0]["text"]["text"] == "hello"
    # Second block is attribution
    attr = blocks[1]
    assert attr["type"] == "context"
    assert attr["elements"][0]["text"] == f":blue_heart: <@{ATLAS_BOT_USER_ID}>"
    # Original text param preserved for fallback rendering
    assert call_params["text"] == "hello"


def test_slack_api_post_message_preserves_existing_blocks(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": True, "ts": "1.2"}
    original_blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Bold*"}},
        {"type": "divider"},
    ]
    slack_api("chat.postMessage", {"channel": "C1", "text": "fallback", "blocks": original_blocks})

    call_params = mock_webclient.api_call.call_args.kwargs["params"]
    blocks = json.loads(call_params["blocks"])
    assert len(blocks) == 3
    assert blocks[:2] == original_blocks
    assert blocks[2]["type"] == "context"


def test_slack_api_post_message_bot_mode_no_attribution(mock_webclient):
    """Bot-token posts already appear as Atlas — no footer needed."""
    mock_webclient.api_call.return_value = {"ok": True, "ts": "1.2"}
    slack_api("chat.postMessage", {"channel": "C1", "text": "hello"}, as_bot=True)

    call_params = mock_webclient.api_call.call_args.kwargs["params"]
    assert "blocks" not in call_params
    assert call_params["text"] == "hello"


def test_slack_api_non_whitelisted_method_no_attribution(mock_webclient):
    """Attribution only for recipient-facing text methods — reactions/pins/reads untouched."""
    mock_webclient.api_call.return_value = {"ok": True}
    slack_api("reactions.add", {"channel": "C1", "name": "eyes", "timestamp": "1.2"})

    call_params = mock_webclient.api_call.call_args.kwargs["params"]
    assert "blocks" not in call_params


def test_slack_api_read_method_no_attribution(mock_webclient):
    """Read methods never get attribution even when params carry text-like fields."""
    mock_webclient.api_call.return_value = {"ok": True, "messages": []}
    slack_api("search.messages", {"query": "from:@owner thread:1.2"})

    call_params = mock_webclient.api_call.call_args.kwargs["params"]
    assert "blocks" not in call_params
    assert call_params["query"] == "from:@owner thread:1.2"


def test_slack_api_chat_update_user_mode_injects_attribution(mock_webclient):
    """chat.update under user token gets attribution block (edited messages re-post as user)."""
    mock_webclient.api_call.return_value = {"ok": True, "ts": "1.2"}
    slack_api("chat.update", {"channel": "C1", "ts": "1.2", "text": "edited"})

    call_params = mock_webclient.api_call.call_args.kwargs["params"]
    blocks = json.loads(call_params["blocks"])
    assert len(blocks) == 2
    assert blocks[1]["type"] == "context"
    assert blocks[1]["elements"][0]["text"] == f":blue_heart: <@{ATLAS_BOT_USER_ID}>"


def test_slack_api_chat_schedule_message_user_mode_injects_attribution(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": True, "scheduled_message_id": "Q1"}
    slack_api("chat.scheduleMessage", {"channel": "C1", "post_at": 9999999, "text": "later"})

    call_params = mock_webclient.api_call.call_args.kwargs["params"]
    assert json.loads(call_params["blocks"])[-1]["type"] == "context"
    assert json.loads(call_params["blocks"])[-1]["elements"][0]["text"] == f":blue_heart: <@{ATLAS_BOT_USER_ID}>"


def test_slack_api_chat_post_ephemeral_user_mode_injects_attribution(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": True, "message_ts": "1.2"}
    slack_api("chat.postEphemeral", {"channel": "C1", "user": "U1", "text": "ping"})

    call_params = mock_webclient.api_call.call_args.kwargs["params"]
    assert json.loads(call_params["blocks"])[-1]["type"] == "context"


def test_slack_api_chat_update_bot_mode_no_attribution(mock_webclient):
    """Bot-token edits stay clean — Atlas is already the author."""
    mock_webclient.api_call.return_value = {"ok": True, "ts": "1.2"}
    slack_api("chat.update", {"channel": "C1", "ts": "1.2", "text": "edited"}, as_bot=True)

    call_params = mock_webclient.api_call.call_args.kwargs["params"]
    assert "blocks" not in call_params


def test_slack_api_files_complete_upload_initial_comment_gets_suffix(mock_webclient):
    """files.completeUploadExternal can't render Block Kit in initial_comment → text suffix."""
    mock_webclient.api_call.return_value = {"ok": True}
    slack_api(
        "files.completeUploadExternal",
        {
            "files": [{"id": "F1", "title": "doc"}],
            "channel_id": "C1",
            "initial_comment": "see attached",
        },
    )

    call_params = mock_webclient.api_call.call_args.kwargs["params"]
    assert "blocks" not in call_params  # suffix path, not blocks path
    assert call_params["initial_comment"].startswith("see attached")
    assert call_params["initial_comment"].endswith(f":blue_heart: <@{ATLAS_BOT_USER_ID}>")


def test_slack_api_files_complete_upload_no_initial_comment_untouched(mock_webclient):
    """If there's no initial_comment, don't fabricate one."""
    mock_webclient.api_call.return_value = {"ok": True}
    slack_api(
        "files.completeUploadExternal",
        {"files": [{"id": "F1", "title": "doc"}], "channel_id": "C1"},
    )

    call_params = mock_webclient.api_call.call_args.kwargs["params"]
    assert "initial_comment" not in call_params
    assert "blocks" not in call_params


def test_slack_api_chat_me_message_user_mode_gets_suffix(mock_webclient):
    """chat.meMessage posts italicized /me text under user token — no blocks support → suffix path."""
    mock_webclient.api_call.return_value = {"ok": True, "ts": "1.2"}
    slack_api("chat.meMessage", {"channel": "C1", "text": "shrugs"})

    call_params = mock_webclient.api_call.call_args.kwargs["params"]
    assert "blocks" not in call_params
    assert call_params["text"].startswith("shrugs")
    assert call_params["text"].endswith(f":blue_heart: <@{ATLAS_BOT_USER_ID}>")


def test_slack_api_chat_me_message_bot_mode_no_suffix(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": True, "ts": "1.2"}
    slack_api("chat.meMessage", {"channel": "C1", "text": "shrugs"}, as_bot=True)

    call_params = mock_webclient.api_call.call_args.kwargs["params"]
    assert call_params["text"] == "shrugs"


def test_slack_api_files_complete_upload_bot_mode_no_suffix(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": True}
    slack_api(
        "files.completeUploadExternal",
        {"files": [{"id": "F1"}], "channel_id": "C1", "initial_comment": "from Atlas"},
        as_bot=True,
    )

    call_params = mock_webclient.api_call.call_args.kwargs["params"]
    assert call_params["initial_comment"] == "from Atlas"


# === v1.2 fix #3 — default limit on history/replies ===

def test_history_without_limit_gets_default_100(mock_webclient):
    """No-limit history calls let Slack serve up to 1000 messages (3× 78–98K-char
    responses in the audit) — the gateway must inject an explicit default."""
    mock_webclient.api_call.return_value = {"ok": True, "messages": []}
    slack_api("conversations.history", {"channel": "C1"})
    assert mock_webclient.api_call.call_args.kwargs["params"]["limit"] == 100


def test_replies_without_limit_gets_default_100(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": True, "messages": []}
    slack_api("conversations.replies", {"channel": "C1", "ts": "1.0"})
    assert mock_webclient.api_call.call_args.kwargs["params"]["limit"] == 100


def test_explicit_limit_preserved(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": True, "messages": []}
    slack_api("conversations.history", {"channel": "C1", "limit": 500})
    assert mock_webclient.api_call.call_args.kwargs["params"]["limit"] == 500


def test_default_limit_not_injected_elsewhere(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": True, "channels": []}
    slack_api("conversations.list", {"types": "public_channel"})
    assert "limit" not in mock_webclient.api_call.call_args.kwargs["params"]


# === v1.2 fix #4 — caller-blocks pre-validation ===

def test_oversized_caller_section_fails_fast_with_hint(mock_webclient):
    """Repro of the 07-30 invalid_blocks incident: caller-supplied section >3000
    chars must fail pre-flight with an actionable hint, not an API 400."""
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "x" * 3500}}]
    with pytest.raises(ToolError) as exc_info:
        slack_api("chat.postMessage", {"channel": "C1", "blocks": blocks}, as_bot=True)
    msg = str(exc_info.value)
    assert "invalid_blocks" in msg
    assert "blocks[0]" in msg
    assert "3500" in msg
    assert "blocks.validate" in msg
    mock_webclient.api_call.assert_not_called()


def test_valid_caller_blocks_pass_validation(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": True, "ts": "1.2"}
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "x" * 3000}},
        {"type": "divider"},
    ]
    slack_api("chat.postMessage", {"channel": "C1", "blocks": blocks}, as_bot=True)
    mock_webclient.api_call.assert_called_once()


def test_blocks_validation_applies_in_user_mode_before_attribution(mock_webclient):
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "y" * 4000}}]
    with pytest.raises(ToolError, match="invalid_blocks"):
        slack_api("chat.update", {"channel": "C1", "ts": "1.0", "blocks": blocks})
    mock_webclient.api_call.assert_not_called()


# === Contract smoke — output-contract.md end-to-end via gateway ===

def test_smoke_message_list_full_text_via_gateway(mock_webclient):
    """The original bug: 80-char truncation. Verifies fix flows through gateway."""
    long = "x" * 1500
    mock_webclient.api_call.return_value = {
        "ok": True,
        "messages": [{"ts": "1.0", "user": "U1", "text": long}],
    }
    result = slack_api("conversations.replies", {"channel": "C1", "ts": "1.0"})
    assert long in result


def test_smoke_args_echo_via_gateway(mock_webclient):
    """List responses lead with the caller's args — original params, not the
    injected ones (limit=100 must not appear in the echo)."""
    mock_webclient.api_call.return_value = {"ok": True, "messages": [{"ts": "1.0", "user": "U1", "text": "hi"}]}
    result = slack_api("conversations.history", {"channel": "C1"})
    assert result.split("\n")[0] == "» conversations.history channel=C1"


def test_smoke_cursor_passes_through_gateway(mock_webclient):
    """response_metadata.next_cursor must reach output, not get stripped silently."""
    mock_webclient.api_call.return_value = {
        "ok": True,
        "messages": [{"ts": "1.0", "user": "U1", "text": "hi"}],
        "response_metadata": {"next_cursor": "abc"},
    }
    result = slack_api("conversations.history", {"channel": "C1"})
    assert "next_cursor=abc" in result


def test_smoke_warning_prefixed_via_gateway(mock_webclient):
    mock_webclient.api_call.return_value = {
        "ok": True,
        "warning": "deprecated_field",
        "channel": {"id": "C1", "name": "x"},
    }
    result = slack_api("conversations.info", {"channel": "C1"})
    assert result.startswith("⚠️ deprecated_field")


def test_smoke_error_hint_via_gateway(mock_webclient):
    """ERROR_HINTS catalogue must trigger when gateway sees ok=false."""
    mock_webclient.api_call.return_value = {"ok": False, "error": "channel_not_found"}
    with pytest.raises(ToolError) as exc_info:
        slack_api("conversations.history", {"channel": "C_BAD"})
    assert "channel_not_found" in str(exc_info.value)
    assert "DM channel IDs are per-viewer" in str(exc_info.value)


def test_smoke_file_upload_permalink_returned(mock_webclient):
    """files.uploadV2 dispatches to WebClient.files_upload_v2 (never api_call —
    UPL-002: it is an SDK method, not a Web API endpoint) and the render
    surfaces the permalink."""
    mock_webclient.files_upload_v2.return_value = {
        "ok": True,
        "files": [{"id": "F1", "name": "doc.pdf",
                   "permalink": "https://x.slack.com/F1"}],
    }
    result = slack_api("files.uploadV2", {"channel": "C1", "content": "x"}, as_bot=True)
    assert "https://x.slack.com/F1" in result
    mock_webclient.api_call.assert_not_called()
    mock_webclient.files_upload_v2.assert_called_once_with(channel="C1", content="x")


def test_smoke_channel_info_topic_purpose_returned(mock_webclient):
    """conversations.info must surface topic/purpose/num_members — not lost in old #name (ID) only."""
    mock_webclient.api_call.return_value = {
        "ok": True,
        "channel": {
            "id": "C1", "name": "team",
            "num_members": 42,
            "topic": {"value": "the topic"},
            "purpose": {"value": "the purpose"},
        },
    }
    result = slack_api("conversations.info", {"channel": "C1"})
    assert "members: 42" in result
    assert "topic: the topic" in result
    assert "purpose: the purpose" in result


def test_smoke_chat_delete_returns_done_despite_payload(mock_webclient):
    """chat.delete returns {channel, ts} — spec routes to Done ✓."""
    mock_webclient.api_call.return_value = {"ok": True, "channel": "C1", "ts": "1.0"}
    result = slack_api("chat.delete", {"channel": "C1", "ts": "1.0"})
    assert result == "Done ✓"


def test_smoke_long_thread_shows_count_marker(mock_webclient):
    """List cap of 200 messages — overflow shows '... and N more'."""
    msgs = [{"ts": f"{i}.0", "user": "U1", "text": f"m{i}"} for i in range(205)]
    mock_webclient.api_call.return_value = {"ok": True, "messages": msgs}
    result = slack_api("conversations.replies", {"channel": "C1", "ts": "1.0"})
    assert "... and 5 more" in result


# === Issue #1 — stream policy & attribution auto-split ===

def test_stream_methods_user_mode_refused(mock_webclient):
    """User-token streams would post recipient-facing text with no attribution point."""
    for method in ("chat.startStream", "chat.appendStream", "chat.stopStream"):
        with pytest.raises(ToolError, match="user_token_stream"):
            slack_api(method, {"channel": "C1"})
    mock_webclient.api_call.assert_not_called()


def test_stream_methods_bot_mode_pass_through(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": True, "ts": "1.2"}
    slack_api("chat.appendStream", {"channel": "C1", "ts": "1.2", "markdown_text": "chunk"}, as_bot=True)
    call_params = mock_webclient.api_call.call_args.kwargs["params"]
    assert "blocks" not in call_params
    assert call_params["markdown_text"] == "chunk"


def test_long_text_auto_split_into_sections(mock_webclient):
    """Attribution wrapping must not turn a >3000-char post into invalid_blocks."""
    mock_webclient.api_call.return_value = {"ok": True, "ts": "1.2"}
    text = "\n".join(f"line {i} " + "x" * 90 for i in range(70))
    slack_api("chat.postMessage", {"channel": "C1", "text": text})
    blocks = json.loads(mock_webclient.api_call.call_args.kwargs["params"]["blocks"])
    sections = [b for b in blocks if b["type"] == "section"]
    assert len(sections) >= 3
    assert all(len(b["text"]["text"]) <= 3000 for b in sections)
    assert blocks[-1]["type"] == "context"
    assert "".join(b["text"]["text"] for b in sections) == text


def test_short_text_stays_single_section(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": True, "ts": "1.2"}
    slack_api("chat.postMessage", {"channel": "C1", "text": "short"})
    blocks = json.loads(mock_webclient.api_call.call_args.kwargs["params"]["blocks"])
    assert [b["type"] for b in blocks] == ["section", "context"]
    assert blocks[0]["text"]["text"] == "short"


def test_unbroken_text_hard_split(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": True, "ts": "1.2"}
    text = "x" * 6500
    slack_api("chat.postMessage", {"channel": "C1", "text": text})
    sections = [b for b in json.loads(mock_webclient.api_call.call_args.kwargs["params"]["blocks"])
                if b["type"] == "section"]
    assert all(len(b["text"]["text"]) <= 3000 for b in sections)
    assert "".join(b["text"]["text"] for b in sections) == text


# --- form transport: nested params as JSON, expand on auto-split (MC ask 2026-10-20) ---

def test_nested_params_are_json_encoded_for_form_transport(mock_webclient):
    """A bool inside blocks used to leave as Python repr -> invalid_blocks_format."""
    mock_webclient.api_call.return_value = {"ok": True, "ts": "1.2"}
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "expand probe"}, "expand": True}]
    slack_api("chat.postMessage", {"channel": "C1", "text": "t", "blocks": blocks}, as_bot=True)

    call_params = mock_webclient.api_call.call_args.kwargs["params"]
    assert isinstance(call_params["blocks"], str)
    assert '"expand": true' in call_params["blocks"]
    assert "True" not in call_params["blocks"]
    assert json.loads(call_params["blocks"]) == blocks
    assert call_params["channel"] == "C1"                     # scalars untouched


def test_dict_params_are_json_encoded_too(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": True}
    slack_api("views.open", {"trigger_id": "T1", "view": {"type": "modal", "submit_disabled": False}}, as_bot=True)
    call_params = mock_webclient.api_call.call_args.kwargs["params"]
    assert json.loads(call_params["view"]) == {"type": "modal", "submit_disabled": False}
    assert call_params["trigger_id"] == "T1"


def test_auto_split_sections_carry_expand(mock_webclient):
    """Long user-token text -> several sections, none folded under 'Show more'."""
    mock_webclient.api_call.return_value = {"ok": True, "ts": "1.2"}
    slack_api("chat.postMessage", {"channel": "C1", "text": "x" * 7000})
    blocks = json.loads(mock_webclient.api_call.call_args.kwargs["params"]["blocks"])
    sections = [b for b in blocks if b["type"] == "section"]
    assert len(sections) >= 3
    assert all(b.get("expand") is True for b in sections)
    assert blocks[-1]["type"] == "context"                     # attribution still last


# === UPL-002 — files.uploadV2 mapped to WebClient.files_upload_v2 ===

def test_upload_v2_channel_string_passes_through(mock_webclient, tmp_path):
    """UPL-001 regression: one destination travels as `channel` (string), never a
    comma-joined string inside `channels` (List[str] → joined char by char)."""
    f = tmp_path / "doc.txt"
    f.write_text("hello")
    mock_webclient.files_upload_v2.return_value = {"ok": True, "file": {"id": "F1", "name": "doc.txt"}}
    slack_api("files.uploadV2", {"file": str(f), "channel": "D06MG7QF3ME", "title": "Doc"}, as_bot=True)
    kw = mock_webclient.files_upload_v2.call_args.kwargs
    assert kw["channel"] == "D06MG7QF3ME"
    assert kw["file"] == str(f)
    assert "channels" not in kw


def test_upload_v2_channels_string_refused(mock_webclient):
    with pytest.raises(ToolError, match="`channels` must be a list"):
        slack_api("files.uploadV2", {"content": "x", "channels": "D06MG7QF3ME"}, as_bot=True)
    mock_webclient.files_upload_v2.assert_not_called()


def test_upload_v2_relative_or_missing_file_refused(mock_webclient, tmp_path):
    with pytest.raises(ToolError, match="absolute path"):
        slack_api("files.uploadV2", {"file": "report.pdf", "channel": "C1"}, as_bot=True)
    with pytest.raises(ToolError, match="file_not_found"):
        slack_api("files.uploadV2", {"file": str(tmp_path / "nope.pdf"), "channel": "C1"}, as_bot=True)
    with pytest.raises(ToolError, match="missing_params"):
        slack_api("files.uploadV2", {"channel": "C1"}, as_bot=True)
    mock_webclient.files_upload_v2.assert_not_called()


def test_upload_v2_user_mode_suffix_on_initial_comment(mock_webclient):
    mock_webclient.files_upload_v2.return_value = {"ok": True, "file": {"id": "F1", "name": "x"}}
    slack_api("files.uploadV2", {"content": "x", "channel": "C1", "initial_comment": "see attached"})
    kw = mock_webclient.files_upload_v2.call_args.kwargs
    assert kw["initial_comment"].startswith("see attached")
    assert kw["initial_comment"].endswith(f":blue_heart: <@{ATLAS_BOT_USER_ID}>")
    assert "attribution" not in kw
    mock_webclient.api_call.assert_not_called()


def test_upload_v2_bot_mode_untouched(mock_webclient):
    mock_webclient.files_upload_v2.return_value = {"ok": True, "file": {"id": "F1", "name": "x"}}
    slack_api(
        "files.uploadV2",
        {"content": "x", "channel": "C1", "initial_comment": "from Atlas", "attribution": "block"},
        as_bot=True,
    )
    kw = mock_webclient.files_upload_v2.call_args.kwargs
    assert kw["initial_comment"] == "from Atlas"
    assert "attribution" not in kw          # never forwarded to the SDK
    mock_webclient.api_call.assert_not_called()   # bot: no second message


def test_upload_v2_sdk_error_is_structural(mock_webclient):
    mock_webclient.files_upload_v2.side_effect = SlackApiError("boom", {"ok": False, "error": "not_in_channel"})
    with pytest.raises(ToolError, match="not_in_channel"):
        slack_api("files.uploadV2", {"content": "x", "channel": "C1"}, as_bot=True)


def test_upload_v2_file_uploads_paths_checked(mock_webclient, tmp_path):
    ok = tmp_path / "a.txt"
    ok.write_text("a")
    with pytest.raises(ToolError, match=r"file_uploads\[1\]\.file.*absolute"):
        slack_api("files.uploadV2", {"channel": "C1", "file_uploads": [
            {"file": str(ok)}, {"file": "rel.txt"}]}, as_bot=True)
    mock_webclient.files_upload_v2.assert_not_called()

    mock_webclient.files_upload_v2.return_value = {"ok": True, "files": [
        {"id": "F1", "name": "a.txt"}, {"id": "F2", "name": "b"}]}
    slack_api("files.uploadV2", {"channel": "C1", "file_uploads": [
        {"file": str(ok), "title": "A"}, {"content": "b", "filename": "b"}]}, as_bot=True)
    kw = mock_webclient.files_upload_v2.call_args.kwargs
    assert kw["file_uploads"][0]["title"] == "A"
    assert kw["file_uploads"][1] == {"content": "b", "filename": "b"}


# === v1.3 (MC ask 2026-10-22) — bot auto-split, _raw flag ===

def test_bot_long_text_auto_split_no_attribution(mock_webclient):
    """as_bot=True + text > 3000 + no blocks → section blocks (expand), no context
    block, text kept as fallback, concatenation reproduces the input."""
    mock_webclient.api_call.return_value = {"ok": True, "ts": "1.0", "channel": "C1"}
    text = "\n".join(f"line {i} " + "x" * 80 for i in range(110))   # ~9000 chars
    assert len(text) > 2 * SECTION_TEXT_LIMIT
    slack_api("chat.postMessage", {"channel": "C1", "text": text}, as_bot=True)
    sent = mock_webclient.api_call.call_args.kwargs["params"]
    blocks = json.loads(sent["blocks"])
    assert sent["text"] == text
    assert all(b["type"] == "section" and b["expand"] is True for b in blocks)
    assert all(len(b["text"]["text"]) <= SECTION_TEXT_LIMIT for b in blocks)
    assert "".join(b["text"]["text"] for b in blocks) == text
    assert len(blocks) >= 3


def test_bot_short_text_and_caller_blocks_untouched(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": True, "ts": "1.0", "channel": "C1"}
    slack_api("chat.postMessage", {"channel": "C1", "text": "short"}, as_bot=True)
    assert "blocks" not in mock_webclient.api_call.call_args.kwargs["params"]

    own = [{"type": "section", "text": {"type": "mrkdwn", "text": "mine"}}]
    slack_api("chat.postMessage", {"channel": "C1", "text": "x" * 5000, "blocks": own}, as_bot=True)
    assert json.loads(mock_webclient.api_call.call_args.kwargs["params"]["blocks"]) == own

    slack_api("chat.postEphemeral", {"channel": "C1", "user": "U1", "text": "x" * 5000}, as_bot=True)
    assert "blocks" not in mock_webclient.api_call.call_args.kwargs["params"]


def test_raw_flag_returns_json_and_is_stripped(mock_webclient):
    mock_webclient.api_call.return_value = {
        "ok": True,
        "messages": [{"ts": "1.0", "user": "U1", "text": "hi",
                      "blocks": [{"type": "section", "expand": True,
                                  "text": {"type": "mrkdwn", "text": "hi"}}]}],
    }
    out = slack_api("conversations.history", {"channel": "C1", "limit": 1, "_raw": True})
    sent = mock_webclient.api_call.call_args.kwargs["params"]
    assert "_raw" not in sent and sent["limit"] == 1
    data = json.loads(out)
    assert data["messages"][0]["blocks"][0]["expand"] is True
    assert not out.startswith("»")   # no args-echo in raw mode


def test_raw_flag_on_error_still_structural(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": False, "error": "channel_not_found"}
    with pytest.raises(ToolError, match="channel_not_found"):
        slack_api("conversations.history", {"channel": "C1", "_raw": True})


def test_upload_v2_block_attribution_refused_outside_the_core(mock_webclient):
    """v2 core slice: attribution='block' would post its comment as a chat.postMessage
    outside the core (no decision, no journal intent) → refused before the upload.
    Replaces the three v1.3 block-mode tests (separate post, needs channel, comment failure)."""
    with pytest.raises(ToolError, match="composite_not_in_slice"):
        slack_api("files.uploadV2", {"content": "x", "channel": "C1", "initial_comment": "c",
                                     "attribution": "block"})
    mock_webclient.files_upload_v2.assert_not_called()
    mock_webclient.api_call.assert_not_called()
