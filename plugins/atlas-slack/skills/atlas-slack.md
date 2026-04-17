---
name: atlas-slack
description: Use for ANY Slack operation — reads, writes, and preparation steps (find user, lookup by email, open DM, DM someone, get channel ID, list members, check presence / is-online, search messages, read thread). Also post, reply, ping, react, pin, edit, upload, schedule, set status/DND/reminder, manage channels. Covers all 200+ Slack Web API methods through `slack_api` plus 13 typed tools. Invoke instead of raw `curl slack.com/api/...` or grabbing a Slack token from keychain (`security find-generic-password`) — token routing, attribution, dispatch all handled. Triggers on any mention of Slack, a channel/DM/thread, or a colleague to message — including `get X's channel ID` or `find Y's Slack user` that precede a send.
---

# atlas-slack — Slack API for Atlas

Primary Slack integration. Replaces the official Slack plugin.

## Quick start — pick your task

Pick the row that matches your intent. The right column is what to call.

| I want to... | Tool / method |
|---|---|
| Find a user by email or name | `slack_api("users.lookupByEmail", {"email": "..."})` · `slack_api("users.list", {})` |
| Open a DM with a user (get the channel ID) | `slack_api("conversations.open", {"users": "U..."})` |
| Get info on a user | `slack_api("users.info", {"user": "U..."})` |
| List channels or find a channel by name | `slack_api("conversations.list", {"types": "public_channel,private_channel"})` |
| Search messages (with filters) | `slack_api("search.messages", {"query": "keyword in:channel from:@user"})` |
| Read a channel's recent history | `slack_api("conversations.history", {"channel": "C...", "limit": 10})` |
| Read replies in a thread | `slack_api("conversations.replies", {"channel": "C...", "ts": "..."})` |
| List members of a channel | `slack_api("conversations.members", {"channel": "C..."})` |
| Post a message as Atlas | `slack_api("chat.postMessage", {...}, as_bot=True)` |
| Post a message as the owner with Atlas attribution | `slack_api("chat.postMessage", {...}, as_bot=False)` |
| React / pin / unpin | `slack_react(...)` · `slack_pin(...)` |
| Edit or delete one of your messages | `slack_edit(...)` · `slack_delete(...)` |
| Upload a file to a channel | `slack_upload(...)` or 3-step `files.*` flow (below) |
| Set the owner's status / DND / reminder | `slack_set_status(...)` · `slack_dnd(...)` · `slack_remind(...)` |
| Create / invite / archive channel | `slack_create_channel(...)` · `slack_invite(...)` · `slack_archive(...)` |
| Anything else in the Slack Web API | `slack_api(method, params, as_bot=?)` — all 200+ methods work |

## Never use raw bash+curl for Slack

If you're reaching for `curl https://slack.com/api/...` or `security find-generic-password -s slack-user-token`, **stop**. Every Slack Web API method is accessible via `slack_api(method, params)`. Token lookup, attribution, and error formatting are already handled.

| Bash/curl anti-pattern | MCP replacement |
|---|---|
| `curl -H "Authorization: Bearer $TOKEN" slack.com/api/conversations.open -d "users=U..."` | `slack_api("conversations.open", {"users": "U..."})` |
| `curl ... slack.com/api/users.lookupByEmail?email=...` | `slack_api("users.lookupByEmail", {"email": "..."})` |
| `security find-generic-password -s slack-user-token -w` | Unnecessary — `slack_api` resolves tokens via env, set once in `~/.zshenv` |

**The only legitimate raw HTTP call** is the binary PUT to the `upload_url` returned by `files.getUploadURLExternal` (step 2 of a modern file upload). That is a file transfer, not a Web API call — see the file upload section below.

If you're preparing a send (looking up a user, opening a DM, fetching a channel ID), stay in this skill — don't drop into bash. The preparation layer is why most sessions fall out of the MCP path; it's covered here.

## Working model — two axes

Every Slack action lives at the intersection of two questions. Decide both before composing the call.

**Axis 1 — Where is the message going?**
- *Channel/group where Atlas is a member* — Atlas can speak in its own voice (bot token).
- *Personal DM of the owner with someone else* — Atlas is not a user there; the only delivery path is via the owner's user token (`as_bot=False`).

**Axis 2 — Who authored this content?**
- *Atlas drafted, the owner will send manually* — no API call from this skill; copy text to clipboard, hand to the owner.
- *Atlas sends directly* — API call required. Recipient deserves to see whether it was Atlas or the owner at the keyboard.

## Authorship → token mapping

| Where × Who | Token | Tool / mode | Recipient sees |
|---|---|---|---|
| Channel · authored by Atlas | bot (`as_bot=True`) | `slack_api("chat.postMessage", ..., as_bot=True)` or convenience tools | "Atlas 💙" as poster |
| DM-as-person · authored by Atlas | user (`as_bot=False`) | `slack_api("chat.postMessage", ..., as_bot=False)` | the owner's name + `:blue_heart: @Atlas` footer block |
| DM-as-person · drafted for the owner | n/a | no API call — return text to the owner | the owner's name only |
| Reactions / pins / status / file uploads | bot (built-in) | convenience tools (`slack_react`, `slack_pin`, `slack_upload`) | Atlas as actor |

## Attribution rules

When `as_bot=False`, `slack_api` auto-applies `:blue_heart: @Atlas` attribution to recipient-facing text sends. Coverage is method-aware:

| Method | Attribution shape |
|---|---|
| `chat.postMessage`, `chat.update`, `chat.scheduleMessage`, `chat.postEphemeral` | Block Kit context block appended |
| `files.completeUploadExternal` with `initial_comment` | Text suffix on `initial_comment` |
| Reactions, pins, reads, user-state changes (`users.profile.set`, `dnd.*`) | No injection — no recipient-facing text |

Bot-token calls (`as_bot=True`) never get attribution — Atlas is already the author on record.

## File upload with initial_comment in a thread

Modern file send to a thread requires three steps (legacy `files.upload` is deprecated):

```python
# 1. Get upload URL
slack_api("files.getUploadURLExternal", {"filename": "x.md", "length": <bytes>})
# 2. PUT file content via curl/HTTPX to the returned upload_url (this is the only
#    bash/HTTP curl path that belongs here — it's a raw binary PUT, not a Web API call)
# 3. Complete + post in thread (attribution auto-applied to initial_comment)
slack_api("files.completeUploadExternal", {
    "files": [{"id": "<file_id>", "title": "..."}],
    "channel_id": "C...",
    "thread_ts": "...",
    "initial_comment": "<text>",
}, as_bot=False)
```

## Token routing (one-liner)

Reads → user token. Writes → `as_bot` flag picks bot or user. All writes gated by the plugin's internal PreToolUse hook (`hooks/mcp-slack-gate.py`) requiring user confirmation.

## Search modifiers for `search.messages`

Slack search supports these filters in the `query` string:

- `in:channel-name` — within a channel
- `from:username` or `from:<@U123>` — from a user
- `before:2026-04-01` / `after:2026-04-01` / `on:2026-04-01` — date filters
- `"exact phrase"` — exact match
- `has:pin` / `has:link` / `has:file` — content filters
- `is:thread` — threaded messages only
- `-word` — exclude

Combine freely: `"deploy" in:incidents from:@owner after:2026-04-01 -resolved`.

## Convenience tool signatures

The Quick start table above shows which tool covers which task. These are the full parameter lists for the 13 typed wrappers:

- `slack_react(channel, timestamp, emoji, remove?)` — add/remove emoji reaction
- `slack_set_status(text, emoji?, expiration?)` — set the owner's status
- `slack_remind(text, time, user?)` — create reminder
- `slack_pin(channel, timestamp, unpin?)` — pin/unpin message
- `slack_upload(channels, content, title?, filetype?)` — upload file
- `slack_edit(channel, timestamp, text)` — edit message
- `slack_delete(channel, timestamp)` — delete message (destructive)
- `slack_create_channel(name, private?, description?)` — create channel
- `slack_invite(channel, users)` — invite to channel
- `slack_archive(channel)` — archive channel
- `slack_usergroup(handle, name?, users?)` — manage user groups
- `slack_bookmark(channel, title, link)` — add channel bookmark
- `slack_dnd(minutes?)` — toggle Do Not Disturb

Full Slack method list (use via `slack_api`): https://api.slack.com/methods

## Slack mrkdwn Formatting

When composing Slack messages, use mrkdwn (NOT standard Markdown):

| Format | Syntax | NOT |
|--------|--------|----|
| Bold | `*text*` | ~~**text**~~ |
| Italic | `_text_` | same |
| Link | `<url\|text>` | ~~[text](url)~~ |
| User mention | `<@U123>` | |
| Channel | `<#C123>` | |
| Code | `` `code` `` | same |
| Quote | `> text` | same |

No headers (`##`), no horizontal rules (`---`), no `[text](url)` links.

## Message Output Format

`conversations.history` returns: `[timestamp] <user_id> message text`

Use the timestamp directly for `slack_react`, `slack_pin`, `slack_edit`, `slack_delete`.
