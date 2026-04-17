# atlas-slack — Known Issues

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
