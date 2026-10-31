---
name: atlas-slack
description: Use for ANY Slack operation — reads, writes, and preparation steps (find user, lookup by email, open DM, DM someone, get channel ID, list members, check presence / is-online, search messages, read thread). Also post, reply, ping, react, pin, edit, upload, download a shared file, schedule, set status/DND/reminder, manage channels. Covers all 200+ Slack Web API methods through `slack_api` plus `slack_download` for file bytes. Invoke instead of raw `curl slack.com/api/...` or grabbing a Slack token from keychain (`security find-generic-password`) — token routing, attribution, dispatch all handled. Triggers on any mention of Slack, a channel/DM/thread, or a colleague to message — including `get X's channel ID` or `find Y's Slack user` that precede a send.
---

# atlas-slack — Slack for the person and their agent

Works on its own. Where the official Slack MCP is installed too, the two coexist (that one covers canvas and semantic search; this one the full Web API, two voices, attribution and the write gate) — and when both could perform a write, use `slack_api`: the guarantees below (two voices, a decision per operation, the journal) apply only to calls made through it.

Two tools, that's the whole surface (since v1.2; output contract v1.10, plugin 0.7.0):

- **`slack_api(method, params, as_bot=?)`** — every Slack Web API method (200+).
- **`slack_download(file_id, out_dir?, as_bot?)`** — file bytes to local disk (the JSON API never serves file content).

Two identities, set by the profile (`~/.config/atlas-slack/<name>.yaml`): **the person** (`person_user_id`, the user token — the human you work for; the card addresses them as *you*) and **the bot** (`bot_user_id`, the bot token — the app's bot user, whatever it is named in the workspace). The names on the approval card come from the profile and from Slack, not from this skill.

## Quick start — pick your task

Pick the row that matches your intent. The right column is what to call. Three labels recur: **core send** — the six message sends that go through the door (approval card, journal row, binding to the exact operation; § Sending a message); **transitional** — every other write, on today's gate (a compact card, no journal row, no binding); **extra scopes** — the shipped app manifest does not grant what the row needs, so the install must add them (Slack answers `missing_scope` with the name).

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
| Post a message in the bot's voice | `slack_api("chat.postMessage", {...}, as_bot=True)` |
| Post a message in the person's voice, with the bot's mark | `slack_api("chat.postMessage", {...}, as_bot=False)` |
| React to a message | `slack_api("reactions.add", {"channel": "C...", "timestamp": "<ts>", "name": "thumbsup"}, as_bot=True)` |
| Pin / unpin | `slack_api("pins.add" / "pins.remove", {"channel": "C...", "timestamp": "<ts>"}, as_bot=True)` |
| Edit a message | `slack_api("chat.update", {"channel": "C...", "ts": "<ts>", "text": "..."}, as_bot=?)` — core send: the door reads the message first, the card shows «was / becomes»; `as_bot` must match the original's author (`author_mismatch` otherwise) |
| Delete a message | `slack_api("chat.delete", {"channel": "C...", "ts": "<ts>"}, as_bot=?)` — transitional (compact card); `as_bot` must match the message's author token |
| Upload a file | `slack_api("files.uploadV2", {"file": "/abs/path", "channel": "C...", "initial_comment": "..."}, as_bot=?)` — one call, core send (section below) |
| Show something to one person only | `slack_api("chat.postEphemeral", {"channel": "C...", "user": "U...", "text": "..."}, as_bot=?)` — core send; the viewer is a bound recipient |
| Download a Slack file to local disk | `slack_download(file_id)` — the JSON API never serves file bytes |
| Slack's own transcript of a video/clip | `files.info` shows `transcription: complete (en-US) — "…"` + `vtt: available` when Slack has one → `slack_download(file_id, vtt=True)`; otherwise download the media — transcription is outside this plugin |
| See the raw JSON (e.g. the `blocks` of a message you just sent) | add `"_raw": true` to params — `slack_api("conversations.history", {"channel": "C...", "limit": 1, "_raw": true})`; delivery checks and debugging only |
| Set the person's status | `slack_api("users.profile.set", {"profile": {"status_text": "...", "status_emoji": ":...:"}})` |
| DND on/off | `slack_api("dnd.setSnooze", {"num_minutes": N})` · `slack_api("dnd.endSnooze", {})` |
| Reminder | `slack_api("reminders.add", {"text": "...", "time": "in 2 hours"})` |
| Create / invite / archive channel | `slack_api("conversations.create" / "conversations.invite" / "conversations.archive", {...}, as_bot=True)` — transitional; extra scopes `channels:manage` (public) / `groups:write` (private) |
| See a usergroup and its members | `slack_api("usergroups.list", {})` · `slack_api("usergroups.users.list", {"usergroup": "S..."})` — reads; extra scope `usergroups:read` |
| Replace a usergroup's members | `slack_api("usergroups.users.update", {"usergroup": "S...", "users": "U1,U2"}, as_bot=True)` — **replaces the whole member list** with `users`: read the current list first and pass everyone who stays; transitional; extra scope `usergroups:write` |
| Channel bookmark | `slack_api("bookmarks.add", {"channel_id": "C...", "title": "...", "type": "link", "link": "https://..."}, as_bot=True)` — transitional; extra scope `bookmarks:write` |
| What may this host do right now | `slack_api("_rights")` — profile × policy × class, no Slack call |
| Anything else in the Slack Web API | `slack_api(method, params, as_bot=?)` — all 200+ methods work (the app's scopes decide; `missing_scope` names the scope to add) |

Note the v1.2 removal: the typed convenience wrappers (`slack_react`, `slack_upload`, `slack_pin`, …) no longer exist — every row above goes through `slack_api` directly. Only `slack_download` remains as a separate tool.

## Never use raw bash+curl for Slack

If you're reaching for `curl https://slack.com/api/...` or `security find-generic-password -s slack-user-token`, **stop**. Every Slack Web API method is accessible via `slack_api(method, params)`. Token lookup, attribution, and error formatting are already handled.

| Bash/curl anti-pattern | MCP replacement |
|---|---|
| `curl -H "Authorization: Bearer $TOKEN" slack.com/api/conversations.open -d "users=U..."` | `slack_api("conversations.open", {"users": "U..."})` |
| `curl ... slack.com/api/users.lookupByEmail?email=...` | `slack_api("users.lookupByEmail", {"email": "..."})` |
| `curl -H "Authorization: Bearer $TOKEN" files.slack.com/files-pri/... -o file` | `slack_download(file_id)` — auth, login-page detection, safe filename handled |
| `security find-generic-password -s slack-user-token -w` | Unnecessary — `slack_api` resolves tokens via env, set once in the shell profile |

File uploads are no exception: `slack_api("files.uploadV2", …)` does the binary PUT internally (section below). A hand-rolled `getUploadURLExternal` → PUT → `completeUploadExternal` sequence bypasses the write gate and the attribution — every upload incident before 2026-10-22 ran that way.

If you're preparing a send (looking up a user, opening a DM, fetching a channel ID), stay in this skill — don't drop into bash. The preparation layer is why most sessions fall out of the MCP path; it's covered here.

## Working model — two axes

Every Slack action lives at the intersection of two questions. Decide both before composing the call.

**Axis 1 — Where is the message going?**
- *Channel/group where the bot is a member* — the bot can speak in its own voice (bot token).
- *The person's DM with someone else* — the bot is not a member there; the only delivery path is the person's user token (`as_bot=False`).

**Axis 2 — Who authored this content?**
- *The agent drafted, the person will send it themselves* — no API call from this skill; return the text to the person in the reply (a clipboard tool, where one exists, is a convenience, not the path).
- *The agent sends directly* — API call required. The recipient deserves to see whether the agent or the person was at the keyboard.

## Authorship → token mapping

| Where × Who | Token | Call | Recipient sees |
|---|---|---|---|
| Channel · authored by the agent | bot (`as_bot=True`) | `slack_api("chat.postMessage", ..., as_bot=True)` | the bot as poster |
| DM-as-the-person · authored by the agent | user (`as_bot=False`) | `slack_api("chat.postMessage", ..., as_bot=False)` | the person's name + `:blue_heart: @<bot>` footer block |
| Any target · the person will send it themselves | n/a | no API call — return the text to the person | the person's name only |
| Reactions / pins / channel structure | bot (`as_bot=True`) | `reactions.add`, `pins.add`, `conversations.create` / `invite` / `archive` — **not** `conversations.open` for the person's DMs, which runs under the person's token (row above) | the bot as actor |
| Status / DND / reminders (the person's own state) | user (`as_bot=False`, default) | `users.profile.set`, `dnd.*`, `reminders.add` | the person's own state |

## Attribution rules

When `as_bot=False`, `slack_api` auto-applies the `:blue_heart: @<bot>` attribution to recipient-facing text sends (the bot id comes from the profile's `bot_user_id`; without it a person-voice send refuses `attribution_unavailable`). Coverage is method-aware:

| Method | Attribution shape |
|---|---|
| `chat.postMessage`, `chat.update`, `chat.scheduleMessage`, `chat.postEphemeral` | Block Kit context block appended; long `text` auto-split into ≤3000-char sections, each `expand: true` (no "Show more" folds) |
| same three (not postEphemeral) under `as_bot=True` | No attribution; `text` > 3000 chars with no caller `blocks` is auto-split the same way (since v1.3 — before, Slack cut bot posts at ~4000 chars mid-sentence) |
| `files.uploadV2` / `files.completeUploadExternal` with `initial_comment` | Text suffix on `initial_comment` (file-share messages cannot carry blocks); `attribution: "block"` on `files.uploadV2` posts the comment as a separate attributed message instead — a child op under the same approval (0.6.0) |
| `chat.meMessage` | Text suffix on `text` (a /me message has no blocks) |
| `chat.startStream` / `chat.appendStream` / `chat.stopStream` | **Refused under user token** — streaming is bot-only, always pass `as_bot=True` |
| Reactions, pins, reads, user-state changes (`users.profile.set`, `dnd.*`) | No injection — no recipient-facing text |

Bot-token calls (`as_bot=True`) never get attribution — the bot is already the author on record.

Caller-supplied `blocks` are never rewritten; a section over 3000 chars is refused pre-flight with the offending block index (split it, or pre-check with `blocks.validate`). `username` / `icon_emoji` / `icon_url` are refused on every write (`identity_override`): no caller sets the displayed sender.

## File upload — `files.uploadV2` (one call)

Since 2026-10-22 (UPL-002) the gateway maps `files.uploadV2` to the SDK's `files_upload_v2`, which runs `getUploadURLExternal → binary PUT → completeUploadExternal` internally. **Never do the three steps by hand** — the gateway has no tool for the PUT, and the Bash gate blocks `curl` to Slack.

```python
slack_api("files.uploadV2", {
    "file": "/abs/path/report.pdf",   # absolute local path — or "content": "<text>" for a snippet
    "channel": "C...",                 # a STRING — never `channels` (UPL-001: a string there is split char by char)
    "title": "...",                    # optional; filename defaults to the path's basename
    "thread_ts": "...",                # optional
    "initial_comment": "<text>",       # optional; user token → :blue_heart: suffix appended
}, as_bot=False)
```

Result: `File: <name> (ID: F…)` + `permalink` first (§4.8), then `journal: op … ok · comm …`. **The upload is a core send (0.6.0):** one approval card (the file as `📎 <name> (<size>)`, the comment as the text), the file's bytes bound into the approval by sha256 — rewrite the file after the card and the door refuses `decision_mismatch`; a `channels` key (string or list) is refused — one `channel`. Attribution under the user token is a **text suffix** on `initial_comment` (Slack's file-share message cannot carry blocks). `"attribution": "block"` posts the comment as a separate attributed message with `<permalink|title>` — a **composite op**: the result adds `comment: op <id>.c1 ok ts=…`. Two failure branches, distinct: (1) Slack refused the comment post — `⚠ comment_not_posted … Do not upload again`: the file IS in Slack; never upload again; if the comment is still wanted, post it with `chat.postMessage` under the same `comm_id` **with `resend: true`** (the upload already counts as this comm's write). (2) The comment post's outcome is ambiguous — `⚠ comment_outcome_unknown … run slack_api("_reconcile") first`: neither upload nor post anything until `_reconcile` has resolved that child row. Bot uploads (`as_bot=True`) never get attribution; the `attribution` key is ignored there.

DM IDs are per-viewer: a bot upload into a DM needs the bot-side DM ID (`conversations.open` under `as_bot=True`), not the ID the person's client shows.

## Token routing (one-liner)

Reads → user token. Writes → `as_bot` flag picks bot or user. All writes gated by the plugin's internal PreToolUse hook (`hooks/mcp-slack-gate.py`) — a thin adapter over the door's policy engine. `conversations.open` is a **write** (it creates the DM/MPIM); to look up an existing DM use `users.conversations`, or `conversations.open` with `"prevent_creation": true` and exactly one user (row 0, auto-allowed; returns "not found" instead of creating).

**Identity at connect (0.7.0):** before the first write under each token in a session — after policy and the decision check, before the journal intent — the door asks Slack `auth.test` once (a read) and compares the answer with the profile; transitional writes get the same check, reads do not. `Error: identity_mismatch` (another workspace, another person, a bot token in the user slot or the reverse) means **nothing was sent** and nothing will be until the profile or the token is fixed: tell the person to run `python3 -m atlas_slack doctor` and restart the session — do not retry, do not switch `as_bot` to get around it. `Error: identity_unverified` (Slack gave no usable answer: unreachable, or the token is invalid/revoked) also means nothing was sent; the check runs again at the next call — call once more, and if it persists, `doctor`.

## Sending a message — v2 core (contract v1.10)

The six message sends go through the door's core (0.6.0): `chat.postMessage`, `chat.scheduleMessage`, `chat.update`, `chat.postEphemeral`, `chat.meMessage`, `files.uploadV2` — policy → provenance → your human's decision → journal (written **before** the send) → Slack. A scheduled send needs `post_at` (epoch seconds, 1 min … 120 days ahead); it is bound into the approval, so changing the time means a new approval. **A time edit from the approval card** («tomorrow», «in an hour») is answered with `chat.scheduleMessage` — the same card, with the time in its first line. **An edit** (`chat.update`) needs `ts` and the new `text`, and belongs to the **original's `comm_id`** — an edit is a new version of the same communication, not a resend (no `resend` flag, no `duplicate_comm_id`); when the door posted the original, the edit inherits that `comm_id` (and `parent_op_id`) from the journal by `ts` — so you may omit `comm_id`; pass a `comm_id` only for a message the door did not post. Other provenance fields (`checks`, `target.users`) may be passed either way. The door reads the message before the card («was / becomes» — when the read fails the card says «⚠ not read» and the server reads again before the send), journals the prior text, and refuses `author_mismatch` when the original was posted by another identity (the bot edits the bot's, the person edits theirs — pick `as_bot` by the original; a post by the bot, or a bot post with no `user`, is never edited in the person's voice). **An ephemeral** needs `user`; that viewer is a bound recipient (deny lists apply; change the user → new approval). Everything else still runs on the labelled *transitional* path (compact card, no journal op, no binding): reactions, pins, delete, deleteScheduledMessage, structure (`conversations.*`, usergroups), and the row-1 writes that are not message sends — `files.completeUploadExternal`, canvases, streams, `chat.unfurl`, `reminders.add`, `views.publish`, topic/purpose/rename.

- **Provenance.** If this communication already has a `comm_id` (you minted one with `slack_api("_comm_id")` when you started the draft, or the person's workflow keeps one in a ledger file), pass it: `"provenance": {"comm_id": "<id>"}` — the whole minimal form; `cls` is derived from the voice (`as_bot=False` → `A`, `as_bot=True` → `B-h`). If it has none, omit `provenance`: the door mints a `comm_id` and the result line hands it back (`journal: op … ok · comm 20261031-9f3a (minted by the door — record it)`) — record it, and pass it on every later message of the same communication (an edit, a resend). `slack_api("_comm_id")` makes no Slack call; it returns a journal-unique `YYYYMMDD-xxxx` and notes the comm's first sighting (a `ttl_s` counts from here). Never invent "random hex" yourself: a model's random is not random (one ledger had the same 4 hex digits eight times).
- **Full schema** (every field optional except as noted; a wrong *type* refuses `provenance_invalid` before any Slack call — the message names the field):
  - `cls` `A` | `B-h` (| `B-a` `C1` `C2`, refused by the strict policy) · `voice` `person` | `bot` (derived from `as_bot`)
  - `comm_id` string · `op_id` string ≤128 (normally omitted; the door derives it) · `prepared_by` string · `context` string
  - `target` **object** `{"channel": "C…", "users": ["U…"], "is_mpim": false}` — not a string; `channel` must equal `params.channel`
  - `checks` **object** `{"voice": "…", "recipient": "…", "policy_row": "…", "mark": "…", "disclosure": "…"}` — free values, shown in the prompt, never judged; not a string
  - `sources` **list** of strings · `origin` `live` (default) | `prepared-as-human` (then `ttl_s` > 0 **and** `comm_id` are required, and only the person's voice, `as_bot=False`: `ttl_required` / `comm_id_required` / `prepared_as_human_bot_voice` otherwise) · `prepared_at` / `scheduled_for` epoch or ISO-8601 · `resend` true/false
  - `decision` — set by the hook, never by you.
- The approval prompt shows class, voice, recipient, policy row, declared checks and the text. The approval covers **that exact operation**: change text, channel, thread, any param or the voice and the door refuses `decision_mismatch` — ask again instead of editing after approval.
- One approval = one send. A repeat refuses `decision_already_used`; the same `comm_id` again refuses `duplicate_comm_id` unless you pass `"resend": true` and get a fresh approval.
- **The approval window is 30 minutes** from the moment the prompt is issued. If the comm is `prepared-as-human` and its own `ttl_s` ran out meanwhile, the door refuses `ttl_expired`: the comm needs a fresh human look, not a retry. Otherwise `Error: decision_expired` means the human answered later than 30 minutes and **nothing was sent**: call the tool again with the same input — a fresh prompt is issued; no new `op_id`, no `resend`. Do not check the channel first: an expired decision never reaches Slack. The same applies to `decision_unknown` after a prompt sat for more than a day (the row is pruned) — nothing was sent, call again.
- A result line `journal: op <id> ok · comm <comm_id>` names the journal row and the communication it belongs to. `Error: outcome_unknown` means the post may or may not exist — run `slack_api("_reconcile")` (it sends nothing to Slack: it reads Slack and writes the journal; it resolves the row by what Slack has: bot sends by the op id in message metadata, the person's by text and author, edits by the text at `ts`, scheduled sends in the scheduled list, uploads in the channel's files); **never retry blindly**. Until that row is resolved, any send with the same `comm_id` refuses `reconcile_required` — `resend: true` does not override it. When the report says `still unknown` (too early, a read failure, a blocks-only edit, a snippet with no file name), stop: run `_reconcile` again later; if a read can never settle it, ask the person to look in Slack and record their word — `slack_api("_reconcile", {"op_ids": ["<op>"], "resolve": "ok"})` when the write is there, `"absent"` when it is not (the plugin's hook asks the person, showing each op's method, channel, time and text, and binds the answer to a one-time token like a send; the server records only what the hook approved; nothing is sent). **Never work around it by minting a new `comm_id`** — that is exactly the double post the rule prevents. An ephemeral cannot be checked (never in history): reconcile marks it `error reconciled_unobservable`; a repeat shows the viewer a second copy at worst.
- Refusals before any Slack call: `frozen` (profile freeze), `policy_invalid`, `journal_unavailable`, `identity_override` (`username`/`icon_emoji`/`icon_url` are never allowed), `not_enabled_by_policy` (autonomous classes B-a/C1/C2 are off by default), `recipient_denied`, `ttl_expired`, `attribution_unavailable` (profile lacks `bot_user_id`).
- The approval card (the mod in `hooks/approval.tsx`, Claude Code 2.1.314+) shows: to · format · when / from / text / the door line — recipient · voice · binding · journal · policy, and a ledger mark when the profile names a `ledger_dir` (an optional layer: a per-communication Markdown file with `comm_id:` in its frontmatter and an `audit:` field; without the folder there is no ledger line and never a refusal) / «Context checked» (your `checks`, listed, never judged) / «Not checked». Card marks: ✅ the door verified · 🟡 declared / skipped / built-in policy · 🔴 missing, not found, mismatch. Transitional writes (reactions, delete, pins, deleteScheduledMessage, conversations.open) get a compact card too — what · where · when · text — with the door line saying they run outside the core (no journal op, no binding). The card's language follows the profile (`language: en|ru`, default `en`). Text the person types on the card comes back as the tool error «atlas-slack: edit from the person — «…»» (ru: «правка с карточки») and **nothing was sent**. Read it as the person's instruction before doing anything: an edit → rework the message with it and send again (a new card); a question → answer it, no send; «stop», «later», a different plan → follow it. Never resend the same text.
- Refusals after the decision check and before the intent, with one `auth.test` read: `identity_mismatch` (the token is not the workspace / person / bot the profile names) · `identity_unverified` (Slack gave no usable answer; checked again at the next call) — nothing was sent either way.
- `Error: version_skew` = the hook and the MCP server run different plugin versions (a live session after `/reload-plugins` or an update): nothing was sent; tell the person the session needs a restart.
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

Combine freely: `"deploy" in:incidents from:@alice after:2026-04-01 -resolved`.

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

## Reading the output (contract v1.10)

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
