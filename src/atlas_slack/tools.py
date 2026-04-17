"""Convenience wrapper tools for common Slack operations.

Token routing policy:
- Write-as-Atlas (react, pin, upload, edit, delete, create_channel, invite,
  archive, usergroup, bookmark) → bot token. Atlas is the actor.
- User-state (set_status, dnd, remind) → user token. Slack API requires user
  token for self-profile mutations.

For user-as-author posts (chat.postMessage from the owner with attribution),
use the `slack_api` gateway directly with as_bot=False.
"""

from atlas_slack.gateway import mcp
from atlas_slack.client import get_client
from atlas_slack.formatters import format_response, format_error


def _call(fn, method_name: str, **kwargs) -> str:
    """Call a slack_sdk method and format the response."""
    try:
        data = _call_raw(fn, **kwargs)
        return format_response(method_name, data)
    except Exception as e:
        return format_error("exception", str(e))


def _call_raw(fn, **kwargs) -> dict:
    """Call a slack_sdk method and return the raw response dict. Raises on exception."""
    resp = fn(**kwargs)
    return resp if isinstance(resp, dict) else (resp.data if hasattr(resp, "data") else {"ok": True})


@mcp.tool()
def slack_react(channel: str, timestamp: str, emoji: str, remove: bool = False) -> str:
    """Add or remove an emoji reaction on a message (as Atlas).

    Args:
        channel: Channel ID containing the message.
        timestamp: Message timestamp (ts).
        emoji: Emoji name without colons (e.g. 'thumbsup', 'eyes', 'white_check_mark').
        remove: If True, remove the reaction instead of adding.
    """
    client = get_client(as_bot=True)
    if remove:
        return _call(client.reactions_remove, "reactions.remove", channel=channel, timestamp=timestamp, name=emoji)
    return _call(client.reactions_add, "reactions.add", channel=channel, timestamp=timestamp, name=emoji)


@mcp.tool()
def slack_set_status(text: str, emoji: str = "", expiration: int = 0) -> str:
    """Set the owner's Slack status (user-token only).

    Args:
        text: Status text (e.g. 'In a meeting', 'Working remotely', 'On vacation').
        emoji: Status emoji with colons (e.g. ':calendar:', ':house:'). Empty to clear.
        expiration: Unix timestamp when status expires. 0 = no expiration.
    """
    client = get_client(as_bot=False)
    profile = {"status_text": text, "status_emoji": emoji}
    if expiration:
        profile["status_expiration"] = expiration
    return _call(client.users_profile_set, "users.profile.set", profile=profile)


@mcp.tool()
def slack_remind(text: str, time: str, user: str | None = None) -> str:
    """Create a Slack reminder for the owner (user-token).

    Args:
        text: Reminder text.
        time: When to remind. Natural language ('in 2 hours', 'tomorrow 9am') or Unix timestamp.
        user: User ID to remind. Omit to remind yourself.
    """
    client = get_client(as_bot=False)
    return _call(client.reminders_add, "reminders.add", text=text, time=time, user=user)


@mcp.tool()
def slack_pin(channel: str, timestamp: str, unpin: bool = False) -> str:
    """Pin or unpin a message in a channel (as Atlas).

    Args:
        channel: Channel ID.
        timestamp: Message timestamp (ts).
        unpin: If True, unpin instead of pin.
    """
    client = get_client(as_bot=True)
    if unpin:
        return _call(client.pins_remove, "pins.remove", channel=channel, timestamp=timestamp)
    return _call(client.pins_add, "pins.add", channel=channel, timestamp=timestamp)


@mcp.tool()
def slack_upload(channels: str, content: str, title: str = "", filetype: str = "text") -> str:
    """Upload content as a file to Slack channel(s) (as Atlas).

    Args:
        channels: Comma-separated channel IDs to share the file in.
        content: File content as string.
        title: File title.
        filetype: File type (text, python, javascript, markdown, csv, etc).
    """
    client = get_client(as_bot=True)
    return _call(
        client.files_upload_v2,
        "files.uploadV2",
        channels=channels,
        content=content,
        title=title or "Uploaded file",
        filetype=filetype,
    )


@mcp.tool()
def slack_edit(channel: str, timestamp: str, text: str) -> str:
    """Edit a previously sent message (Atlas's own message by default).

    To edit a user-authored message, call slack_api('chat.update', ..., as_bot=False).

    Args:
        channel: Channel ID containing the message.
        timestamp: Message timestamp (ts) to edit.
        text: New message text.
    """
    client = get_client(as_bot=True)
    return _call(client.chat_update, "chat.update", channel=channel, ts=timestamp, text=text)


@mcp.tool()
def slack_delete(channel: str, timestamp: str) -> str:
    """Delete a message (Atlas's own message by default). Destructive — cannot be undone.

    To delete a user-authored message, call slack_api('chat.delete', ..., as_bot=False).

    Args:
        channel: Channel ID containing the message.
        timestamp: Message timestamp (ts) to delete.
    """
    client = get_client(as_bot=True)
    return _call(client.chat_delete, "chat.delete", channel=channel, ts=timestamp)


@mcp.tool()
def slack_create_channel(name: str, private: bool = False, description: str = "") -> str:
    """Create a new Slack channel (as Atlas).

    Args:
        name: Channel name (lowercase, no spaces, use hyphens).
        private: If True, create a private channel.
        description: Channel purpose/description.
    """
    client = get_client(as_bot=True)
    try:
        resp = client.conversations_create(name=name, is_private=private)
        data = resp if isinstance(resp, dict) else resp.data
        if description and data.get("ok"):
            ch_id = data.get("channel", {}).get("id")
            if ch_id:
                client.conversations_setPurpose(channel=ch_id, purpose=description)
        return format_response("conversations.create", data)
    except Exception as e:
        return format_error("exception", str(e))


@mcp.tool()
def slack_invite(channel: str, users: str) -> str:
    """Invite users to a channel (as Atlas).

    Args:
        channel: Channel ID.
        users: Comma-separated user IDs to invite.
    """
    client = get_client(as_bot=True)
    return _call(client.conversations_invite, "conversations.invite", channel=channel, users=users)


@mcp.tool()
def slack_archive(channel: str) -> str:
    """Archive a channel (as Atlas). Can be unarchived later.

    Args:
        channel: Channel ID to archive.
    """
    client = get_client(as_bot=True)
    return _call(client.conversations_archive, "conversations.archive", channel=channel)


@mcp.tool()
def slack_usergroup(handle: str, name: str = "", users: str = "") -> str:
    """Create or update a user group (@mention group) as Atlas.

    Args:
        handle: Group handle without @ (e.g. 'backend-team').
        name: Display name. Provide to create a new group.
        users: Comma-separated user IDs to set as members.
    """
    if not name and not users:
        return format_error("missing_params", "Provide name (to create) or users (to update members)")

    client = get_client(as_bot=True)
    try:
        if name:
            data = _call_raw(client.usergroups_create, handle=handle, name=name)
            if not data.get("ok"):
                return format_response("usergroups.create", data)
            gid = data.get("usergroup", {}).get("id")
            method, response = "usergroups.create", data
        else:
            listing = _call_raw(client.usergroups_list, include_disabled=True)
            if not listing.get("ok"):
                return format_response("usergroups.list", listing)
            gid = next(
                (g["id"] for g in listing.get("usergroups", []) if g.get("handle") == handle),
                None,
            )
            if not gid:
                return format_error("not_found", f"usergroup @{handle} not found")
            method, response = "usergroups.users.update", {"ok": True, "usergroup": {"id": gid, "handle": handle}}

        if users and gid:
            update = _call_raw(client.usergroups_users_update, usergroup=gid, users=users)
            if not update.get("ok"):
                return format_response("usergroups.users.update", update)
            method, response = "usergroups.users.update", update

        return format_response(method, response)
    except Exception as e:
        return format_error("exception", str(e))


@mcp.tool()
def slack_bookmark(channel: str, title: str, link: str) -> str:
    """Add a bookmark (link) to a channel's header (as Atlas).

    Args:
        channel: Channel ID.
        title: Bookmark title.
        link: URL to bookmark.
    """
    client = get_client(as_bot=True)
    return _call(client.bookmarks_add, "bookmarks.add", channel_id=channel, title=title, type="link", link=link)


@mcp.tool()
def slack_dnd(minutes: int = 0) -> str:
    """Toggle Do Not Disturb for the owner (user-token). Call with minutes to snooze, without to end.

    Args:
        minutes: Minutes to snooze. 0 or omit to end current snooze.
    """
    client = get_client(as_bot=False)
    if minutes:
        return _call(client.dnd_setSnooze, "dnd.setSnooze", num_minutes=minutes)
    return _call(client.dnd_endSnooze, "dnd.endSnooze")
