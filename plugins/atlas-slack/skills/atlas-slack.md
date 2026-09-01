---
name: atlas-slack
description: Use for ANY Slack operation — reads, writes, and preparation steps (find user, lookup by email, open DM, DM someone, get channel ID, list members, check presence / is-online, search messages, read thread). Also post, reply, ping, react, pin, edit, upload, download a shared file, schedule, set status/DND/reminder, manage channels. Covers all 200+ Slack Web API methods through `slack_api` plus `slack_download` for file bytes. Invoke instead of raw `curl slack.com/api/...` or grabbing a Slack token from keychain (`security find-generic-password`) — token routing, attribution, dispatch all handled. Triggers on any mention of Slack, a channel/DM/thread, or a colleague to message — including `get X's channel ID` or `find Y's Slack user` that precede a send.
---

# atlas-slack — Slack API for Atlas

Primary Slack integration. Replaces the official Slack plugin.

Two tools, that's the whole surface (v1.2):

- **`slack_api(method, params, as_bot=?)`** — every Slack Web API method (200+).
- **`slack_download(file_id, out_dir?, as_bot?)`** — file bytes to local disk (the JSON API never serves file content).

## Quick start — pick your task

Pick the row that matches your intent. The right column is what to call.

| I want to... | Method |
|---|---|
| Find a user by email or name | `slack_api("users.lookupByEmail", {"email": "..."})` · `slack_api("users.list", {})` |
| Find existing DMs/conversations of a user | `slack_api("users.conversations", {"user": "U...", "types": "im,mpim"})` — read, no side effects |
| Open a DM with a user (get the channel ID) | `slack_api("conversations.open", {"users": "U..."})` — **creates** the DM/MPIM if absent |
| Get info on a user | `slack_api("users.info", {"user": "U..."})` |
| List channels or find a channel by name | `slack_api("conversations.list", {"types": "public_channel,private_channel"})` |
| Search messages (with filters) | `slack_api("search.messages", {"query": "keyword in:channel from:@user"})` |
| Read a channel's recent history | `slack_api("conversations.history", {"channel": "C..."})` — limit defaults to 100 |
| Read replies in a thread | `slack_api("conversations.replies", {"channel": "C...", "ts": "..."})` |
| List members of a channel | `slack_api("conversations.members", {"channel": "C..."})` |
| Check unread state of a channel/DM | `slack_api("conversations.info", {"channel": "..."})` — shows `last_read` / `unread` |
| Post a message as Atlas | `slack_api("chat.postMessage", {...}, as_bot=True)` |
| Post a message as the owner with Atlas attribution | `slack_api("chat.postMessage", {...}, as_bot=False)` |
| React to a message | `slack_api("reactions.add", {"channel": "C...", "timestamp": "<ts>", "name": "thumbsup"}, as_bot=True)` |
| Pin / unpin | `slack_api("pins.add" / "pins.remove", {"channel": "C...", "timestamp": "<ts>"}, as_bot=True)` |
| Edit / delete a message | `slack_api("chat.update" / "chat.delete", {"channel": "C...", "ts": "<ts>", ...}, as_bot=?)` — `as_bot` must match the message's author token |
| Upload a file | 3-step `files.*` flow (section below) |
| Download a Slack file to local disk | `slack_download(file_id)` — the JSON API never serves file bytes |
| Set the owner's status | `slack_api("users.profile.set", {"profile": {"status_text": "...", "status_emoji": ":...:"}})` |
| DND on/off | `slack_api("dnd.setSnooze", {"num_minutes": N})` · `slack_api("dnd.endSnooze", {})` |
| Reminder | `slack_api("reminders.add", {"text": "...", "time": "in 2 hours"})` |
| Create / invite / archive channel | `slack_api("conversations.create" / "conversations.invite" / "conversations.archive", {...}, as_bot=True)` |
| Usergroup members | `slack_api("usergroups.list", {})` → `slack_api("usergroups.users.update", {"usergroup": "S...", "users": "U1,U2"}, as_bot=True)` |
| Channel bookmark | `slack_api("bookmarks.add", {"channel_id": "C...", "title": "...", "type": "link", "link": "https://..."}, as_bot=True)` |
| Anything else in the Slack Web API | `slack_api(method, params, as_bot=?)` — all 200+ methods work |

Note the v1.2 removal: the typed convenience wrappers (`slack_react`, `slack_upload`, `slack_pin`, …) no longer exist — every row above goes through `slack_api` directly. Only `slack_download` remains as a separate tool.

## Never use raw bash+curl for Slack

If you're reaching for `curl https://slack.com/api/...` or `security find-generic-password -s slack-user-token`, **stop**. Every Slack Web API method is accessible via `slack_api(method, params)`. Token lookup, attribution, and error formatting are already handled.

| Bash/curl anti-pattern | MCP replacement |
|---|---|
| `curl -H "Authorization: Bearer $TOKEN" slack.com/api/conversations.open -d "users=U..."` | `slack_api("conversations.open", {"users": "U..."})` |
| `curl ... slack.com/api/users.lookupByEmail?email=...` | `slack_api("users.lookupByEmail", {"email": "..."})` |
| `curl -H "Authorization: Bearer $TOKEN" files.slack.com/files-pri/... -o file` | `slack_download(file_id)` — auth, login-page detection, safe filename handled |
| `security find-generic-password -s slack-user-token -w` | Unnecessary — `slack_api` resolves tokens via env, set once in `~/.zshenv` |

**The only legitimate raw HTTP call** is the binary PUT to the `upload_url` returned by `files.getUploadURLExternal` (step 2 of a file upload). That is a file transfer, not a Web API call — see the file upload section below.

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

| Where × Who | Token | Call | Recipient sees |
|---|---|---|---|
| Channel · authored by Atlas | bot (`as_bot=True`) | `slack_api("chat.postMessage", ..., as_bot=True)` | "Atlas 💙" as poster |
| DM-as-person · authored by Atlas | user (`as_bot=False`) | `slack_api("chat.postMessage", ..., as_bot=False)` | the owner's name + `:blue_heart: @Atlas` footer block |
| DM-as-person · drafted for the owner | n/a | no API call — return text to the owner | the owner's name only |
| Reactions / pins / channel admin | bot (`as_bot=True`) | `reactions.add`, `pins.add`, `conversations.*` | Atlas as actor |
| Status / DND / reminders (the owner's user state) | user (`as_bot=False`, default) | `users.profile.set`, `dnd.*`, `reminders.add` | the owner's own state |

## Attribution rules

When `as_bot=False`, `slack_api` auto-applies `:blue_heart: @Atlas` attribution to recipient-facing text sends. Coverage is method-aware:

| Method | Attribution shape |
|---|---|
| `chat.postMessage`, `chat.update`, `chat.scheduleMessage`, `chat.postEphemeral` | Block Kit context block appended; long `text` auto-split into ≤3000-char sections |
| `files.completeUploadExternal` with `initial_comment` | Text suffix on `initial_comment` |
| `chat.startStream` / `chat.appendStream` / `chat.stopStream` | **Refused under user token** — streaming is bot-only, always pass `as_bot=True` |
| Reactions, pins, reads, user-state changes (`users.profile.set`, `dnd.*`) | No injection — no recipient-facing text |

Bot-token calls (`as_bot=True`) never get attribution — Atlas is already the author on record.

Caller-supplied `blocks` are never rewritten; a section over 3000 chars is refused pre-flight with the offending block index (split it, or pre-check with `blocks.validate`).

## File upload (the only path — 3 steps)

Legacy `files.upload` is deprecated and the `slack_upload` wrapper is gone; every upload is:

```python
# 1. Get upload URL
slack_api("files.getUploadURLExternal", {"filename": "x.md", "length": <bytes>})
# 2. PUT file content via curl/HTTPX to the returned upload_url (this is the only
#    bash/HTTP curl path that belongs here — it's a raw binary PUT, not a Web API call)
# 3. Complete + share to channel/thread (attribution auto-applied to initial_comment)
slack_api("files.completeUploadExternal", {
    "files": [{"id": "<file_id>", "title": "..."}],
    "channel_id": "C...",
    "thread_ts": "...",          # optional
    "initial_comment": "<text>",
}, as_bot=False)
```

For a text snippet you control end-to-end, step 2 from bash: `curl -s -X POST -H "Content-Type: application/octet-stream" --data-binary @/path/to/file "<upload_url>"` (uploads to `files.slack.com` are allowed by the bash gate).

## Token routing (one-liner)

Reads → user token. Writes → `as_bot` flag picks bot or user. All writes gated by the plugin's internal PreToolUse hook (`hooks/mcp-slack-gate.py`) requiring user confirmation. `conversations.open` is a **write** (it creates the DM/MPIM); to look up existing conversations use `users.conversations` (read, auto-allowed).

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

## Reading the output (contract v1.2)

- **Errors are structural**: a failed Slack call comes back as an MCP tool error (`is_error=true`) with text `Error: <code> — <hint>`. Success text never starts with `Error:`.
- **List responses lead with an args echo**: `» conversations.members channel=C123` — use it to match parallel responses to their calls.
- `conversations.history` / `replies` default to `limit=100` when you pass none; the header shows `next_cursor=...` when more pages exist.
- `conversations.info` includes unread state when Slack provides it: `last_read: <ts>`, `unread: N (M display)`. DMs render as `Channel: DM with U... (ID: D...)`.
- Messages render as `[timestamp] <user_id>` + text, then any extras the message carries:
  - `↳ [shared message] <author> <permalink>` + quoted text — a forwarded Slack message (full body, nothing truncated)
  - `↳ [attachment] ...` — link unfurls / legacy bot attachments
  - `📎 file F0XXXX: name (type, size)` — file stub; fetch the bytes with `slack_download(file_id)`

Use the timestamp directly as `ts`/`timestamp` for `reactions.add`, `pins.add`, `chat.update`, `chat.delete`.
