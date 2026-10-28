"""Unit tests for atlas_slack.formatters per docs/output-contract.md.

Organized as one class per §4 shape. Each class verifies:
  - Required fields preserved
  - Truncation rules per category (§3, §6)
  - List caps (§7)
  - Cursor & warning passthrough (§8)
  - Error hints (§10)

Fixture style: response dicts mirror real Slack API shapes.
"""

from atlas_slack.formatters import (
    format_response,
    format_error,
    ERROR_HINTS,
    LIST_CAPS,
    LABEL_CAP,
)


# === §10 errors & hints ===

class TestErrors:
    def test_explicit_detail_overrides_hint(self):
        assert format_error("not_in_channel", "Custom detail") == (
            "Error: not_in_channel — Custom detail"
        )

    def test_known_code_picks_up_hint(self):
        result = format_error("channel_not_found")
        assert "channel_not_found" in result
        assert "DM channel IDs are per-viewer" in result

    def test_unknown_code_no_hint(self):
        result = format_error("custom_xyz")
        assert result == "Error: custom_xyz"

    def test_response_with_ok_false_uses_hint(self):
        resp = {"ok": False, "error": "channel_not_found"}
        result = format_response("conversations.info", resp)
        assert "channel_not_found" in result
        assert "DM channel IDs are per-viewer" in result

    def test_unknown_error_falls_back(self):
        resp = {"ok": False, "error": "weird_thing"}
        result = format_response("any.method", resp)
        assert result == "Error: weird_thing"

    def test_hint_catalogue_contents(self):
        # 5 starters (§10) + invalid_blocks (issue #1, 2026-08-21)
        assert set(ERROR_HINTS) == {
            "channel_not_found", "not_in_channel",
            "missing_scope", "cannot_dm_bot", "is_archived",
            "invalid_blocks",
        }


# === §4.1 single message ===

class TestSingleMessage:
    def test_basic(self):
        resp = {"ok": True, "channel": "C1", "ts": "1.2",
                "message": {"text": "hello", "ts": "1.2"}}
        result = format_response("chat.postMessage", resp)
        assert "hello" in result
        assert "ts=1.2" in result

    def test_long_text_never_truncated(self):
        long = "x" * 5000
        resp = {"ok": True, "channel": "C1",
                "message": {"text": long, "ts": "1.2"}}
        result = format_response("chat.postMessage", resp)
        assert long in result

    def test_thread_ts_shown_when_reply(self):
        resp = {"ok": True, "channel": "C1",
                "message": {"text": "reply", "ts": "2.0", "thread_ts": "1.0"}}
        result = format_response("chat.postMessage", resp)
        assert "thread_ts=1.0" in result

    def test_thread_ts_hidden_when_parent(self):
        resp = {"ok": True, "channel": "C1",
                "message": {"text": "parent", "ts": "1.0", "thread_ts": "1.0"}}
        result = format_response("chat.postMessage", resp)
        assert "thread_ts=1.0" not in result

    def test_channel_echo(self):
        resp = {"ok": True, "channel": "C1",
                "message": {"text": "x", "ts": "1.2"}}
        result = format_response("chat.postMessage", resp)
        assert "channel: C1" in result


# === §4.2 message list ===

class TestMessageList:
    def test_full_text_preserved(self):
        long = "y" * 1500
        resp = {"ok": True, "messages": [{"ts": "1.0", "user": "U1", "text": long}]}
        result = format_response("conversations.replies", resp)
        assert long in result
        assert "..." not in result

    def test_cursor_in_header(self):
        resp = {
            "ok": True,
            "messages": [{"ts": "1.0", "user": "U1", "text": "hi"}],
            "response_metadata": {"next_cursor": "abc123"},
        }
        result = format_response("conversations.history", resp)
        assert "next_cursor=abc123" in result.split("\n")[0]

    def test_no_cursor_when_empty(self):
        resp = {
            "ok": True,
            "messages": [{"ts": "1.0", "user": "U1", "text": "hi"}],
            "response_metadata": {"next_cursor": ""},
        }
        result = format_response("conversations.history", resp)
        assert "next_cursor" not in result

    def test_reactions_aggregated(self):
        resp = {"ok": True, "messages": [{
            "ts": "1.0", "user": "U1", "text": "x",
            "reactions": [
                {"name": "thumbsup", "count": 3},
                {"name": "heart", "count": 1},
            ],
        }]}
        result = format_response("conversations.replies", resp)
        assert ":thumbsup:3" in result
        assert ":heart:1" in result

    def test_no_reactions_section_when_empty(self):
        resp = {"ok": True, "messages": [{"ts": "1.0", "user": "U1", "text": "x"}]}
        result = format_response("conversations.replies", resp)
        # No reactions key → no reactions output line content
        assert ":" not in result.split("[1.0]")[1].split("\n")[0]

    def test_replies_count_for_parent(self):
        resp = {"ok": True, "messages": [{
            "ts": "1.0", "user": "U1", "text": "parent",
            "thread_ts": "1.0", "reply_count": 7,
        }]}
        result = format_response("conversations.replies", resp)
        assert "replies=7" in result
        assert "thread_ts=" not in result  # parent, not reply

    def test_thread_ts_for_reply(self):
        resp = {"ok": True, "messages": [{
            "ts": "2.0", "user": "U1", "text": "reply",
            "thread_ts": "1.0",
        }]}
        result = format_response("conversations.replies", resp)
        assert "thread_ts=1.0" in result

    def test_list_cap_truncates_with_more_marker(self):
        cap = LIST_CAPS["messages"]
        msgs = [{"ts": f"{i}.0", "user": "U1", "text": f"msg{i}"} for i in range(cap + 5)]
        resp = {"ok": True, "messages": msgs}
        result = format_response("conversations.history", resp)
        assert f"... and 5 more" in result

    def test_search_messages_uses_same_shape(self):
        resp = {"ok": True, "messages": [{"ts": "1.0", "user": "U1", "text": "hit"}]}
        result = format_response("search.messages", resp)
        assert "hit" in result
        assert "[1.0]" in result


# === §4.3 channel info ===

class TestChannelInfo:
    def test_basic(self):
        resp = {"ok": True, "channel": {
            "id": "C1", "name": "general", "is_general": True,
            "num_members": 423,
            "topic": {"value": "company-wide"},
            "purpose": {"value": "all hands"},
        }}
        result = format_response("conversations.info", resp)
        assert "#general" in result
        assert "C1" in result
        assert "general" in result.split("\n")[0]  # flag in header
        assert "members: 423" in result
        assert "topic: company-wide" in result
        assert "purpose: all hands" in result

    def test_topic_full_no_cap(self):
        long_topic = "t" * 5000
        resp = {"ok": True, "channel": {
            "id": "C1", "name": "x",
            "topic": {"value": long_topic},
        }}
        result = format_response("conversations.info", resp)
        assert long_topic in result

    def test_string_channel_falls_through(self):
        resp = {"ok": True, "channel": "C123"}
        result = format_response("conversations.create", resp)
        assert "C123" in result

    def test_private_archived_flags(self):
        resp = {"ok": True, "channel": {
            "id": "C1", "name": "x",
            "is_private": True, "is_archived": True,
        }}
        result = format_response("conversations.info", resp)
        assert "private" in result
        assert "archived" in result

    def test_general_flag_only_when_true(self):
        resp = {"ok": True, "channel": {"id": "C1", "name": "x", "is_general": False}}
        result = format_response("conversations.info", resp)
        # The literal token 'general' should not appear in flag position
        first_line = result.split("\n")[0]
        assert " general" not in first_line

    # --- v1.2: unread state (was whitelisted away — unread-blindness 2026-09-01) ---

    def test_unread_fields_rendered(self):
        resp = {"ok": True, "channel": {
            "id": "D2SH1V09K", "is_im": True, "user": "U123",
            "last_read": "1756713600.000100",
            "unread_count": 7, "unread_count_display": 5,
        }}
        result = format_response("conversations.info", resp)
        assert "last_read: 1756713600.000100" in result
        assert "unread: 7 (5 display)" in result

    def test_unread_display_omitted_when_equal(self):
        resp = {"ok": True, "channel": {
            "id": "C1", "name": "x", "unread_count": 3, "unread_count_display": 3,
        }}
        result = format_response("conversations.info", resp)
        assert "unread: 3" in result
        assert "display" not in result

    def test_unread_zero_still_shown(self):
        """unread_count=0 is information (all read), not absence — must render."""
        resp = {"ok": True, "channel": {"id": "C1", "name": "x", "unread_count": 0}}
        result = format_response("conversations.info", resp)
        assert "unread: 0" in result

    def test_no_unread_fields_no_lines(self):
        resp = {"ok": True, "channel": {"id": "C1", "name": "x"}}
        result = format_response("conversations.info", resp)
        assert "unread" not in result
        assert "last_read" not in result

    def test_im_renders_dm_with_user(self):
        """DMs have no name — header shows the counterpart, not '#?' (v1.2)."""
        resp = {"ok": True, "channel": {"id": "D2SH1V09K", "is_im": True, "user": "U123"}}
        result = format_response("conversations.info", resp)
        assert result.startswith("Channel: DM with U123 (ID: D2SH1V09K)")
        assert "#?" not in result


# === v1.2 §8 — args echo on list shapes ===

class TestArgsEcho:
    def test_members_echo_channel(self):
        """The 04-28 confusion class: '12 members:' — of which channel?"""
        resp = {"ok": True, "members": ["U1", "U2"]}
        result = format_response("conversations.members", resp, req_params={"channel": "C123"})
        assert result.split("\n")[0] == "» conversations.members channel=C123"
        assert "2 members" in result

    def test_history_echo_channel(self):
        resp = {"ok": True, "messages": [{"ts": "1.0", "user": "U1", "text": "hi"}]}
        result = format_response("conversations.history", resp, req_params={"channel": "C9", "limit": 100})
        assert result.split("\n")[0] == "» conversations.history channel=C9"

    def test_search_echo_query(self):
        resp = {"ok": True, "messages": {"total": 0, "matches": []}}
        result = format_response("search.messages", resp, req_params={"query": "deploy in:incidents"})
        assert result.split("\n")[0] == "» search.messages query=deploy in:incidents"

    def test_no_req_params_no_echo(self):
        resp = {"ok": True, "members": ["U1"]}
        result = format_response("conversations.members", resp)
        assert not result.startswith("»")

    def test_single_shape_no_echo(self):
        """Single-entity shapes self-identify — no echo line."""
        resp = {"ok": True, "channel": {"id": "C1", "name": "x"}}
        result = format_response("conversations.info", resp, req_params={"channel": "C1"})
        assert "»" not in result

    def test_echo_without_disambiguating_keys_shows_method(self):
        resp = {"ok": True, "members": [{"id": "U1", "name": "a"}]}
        result = format_response("users.list", resp, req_params={"limit": 10})
        assert result.split("\n")[0] == "» users.list"

    def test_warning_stays_before_echo(self):
        resp = {"ok": True, "warning": "deprecated", "members": ["U1"]}
        result = format_response("conversations.members", resp, req_params={"channel": "C1"})
        lines = result.split("\n")
        assert lines[0].startswith("⚠️")
        assert lines[1] == "» conversations.members channel=C1"


# === §4.4 channel list ===

class TestChannelList:
    def test_basic(self):
        resp = {"ok": True, "channels": [
            {"id": "C1", "name": "general", "num_members": 100},
            {"id": "C2", "name": "random", "is_private": True},
        ]}
        result = format_response("conversations.list", resp)
        assert "#general" in result
        assert "#random" in result
        assert "private" in result
        assert "(100)" in result

    def test_cap_100(self):
        cap = LIST_CAPS["channels"]
        ch = [{"id": f"C{i}", "name": f"ch{i}"} for i in range(cap + 10)]
        resp = {"ok": True, "channels": ch}
        result = format_response("conversations.list", resp)
        assert "... and 10 more" in result

    def test_long_name_truncates(self):
        long = "n" * (LABEL_CAP + 50)
        resp = {"ok": True, "channels": [{"id": "C1", "name": long}]}
        result = format_response("conversations.list", resp)
        assert "..." in result

    def test_cursor_passed(self):
        resp = {"ok": True, "channels": [{"id": "C1", "name": "x"}],
                "response_metadata": {"next_cursor": "page2"}}
        result = format_response("conversations.list", resp)
        assert "next_cursor=page2" in result

    def test_conversations_members_user_id_strings(self):
        resp = {"ok": True, "members": ["U1", "U2", "U3"]}
        result = format_response("conversations.members", resp)
        assert "U1" in result
        assert "U2" in result


# === §4.5 user info ===

class TestUserInfo:
    def test_users_info(self):
        resp = {"ok": True, "user": {
            "id": "U1", "real_name": "Alice", "tz": "America/Los_Angeles",
            "profile": {
                "display_name": "alice", "email": "a@x.com", "title": "Eng",
                "status_emoji": ":coffee:", "status_text": "brewing",
                "image_72": "https://x.com/a.png",
            },
        }}
        result = format_response("users.info", resp)
        assert "Alice" in result
        assert "U1" in result
        assert "display_name: alice" in result
        assert "email: a@x.com" in result
        assert "title: Eng" in result
        assert "tz: America/Los_Angeles" in result
        assert ":coffee:" in result
        assert "brewing" in result
        assert "https://x.com/a.png" in result

    def test_profile_get_no_user_wrapper(self):
        resp = {"ok": True, "profile": {
            "real_name": "Bob", "email": "b@x.com",
        }}
        result = format_response("users.profile.get", resp)
        assert "Bob" in result
        assert "email: b@x.com" in result

    def test_bot_flag(self):
        resp = {"ok": True, "user": {
            "id": "B1", "real_name": "Atlas", "is_bot": True, "profile": {},
        }}
        result = format_response("users.info", resp)
        assert "bot" in result

    def test_real_name_fallback_to_name(self):
        # System users sometimes have only `name`
        resp = {"ok": True, "user": {"id": "U1", "name": "slackbot", "profile": {}}}
        result = format_response("users.info", resp)
        assert "slackbot" in result


# === §4.6 profile set ===

class TestProfileSet:
    def test_diff(self):
        resp = {"ok": True, "profile": {
            "status_emoji": ":calendar:", "status_text": "in mtg",
        }}
        result = format_response("users.profile.set", resp)
        assert "Profile updated" in result
        assert ":calendar:" in result
        assert "in mtg" in result


# === §4.7 user list ===

class TestUserList:
    def test_basic(self):
        resp = {"ok": True, "members": [
            {"id": "U1", "real_name": "Alice", "profile": {"email": "a@x.com"}},
            {"id": "U2", "real_name": "Bob"},
        ]}
        result = format_response("users.list", resp)
        assert "Alice" in result
        assert "Bob" in result
        assert "<a@x.com>" in result

    def test_name_fallback(self):
        resp = {"ok": True, "members": [{"id": "U1", "name": "alice", "profile": {}}]}
        result = format_response("users.list", resp)
        assert "alice" in result

    def test_cap_50(self):
        cap = LIST_CAPS["users"]
        members = [{"id": f"U{i}", "real_name": f"User{i}"} for i in range(cap + 3)]
        resp = {"ok": True, "members": members}
        result = format_response("users.list", resp)
        assert "... and 3 more" in result


# === §4.8 file single ===

class TestFileSingle:
    def test_permalink_first_after_name(self):
        resp = {"ok": True, "file": {
            "id": "F1", "name": "report.pdf",
            "permalink": "https://slack.com/files/F1",
            "filetype": "pdf", "size": 4096,
        }}
        result = format_response("files.info", resp)
        lines = result.split("\n")
        assert "report.pdf" in lines[0]
        assert "permalink:" in lines[1]

    def test_human_size(self):
        resp = {"ok": True, "file": {"id": "F1", "name": "x", "size": 1536}}
        result = format_response("files.info", resp)
        assert "1.5KB" in result

    def test_uploadv2_single_file(self):
        resp = {"ok": True, "files": [{
            "id": "F1", "name": "img.png", "permalink": "https://x.com/F1",
        }]}
        result = format_response("files.uploadV2", resp)
        assert "img.png" in result
        assert "https://x.com/F1" in result


# === §4.9 file list ===

class TestFileList:
    def test_permalink_shown(self):
        resp = {"ok": True, "files": [
            {"id": "F1", "name": "a.txt", "permalink": "https://x.com/F1"},
            {"id": "F2", "name": "b.txt", "permalink": "https://x.com/F2"},
        ]}
        result = format_response("files.list", resp)
        assert "https://x.com/F1" in result
        assert "https://x.com/F2" in result

    def test_cap_20(self):
        cap = LIST_CAPS["files"]
        files = [{"id": f"F{i}", "name": f"f{i}.txt"} for i in range(cap + 2)]
        resp = {"ok": True, "files": files}
        result = format_response("files.list", resp)
        assert "... and 2 more" in result


# === §4.10 reminder ===

class TestReminder:
    def test_single(self):
        resp = {"ok": True, "reminder": {
            "id": "Rm1", "text": "drink water",
            "time": 1714483200, "recurring": False,
        }}
        result = format_response("reminders.add", resp)
        assert "Rm1" in result
        assert "drink water" in result
        assert "recurring: False" in result

    def test_list(self):
        resp = {"ok": True, "reminders": [
            {"id": "R1", "text": "task A"},
            {"id": "R2", "text": "task B"},
        ]}
        result = format_response("reminders.list", resp)
        assert "task A" in result
        assert "task B" in result


# === §4.11 usergroup ===

class TestUsergroup:
    def test_single_create(self):
        resp = {"ok": True, "usergroup": {
            "id": "S1", "handle": "team-eng", "name": "Engineering",
            "users": ["U1", "U2", "U3"],
        }}
        result = format_response("usergroups.create", resp)
        assert "@team-eng" in result
        assert "Engineering" in result
        assert "3 members" in result

    def test_list(self):
        resp = {"ok": True, "usergroups": [
            {"id": "S1", "handle": "eng", "name": "Eng", "users": ["U1"]},
            {"id": "S2", "handle": "ops", "name": "Ops", "users": []},
        ]}
        result = format_response("usergroups.list", resp)
        assert "@eng" in result
        assert "@ops" in result


# === §4.12 pin / star list ===

class TestPinList:
    def test_message_text_full(self):
        long_text = "p" * 1000
        resp = {"ok": True, "items": [{
            "type": "message", "channel": "C1",
            "message": {"ts": "1.0", "user": "U1", "text": long_text},
        }]}
        result = format_response("pins.list", resp)
        assert long_text in result

    def test_stars_list_same_shape(self):
        resp = {"ok": True, "items": [{
            "type": "message", "channel": "C1",
            "message": {"ts": "1.0", "user": "U1", "text": "starred"},
        }]}
        result = format_response("stars.list", resp)
        assert "starred" in result


# === §4.13 permalink ===

class TestPermalink:
    def test_basic(self):
        resp = {"ok": True, "permalink": "https://x.slack.com/p1", "channel": "C1"}
        result = format_response("chat.getPermalink", resp)
        assert "https://x.slack.com/p1" in result
        assert "C1" in result


# === §4.14 auth / team ===

class TestAuthTeam:
    def test_auth_test(self):
        resp = {"ok": True, "url": "https://x.slack.com/", "team": "Acme",
                "user": "owner", "team_id": "T1", "user_id": "U1"}
        result = format_response("auth.test", resp)
        assert "owner@Acme" in result
        assert "T1" in result
        assert "U1" in result

    def test_team_info(self):
        resp = {"ok": True, "team": {
            "id": "T1", "name": "Acme", "domain": "acme",
            "email_domain": "acme.com",
        }}
        result = format_response("team.info", resp)
        assert "Team: Acme" in result
        assert "domain: acme" in result
        assert "email_domain: acme.com" in result


# === §4.15 empty success ===

class TestEmptySuccess:
    def test_reaction_add_done(self):
        resp = {"ok": True}
        assert format_response("reactions.add", resp) == "Done ✓"

    def test_chat_delete_with_payload_still_done(self):
        # chat.delete returns {ok, channel, ts} but spec routes to Done ✓
        resp = {"ok": True, "channel": "C1", "ts": "1.0"}
        assert format_response("chat.delete", resp) == "Done ✓"

    def test_conversations_invite_done(self):
        # invite returns {ok, channel: {...}} but spec routes to Done ✓
        resp = {"ok": True, "channel": {"id": "C1", "name": "x"}}
        assert format_response("conversations.invite", resp) == "Done ✓"

    def test_dnd_set_done(self):
        resp = {"ok": True, "snooze_enabled": True}
        assert format_response("dnd.setSnooze", resp) == "Done ✓"


# === §4.16 unknown shape JSON fallback ===

class TestJsonFallback:
    def test_unknown_shape_dumps_json(self):
        resp = {"ok": True, "weird": {"nested": True}}
        result = format_response("some.unknown.method", resp)
        assert "weird" in result
        assert "nested" in result

    def test_unknown_with_cursor_injects_next_cursor(self):
        resp = {"ok": True, "weird": "data",
                "response_metadata": {"next_cursor": "xyz"}}
        result = format_response("any.method", resp)
        assert "_next_cursor" in result
        assert "xyz" in result

    def test_no_raise_on_complex_data(self):
        from datetime import datetime
        resp = {"ok": True, "weird": datetime.now()}
        result = format_response("any.method", resp)
        assert "weird" in result


# === §8 metadata pass-through (cross-shape) ===

class TestMetadataPassthrough:
    def test_warning_prefixed(self):
        resp = {"ok": True, "warning": "missing_charts_scope",
                "messages": [{"ts": "1.0", "user": "U1", "text": "x"}]}
        result = format_response("conversations.history", resp)
        assert result.startswith("⚠️ missing_charts_scope")

    def test_warning_list_joined(self):
        resp = {"ok": True, "warning": ["w1", "w2"],
                "channel": {"id": "C1", "name": "x"}}
        result = format_response("conversations.info", resp)
        assert "⚠️ w1; w2" in result

    def test_no_warning_no_prefix(self):
        resp = {"ok": True, "channel": {"id": "C1", "name": "x"}}
        result = format_response("conversations.info", resp)
        assert not result.startswith("⚠️")

    def test_response_metadata_stripped_from_body(self):
        resp = {
            "ok": True,
            "channels": [{"id": "C1", "name": "x"}],
            "response_metadata": {"next_cursor": "abc"},
        }
        result = format_response("conversations.list", resp)
        # cursor surfaces in header but raw response_metadata key shouldn't appear
        assert "response_metadata" not in result


# === §6 truncation policy (cross-shape) ===

class TestTruncationPolicy:
    def test_label_truncates_at_cap(self):
        long = "n" * (LABEL_CAP + 50)
        resp = {"ok": True, "channels": [{"id": "C1", "name": long}]}
        result = format_response("conversations.list", resp)
        assert "..." in result

    def test_label_at_exact_cap_no_truncation(self):
        exact = "n" * LABEL_CAP
        resp = {"ok": True, "channels": [{"id": "C1", "name": exact}]}
        result = format_response("conversations.list", resp)
        assert "..." not in result

    def test_message_text_never_truncates(self):
        long = "x" * 10_000
        resp = {"ok": True, "messages": [{"ts": "1.0", "user": "U1", "text": long}]}
        result = format_response("conversations.replies", resp)
        assert long in result

    def test_topic_purpose_never_truncate(self):
        long = "t" * 10_000
        resp = {"ok": True, "channel": {
            "id": "C1", "name": "x",
            "topic": {"value": long},
            "purpose": {"value": long},
        }}
        result = format_response("conversations.info", resp)
        assert result.count(long) == 2  # both topic and purpose


# === Backward-compat: existing simple tests preserved ===

class TestBaseline:
    def test_format_error_with_detail(self):
        assert format_error("not_in_channel", "x") == "Error: not_in_channel — x"

    def test_format_response_simple_ok(self):
        resp = {"ok": True, "channel": "C123"}
        result = format_response("conversations.create", resp)
        assert "C123" in result

    def test_format_response_with_message(self):
        resp = {"ok": True, "message": {"text": "hello", "ts": "123.456"}}
        result = format_response("chat.postMessage", resp)
        assert "hello" in result


# === §4.2b message extras — attachments & file stubs (issue #3) ===

class TestMessageAttachmentsAndFiles:
    def _history(self, msg):
        return format_response("conversations.history", {"ok": True, "messages": [msg]})

    def test_message_share_attachment_rendered(self):
        result = self._history({
            "ts": "2.0", "user": "U1", "text": "sharing the request:",
            "attachments": [{
                "is_msg_unfurl": True,
                "author_name": "Lu Borko",
                "text": "Here is the full ask for TikTok",
                "footer": "Slack Conversation",
                "ts": "1755500000.000100",
                "from_url": "https://x.slack.com/archives/C1/p1",
            }],
        })
        assert "↳ [shared message]" in result
        assert "<Lu Borko>" in result
        assert "Here is the full ask for TikTok" in result
        assert "Slack Conversation" in result
        assert "https://x.slack.com/archives/C1/p1" in result

    def test_attachment_text_never_truncated(self):
        long = "z" * 4000
        result = self._history({
            "ts": "1.0", "user": "U1", "text": "share",
            "attachments": [{"is_msg_unfurl": True, "text": long}],
        })
        assert long in result

    def test_link_unfurl_attachment(self):
        result = self._history({
            "ts": "1.0", "user": "U1", "text": "look",
            "attachments": [{
                "service_name": "GitHub",
                "title": "Fix the bug",
                "title_link": "https://github.com/x/y/pull/1",
                "text": "PR description",
            }],
        })
        assert "↳ [attachment]" in result
        assert "<GitHub>" in result
        assert "Fix the bug" in result
        assert "https://github.com/x/y/pull/1" in result
        assert "PR description" in result

    def test_attachment_multiline_text_indented(self):
        result = self._history({
            "ts": "1.0", "user": "U1", "text": "share",
            "attachments": [{"is_msg_unfurl": True, "text": "line one\nline two"}],
        })
        assert "    line one" in result
        assert "    line two" in result

    def test_attachment_cap_with_overflow_marker(self):
        atts = [{"text": f"att-{i}"} for i in range(7)]
        result = self._history({"ts": "1.0", "user": "U1", "text": "x", "attachments": atts})
        assert "... and 2 more attachments" in result
        assert "att-4" in result
        assert "att-6" not in result

    def test_file_stub_rendered(self):
        result = self._history({
            "ts": "1.0", "user": "U1", "text": "doc attached",
            "files": [{"id": "F123", "name": "report.pdf", "filetype": "pdf", "size": 1258291}],
        })
        assert "📎 file F123: report.pdf" in result
        assert "1.2MB" in result

    def test_file_stub_cap(self):
        files = [{"id": f"F{i}", "name": f"f{i}.png"} for i in range(12)]
        result = self._history({"ts": "1.0", "user": "U1", "text": "x", "files": files})
        assert "... and 2 more files" in result

    def test_message_without_extras_unchanged(self):
        result = self._history({"ts": "1.0", "user": "U1", "text": "plain"})
        assert "↳" not in result
        assert "📎" not in result

    def test_tombstoned_file_defensive(self):
        result = self._history({
            "ts": "1.0", "user": "U1", "text": "x",
            "files": [{"id": "F9", "mode": "tombstone"}],
        })
        assert "📎 file F9: ?" in result

    def test_pins_list_renders_attachments(self):
        resp = {"ok": True, "items": [{
            "channel": "C1",
            "message": {"ts": "1.0", "user": "U1", "text": "pinned share",
                        "attachments": [{"is_msg_unfurl": True, "text": "quoted body"}]},
        }]}
        result = format_response("pins.list", resp)
        assert "quoted body" in result


# === §4.2 search.messages nested shape (FMT-003) ===

class TestSearchMessagesShape:
    def test_search_real_nested_shape_renders(self):
        resp = {"ok": True, "query": "q", "messages": {"total": 1, "matches": [{
            "ts": "1.0", "user": "U1", "text": "found it",
            "attachments": [{"text": "unfurl body"}],
        }]}}
        result = format_response("search.messages", resp)
        assert "found it" in result
        assert "unfurl body" in result

    def test_search_empty_matches(self):
        resp = {"ok": True, "query": "q", "messages": {"total": 0, "matches": []}}
        assert format_response("search.messages", resp) == "No messages found."


# === v1.3 — Slack transcription in files.info (§4.8) ===

class TestFileTranscription:
    def test_complete_transcription_rendered_with_vtt_pointer(self):
        resp = {"ok": True, "file": {
            "id": "F0C69E1NZBL", "name": "video.mp4", "filetype": "mp4",
            "transcription": {"status": "complete", "locale": "en-US",
                              "preview": {"content": "So, we sign up, we complete it.", "has_more": True}},
            "vtt": "https://files.slack.com/files-tmb/T1-F0C69E1NZBL-x/file.vtt",
        }}
        out = format_response("files.info", resp)
        assert 'transcription: complete (en-US) — "So, we sign up, we complete it.…"' in out
        assert "vtt: available — slack_download('F0C69E1NZBL', vtt=True)" in out
        assert "files.slack.com" not in out    # URL stays out of the render

    def test_status_none_or_missing_renders_nothing(self):
        for tr in ({"status": "none"}, None):
            f = {"id": "F1", "name": "clip.mp4", "filetype": "mp4"}
            if tr is not None:
                f["transcription"] = tr
            out = format_response("files.info", {"ok": True, "file": f})
            assert "transcription" not in out and "vtt" not in out

    def test_long_preview_capped(self):
        f = {"id": "F1", "name": "v.mp4",
             "transcription": {"status": "complete", "preview": {"content": "w" * 400, "has_more": False}}}
        out = format_response("files.info", {"ok": True, "file": f})
        assert '"' + "w" * 160 + '…"' in out


# === v1.4: FMT-007 search matches keep channel + permalink; FMT-008 empty page keeps cursor ===

class TestSearchMatchSource:
    def test_search_match_renders_channel_and_permalink(self):
        resp = {"ok": True, "messages": {"total": 1, "matches": [{
            "ts": "1700000000.000100", "user": "U1", "text": "deploy done",
            "channel": {"id": "C0INCIDENT", "name": "incidents"},
            "permalink": "https://example.slack.com/archives/C0INCIDENT/p1700000000000100"}]}}
        out = format_response("search.messages", resp)
        assert "[1700000000.000100] <U1> in #incidents (C0INCIDENT)" in out
        assert "permalink: https://example.slack.com/archives/C0INCIDENT/p1700000000000100" in out

    def test_search_match_in_dm_has_no_hash(self):
        resp = {"ok": True, "messages": {"matches": [{
            "ts": "1.1", "user": "U1", "text": "x", "channel": {"id": "D0DM", "name": "U2", "is_im": True}}]}}
        assert "in U2 (D0DM)" in format_response("search.messages", resp)

    def test_history_without_channel_unchanged(self):
        resp = {"ok": True, "messages": [{"ts": "1.1", "user": "U1", "text": "x"}]}
        out = format_response("conversations.history", resp)
        assert " in " not in out.split("\n")[1] and "permalink" not in out


class TestEmptyPageCursor:
    def test_empty_history_page_keeps_cursor(self):
        resp = {"ok": True, "messages": [], "has_more": True,
                "response_metadata": {"next_cursor": "bmV4dA=="}}
        out = format_response("conversations.history", resp, req_params={"channel": "C1"})
        assert "next_cursor=bmV4dA==" in out
        assert "No messages found." not in out

    def test_empty_page_without_cursor_unchanged(self):
        assert format_response("conversations.history", {"ok": True, "messages": []}) == "No messages found."

    def test_empty_channel_and_user_pages_keep_cursor(self):
        meta = {"response_metadata": {"next_cursor": "abc"}}
        assert "next_cursor=abc" in format_response("conversations.list", {"ok": True, "channels": [], **meta})
        assert "next_cursor=abc" in format_response("users.list", {"ok": True, "members": [], **meta})
