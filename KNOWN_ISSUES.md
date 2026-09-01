# atlas-slack — Known Issues

## FMT-001 — Message body truncated to 80 chars in list outputs

**Severity:** High · **Status:** Resolved · **Reported:** 2026-04-30 · **Fixed:** 2026-04-30

### Symptom

`slack_api('conversations.replies', ...)` (and any other list-shape method) returned each message's `text` truncated to 80 characters with `...` suffix, regardless of body length. A 1500-char post came back as `Hey all, Thanks for investing the time and miles last week on the TikTok Worksh...`. Fetching by Slack permalink required two calls: first one returned the preview, second was needed for full body — defeating the point of `conversations.replies`.

### Root cause

`src/atlas_slack/formatters.py:53` (pre-fix):

```python
for item in items[:50]:
    if isinstance(item, dict):
        label = item.get("name") or item.get("text") or item.get("id") or str(item)
        if isinstance(label, str) and len(label) > 80:
            label = label[:80] + "..."
```

Single shared list-handler treated `text` as a `label` field across all shapes (channels, users, files, messages, items, pins). For `channels`/`users` that's correct — `text` is description, OK to truncate. For `messages` it's wrong — `text` is the body, the entire payload. Code reuse won over semantic clarity. Six bugs of this category surfaced in 5 minutes of code review (see Appendix B of `docs/output-contract.md`).

### Resolution (2026-04-30)

Two-stage fix:

1. **Hot patch** — split `messages` branch in the shared list handler. Body preserved full, other shapes keep 80-char preview. 2 regression tests added.
2. **Redesign per spec** — locked output contract `docs/output-contract.md` v1.0 with the owner, then refactored `format_response` from fallthrough-by-key to dispatch-by-shape. 16 explicit shape handlers, error hint catalogue (5 starters), `response_metadata.next_cursor` passthrough, warning prefix line, list caps per shape (200 messages, 100 channels, 50 users, 20 files, etc.).

This closed five additional bugs of the same category that hadn't surfaced as user complaints yet:
- List cap of 50 messages hid long threads without surfacing cursor
- `response_metadata.next_cursor` was stripped, breaking pagination
- `files.uploadV2` permalink was lost (most-needed field after upload)
- `conversations.info` showed only `#name (ID)` — topic, purpose, num_members dropped
- `users.profile.get` for other users returned near-empty (real_name/email/title missing)

### Test coverage

- `tests/test_formatters.py` — 70 unit tests, one class per `output-contract.md` §4 shape
- `tests/test_gateway.py` — 8 contract smoke tests verifying end-to-end through gateway (full text, cursor, warning, hint, file permalink, channel info metadata, chat.delete Done ✓, list cap markers)
- `tests/test_e2e.py` — 6 live-Slack tests gated by `SLACK_E2E=1`

Total: 126 passing + 6 e2e skipped without env flag.

### Related references

- Spec: `docs/output-contract.md` — locked v1.0, 2026-04-30
- Memory: `~/.claude/projects/-Users-owner-Obsidian-Acme/memory/feedback_authored_doc_review.md`
- Same class of failure as ATT-001 (tool description vs tool behavior drift) — both resolved by replacing implicit fallthroughs with explicit contracts

---

## ATT-001 — Attribution footer applied only to `chat.postMessage`

**Severity:** Medium · **Status:** Resolved · **Reported:** 2026-04-13 · **Fixed:** 2026-04-14

### Symptom

When `slack_api` is called with `as_bot=False` (i.e. posting as the owner with `@Atlas` attribution expected), the `:blue_heart: @Atlas` Block Kit footer is appended **only** if `method == "chat.postMessage"`. Any other text-bearing method delivers the owner-only output.

Concretely: a file send via `files.completeUploadExternal` with substantive `initial_comment` arrived in a thread without the @Atlas marker (verified 2026-04-13, DM `DU37R64MT`, file `F0ASM37EEAW`).

### Root cause

`src/atlas_slack/gateway.py:55`:

```python
if method == "chat.postMessage" and not as_bot:
    call_params = _inject_attribution(call_params)
```

Attribution helper `_inject_attribution()` is gated on a single method name. The MCP tool description (`gateway.py:17`) promises broader coverage:

> "as_bot=False (default) posts as the owner with :blue_heart: @Atlas attribution footer."

This contradicts current behavior — classic tool-description-vs-tool-behavior drift.

### Methods that should also receive attribution

Any method that posts recipient-facing text under the user token:

- `chat.postMessage` ✓ already covered
- `chat.scheduleMessage` — text/blocks/attachments
- `chat.postEphemeral` — text/blocks/attachments
- `chat.update` — text/blocks/attachments (when editing user-token messages)
- `files.completeUploadExternal` — `initial_comment`
- `files.getUploadURLExternal` → `completeUploadExternal` chain — same `initial_comment` concern

### Proposed fix — universal middleware

Replace method-specific gate with parameter-shape detection. When `as_bot=False`, scan call_params for any text-bearing key and inject attribution accordingly.

```python
# Sketch — actual implementation may want named per-method handlers
TEXT_KEYS = ("text", "initial_comment", "blocks", "attachments")

def _needs_attribution(method: str, params: dict, as_bot: bool) -> bool:
    if as_bot:
        return False
    return any(k in params for k in TEXT_KEYS)

def _inject_attribution_for(method: str, params: dict) -> dict:
    """Method-aware attribution: add Block Kit context block where supported,
    fall back to text suffix where blocks aren't allowed."""
    # chat.postMessage / chat.update / chat.scheduleMessage / chat.postEphemeral
    #   → blocks-based attribution (existing _inject_attribution)
    # files.completeUploadExternal
    #   → suffix on initial_comment (no blocks support)
    ...
```

### Test coverage to add

`tests/test_gateway.py` should assert:
1. `chat.postMessage` with `as_bot=False` gets attribution block (regression).
2. `chat.postMessage` with `as_bot=True` gets no attribution block (regression).
3. `files.completeUploadExternal` with `initial_comment` and `as_bot=False` → attribution suffix appended to comment.
4. `chat.scheduleMessage`, `chat.postEphemeral`, `chat.update` likewise.
5. Read methods (`*.list`, `*.info`, etc.) untouched regardless of params.

### Resolution (2026-04-14)

Replaced method-specific gate with whitelist-based `_maybe_inject_attribution(method, params)` middleware in `src/atlas_slack/gateway.py`:

- **Blocks path** (Block Kit context block appended) — `chat.postMessage`, `chat.update`, `chat.scheduleMessage`, `chat.postEphemeral`.
- **Suffix path** (text suffix on `initial_comment`) — `files.completeUploadExternal`.
- **No injection** — reactions, pins, reads, user-state changes, anything not in whitelist.

Coverage in `tests/test_gateway.py`: 9 new assertions covering both paths, bot-mode skips, and whitelist boundary (read methods untouched).

The manual workarounds (A/B) documented earlier in the skill are no longer necessary and have been removed from `plugins/atlas-slack/skills/atlas-slack.md`.

### Related references

- Memory: `~/.claude/projects/-Users-owner-Atlas/memory/reference/reference_atlas-slack-footer-coverage.md`
- Memory: `~/.claude/projects/-Users-owner-Atlas/memory/feedback/feedback_tool-encodes-truth.md` — same class of issue
- Memory: `~/.claude/projects/-Users-owner-Atlas/memory/feedback/feedback_source-and-empirical-before-tooling.md` — verify tool semantics empirically

---

## FMT-002 — Attachments (message shares) and files silently dropped from history output

**Severity:** High · **Status:** Resolved · **Reported:** 2026-08-21 · **Fixed:** 2026-08-21 · **Issue:** [#3](https://github.com/dkushnikov/atlas-slack/issues/3)

### Symptom

`conversations.history` / `conversations.replies` / `pins.list` rendered only each message's `text`. Message-share attachments (`attachments[]` — the entire quoted message of a Slack forward) and file stubs (`files[]`) were invisible. Real case: a message reading "sharing also the exact request from Lu, for full transparency:" carried the whole quoted request as an attachment — unreadable through the MCP tool; fallback was manual screenshots.

### Resolution

Output contract v1.1 §4.2b: attachments render as indented blocks (`↳ [shared message] <author> … text … footer`), files as stubs (`📎 file <id>: name (type, size)`) feeding `slack_download`. Payload class — attachment text never truncated. Render caps: 5 attachments / 10 files per message. Applies to §4.2 lists and §4.12 pins. 11 tests in `TestMessageAttachmentsAndFiles`.

---

## FMT-003 — search.messages crashed on real nested response shape

**Severity:** High · **Status:** Resolved · **Reported:** 2026-08-21 · **Fixed:** 2026-08-21

### Symptom

Real `search.messages` responses nest the list: `{"messages": {"total": N, "matches": [...]}}`. The §4.2 handler assumed a flat list and sliced the dict → `TypeError`, surfaced as `Error: exception — unhashable type: 'slice'`. This is the long-standing "search.messages broken → use conversations.history" workaround recorded in Atlas memory (2026-05).

### Resolution

Handler unwraps `messages.matches` when `messages` is a dict (contract v1.1 §4.2 note). Search results now render, including attachments per §4.2b. Note: Slack still server-truncates `text` in search results — follow up with `conversations.replies` for full bodies (unchanged §4.2 guidance).

---

## ERR-001 — Slack failures structurally indistinguishable from success

**Severity:** High · **Status:** Resolved · **Reported:** 2026-09-01 (audit) · **Fixed:** 2026-09-01 (v1.2)

### Symptom

Every Slack failure (`ok:false`, `SlackApiError`, network exceptions) returned as *success content* — a plain string starting with `Error:`. At the MCP level `is_error` was never set: a naive error detector saw a 0% failure rate, and 11/1045 calls in the 86-day audit were structurally invisible failures. Same class as FMT-001/FMT-003: a tool artifact arriving as a plausible fact about Slack.

Compounding bug: slack_sdk's `WebClient` **raises** `SlackApiError` on `ok:false`, so the `format_response` ok-false branch (and with it the §10 `ERROR_HINTS` catalogue) was bypassed on live calls — errors surfaced as `Error: exception — ...The server responded with: {'ok': False, 'error': 'channel_not_found'}` with no hint.

### Resolution (v1.2)

Gateway raises `ToolError` on all failure paths → FastMCP sets `is_error=true`; the Slack error code is extracted from `SlackApiError` so `Error: <code> — <hint>` fires again. `slack_download` converted likewise. Live-verified same day: a bot-token call against a user-side DM ID returned a structural error whose hint ("DM channel IDs are per-viewer...") correctly diagnosed the failure. Contract v1.2 §10; tests in `test_gateway.py` / `test_tools.py`.

---

## FMT-004 — Unread state stripped from conversations.info (unread-blindness)

**Severity:** Medium · **Status:** Resolved · **Reported:** 2026-09-01 · **Fixed:** 2026-09-01 (v1.2)

### Symptom

`_h_channel_info`'s §4.3 whitelist dropped `last_read` / `unread_count` / `unread_count_display` — "what's unread in my DM with X?" was unanswerable through the tool (incident 2026-09-01). DMs also rendered as `Channel: #? (ID: D...)`.

### Resolution (v1.2)

§4.3 renders `last_read:` and `unread: N (M display)` when present (`unread: 0` shown — "all read" is information); DMs render `Channel: DM with <user>`. Unit tests in `TestChannelInfo`.

---

## FMT-005 — Unbounded conversations.history/replies responses

**Severity:** Medium · **Status:** Resolved · **Reported:** 2026-08 (3 incidents) · **Fixed:** 2026-09-01 (v1.2)

### Symptom

Calls without `limit` let Slack serve up to 1000 messages — three oversized responses (78–98K chars) hit sessions on 08-04/13/15. The §7 render cap (200) bounded *rendering*, not payload fetched and formatted.

### Resolution (v1.2)

Gateway injects `limit=100` when the caller passes none; explicit `limit` always wins. Contract v1.2 §8.

---

## FMT-006 — Parallel list responses not attributable to their calls

**Severity:** Medium · **Status:** Resolved · **Reported:** 2026-04-28 · **Fixed:** 2026-09-01 (v1.2)

### Symptom

List bodies don't self-identify (`12 members:` — of which channel?). Two parallel `conversations.members` calls were attributable only by response order — the 04-28 mixed-up-responses incident.

### Resolution (v1.2)

List shapes lead with an args-echo line `» <method> channel=C123 ...` echoing the caller's original request args. Contract v1.2 §8; `TestArgsEcho`.

---

## UPL-001 — slack_upload truncated DM channel IDs (channels → "D")

**Severity:** Medium · **Status:** Closed (tool removed; root cause identified) · **Reported:** 2026-04-10 · **Closed:** 2026-09-01 (v1.2)

### Symptom

`slack_upload(channels="D06MG7QF3ME", ...)` failed with `invalid_channel` — Slack received `channel=D` (observed 2026-04-10, Pearch artifacts to a DM).

### Root cause

`tools.py` passed `channels` as a comma-separated **string**; slack_sdk `files_upload_v2` types it `List[str]` and serializes with `",".join(channels)` — joining a plain string character-by-character (`"D06MG7QF3ME"` → `"D,0,6,M,..."`; Slack reads element one, `D`). Re-verified live 2026-09-01 (v1.1 call shape vs DM D2SH1V09K → `channel_not_found`): the bug did **not** die in v1.1.

### Resolution (v1.2)

`slack_upload` removed with the convenience-wrapper cut (fix #7). The documented 3-step flow (`files.getUploadURLExternal` → binary PUT → `files.completeUploadExternal` with string `channel_id`) is immune, live-verified into DM D2SH1V09K same day (file `F0BU04ZPE3D`, shared with attribution suffix). Gotcha recorded in the skill: DM IDs are per-viewer — bot-token uploads need the bot-side DM ID via `conversations.open`. MC memory `feedback_slack_upload_dm_bug` updated/closed.

---

## GATE-001 — conversations.open confirmed as if a read-like lookup

**Severity:** Low · **Status:** Resolved · **Reported:** 2026-08-28 · **Fixed:** 2026-09-01 (v1.2)

### Symptom

The gate's ask-text for `conversations.open` was the generic `Slack write: conversations.open`, hiding that the call **creates** a DM/MPIM — an accidental empty multi-party DM was created during a "lookup" (08-28; cleaned up 09-01).

### Resolution (v1.2)

`mcp-slack-gate.py` emits an explanatory reason: creates/opens DM vs group DM (MPIM member list included), and points at `users.conversations` as the read-side lookup — which is now correctly classified as a read (it previously fell through to "ask" because `.conversations` matched no read suffix). Tests in `TestConversationsOpenHint`.
