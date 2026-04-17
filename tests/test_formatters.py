"""Tests for response formatters."""

from atlas_slack.formatters import format_response, format_error


def test_format_error_string():
    assert format_error("not_in_channel", "You are not in this channel") == (
        "Error: not_in_channel — You are not in this channel"
    )


def test_format_response_simple_ok():
    resp = {"ok": True, "channel": "C123"}
    result = format_response("conversations.create", resp)
    assert "C123" in result
    assert "ok" not in result.lower() or "Error" not in result


def test_format_response_with_message():
    resp = {"ok": True, "message": {"text": "hello", "ts": "123.456"}}
    result = format_response("chat.postMessage", resp)
    assert "hello" in result


def test_format_response_with_items_list():
    resp = {"ok": True, "channels": [
        {"id": "C1", "name": "general"},
        {"id": "C2", "name": "random"},
    ]}
    result = format_response("conversations.list", resp)
    assert "general" in result
    assert "random" in result


def test_format_response_reaction_added():
    resp = {"ok": True}
    result = format_response("reactions.add", resp)
    assert "ok" in result.lower() or "success" in result.lower() or "added" in result.lower() or "Done" in result or "✓" in result


def test_format_response_unknown_shape_falls_back_to_json():
    resp = {"ok": True, "weird_field": {"nested": True}}
    result = format_response("some.unknown.method", resp)
    assert "weird_field" in result


def test_format_error_from_response():
    resp = {"ok": False, "error": "channel_not_found"}
    result = format_response("conversations.info", resp)
    assert "channel_not_found" in result
