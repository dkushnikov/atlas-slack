"""Tests for convenience wrapper tools."""

from atlas_slack.tools import (
    slack_react,
    slack_set_status,
    slack_remind,
    slack_pin,
    slack_upload,
    slack_edit,
    slack_delete,
    slack_create_channel,
    slack_invite,
    slack_archive,
    slack_usergroup,
    slack_bookmark,
    slack_dnd,
)


# --- Write-as-Atlas tools: must request bot client ---


def test_react_add_uses_bot_client(mock_webclient):
    mock_webclient.reactions_add.return_value = {"ok": True}
    slack_react("C123", "1234.5678", "thumbsup")
    mock_webclient._tools_get_client.assert_called_with(as_bot=True)
    mock_webclient.reactions_add.assert_called_once_with(channel="C123", timestamp="1234.5678", name="thumbsup")


def test_react_remove_uses_bot_client(mock_webclient):
    mock_webclient.reactions_remove.return_value = {"ok": True}
    slack_react("C123", "1234.5678", "thumbsup", remove=True)
    mock_webclient._tools_get_client.assert_called_with(as_bot=True)
    mock_webclient.reactions_remove.assert_called_once()


def test_pin_uses_bot_client(mock_webclient):
    mock_webclient.pins_add.return_value = {"ok": True}
    slack_pin("C123", "1234.5678")
    mock_webclient._tools_get_client.assert_called_with(as_bot=True)
    mock_webclient.pins_add.assert_called_once_with(channel="C123", timestamp="1234.5678")


def test_unpin_uses_bot_client(mock_webclient):
    mock_webclient.pins_remove.return_value = {"ok": True}
    slack_pin("C123", "1234.5678", unpin=True)
    mock_webclient._tools_get_client.assert_called_with(as_bot=True)


def test_upload_uses_bot_client(mock_webclient):
    mock_webclient.files_upload_v2.return_value = {"ok": True}
    slack_upload("C1", "hello world", title="note")
    mock_webclient._tools_get_client.assert_called_with(as_bot=True)


def test_edit_uses_bot_client(mock_webclient):
    mock_webclient.chat_update.return_value = {"ok": True, "message": {"text": "fixed", "ts": "1234.5678"}}
    slack_edit("C123", "1234.5678", "fixed text")
    mock_webclient._tools_get_client.assert_called_with(as_bot=True)
    mock_webclient.chat_update.assert_called_once_with(channel="C123", ts="1234.5678", text="fixed text")


def test_delete_uses_bot_client(mock_webclient):
    mock_webclient.chat_delete.return_value = {"ok": True}
    slack_delete("C123", "1234.5678")
    mock_webclient._tools_get_client.assert_called_with(as_bot=True)


def test_create_channel_uses_bot_client(mock_webclient):
    mock_webclient.conversations_create.return_value = {"ok": True, "channel": {"id": "C999", "name": "new-proj"}}
    slack_create_channel("new-proj")
    mock_webclient._tools_get_client.assert_called_with(as_bot=True)
    mock_webclient.conversations_create.assert_called_once_with(name="new-proj", is_private=False)


def test_create_private_channel_uses_bot_client(mock_webclient):
    mock_webclient.conversations_create.return_value = {"ok": True, "channel": {"id": "G999", "name": "secret"}}
    slack_create_channel("secret", private=True)
    mock_webclient._tools_get_client.assert_called_with(as_bot=True)


def test_invite_uses_bot_client(mock_webclient):
    mock_webclient.conversations_invite.return_value = {"ok": True, "channel": {"id": "C123"}}
    slack_invite("C123", "U1,U2")
    mock_webclient._tools_get_client.assert_called_with(as_bot=True)


def test_archive_uses_bot_client(mock_webclient):
    mock_webclient.conversations_archive.return_value = {"ok": True}
    slack_archive("C123")
    mock_webclient._tools_get_client.assert_called_with(as_bot=True)


def test_usergroup_create_uses_bot_client(mock_webclient):
    mock_webclient.usergroups_create.return_value = {"ok": True, "usergroup": {"id": "S1", "handle": "backend-team"}}
    slack_usergroup("backend-team", name="Backend Team")
    mock_webclient._tools_get_client.assert_called_with(as_bot=True)
    assert mock_webclient.usergroups_create.call_count == 1


def test_usergroup_create_with_users_calls_create_once_then_users_update(mock_webclient):
    """Regression: previously called usergroups_create twice — second call failed with name_taken."""
    mock_webclient.usergroups_create.return_value = {"ok": True, "usergroup": {"id": "S1", "handle": "backend-team"}}
    mock_webclient.usergroups_users_update.return_value = {"ok": True, "usergroup": {"id": "S1", "users": ["U1", "U2"]}}
    slack_usergroup("backend-team", name="Backend Team", users="U1,U2")
    assert mock_webclient.usergroups_create.call_count == 1
    mock_webclient.usergroups_users_update.assert_called_once_with(usergroup="S1", users="U1,U2")


def test_usergroup_users_only_resolves_handle_via_list(mock_webclient):
    """When only users provided, resolve handle → gid via usergroups.list before update."""
    mock_webclient.usergroups_list.return_value = {
        "ok": True,
        "usergroups": [
            {"id": "S9", "handle": "other"},
            {"id": "S1", "handle": "backend-team"},
        ],
    }
    mock_webclient.usergroups_users_update.return_value = {"ok": True, "usergroup": {"id": "S1"}}
    slack_usergroup("backend-team", users="U1,U2")
    mock_webclient.usergroups_list.assert_called_once_with(include_disabled=True)
    mock_webclient.usergroups_users_update.assert_called_once_with(usergroup="S1", users="U1,U2")


def test_usergroup_users_only_unknown_handle_returns_error(mock_webclient):
    mock_webclient.usergroups_list.return_value = {"ok": True, "usergroups": [{"id": "S9", "handle": "other"}]}
    result = slack_usergroup("does-not-exist", users="U1")
    assert "not_found" in result
    mock_webclient.usergroups_users_update.assert_not_called()


def test_usergroup_no_params_returns_missing_params(mock_webclient):
    result = slack_usergroup("backend-team")
    assert "missing_params" in result


def test_bookmark_uses_bot_client(mock_webclient):
    mock_webclient.bookmarks_add.return_value = {"ok": True, "bookmark": {"id": "B1"}}
    slack_bookmark("C123", "Wiki", "https://wiki.example.com")
    mock_webclient._tools_get_client.assert_called_with(as_bot=True)
    mock_webclient.bookmarks_add.assert_called_once_with(channel_id="C123", title="Wiki", type="link", link="https://wiki.example.com")


# --- User-state tools: must stay on user client (Slack API requires user token) ---


def test_set_status_uses_user_client(mock_webclient):
    mock_webclient.users_profile_set.return_value = {"ok": True, "profile": {"status_text": "Focusing", "status_emoji": ":headphones:"}}
    slack_set_status("Focusing", ":headphones:")
    mock_webclient._tools_get_client.assert_called_with(as_bot=False)
    call_kwargs = mock_webclient.users_profile_set.call_args[1]
    assert call_kwargs["profile"]["status_text"] == "Focusing"


def test_remind_uses_user_client(mock_webclient):
    mock_webclient.reminders_add.return_value = {"ok": True, "reminder": {"id": "R1", "text": "Review PR"}}
    slack_remind("Review PR", "in 2 hours")
    mock_webclient._tools_get_client.assert_called_with(as_bot=False)
    mock_webclient.reminders_add.assert_called_once_with(text="Review PR", time="in 2 hours", user=None)


def test_remind_other_user_uses_user_client(mock_webclient):
    mock_webclient.reminders_add.return_value = {"ok": True, "reminder": {"id": "R1", "text": "Check logs"}}
    slack_remind("Check logs", "tomorrow 9am", user="U456")
    mock_webclient._tools_get_client.assert_called_with(as_bot=False)


def test_dnd_on_uses_user_client(mock_webclient):
    mock_webclient.dnd_setSnooze.return_value = {"ok": True, "snooze_endtime": 1234}
    slack_dnd(30)
    mock_webclient._tools_get_client.assert_called_with(as_bot=False)
    mock_webclient.dnd_setSnooze.assert_called_once_with(num_minutes=30)


def test_dnd_off_uses_user_client(mock_webclient):
    mock_webclient.dnd_endSnooze.return_value = {"ok": True}
    slack_dnd()
    mock_webclient._tools_get_client.assert_called_with(as_bot=False)
