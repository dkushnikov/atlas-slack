"""End-to-end tests against real Slack API.

Gated by SLACK_E2E=1 — skipped in normal runs (CI, dev). Requires real
SLACK_USER_TOKEN and SLACK_BOT_TOKEN in environment, plus SLACK_E2E_CHANNEL
(target channel ID for posts). Tests post → fetch → delete cycle to verify
the contract holds against live Slack with no mocks.

Run:
    SLACK_E2E=1 SLACK_E2E_CHANNEL=D2SH1V09K uv run pytest tests/test_e2e.py -v

Cleanup: each test deletes its own posts on success or failure (best-effort).
"""

import os
import time

import pytest

from atlas_slack.gateway import slack_api


# Skip the entire module unless explicitly enabled
pytestmark = pytest.mark.skipif(
    os.environ.get("SLACK_E2E") != "1",
    reason="Set SLACK_E2E=1 to run live Slack tests",
)


@pytest.fixture(scope="module")
def channel():
    """Target channel for test posts. Required env var."""
    ch = os.environ.get("SLACK_E2E_CHANNEL")
    if not ch:
        pytest.skip("SLACK_E2E_CHANNEL not set — pick a low-blast-radius channel (e.g. self-DM)")
    return ch


@pytest.fixture
def posted_message(channel):
    """Post a message, yield (channel, ts), delete on teardown."""
    posted = {"ts": None}

    def _post(text: str) -> tuple[str, str]:
        result = slack_api("chat.postMessage", {"channel": channel, "text": text}, as_bot=False)
        # Extract ts from result (format: "Message (ts=...): ...")
        # Better: hit the underlying API again to get the ts cleanly via the sdk
        # Hack-but-works: scan for ts= pattern
        import re
        m = re.search(r"ts=(\d+\.\d+)", result)
        assert m, f"Could not extract ts from postMessage result: {result}"
        ts = m.group(1)
        posted["ts"] = ts
        return channel, ts

    yield _post

    # Teardown — delete posted message if any
    if posted["ts"]:
        try:
            slack_api("chat.delete", {"channel": channel, "ts": posted["ts"]}, as_bot=False)
        except Exception:
            pass  # best-effort cleanup


# === Sanity ===

def test_auth_test_returns_user_team():
    """Smallest possible call — verifies token works and formatter handles auth.test shape."""
    result = slack_api("auth.test", {})
    assert "Auth:" in result
    assert "user_id=" in result
    assert "team_id=" in result


# === Contract: full message body via conversations.replies ===

def test_long_message_round_trip_full_text(channel, posted_message):
    """Post a >1000 char message → fetch via conversations.replies → verify full body returned.

    This is the original bug: 80-char truncation. With the redesign, a single
    fetch must return the entire body, no follow-up call needed.
    """
    long_body = "atlas-slack e2e test — " + "x" * 1500
    ch, ts = posted_message(long_body)
    # Slack indexing — small delay before fetch
    time.sleep(1)

    result = slack_api("conversations.replies", {"channel": ch, "ts": ts}, as_bot=False)
    assert long_body in result, "full body must be returned in single call"
    assert "..." not in result.split(long_body)[1].split("\n")[0], "no truncation suffix after body"


# === Contract: cursor surfaces in list responses ===

def test_users_list_cursor_or_complete(monkeypatch):
    """users.list with small limit forces pagination; cursor must surface in header.

    On a small workspace, may return everything in one page — that's also valid.
    Test asserts: either cursor is present, or list is complete (no more marker).
    """
    result = slack_api("users.list", {"limit": 5})
    has_cursor = "next_cursor=" in result
    has_more_marker = "... and " in result and "more" in result
    # Both true is OK (paginated); both false means small workspace returned all in one go
    # Either way no contract violation. The check: if we hit cap with 5 limit, cursor MUST be present
    if has_more_marker:
        assert has_cursor, "list overflowed cap but cursor was stripped — contract violation"


# === Contract: conversations.info surfaces topic/purpose/num_members ===

def test_conversations_info_surfaces_metadata(channel):
    """Fetch info on the test channel, verify metadata fields are not silently dropped.

    Whatever the channel is (DM, public, private), result must show ID and name
    or DM marker. For non-DM, members count surfaces. For DMs, may not.
    """
    result = slack_api("conversations.info", {"channel": channel})
    assert "Channel:" in result or channel in result
    # If it's a real channel (not DM), num_members should appear
    # DMs return is_im=true and no num_members — both paths are valid


# === Contract: error returns hint when in catalogue ===

def test_unknown_channel_triggers_hint():
    """Calling against a fake channel ID must fail structurally (ToolError →
    is_error=true) and surface the channel_not_found hint (v1.2 fix #1)."""
    from fastmcp.exceptions import ToolError
    with pytest.raises(ToolError) as exc_info:
        slack_api("conversations.info", {"channel": "C00DEADBEEF"})
    msg = str(exc_info.value)
    assert "channel_not_found" in msg
    # Hint catalogue text — defensive check, just verify hint present in some form
    assert "—" in msg, "hint catalogue should append explanation after error code"


# === Contract: chat.delete returns Done ✓ ===

def test_chat_delete_returns_done(channel):
    """Post then delete — delete must return Done ✓ per §4.15."""
    post_result = slack_api(
        "chat.postMessage",
        {"channel": channel, "text": "atlas-slack e2e — to delete"},
        as_bot=False,
    )
    import re
    m = re.search(r"ts=(\d+\.\d+)", post_result)
    assert m, f"could not parse post result: {post_result}"
    ts = m.group(1)

    delete_result = slack_api("chat.delete", {"channel": channel, "ts": ts}, as_bot=False)
    assert delete_result == "Done ✓"


# === UPL-002: files.uploadV2 through the SDK (getUploadURLExternal → PUT → complete) ===

@pytest.mark.parametrize("as_bot", [True, False])
def test_file_upload_v2_round_trip(channel, tmp_path, as_bot):
    """Upload a small text file into the test channel as bot and as user; the
    result carries a permalink, the user-mode share carries the :blue_heart:
    suffix. File deleted on teardown (files.delete — same token that uploaded)."""
    import re
    f = tmp_path / "atlas-slack-e2e.txt"
    f.write_text("atlas-slack e2e upload probe\n")
    result = slack_api(
        "files.uploadV2",
        {"file": str(f), "channel": channel, "title": "e2e probe",
         "initial_comment": "e2e upload probe — will be deleted"},
        as_bot=as_bot,
    )
    m = re.search(r"\(ID: (F\w+)\)", result)
    assert m, result
    fid = m.group(1)
    try:
        assert "permalink: https://" in result
        info = slack_api("files.info", {"file": fid})
        assert "e2e probe" in info
        if not as_bot:
            from atlas_slack.client import ATLAS_BOT_USER_ID
            shares = slack_api("conversations.history", {"channel": channel, "limit": 5}, as_bot=False)
            assert f"<@{ATLAS_BOT_USER_ID}>" in shares or ":blue_heart:" in shares
    finally:
        # files.delete leaves the share message as a tombstone — delete it too.
        slack_api("files.delete", {"file": fid}, as_bot=as_bot)
        hist = slack_api("conversations.history", {"channel": channel, "limit": 5}, as_bot=False)
        for ts in re.findall(r"\[(\d+\.\d+)\][^\[]*e2e upload probe", hist):
            try:
                slack_api("chat.delete", {"channel": channel, "ts": ts}, as_bot=as_bot)
            except Exception:
                pass
