---
title: atlas-slack — Output Contract
status: locked
version: 1.0
created: 2026-04-30
locked: 2026-04-30
owner: Dmitry Kushnikov
---

# atlas-slack — Output Contract

What `slack_api(method, params, as_bot=...)` and convenience tools (`slack_react`, `slack_upload`, etc.) return as text. The contract LLM consumers can rely on, and the contract `formatters.py` must implement.

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
```

Preserved: `ts`, `text` (full), `thread_ts` (if present), `channel` (echo).
Dropped: `bot_profile`, `app_id`, `team`, internals.

### 4.2 Message list (`conversations.history`, `conversations.replies`, `search.messages`)

```
N messages: <next_cursor=<cursor>>?
  [<ts>] <<user>> <thread_ts=<thread_ts>>? <replies=N>? <👍3 ❤️1>?
  <text — full, no cap>

  [<ts>] ...
  ... and N more
```

Preserved per item: `ts`, `user`, `text` (full), `thread_ts` (if reply), reactions.
- **Reactions** rendered as visual aggregate `👍3 ❤️1 🎉2` (emoji char + count, space-separated). Omitted entirely when no reactions.
- **`thread_ts` shown only when message is a reply** (`thread_ts` present and `!= ts`). For thread parents, `reply_count` field signals thread existence — shown as `replies=N` instead.
- List cap: **200 messages**. Slack's natural max is 1000 per call.
- Cursor: passed through in header only when non-empty.

> Search results note: Slack's `search.messages` returns highlighted/truncated `text` server-side. Formatter preserves verbatim — caller should follow up with `conversations.replies(channel, ts)` if full body needed. This is documented in §5 of SKILL.md.

### 4.3 Channel info (`conversations.info`, `conversations.create`)

```
Channel: #<name> (ID: <id>) <private>? <archived>? <general>?
  members: <num_members>
  topic: <topic.value>
  purpose: <purpose.value>
  created: <YYYY-MM-DD>
```

`<general>?` — emitted as the literal token `general` in the header line when `is_general=true`. Acts as a "tread carefully" signal for posts; everyone sees this channel.

Preserved: `id`, `name`, `is_private`, `is_archived`, `is_general` (when true), `num_members`, `topic.value`, `purpose.value`, `created`.
`topic`/`purpose`: full, no cap.
Dropped: `last_read`, `latest`, `parent_conversation`, `pending_shared`, locale.

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

`permalink` is **first** after name — most common need post-upload.

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

Last resort. Stable behavior: never raise, never truncate, just dump.

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

## 9. Attribution policy (formalize current behavior)

When `as_bot=False` (the owner's user token, recipient-facing):

- `chat.postMessage`, `chat.update`, `chat.scheduleMessage`, `chat.postEphemeral` → append Block Kit context block: `:blue_heart: <@ATLAS_BOT_USER_ID>`. If no `blocks` key in params, wrap existing `text` into a section block first.
- `files.completeUploadExternal.initial_comment`, `chat.meMessage.text` → append text suffix: `\n\n:blue_heart: <@ATLAS_BOT_USER_ID>`.
- All other methods (reactions, pins, reads, profile, dnd) → no injection.

When `as_bot=True` → never inject.

This is unchanged from current `gateway.py:67-79`. Spec'd here so it doesn't drift.

## 10. Error policy

| Error type | Output |
|-----------|--------|
| Slack `ok=false`, error code in hint catalogue | `Error: <error_code> — <hint>` |
| Slack `ok=false`, error code not in catalogue | `Error: <error_code>` |
| Network/SDK exception | `Error: exception — <str(exception)>` |
| Missing required params (caught early) | `Error: missing_params — <which>` |

### How hints work

Hint catalogue lives as a `dict[str, str]` constant in `formatters.py`:

```python
ERROR_HINTS = {
    "channel_not_found": "DM channel IDs are per-viewer; try conversations.open from the correct token side.",
    "not_in_channel":    "Atlas isn't a member; invite via conversations.invite or post as user.",
    "missing_scope":     "Token lacks the scope; check auth.test for granted scopes.",
    "cannot_dm_bot":     "Target is a bot account — DMs not supported.",
    "is_archived":       "Channel is archived; unarchive first or pick another.",
}
```

`format_error(code, ...)` does `ERROR_HINTS.get(code)` — appends hint if matched, returns plain `Error: <code>` otherwise. No regex, no pattern matching, just dict lookup.

**Why this works:** the LLM caller reads `Error: channel_not_found — DM channel IDs are per-viewer; try conversations.open...` and knows the recovery path in one shot — no second round-trip to figure out what to try. Without the hint, the LLM has to know recovery patterns for every Slack error code, or guess.

**Extension discipline:** add a hint when a recovery path proves itself useful 2+ times. One-off errors stay un-hinted. Each hint is one line, imperative, names the specific recovery method.

Hints are advisory, not authoritative — caller can override based on context.

## 11. Out of scope

- **Raw JSON access mode.** Not exposing a `raw=True` parameter — single output format keeps SKILL guidance simple. If raw is genuinely needed, caller can `slack_api` against a method whose shape isn't in §5 (falls into §4.16 JSON fallback).
- **Multi-workspace.** `ATLAS_BOT_USER_ID` is hardcoded to Acme; multi-workspace work is a separate spec.
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

v1.0 — locked 2026-04-30 with the owner. Implementation begins: redesign `format_response` per §4 dispatch + tests (unit per shape, gateway smoke, e2e against real Slack).

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
