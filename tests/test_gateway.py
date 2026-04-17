"""Tests for the generic gateway tool."""

from atlas_slack.gateway import slack_api
from atlas_slack.client import ATLAS_BOT_USER_ID


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


def test_slack_api_error(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": False, "error": "not_authed"}
    result = slack_api("chat.postMessage", {"channel": "C1", "text": "hi"})
    assert "not_authed" in result


def test_slack_api_exception(mock_webclient):
    mock_webclient.api_call.side_effect = Exception("connection timeout")
    result = slack_api("users.list")
    assert "connection timeout" in result


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
    blocks = call_params["blocks"]
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
    blocks = call_params["blocks"]
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
    blocks = call_params["blocks"]
    assert len(blocks) == 2
    assert blocks[1]["type"] == "context"
    assert blocks[1]["elements"][0]["text"] == f":blue_heart: <@{ATLAS_BOT_USER_ID}>"


def test_slack_api_chat_schedule_message_user_mode_injects_attribution(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": True, "scheduled_message_id": "Q1"}
    slack_api("chat.scheduleMessage", {"channel": "C1", "post_at": 9999999, "text": "later"})

    call_params = mock_webclient.api_call.call_args.kwargs["params"]
    assert call_params["blocks"][-1]["type"] == "context"
    assert call_params["blocks"][-1]["elements"][0]["text"] == f":blue_heart: <@{ATLAS_BOT_USER_ID}>"


def test_slack_api_chat_post_ephemeral_user_mode_injects_attribution(mock_webclient):
    mock_webclient.api_call.return_value = {"ok": True, "message_ts": "1.2"}
    slack_api("chat.postEphemeral", {"channel": "C1", "user": "U1", "text": "ping"})

    call_params = mock_webclient.api_call.call_args.kwargs["params"]
    assert call_params["blocks"][-1]["type"] == "context"


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
