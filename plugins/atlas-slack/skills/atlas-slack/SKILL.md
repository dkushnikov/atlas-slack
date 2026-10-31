---
name: atlas-slack
description: Use for ANY Slack operation — reads, writes, and preparation steps (find user, lookup by email, open DM, DM someone, get channel ID, list members, check presence / is-online, search messages, read thread). Also post, reply, ping, react, pin, edit, upload, download a shared file, schedule, set status/DND/reminder, manage channels. Covers all 200+ Slack Web API methods through `slack_api` plus `slack_download` for file bytes. Invoke instead of raw `curl slack.com/api/...` or grabbing a Slack token from keychain (`security find-generic-password`) — token routing, attribution, dispatch all handled. Triggers on any mention of Slack, a channel/DM/thread, or a colleague to message — including `get X's channel ID` or `find Y's Slack user` that precede a send.
---

# atlas-slack — Slack API for Atlas

Primary Slack integration for agent sessions. Coexists with the official Slack plugin/MCP (decision 2026-08-21: no migration; the official one covers canvas and semantic search, this one covers the full Web API, two voices, attribution and the write gate).

Two tools, that's the whole surface (since v1.2; output contract v1.5, plugin 0.5.1):

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
| Upload a file | `slack_api("files.uploadV2", {"file": "/abs/path", "channel": "C...", "initial_comment": "..."}, as_bot=?)` — one call (section below) |
| Download a Slack file to local disk | `slack_download(file_id)` — the JSON API never serves file bytes |
| Slack's own transcript of a video/clip | `files.info` shows `transcription: complete (en-US) — "…"` + `vtt: available` when Slack has one → `slack_download(file_id, vtt=True)`; otherwise download the media and use atlas-transcribe |
| See the raw JSON (e.g. the `blocks` of a message you just sent) | add `"_raw": true` to params — `slack_api("conversations.history", {"channel": "C...", "limit": 1, "_raw": true})`; delivery checks and debugging only |
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

File uploads are no exception: `slack_api("files.uploadV2", …)` does the binary PUT internally (section below). A hand-rolled `getUploadURLExternal` → PUT → `completeUploadExternal` sequence bypasses the write gate and the attribution — the pre-2026-10-22 incidents (a colleague one-pager 09-03, Lu memo 10-15, two colleagues SQL 10-17) all ran that way.

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
| `chat.postMessage`, `chat.update`, `chat.scheduleMessage`, `chat.postEphemeral` | Block Kit context block appended; long `text` auto-split into ≤3000-char sections, each `expand: true` (no "Show more" folds) |
| same three (not postEphemeral) under `as_bot=True` | No attribution; `text` > 3000 chars with no caller `blocks` is auto-split the same way (since v1.3 — before, Slack cut bot posts at ~4000 chars mid-sentence) |
| `files.uploadV2` / `files.completeUploadExternal` with `initial_comment` | Text suffix on `initial_comment` (file-share messages cannot carry blocks); `attribution: "block"` is refused since the v2 core slice (`composite_not_in_slice`) |
| `chat.startStream` / `chat.appendStream` / `chat.stopStream` | **Refused under user token** — streaming is bot-only, always pass `as_bot=True` |
| Reactions, pins, reads, user-state changes (`users.profile.set`, `dnd.*`) | No injection — no recipient-facing text |

Bot-token calls (`as_bot=True`) never get attribution — Atlas is already the author on record.

Caller-supplied `blocks` are never rewritten; a section over 3000 chars is refused pre-flight with the offending block index (split it, or pre-check with `blocks.validate`).

## File upload — `files.uploadV2` (one call)

Since 2026-10-22 (UPL-002) the gateway maps `files.uploadV2` to the SDK's `files_upload_v2`, which runs `getUploadURLExternal → binary PUT → completeUploadExternal` internally. **Never do the three steps by hand** — the gateway has no tool for the PUT, and `curl`/`wget` are denied in the vault permission sets.

```python
slack_api("files.uploadV2", {
    "file": "/abs/path/report.pdf",   # absolute local path — or "content": "<text>" for a snippet
    "channel": "C...",                 # a STRING — never `channels` (UPL-001: a string there is split char by char)
    "title": "...",                    # optional; filename defaults to the path's basename
    "thread_ts": "...",                # optional
    "initial_comment": "<text>",       # optional; user token → :blue_heart: suffix appended
}, as_bot=False)
```

Result: `File: <name> (ID: F…)` + `permalink` first (§4.8). Attribution under the user token is a **text suffix** on `initial_comment` (Slack's file-share message cannot carry blocks). `"attribution": "block"` (v1.3: comment as a separate attributed post) is **refused** on the v2 core branch with `composite_not_in_slice` — that second post would bypass the per-send approval and journal. For the context-block look, upload without `initial_comment`, then send the comment with `chat.postMessage` (its own approval). Bot uploads (`as_bot=True`) never get attribution; the `attribution` key is ignored there.

DM IDs are per-viewer: a bot upload into a DM needs the bot-side DM ID (`conversations.open` under `as_bot=True`), not the ID the owner's client shows.

## Token routing (one-liner)

Reads → user token. Writes → `as_bot` flag picks bot or user. All writes gated by the plugin's internal PreToolUse hook (`hooks/mcp-slack-gate.py`) — a thin adapter over the door's policy engine. `conversations.open` is a **write** (it creates the DM/MPIM); to look up an existing DM use `users.conversations`, or `conversations.open` with `"prevent_creation": true` and exactly one user (row 0, auto-allowed; returns "not found" instead of creating).

## Sending a message — v2 core (contract v1.5)

`chat.postMessage` goes through the door's core: policy → provenance → your human's decision → journal (written **before** the send) → Slack. Every other write still runs on the labelled *transitional* path (today's ask).

- **Provenance — pass at least `comm_id`:** `"provenance": {"comm_id": "<ledger comm_id>"}`. That is the whole minimal form; `cls` is derived from the voice (`as_bot=False` → `A`, `as_bot=True` → `B-h`). **Where the id comes from:** `slack_api("_comm_id")` — no Slack call, returns a journal-unique `YYYYMMDD-xxxx` and notes the comm's first sighting (a `ttl_s` counts from here); put it in the ledger file's `comm_id:` when you create the draft. A live send with no id at all may omit `provenance`: the door mints one and the result line hands it back (`journal: op … ok · comm 20261031-9f3a (minted by the door — record it in the ledger)`). Never invent "random hex" yourself: a model's random is not random (the ledger had `7c3e` eight times).
- **Full schema** (every field optional except as noted; a wrong *type* refuses `provenance_invalid` before any Slack call — the message names the field):
  - `cls` `A` | `B-h` (| `B-a` `C1` `C2`, refused by the strict policy) · `voice` `person` | `bot` (derived from `as_bot`)
  - `comm_id` string · `op_id` string ≤128 (normally omitted; the door derives it) · `prepared_by` string · `context` string
  - `target` **object** `{"channel": "C…", "users": ["U…"], "is_mpim": false}` — not a string; `channel` must equal `params.channel`
  - `checks` **object** `{"voice": "…", "recipient": "…", "policy_row": "…", "mark": "…", "disclosure": "…"}` — free values, shown in the prompt, never judged; not a string
  - `sources` **list** of strings · `origin` `live` (default) | `prepared-as-human` (then `ttl_s` > 0 **and** `comm_id` are required, and only the person's voice, `as_bot=False`: `ttl_required` / `comm_id_required` / `prepared_as_human_bot_voice` otherwise) · `prepared_at` / `scheduled_for` epoch or ISO-8601 · `resend` true/false
  - `decision` — set by the hook, never by you.
- The approval prompt shows class, voice, recipient, policy row, declared checks and the text. The approval covers **that exact operation**: change text, channel, thread, any param or the voice and the door refuses `decision_mismatch` — ask again instead of editing after approval.
- One approval = one send. A repeat refuses `decision_already_used`; the same `comm_id` again refuses `duplicate_comm_id` unless you pass `"resend": true` and get a fresh approval.
- **The approval window is 30 minutes** from the moment the prompt is issued. `Error: decision_expired` means the human answered later than that and **nothing was sent**: call the tool again with the same input — a fresh prompt is issued; no new `op_id`, no `resend`. Do not check the channel first: an expired decision never reaches Slack. The same applies to `decision_unknown` after a prompt sat for more than a day (the row is pruned) — nothing was sent, call again. The one exception is a prepared-as-human comm whose own `ttl_s` ran out meanwhile: that refuses `ttl_expired`, and the comm needs a fresh human look, not a retry.
- A result line `journal: op <id> ok · comm <comm_id>` names the journal row and the communication it belongs to. `Error: outcome_unknown` means the post may or may not exist — check the channel history before anything else; **never retry blindly**.
- Refusals before any Slack call: `frozen` (profile freeze), `policy_invalid`, `journal_unavailable`, `identity_override` (`username`/`icon_emoji`/`icon_url` are never allowed), `not_enabled_by_policy` (autonomous classes B-a/C1/C2 are off by default), `recipient_denied`, `ttl_expired`, `attribution_unavailable` (profile lacks `bot_user_id`).
- The approval card (the mod in `hooks/approval.tsx`, Claude Code 2.1.314+) shows the ledger behind your `comm_id` when the context has one: `🎯 Cicero ✅` only if the ledger file's frontmatter says `audit: pass` **and** the text you send equals the file's last `## Draft vN` section; an edited text reads ⚠ until you re-audit and append the new draft; `audit: skip` (the person asked for no audit) reads `🎯 Cicero ☑ аудит пропущен`. A `checks.cicero` claim is listed under «Контекст проверил», never as ✅. Text the person types on the card comes back as the tool error «atlas-slack: правка от владельца — «…»» (nothing was sent): rework the message, re-audit, append `## Draft vN`, send again.
- `slack_api("_rights")` prints what this host may do right now — capabilities × class × policy. Use it when a refusal surprises you.

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

## Reading the output (contract v1.5)

- **Errors are structural**: a failed Slack call comes back as an MCP tool error (`is_error=true`) with text `Error: <code> — <hint>`. Success text never starts with `Error:`.
- **List responses lead with an args echo**: `» conversations.members channel=C123` — use it to match parallel responses to their calls.
- `conversations.history` / `replies` default to `limit=100` when you pass none; the header shows `next_cursor=...` when more pages exist — also on an empty page (`0 messages on this page next_cursor=…` → keep paginating).
- `search.messages` matches show `in #channel (C…)` and a `permalink:` line — follow them to `conversations.replies` for the full thread.
- `conversations.info` includes unread state when Slack provides it: `last_read: <ts>`, `unread: N (M display)`. DMs render as `Channel: DM with U... (ID: D...)`.
- Messages render as `[timestamp] <user_id>` + text, then any extras the message carries:
  - `↳ [shared message] <author> <permalink>` + quoted text — a forwarded Slack message (full body, nothing truncated)
  - `↳ [attachment] ...` — link unfurls / legacy bot attachments
  - `📎 file F0XXXX: name (type, size)` — file stub; fetch the bytes with `slack_download(file_id)`

Use the timestamp directly as `ts`/`timestamp` for `reactions.add`, `pins.add`, `chat.update`, `chat.delete`.
