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
