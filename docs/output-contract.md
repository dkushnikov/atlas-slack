---
title: atlas-slack — Output Contract
status: locked
version: 1.4
created: 2026-04-30
locked: 2026-04-30
updated: 2026-10-28
owner: Dmitry Kushnikov
---

# atlas-slack — Output Contract

## Changelog

- **v1.6 (2026-10-31, plugin 0.5.2)** — the approval prompt carries the ledger behind `comm_id` when a ledger dir is set: `ledger: ✅ аудит Cicero <date> · <file>` (only when `audit: pass` **and** the text being sent equals the file's last `## Draft vN` section) / `⚠ аудит … был для другого текста` / `☑ аудит пропущен` (`audit: skip`) / `⚠ аудит: <other value>` / `⚠ аудита нет` / `⚠ не найден` / `⚠ помечен dropped` / `⚠ папка ledger недоступна`, and `intention: …` (§15). The card reads these lines from just above the technical line, so message text cannot forge them; `comm_id` is one printable line (`provenance_invalid` otherwise). Read from the file's frontmatter (`audit:`, `audit_at:`, `intention:`, `status:`) and its last draft section, never judged, never a refusal. Profile field `ledger_dir` (a path list), env `ATLAS_SLACK_LEDGER_DIR`.
- **v1.5 (2026-10-31, plugin 0.5.1)** — after three days of 0.5.0 in production. **Result line** after a core send carries the communication: `journal: op <op_id> ok · comm <comm_id>`, with ` (minted by the door — record it in the ledger)` when the caller passed none (§4.1). **`comm_id` is minted by the door** (`YYYYMMDD-<4 hex>` from OS entropy, journal-unique) when provenance has none; the op row carries `comm_id_source: door` (§15). **`cls` is derived from `as_bot`** when provenance omits it, so `{"comm_id": …}` alone is valid provenance (§15). **Decision window 30 minutes** (was 10): `decision_expired` text now states the age, the window, that nothing was sent, and that the same call again yields a fresh prompt (§10b). **Hook-side refusals are journaled** as `calls` rows with `caller=hook` (§10b). `canvases.getContent` registered as a read. **`slack_api("_comm_id")`** mints a ledger id locally. **`comm_id_required`** for `prepared-as-human` (§10b). Unconsumed decisions pruned after 24 h (was 1 h), so a late approval reads `decision_expired`, not `decision_unknown`.
- **v1.4 (2026-10-28, branch `feat/v2-core`, not installed)** — first slice of Architecture v2. **`chat.postMessage` runs through the door core** (policy → provenance → one-time human decision → write-ahead journal → Slack); every other write is on the labelled *transitional* path (0.4.0 behaviour + verdict refusals). New optional tool parameter **`provenance`** (§15). New **result line** after a core send: `journal: op <op_id> ok` (§4.1). New pseudo-method **`slack_api("_rights")`** — the current rights table, no Slack call (§15). **Verdict text** in the approval prompt: class, voice, recipient, policy row/mode, declared checks, text preview, op hash (§15). **New error codes** (§10b): refusals before any Slack call (`frozen`, `policy_invalid`, `profile_invalid`/`profile_missing`, `journal_unavailable`, `identity_override`, `no_decision`, `decision_mismatch`, `decision_already_used`, `decision_expired`, `decision_unknown`, `duplicate_comm_id`, `duplicate_op_id`, `ttl_expired`, `ttl_required`, `prepared_at_future`, `not_enabled_by_policy`, `recipient_denied`, `voice_mismatch`, `target_mismatch`, `provenance_invalid`, `user_token_in_process`, `no_notary`, `attribution_unavailable`, `token_unavailable`, `method_refused`) and **`outcome_unknown`** after an ambiguous transport failure. **FMT-007** — `search.messages` matches render `in #<name> (<id>)` and a `permalink:` line (§4.2). **FMT-008** — an empty page with a cursor renders `0 <items> on this page next_cursor=<c> — more remain; continue with this cursor.` instead of `No <items> found.` (§4.2, §4.4, §4.7, §4.9, §4.12). **GATE-002** — `username` / `icon_emoji` / `icon_url` on any write refuse `identity_override`. §9 — the attributed bot id comes from the profile (`bot_user_id`), no workspace default in code.
- **v1.3 (2026-10-22)** — UPL-002 (issue #4): **`files.uploadV2` is live** — the gateway maps it to slack_sdk `files_upload_v2` (getUploadURLExternal → PUT → completeUploadExternal run by the SDK); before, it returned `unknown_method` and uploads from agent sessions needed a hand-rolled HTTP PUT outside the plugin. Pre-flight: `channel` string (a string in `channels` is refused — UPL-001), `file` absolute and existing, `content`/`file_uploads` passed through. §9 — attribution on `initial_comment` as a text suffix (as `completeUploadExternal`); `attribution: "block"` posts the comment separately with the context block and `<permalink|title>`. Render unchanged (§4.8/§4.9) plus a `comment: posted ts=…` line in block mode. Gate: `files.uploadV2` asks, like every write. **Same day, MC ask after the live checks:** **bot auto-split** — `as_bot=True` on `chat.postMessage`/`update`/`scheduleMessage` with `text` > 3000 chars and no caller `blocks` gets the §9 section split (no context block); `text` kept as the fallback. **`_raw` flag** — `"_raw": true` in params is stripped before the call and the response is rendered as §4.16 JSON with no args-echo (delivery checks: the `blocks` of a sent message). **Transcription in §4.8** — `files.info` renders `transcription: <status> (<locale>) — "<preview ≤160>…"` and `vtt: available — slack_download(id, vtt=True)` when Slack has transcribed the media; `slack_download(…, vtt=True)` fetches the WebVTT (`no_transcript` when there is none).
- **v1.2 (2026-09-01)** — v1.2 build plan (Slack-stack audit): **structural errors** — Slack failures (ok:false / SlackApiError / exceptions) raise `ToolError`, so the MCP result carries `is_error=true`; error text (`Error: <code> — <hint>`) unchanged, and the Slack error code is now extracted from `SlackApiError` so the §10 hint catalogue fires on live calls (§10). **Unread state** — §4.3 renders `last_read` / `unread` (was whitelisted away); DMs render `Channel: DM with <user>` instead of `#?`. **Default limit** — `conversations.history`/`replies` get `limit=100` injected when the caller passes none. **Args echo** — list shapes lead with `» <method> <k=v ...>` echoing the caller's request args (§8). **Blocks pre-validation** — caller-supplied section blocks >3000 chars are refused pre-flight with block index + hint instead of an API 400 (§10). **Tool surface** — the 13 convenience wrappers removed; surface is `slack_api` + `slack_download`.
- **v1.1 (2026-08-21)** — minor, additive (issues #1/#3): §4.2b message extras — attachments (message shares, unfurls) and file stubs render in message lists and pins; `search.messages` nested `{"messages": {"matches": [...]}}` shape unwrapped (was erroring — FMT-003); §9 — streaming methods bot-only (pre-flight refusal under user token) + auto-split of >3000-char text into multiple section blocks on attribution wrap; §10 — `invalid_blocks` hint.
- **v1.0 (2026-04-30)** — locked with the owner.

What `slack_api(method, params, as_bot=...)` and `slack_download` return as text (v1.2 removed the typed convenience wrappers). The contract LLM consumers can rely on, and the contract `formatters.py` must implement.

> [!info] How to read this doc
> §2 is the orienting "why". §3 is the field taxonomy. §4 is the per-shape registry — that's the actual contract. §§5–10 are policy details (routing, truncation, lists, metadata, attribution, errors). §11 is out-of-scope. §12 is the locked decisions. Appendices give context.

## 1. TL;DR — what changes vs current

> [!decision] What you're accepting
> - Move from fallthrough-by-key to dispatch-by-shape with explicit field contract per shape.
> - Distinguish five field categories: **identifier**, **label**, **payload**, **metadata**, **routing** — each with own truncation rule.
> - List size cap becomes per-shape (was uniform 50). Messages 200, channels 100, users 50, pins/items 50, files 20, reminders uncapped.
> - `response_metadata.next_cursor` is no longer dropped — always echoed when non-empty.
> - `permalink`, `topic`, `purpose`, `num_members`, full `users.profile.get` fields are preserved (currently lost).
> - Errors get optional hints for common cases (e.g. `channel_not_found` → suggest `conversations.open`).

> [!implementation] What gets built
> A formatter dispatch table keyed by `method` (with shape inferred from response when method-specific contract is absent). One handler function per shape. Shape contracts live as constants — easy to read and test in isolation. ~3 days work including tests.

## 2. Principles

1. **The text output is the API surface.** LLM reads it. Spec the output, not the internal call.
2. **Field categories drive truncation, not shapes.** A name is a name in any shape; a body is a body in any shape. Truncation rules attach to category.
3. **Never silently lose payload.** Labels may truncate (visible `...` suffix); payload (body text, descriptions) never truncates.
4. **Always pass cursor.** Pagination control flow is the caller's job — formatter must surface it.
5. **Hints over raw errors.** When a known-recoverable error returns, suggest the recovery path (one line, no narration).
6. **Code reuse is not a contract.** Two methods sharing a key (`messages`) don't share semantics — split when they diverge.

## 3. Field categories

| Category | Definition | Examples | Truncation rule |
|----------|------------|----------|----------------|
| **identifier** | Opaque ID; meaningful only as a handle | channel ID, user ID, file ID, ts, thread_ts | **Never** |
| **label** | Short human-readable name | channel name, user real_name, file title, usergroup handle | Cap **200 chars**, suffix `...` |
| **payload** | Actual content carried by the response | message text, channel topic/purpose, file content | **Never** truncate. |
| **metadata** | Side info about the entity | ts, user, edited, num_members, reaction counts, file size | **Never** truncate; some fields conditionally included per shape (§4) |
| **routing** | Pagination & system signals | next_cursor, warnings, rate-limit hints | Always shown when present |

## 4. Shape registry

The contract: which fields each shape preserves, in which order. Output format is human-readable text, not JSON. Order matters — first field is the most-likely-needed.

### 4.1 Single message (`chat.postMessage`, `chat.update`, `chat.scheduleMessage` responses)

```
Message (ts=<ts>) <thread_ts=<thread_ts>>?:
<text>
<channel: <channel_id>>?
journal: op <op_id> ok · comm <comm_id>   ← v1.4/1.5, chat.postMessage through the core only; " (minted by the door — record it in the ledger)" when the caller passed no comm_id
```

Preserved: `ts`, `text` (full), `thread_ts` (if present), `channel` (echo).
Dropped: `bot_profile`, `app_id`, `team`, internals.

### 4.2 Message list (`conversations.history`, `conversations.replies`, `search.messages`)

```
N messages: <next_cursor=<cursor>>?
  [<ts>] <<user>> <in #<name> (<channel_id>)>? <thread_ts=<thread_ts>>? <replies=N>? <👍3 ❤️1>?
  <text — full, no cap>
  <permalink: <url>>?

  [<ts>] ...
  ... and N more
```

Preserved per item: `ts`, `user`, `text` (full), `thread_ts` (if reply), reactions.
- **Reactions** rendered as visual aggregate `👍3 ❤️1 🎉2` (emoji char + count, space-separated). Omitted entirely when no reactions.
- **`thread_ts` shown only when message is a reply** (`thread_ts` present and `!= ts`). For thread parents, `reply_count` field signals thread existence — shown as `replies=N` instead.
- List cap: **200 messages**. Slack's natural max is 1000 per call.
- Cursor: passed through in header only when non-empty. **v1.4 (FMT-008):** an empty page with a non-empty cursor renders `0 messages on this page next_cursor=<c> — more remain; continue with this cursor.`; `No messages found.` only when there is no cursor. Same rule for channel, member, user, file and pin lists.
- **v1.4 (FMT-007):** a message that carries its channel (search matches: `channel: {id, name}`) renders `in #<name> (<id>)` first in the meta (DM/MPIM: no `#`), and its `permalink` on its own line after the text — the path from a hit to `conversations.replies`.

> Search results note: Slack's `search.messages` returns highlighted/truncated `text` server-side. Formatter preserves verbatim — caller should follow up with `conversations.replies(channel, ts)` if full body needed. This is documented in §5 of SKILL.md.
> The nested response shape `{"messages": {"total": N, "matches": [...]}}` is unwrapped to the match list before rendering (v1.1, FMT-003).

### 4.2b Message extras — attachments & file stubs (v1.1)

Messages carrying `attachments[]` (message shares, link unfurls, legacy bot attachments) or `files[]` render them under the text line. Applies to §4.2 message lists and §4.12 pins (which embed messages).

```
  ↳ [shared message|attachment] <<author>>? <title>? <link>?
    <attachment text — payload class, never truncated>
    — <footer>, <date>?
  📎 file <id>: <name> (<filetype>, <size>)?
```

- Kind marker: `[shared message]` when `is_msg_unfurl` is set, else `[attachment]`.
- Author = `author_name || author_subname || service_name` (label class); link = `title_link || from_url`.
- Attachment `text` (fallback field: `fallback`) is **payload class** — full, never truncated (principle #3; a message share carries the entire quoted message here — see FMT-002).
- File lines are **stubs**: id first — bytes come via `slack_download(file_id)`; the JSON API never serves file content.
- Render caps per message: **5 attachments, 10 files**; overflow → `... and N more attachments|files`. Caps bound pathological bot messages, not real usage.

### 4.3 Channel info (`conversations.info`, `conversations.create`)

```
Channel: #<name> (ID: <id>) <private>? <archived>? <general>?
  members: <num_members>
  topic: <topic.value>
  purpose: <purpose.value>
  last_read: <ts>
  unread: <unread_count> (<unread_count_display> display)?
  created: <YYYY-MM-DD>
```

`<general>?` — emitted as the literal token `general` in the header line when `is_general=true`. Acts as a "tread carefully" signal for posts; everyone sees this channel.

**DM header (v1.2):** when `is_im=true` (no `name`), the header is `Channel: DM with <user> (ID: <id>)` — was `#? (ID: D...)`.

**Unread state (v1.2):** `last_read`, `unread_count`, `unread_count_display` render when Slack returns them (membership-dependent). `unread: 0` is shown — "all read" is information, not absence; the display variant is appended only when it differs. Previously whitelisted away, which made "what's unread?" unanswerable through the tool (incident 2026-09-01).

Preserved: `id`, `name`, `is_private`, `is_archived`, `is_general` (when true), `is_im`+`user` (DM header), `num_members`, `topic.value`, `purpose.value`, `last_read`, `unread_count`, `unread_count_display`, `created`.
`topic`/`purpose`: full, no cap.
Dropped: `latest`, `parent_conversation`, `pending_shared`, locale.

### 4.4 Channel list (`conversations.list`)

```
N channels: <next_cursor: <cursor>>?
  C0XXXXXXX  #channel-name           private? archived? (<num_members>)
  ...
```

Preserved per item: `id`, `name`, `is_private`, `is_archived`, `num_members`.
List cap: **100**.

### 4.5 User info (`users.info`, `users.profile.get`)

```
User: <real_name> (ID: <id>) <bot>? <deleted>?
  display_name: <profile.display_name>
  email: <profile.email>
  title: <profile.title>
  tz: <tz>
  status: <status_emoji> <status_text>
  image: <profile.image_72>
```

Preserved: `id`, `real_name`, `display_name`, `email`, `title`, `tz`, `is_bot`, `deleted`, status fields, image url.
Dropped: phone, color, locale, two_factor settings.

### 4.6 User profile set (`users.profile.set`)

```
Profile updated: <status_emoji> <status_text>
```

Returns the diff — only updated fields. Same shape as current behavior.

### 4.7 User list (`users.list`)

```
N users: <next_cursor: <cursor>>?
  U0XXXXXXX  <real_name> <<email>>
  ...
```

List cap: **50**.

### 4.8 File (single — `files.info`)

```
File: <name> (ID: <id>)
  permalink: <permalink>
  type: <filetype>, size: <size_human>
  title: <title>
  created: <YYYY-MM-DD>
```

`permalink` is **first** after name — most common need post-upload. Transcription (v1.3): when `transcription.status` is not `none`, one line `transcription: <status> (<locale>) — "<preview, ≤160 chars>…"`, and `vtt: available — slack_download('<id>', vtt=True)` when the `vtt` URL is present (Slack transcribes clips and uploaded video asynchronously; uploaded mp4s had `complete`/`en-US` live on 2026-10-22, three native clips had `none`).

### 4.9 File list (`files.list`, `files.uploadV2` result)

```
N files: <next_cursor: <cursor>>?
  F0XXXXXXX  <name>  <permalink>
  ...
```

List cap: **20**.

### 4.10 Reminder (`reminders.add`, `reminders.info`)

```
Reminder (ID: <id>): <text>
  time: <ISO datetime>
  recurring: <true/false>
  complete: <true/false>
```

### 4.11 Usergroup (`usergroups.create`, `usergroups.users.update`)

```
Usergroup @<handle> (ID: <id>): <name>
  users: <count> members
  created: <YYYY-MM-DD>
```

For `usergroups.list`: list shape with `id`, `handle`, `name`, user count.

### 4.12 Pin / Star list (`pins.list`, `stars.list`)

```
N pins: <next_cursor: <cursor>>?
  [<ts>] <<user>> in #<channel_name>
  <text — full>
  ...
```

List cap: **50**.

### 4.13 Permalink (`chat.getPermalink`)

```
Permalink: <permalink>
  channel: <channel>, ts: <ts>
```

### 4.14 Auth / team (`auth.test`, `team.info`)

```
Auth: <user>@<team> (user_id=<id>, team_id=<id>)
  url: <url>
```

### 4.15 Reactions (`reactions.add`, `pins.add`, etc — empty success)

```
Done ✓
```

### 4.16 Unknown shape — JSON fallback

```
<pretty JSON, indent=2, ensure_ascii=False>
```

Last resort. Stable behavior: never raise, never truncate, just dump. Also the explicit path (v1.3): `"_raw": true` in the caller's params routes any method's response here (the cleaned dict — `ok`/`response_metadata` stripped, cursor as `_next_cursor`), without the args-echo line — the only way to inspect fields the shapes drop (`blocks`, `metadata`, …). Reserved flag, stripped before the API call; use with `limit: 1`.

If JSON fallback hits a method that returns a large response (e.g. an unrecognized list endpoint), the right fix is to **add the shape to the registry**, not to truncate. JSON fallback is intentionally permissive — its output size signals a missing shape entry.

## 5. Method → shape routing

Explicit table for ambiguity-prone methods. For unlisted methods, formatter infers from response keys per §4.16.

| Method | Shape |
|--------|-------|
| `chat.postMessage`, `chat.update`, `chat.scheduleMessage`, `chat.postEphemeral` | 4.1 |
| `conversations.history`, `conversations.replies` | 4.2 |
| `search.messages` | 4.2 (with note on Slack-side truncation) |
| `conversations.info`, `conversations.create`, `conversations.open` | 4.3 |
| `conversations.list`, `conversations.members` | 4.4 |
| `users.info`, `users.profile.get` | 4.5 |
| `users.profile.set` | 4.6 |
| `users.list` | 4.7 |
| `files.info`, `files.uploadV2` (single file) | 4.8 |
| `files.list`, `files.uploadV2` (multi) | 4.9 |
| `reminders.add`, `reminders.info` | 4.10 |
| `reminders.list` | list of 4.10 |
| `usergroups.create`, `usergroups.users.update` | 4.11 |
| `usergroups.list` | list of 4.11 |
| `pins.list`, `stars.list` | 4.12 |
| `chat.getPermalink` | 4.13 |
| `auth.test`, `team.info` | 4.14 |
| `reactions.add`, `reactions.remove`, `pins.add`, `pins.remove`, `stars.add`, `stars.remove`, `dnd.setSnooze`, `dnd.endSnooze`, `bookmarks.add`, `conversations.archive`, `conversations.invite`, `chat.delete` | 4.15 |

## 6. Truncation policy (cross-shape)

| Field type | Cap | Suffix |
|-----------|-----|--------|
| Identifier | none | — |
| Label (name, title, handle, real_name) | 200 chars | `...` |
| Body text (message text, file content) | none | — |
| Description (topic.value, purpose.value, file title, channel description) | none | — |
| Email, URL, permalink | none | — |
| Reaction aggregate string | 200 chars total | `...` |

Rule of thumb: when a field is the **reason the call was made**, no cap. When it's accessory metadata, cap 200/2000 by length class.

## 7. List size caps

| Shape | Cap | Rationale |
|-------|-----|-----------|
| Messages | **200** | Threads regularly exceed 50; 200 covers ~95% of real threads |
| Channels | **100** | Channel pickers; rare to need more for inspection |
| Users | **50** | Same — mostly used to find a user, not enumerate |
| Files | **20** | Most upload responses are single-file; 20 covers batch uploads |
| Pins, stars, items | **50** | Inbox-shaped lists |
| Reminders | **none** | Personal reminders rarely number more than ~30 |

When list exceeds cap: append `... and N more` as the last line. The cursor (if present) lives in the header line as `next_cursor=<cursor>` — not duplicated in footer.

## 8. Metadata pass-through

- **`response_metadata.next_cursor`**: appended as `next_cursor=<cursor>` in the **list-shape header line only** (one location, not duplicated in footer). Omitted when empty.
- **Slack `warning` field** (deprecation, missing optional scope): prefixed as a single `⚠️ <warning>` line **before everything else** in the output, including before shape header. Only one warning line per response, joined by `; ` if multiple.
- **Rate-limit hints (`Retry-After`)**: Slack SDK raises `SlackApiError` on 429; caught in gateway, returned as `format_error("rate_limit", retry_after_str)`.
- **Args echo (v1.2)** — list shapes only. List bodies don't self-identify (`12 members:` — of which channel?), so parallel responses were attributable only by call order (04-28 confusion class). When the gateway has the caller's request params, list-shape output leads with:

  ```
  » <method> <key>=<value> ...
  ```

  Echoed keys: `channel`, `ts`, `user`, `users`, `query`, `email`, `types` — the caller's **original** args (injected params like the default `limit` are not echoed). With none of these present, the line is `» <method>` alone. Order when combined: warning line (⚠️) first, then args echo, then shape body. Single-entity shapes (§4.1, §4.3, §4.5, …) are unchanged — they self-identify.

- **Default limit (v1.2)** — `conversations.history` / `conversations.replies` called without `limit` get `limit=100` injected by the gateway (Slack would otherwise serve up to 1000 — three 78–98K-char responses in the 86-day audit). An explicit caller `limit` always wins. Pagination via `next_cursor` is unchanged.

## 9. Attribution policy (formalize current behavior)

When `as_bot=False` (the owner's user token, recipient-facing):

- `chat.postMessage`, `chat.update`, `chat.scheduleMessage`, `chat.postEphemeral` → append Block Kit context block: `:blue_heart: <@bot_user_id>` (v1.4: the bot id comes from the profile — `bot_user_id`, or `ATLAS_BOT_USER_ID` for the built-in profile; without it person-voice sends refuse `attribution_unavailable`, never attribute to a guessed bot). If no `blocks` key in params, wrap existing `text` into a section block first.
- `files.uploadV2.initial_comment`, `files.completeUploadExternal.initial_comment`, `chat.meMessage.text` → append text suffix: `\n\n:blue_heart: <@ATLAS_BOT_USER_ID>` (a file-share message cannot carry blocks). `files.uploadV2` with `attribution: "block"` (v1.3): the file is uploaded without the comment, then one `chat.postMessage` carries the comment (auto-split sections), `<permalink|title>` and the context block — two messages, block attribution preserved; `thread_ts` is carried over. Ignored under `as_bot=True`. **v1.4: refused** with `composite_not_in_slice` under `as_bot=False` — the comment post would be a `chat.postMessage` outside the v2 core (no decision, no journal intent); it returns as a composite operation with a child `op_id` (A4).
- All other methods (reactions, pins, reads, profile, dnd) → no injection.

When `as_bot=True` → never inject. The auto-split still applies (v1.3): `chat.postMessage` / `chat.update` / `chat.scheduleMessage` with plain `text` > 3000 chars and no caller `blocks` get the same section split, without the context block; `text` stays as the notification fallback.

v1.1 additions:

- **Streaming is bot-only.** `chat.startStream` / `chat.appendStream` / `chat.stopStream` have no reliable attribution point (chunks arrive incrementally), so `as_bot=False` stream calls are refused pre-flight with `Error: user_token_stream — …` rather than ever posting unattributed. Streams post as Atlas.
- **Auto-split on attribution wrap.** When wrapping plain `text` into blocks, text >3000 chars is split into multiple ≤3000-char `section` blocks at newline/space boundaries (concatenation reproduces the input exactly; multiple sections render seamlessly). Slack's per-section cap otherwise fails the send with `invalid_blocks`. Caller-supplied `blocks` are never rewritten — they fail fast with the `invalid_blocks` hint (§10), pre-checkable via `blocks.validate`. Every auto-split section carries `expand: true`, so the client does not fold each chunk under "Show more" (2026-10-20).
- **Nested params travel as JSON strings.** `slack_api` JSON-encodes every list/dict value (`blocks`, `attachments`, `view`, `metadata`, …) before the form-encoded call, for every method. Before 2026-10-20 a nested list left as its Python repr: strings survived, any boolean broke the block with `invalid_blocks_format`.

## 10. Error policy

**Structural since v1.2:** every row below is raised as a `ToolError`, which FastMCP converts into an MCP result with `is_error=true` — callers detect failure structurally; the text below is the error message. (Pre-v1.2 these were plain success-content strings: a naive error detector saw 0% failures — 11/1045 calls in the 86-day audit were structurally invisible.) The gateway also extracts the Slack error code from `SlackApiError` (the WebClient raises on ok:false), so the hint catalogue fires on live calls — previously the code arrived buried inside `Error: exception — ...`.

| Error type | Message text |
|-----------|--------|
| Slack `ok=false` / `SlackApiError`, error code in hint catalogue | `Error: <error_code> — <hint>` |
| Slack `ok=false` / `SlackApiError`, error code not in catalogue | `Error: <error_code>` |
| Network/SDK exception (no Slack error code) | `Error: exception — <str(exception)>` |
| Missing required params (caught early) | `Error: missing_params — <which>` |
| Caller-supplied section block >3000 chars (v1.2, pre-flight) | `Error: invalid_blocks — blocks[<i>] section text is <n> chars ...` — refused before the API call; auto-split (§9) covers plain `text` only, never caller blocks |

### 10b. Door refusals and outcomes (v1.4, v2 core)

Raised as `ToolError` like every row above, text `Error: <code> — <message>`. **No Slack call was made** for any code in the first table.

| Code | When |
|---|---|
| `frozen` | profile `freeze: true` — every write refuses, reads continue |
| `policy_invalid` | the policy file has bad yaml, a schema error, or its sha256 ≠ the profile's `policy_sha256` pin |
| `profile_missing` / `profile_invalid` | `ATLAS_SLACK_PROFILE` names a missing file / the file is unparsable, has unknown keys or something token-shaped |
| `journal_unavailable` | the journal cannot record the intent (directory unwritable, disk) — nothing is sent, reports to the person included |
| `identity_override` | `username` / `icon_emoji` / `icon_url` on any write (invariant vii) |
| `method_refused` | `auth.revoke` (row 8 — app/token management is not done through the door) |
| `no_decision` | a core send arrived without a decision token (not approved through the hook) |
| `decision_mismatch` | the operation changed after approval: text, channel, thread, any param, voice, class, `scheduled_for` |
| `decision_already_used` | the one-time decision was already consumed by a send |
| `decision_expired` / `decision_unknown` | the approval came more than 30 min after the prompt was issued — nothing was sent; the same call again yields a fresh prompt (a prepared-as-human comm past its own `ttl_s` refuses `ttl_expired` instead) / token not recorded in this door's journal: another profile or journal, or a prompt unanswered for more than a day (unconsumed rows are pruned after 24 h) — nothing was sent, call again |
| `duplicate_comm_id` | the `comm_id` already has a write (ok/pending/unknown); pass `resend: true` with a fresh approval |
| `duplicate_op_id` | the `op_id` is already reserved |
| `ttl_expired` / `ttl_required` / `comm_id_required` | `ttl_s` elapsed since the journal's first sighting of the `comm_id` / `origin: prepared-as-human` without `ttl_s` / `origin: prepared-as-human` without `comm_id` (get one from `slack_api("_comm_id")`) |
| `prepared_at_future` | caller `prepared_at`, or the journal's first sighting, is > 60 s in the future |
| `not_enabled_by_policy` | the class is off for that channel (built-in strict: B-a, C1, C2) |
| `recipient_denied` | channel or a declared recipient is on the policy deny list for the class |
| `voice_mismatch` / `prepared_as_human_bot_voice` | provenance voice ≠ token voice or ≠ the class's voice / prepared-as-human in the bot voice |
| `target_mismatch` | `provenance.target.channel` ≠ `params.channel` |
| `provenance_invalid` | provenance fails the schema (unknown fields, bad class, ...) |
| `user_token_in_process` / `no_notary` | autonomous class B-a/C1 in a process holding the person's token / without a notary stamp |
| `c2_not_to_person` | class C2 posting anywhere but the person's bot DM |
| `attribution_unavailable` | person-voice send and the profile has no `bot_user_id` |
| `token_unavailable` | the token env var for the chosen voice is not set |
| `no_decision_adapter` | library face (`Door`): the send needs a human and the library has no adapter |
| `missing_params` | `chat.postMessage` without `channel` |
| `method_noncanonical` | the method name is not exactly a Slack method name — another case or whitespace around a registered name (`"chat.PostMessage"`, `"chat.postMessage "`), or not a valid dotted name; the message names the canonical spelling |
| `profile_mismatch` | the decision was issued by the hook under another profile than the MCP server runs — `ATLAS_SLACK_PROFILE` differs between the two environments |
| `permission_mode_unsafe` | (hook `deny`) the session's permission mode cannot guarantee that a human sees the prompt — `bypassPermissions`, `dontAsk` or an unknown mode; allowed list: profile `decision_permission_modes` (default: default, acceptEdits, plan, auto) |
| `composite_not_in_slice` | `files.uploadV2` with `attribution: "block"` (person voice) — its comment post would be a `chat.postMessage` outside the core |
| `provenance_required` | an autonomous class (B-a, C1) without real provenance |
| `voice_not_allowed` | the policy's `voices` for the channel exclude the voice |
| `read_scope_violation` | library manifest: a read outside `read_scope` (channel not listed, channel-less method not in `read_scope.methods`, any `search.*`) |
| `method_not_in_manifest` / `target_not_in_manifest` / `class_not_in_manifest` | library manifest: method not in `allowed_methods` / channel not in `allowed_targets` / provenance class ≠ manifest `cls` |
| `not_in_slice` | library face: a transitional write or an unregistered read — the library runs registered reads and `chat.postMessage` only |

Pre-flight codes kept from v1.1–v1.3 (no Slack call): `user_token_stream` (streams under the user token), `invalid_blocks` (caller section > 3000 chars), `invalid_arguments` (`channels` as a string on `files.uploadV2`), `file_not_found` (upload path missing / not absolute), `exception` (SDK or network error without a Slack code — on the transitional path; on the core path it is `outcome_unknown`).

After the call (the intent row exists) — `outcome_not_journaled` is **not** an error: Slack accepted the post (ts shown) but the outcome row could not be written; the result ends with `⚠ outcome_not_journaled: sent (ts=…) … do not resend` and the op stays `pending` → `unknown` in the journal. Slack `ok=false` → `Error: <code> — <hint> — journal: op <id> error` (outcome `error`, nothing posted). Timeout / connection error / any non-Slack exception → `Error: outcome_unknown — … outcome unknown (op <id>); reconcile before any resend, never retry blindly` (outcome `unknown`).

### How hints work

Hint catalogue lives as a `dict[str, str]` constant in `formatters.py`:

```python
ERROR_HINTS = {
    "channel_not_found": "DM channel IDs are per-viewer; try conversations.open from the correct token side.",
    "not_in_channel":    "Atlas isn't a member; invite via conversations.invite or post as user.",
    "missing_scope":     "Token lacks the scope; check auth.test for granted scopes.",
    "cannot_dm_bot":     "Target is a bot account — DMs not supported.",
    "is_archived":       "Channel is archived; unarchive first or pick another.",
    "invalid_blocks":    "Per-block limits exceeded — section text caps at 3000 chars; split into multiple sections or pre-check with blocks.validate.",  # v1.1
}
```

`format_error(code, ...)` does `ERROR_HINTS.get(code)` — appends hint if matched, returns plain `Error: <code>` otherwise. No regex, no pattern matching, just dict lookup.

**Why this works:** the LLM caller reads `Error: channel_not_found — DM channel IDs are per-viewer; try conversations.open...` and knows the recovery path in one shot — no second round-trip to figure out what to try. Without the hint, the LLM has to know recovery patterns for every Slack error code, or guess.

**Extension discipline:** add a hint when a recovery path proves itself useful 2+ times. One-off errors stay un-hinted. Each hint is one line, imperative, names the specific recovery method.

Hints are advisory, not authoritative — caller can override based on context.

## 11. Out of scope

- **Raw JSON access mode.** Not exposing a `raw=True` parameter — single output format keeps SKILL guidance simple. If raw is genuinely needed, caller can `slack_api` against a method whose shape isn't in §5 (falls into §4.16 JSON fallback).
- **Multi-workspace.** One workspace per profile (v1.4: profile `workspace_id`, `bot_user_id`; `Door.doctor()` checks an `auth.test` against them). A second workspace = a second profile.
- **Streaming / partial responses.** Slack SDK is request/response; no streaming.
- **Localization.** All output English. Slack content is preserved verbatim including non-Latin scripts.
- **Block Kit pretty-printing.** When a message has `blocks`, formatter shows `text` (Slack-rendered fallback). Pretty-printing block trees would balloon output.

## 12. Resolved questions (was: Open questions for the owner review)

Locked 2026-04-30 in review with the owner.

1. **Message list cap = 200.** ✅
2. **Topic/purpose — no cap.** ✅ Body-text class, no truncation.
3. **Reactions aggregated as `👍3 ❤️1`.** ✅
4. **Pin list message text — full.** ✅
5. **Search results — preserve Slack-side truncation, document follow-up in SKILL.** ✅
6. **Unknown method — JSON fallback.** ✅
7. **Hint catalogue — start with 5 listed.** ✅ Mechanism: dict lookup in `format_error()`. See §10 "How hints work". Extension when recovery path proves useful 2+ times.
8. **`is_general` — keep when true.** ✅ Acts as a "tread carefully" guardrail signal — `#general` is the all-hands channel; posting there is high-blast-radius. Showing the marker lets Atlas raise approval rigor automatically when about to post (e.g. force draft-then-confirm even if class default is direct send). Cost is one token in the rare info responses where it matters.

## 13. Versioning & change discipline

- This file is the source of truth. Code changes that alter output go via spec change first.
- Semver discipline:
  - **patch** — bug fix, no contract change (e.g. handler crashes on missing field, fix without altering output)
  - **minor** — additive (new shape, new optional field in existing shape, new error hint)
  - **major** — breaking (output format change, removed field, renamed key)
- Changelog at top of file from v1.0+.
- Tests in `tests/test_formatters.py` mirror shape sections — one test class per §4 subsection.

## 14. Status

v1.4 — 2026-10-28 on branch `feat/v2-core` (not installed). v1.3 — 2026-10-22. v1.2 — 2026-09-01, implemented in the v1.2 build (headless AI-SDLC pilot run from the Slack-stack audit). v1.0 locked 2026-04-30 with the owner; v1.1 additions 2026-08-21.


## 15. Provenance, verdict text, `_rights` (v1.4)

**`provenance`** (optional object, `chat.postMessage`; schema in `docs/policy-schema.md` § Provenance): `cls` (`A` person voice · `B-h` bot voice, human-approved · `B-a` / `C1` / `C2` autonomous — refused by the built-in strict policy), `voice`, `comm_id`, `op_id`, `prepared_by`, `target {channel, users, is_mpim}`, `checks {…}`, `sources […]`, `origin` (`live` | `prepared-as-human`), `ttl_s`, `prepared_at`, `scheduled_for`, `resend`, `decision` (set by the hook — callers never set it). Without provenance a send still asks, with the warning `provenance missing — migration rule A12.2` (classes A/B-h only).

**Approval prompt** (hook `permissionDecisionReason`, ask; v1.4 after the live check — language from the profile):

```
<sender> → <target>: “<text>” | message below (<n> chars). <voice sentence>. <Resend.>? Send?
<text — full up to 400 chars, else first 400 + "… (<n> chars)">?      ← only when not quoted inline (> 120 chars or multi-line)
<checks: k=v, …>?                                                     ← only when the context declared checks
<⚠ no context policy — the built-in strict policy applies>?           ← only under the built-in policy
<ledger: ✅|☑|⚠ …>? <intention: …>?                                     ← v1.6, only with a ledger dir (profile `ledger_dir` / env ATLAS_SLACK_LEDGER_DIR): the audit field and intention of the ledger file behind comm_id
class <cls> · policy <version> <· comm <comm_id>>? · op <8 hex> <· blocks N>? <· params a,b>?
```

`sender` = the bot's or the person's display name; `target` = `#channel`, `DM <name>`, `DM <person>↔<bot>` (the person's bot DM), `group DM <names>`, with ` (in thread)` / ` + also to the channel` (reply_broadcast); an id that cannot be resolved stays as `<id> (unresolved)`. Russian: «Atlas → DM владелец↔Atlas: “…”. Голос бота, по твоему запросу. Отправить?». Row, mode, profile, origin and empty checks are in the journal, not in the prompt. The tool input Claude Code shows above it is compact: the caller's provenance keys plus `decision: {t, p}`.

Transitional writes keep the 0.4.0 text with ` (transitional — not yet under the v2 core; today's gate semantics)` appended; `conversations.open` keeps its GATE-001 text. Refusals reach Claude as a hook `deny`: `atlas-slack refused — <code>: <message>` — and, since v1.5, land in the call journal as `status=refused, caller=hook, error_code=<code>` whenever the hook has a journal at hand (the server never sees a hook-denied call, so the hook is the only place that can record it; a deny caused by the journal itself being unavailable cannot be recorded).

**Provenance defaults (v1.5).** `cls` omitted → derived from `as_bot` (`A` / `B-h`), so `{"comm_id": "…"}` is the minimal valid provenance. **`slack_api("_comm_id")`** (local pseudo-method like `_rights`, no Slack call) returns a journal-unique `YYYYMMDD-<4 hex>` and notes the comm's first sighting — the id for a ledger draft before anything is sent; `prepared-as-human` requires one (`comm_id_required`). `comm_id` omitted (or no provenance at all) → the door mints `YYYYMMDD-<4 hex>` from OS entropy, unique in this journal, outside the decision binding (the hash has no comm_id on either side), and returns it in the result line; the op row's provenance carries `comm_id_source: door`.

**`slack_api("_rights")`** — text table: header (profile, policy version/state/digest, freeze, capabilities: tokens, journal path, decision adapters, notary, identity source), then rows 0/1/2/4 × classes A, B-h, B-a, C1, C2 with `allow`, `human decision`, `disabled: <why>`, `refuse (<why>)`, then the classified channels of the policy. No Slack call.

---

## Appendix A — Why we needed this

The current `format_response` (commit `2f91d4b`, 2026-04-08) is fallthrough-by-key with one shared list-handler across `(channels, users, members, messages, files, items, reminders, pins)`. One lookup chain `name or text or id` and one 80-char cap for all of them. Code reuse won over semantic clarity.

That chose to treat `text` as a label across all shapes. For channels/users it's true — `text` is description, OK to truncate. For messages it's not — `text` is the body, the entire payload. Six bugs found in 5 minutes of reading (see §B).

The redesign isn't because the original code is bad — it's because the contract was never explicit. Without a contract, a fallthrough is the natural shape; with a contract, dispatch becomes obvious.

## Appendix B — Bugs this contract closes

| # | Bug | Symptom | Closed by |
|---|-----|---------|-----------|
| 1 | 80-char truncation on message body | `Hey all, Thanks for the time and miles last week on the TikTok Worksh...` | §4.2, §6 (no cap on body) |
| 2 | List cap 50 hides long threads | Threads >50 replies show `...and N more` without cursor | §7 (cap 200) + §8 (cursor pass) |
| 3 | `response_metadata.next_cursor` stripped | Pagination requires guesswork | §8 (cursor explicit) |
| 4 | `files.uploadV2` permalink lost | Post-upload share requires second call | §4.8, §4.9 (permalink first) |
| 5 | `conversations.info` shows only `#name (ID)` | Topic, purpose, member count gone | §4.3 |
| 6 | `users.profile.get` for others returns near-empty | Real name, email, title missing | §4.5 |

## Appendix C — Trade-offs accepted

- Output is text not JSON. JSON would be unambiguous for parsers but heavier for LLMs. Text-as-API matches actual usage pattern.
- Per-shape order is fixed. Callers can't reorder. Trade: predictability over flexibility.
- 200/100/50/20 list caps are policy. Will need adjustment as usage patterns surface.
- No streaming. Slack SDK doesn't, neither do we. Non-issue at current scale.
