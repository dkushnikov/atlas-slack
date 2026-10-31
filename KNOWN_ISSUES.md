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

- This file plus `tests/test_gateway.py` (footer assertions on both paths) are the source of truth for footer coverage. The Atlas reference memory that duplicated it was retired 2026-10-14; its history is in `<atlas>` git.
- Same class of issue — a doc that softly disagrees with the shipped tool: `<atlas>/memory/feedback/feedback_verify-premise-when-advocating.md` (the mechanism is the truth; provenance decides whether the doc is stale or ahead).
- Verify tool semantics empirically before encoding them: `<atlas>/rules/reality-check.md` (docs → installed source → empirical test).

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

`slack_upload` removed with the convenience-wrapper cut (fix #7). The documented 3-step flow (`files.getUploadURLExternal` → binary PUT → `files.completeUploadExternal` with string `channel_id`) is immune, live-verified into DM D2SH1V09K same day (file `F0BU04ZPE3D`, shared with attribution suffix) — but it left agent sessions without a tool for the PUT; see UPL-002 for the v1.3 `files.uploadV2` mapping that replaced it as the documented path. Gotcha recorded in the skill: DM IDs are per-viewer — bot-token uploads need the bot-side DM ID via `conversations.open`. MC memory `feedback_slack_upload_dm_bug` updated/closed.

---

## GATE-001 — conversations.open confirmed as if a read-like lookup

**Severity:** Low · **Status:** Resolved · **Reported:** 2026-08-28 · **Fixed:** 2026-09-01 (v1.2)

### Symptom

The gate's ask-text for `conversations.open` was the generic `Slack write: conversations.open`, hiding that the call **creates** a DM/MPIM — an accidental empty multi-party DM was created during a "lookup" (08-28; cleaned up 09-01).

### Resolution (v1.2)

`mcp-slack-gate.py` emits an explanatory reason: creates/opens DM vs group DM (MPIM member list included), and points at `users.conversations` as the read-side lookup — which is now correctly classified as a read (it previously fell through to "ask" because `.conversations` matched no read suffix). Tests in `TestConversationsOpenHint`.

---

## UPL-002 — No upload path from agent sessions: `files.uploadV2` is not a Web API method, binary PUT has no tool

**Severity:** Medium · **Status:** Resolved · **Reported:** 2026-09-03 · **Fixed:** 2026-10-22 (v1.3) · **Issue:** [#4](https://github.com/dkushnikov/atlas-slack/issues/4)

### Symptom

`slack_api('files.uploadV2', …)` returns `unknown_method` live (bot token, 2026-09-03): the method exists only as slack_sdk `WebClient.files_upload_v2`, and the gateway dispatches through `client.api_call`. `tests/test_gateway.py::test_smoke_file_upload_permalink_returned` passes against a mock that accepts any method name — false-green. The documented 3-step flow (`files.getUploadURLExternal` → binary PUT → `files.completeUploadExternal`) has no tool for the PUT: the gateway cannot POST bytes, `curl`/`wget` are denied in the cockpit and vault permissions. Incident: the Creator Post Boosting one-pager for a colleague (23:27 BST, `F0BUY507FPT`) was uploaded with a Python stdlib HTTP call outside the plugin. Visible side effect: file posts carry the `:blue_heart: @Atlas` attribution as a text suffix (`SUFFIX_METHODS`), `chat.postMessage` as a context block (`BLOCKS_METHODS`) — by design (ATT-001), but it reads as inconsistent (compare `C0BRX9SLQA0/p1788471220408099` and `D0A1D8X25SL/p1788472027062419`).

### Resolution (v1.3)

**0.6.0 (2026-10-31):** `files.uploadV2` is a core send — pre-flight in `shaping.prepare_upload`, the file bytes bound by sha256 into the decision, dispatch in `gateway._sdk_transport`; `attribution: "block"` is a composite op (child `<op>.c1`). Below, the v1.3 text: `gateway._upload_v2` maps `files.uploadV2` to `client.files_upload_v2(...)` (slack_sdk 3.41.0 runs the three steps internally); the v1.2 surface is unchanged. Pre-flight: a string in `channels` is refused with a pointer to `channel` (UPL-001 regression guard); `file` must be an absolute, existing path (the MCP server's cwd is not the session's); `content` / `file_uploads` pass through. Attribution (user token): text suffix on `initial_comment` by default; `attribution: "block"` uploads without the comment and posts it as a separate `chat.postMessage` with the context block and `<permalink|title>` — the result carries `comment: posted ts=…`. Bot uploads untouched. The gate asks on `files.uploadV2` (fall-through write; pinned in `TestSlackApiGate.test_upload_v2_asks`). The false-green smoke test now asserts the dispatch (`files_upload_v2` called, `api_call` not). Live-verified 2026-10-22: bot upload into `D0ARL945PD1`, user upload into `D2SH1V09K` (e2e `test_file_upload_v2_round_trip`, both modes), block-mode upload + comment in `D2SH1V09K` (file + message deleted after). Repeat offenders before the fix: a colleague one-pager (09-03), Lu memo (10-15), two colleagues SQL (10-17) — each a hand-rolled PUT outside the plugin.

### Related references

- UPL-001 (wrapper removal), ATT-001 (suffix vs block by method)
- Ledger of the incident: MC vault `notes/comms/2026-09-03--colleague--post-boosting-one-pager.md`

## FMT-007 — search.messages output drops `channel` and `permalink` of each match

**Severity:** Medium · **Status:** Resolved on branch `feat/v2-core` (contract v1.4, not installed) · **Reported:** 2026-10-28 (Codex review, reproduced on the formatter with a synthetic response) · **Fix:** with the v2 read path (project.md § Strategy п. 17)

### Symptom
`format_response("search.messages", …)` renders `[ts] <user>` + text only; the match's `channel` (id/name) and `permalink` are not in the output, so the agent cannot follow a hit to its source thread without a second guess. The skill's own rule «search = discovery only → `conversations.replies` for the body» depends on exactly these two fields.

### Resolution (2026-10-28, branch `feat/v2-core`, contract v1.4)
Each match renders `in #<name> (<id>)` first in its meta line (DM/MPIM without `#`) and a `permalink:` line after the text; history items without a `channel` are unchanged. Tests: `tests/test_formatters.py::TestSearchMatchSource`. The library `read_scope` runs on the request before any call. A post-filter over search results is not built: `search.*` under a read scope refuses `read_scope_violation`.

## FMT-008 — empty page hides a non-empty `next_cursor`

**Severity:** Low · **Status:** Resolved on branch `feat/v2-core` (contract v1.4, not installed) · **Reported:** 2026-10-28 (Codex review, reproduced) · **Fix:** with FMT-007

### Symptom
A `conversations.history`/`replies` page with `messages: []` but `has_more: true` and a `response_metadata.next_cursor` renders as «No messages found.» — the cursor is dropped, pagination silently ends. Slack returns such pages (filters, deleted messages, tombstones).

### Resolution (2026-10-28, branch `feat/v2-core`, contract v1.4)
An empty page with a cursor renders `0 <items> on this page next_cursor=<c> — more remain; continue with this cursor.` for message, channel, member, user, file and pin lists. With no cursor it stays `No <items> found.`. Tests: `tests/test_formatters.py::TestEmptyPageCursor`.

## GATE-002 — identity overrides (`username`, `icon_emoji`, `icon_url`) are forwarded unfiltered

**Severity:** High (identity) · **Status:** Resolved on branch `feat/v2-core` (not installed) · **Reported:** 2026-10-28 (Codex review; confirmed by code read — no filtering in `gateway.py`; the live bot token carries `chat:write.customize`) · **Fix:** first v2 slice (project.md § Strategy п. 16–17), invariant (vii)

### Symptom
A `chat.postMessage` with `username`/`icon_*` under the bot token would post under an arbitrary display identity. Nothing in the gateway or the gate refuses it.

### Resolution (2026-10-28, branch `feat/v2-core`)
The policy engine refuses `username` / `icon_emoji` / `icon_url` on **every** write with `identity_override` (invariant vii). The hook denies the call, and the MCP server refuses it again if it arrives anyway, on the core and the transitional path alike. Tests: `tests/test_v2_slice.py::test_15_identity_overrides_never_reach_slack` asserts the mock client is never called for chat.postMessage / update / scheduleMessage / postEphemeral. Still open: drop `chat:write.customize` from the app at the next scope review (manifest is co-owned with the communication store).

## GATE-003 — read classification by name prefix/suffix, not by effect

**Severity:** Medium · **Status:** Partially resolved on branch `feat/v2-core` (not installed) · **Reported:** 2026-10-28 (Codex review; `is_read("auth.futureMutation") == True` with a made-up name, `users.lookupByEmail` / `users.getPresence` ask) · **Fix:** v2 policy engine (effect-based allow-list, unknown write → strictest row or refuse)

### Symptom
`mcp-slack-gate.py` auto-allows any future `auth.*`/`search.*`/`team.*`/`emoji.*` name and any name ending in `.list/.info/.get/…`, while asking on genuine reads such as `users.lookupByEmail`, `users.getPresence`, `chat.getPermalink`. The classifier is a convenience heuristic, not a registry of supported effects.

### Resolution (2026-10-28, branch `feat/v2-core`) — partial
- An explicit registry lives in `src/atlas_slack/policy.py`. `READ_METHODS` are row 0 and include `users.lookupByEmail`, `users.getPresence`, `chat.getPermalink`, `chat.scheduledMessages.list`, and `conversations.open` with `prevent_creation=true` and one user. `chat.postMessage` is row 1/2 through the core. `conversations.open` otherwise is row 4 and asks with the GATE-001 text. `auth.revoke` refuses: it matched `auth.` and was **auto-allowed** in 0.4.0. Known writes carry their A3 row.
- The hook is a thin adapter over `atlas_slack.gate`.
- **Partial, deliberately:** names outside the registry still pass through the legacy prefix/suffix heuristic, but only on the *transitional* path, and the verdict says so (`auth.futureMutation` is still allowed, flagged `transitional_read`). An unregistered write asks with "transitional" in the prompt, as in 0.4.0. Making unregistered writes refuse is the cutover step after the matrix covers the remaining rows (Strategy п. 15).

## DEC-001 — Approval window of 10 minutes expired in production use

**Severity:** Medium · **Status:** Resolved · **Reported:** 2026-10-31 (journal review of 0.5.0, days 1–3) · **Fixed:** 2026-10-31 (0.5.1)

### Symptom
5 of 39 core sends refused `decision_expired`; consumed approvals waited up to 588 s. The window counted from the hook issuing the prompt, and the prompt sat in a background session until the owner reached it (one case 3 h 47 min, one 14 min 43 s). The MC-vault memory then recorded a wrong mechanism («a resend reuses the stale decision; pass a new op_id») — the transcript shows a fresh token was issued each time; the second failure was simply a second long wait.

### Resolution (0.5.1)
`DECISION_MAX_AGE_S` 600 → 1800; unconsumed rows pruned after 24 h instead of 1 h (a late approval must read `decision_expired`, not `decision_unknown` — spec-verifier finding). The refusal texts state the age, the window, that nothing was sent, and that the same call again yields a fresh prompt. Tests: `tests/test_door_contract_051.py`.

## GATE-004 — Provenance schema enforced by the parser, absent from the skill

**Severity:** Medium · **Status:** Resolved · **Reported:** 2026-10-31 · **Fixed:** 2026-10-31 (0.5.1)

### Symptom
`target` / `checks` must be objects, `origin` ∈ {live, prepared-as-human}, `ttl_s` with the latter — none of it in SKILL.md, which showed only `checks: {recipient, voice}`. Four sessions on 2026-10-30 hit two `provenance_invalid` refusals each, then sent without provenance: 10 of 34 ops carried no `comm_id`, and the ledger ↔ journal link was lost for them. The refusals were invisible: the hook denies before the server, and only the server journaled.

### Resolution (0.5.1)
The skill carries the minimal form (`{"comm_id": …}`; `cls` now derived from `as_bot`) and the full schema. The door mints `comm_id` when absent (`YYYYMMDD-<4 hex>` from OS entropy; an LLM's «4 random hex» gave 131 distinct suffixes in 160 ledger ids, `7c3e` ×8) and returns it in the result line; `slack_api("_comm_id")` mints one ahead of the draft (the comms skill in `<atlas>` now says so). Hook-side refusals land in `calls` with `caller=hook`.
