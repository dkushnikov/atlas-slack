# Atlas Slack — Claude Code plugin

Slack for one person and their agent, with a decision per operation: one gateway tool for the Slack Web API, two voices (yours with a mark, the bot's), a policy engine, a write-ahead journal and reconciliation. The user-facing description is in `README.md`; this file is for working on the code.

## Quick reference

- **Type:** plugin (MCP server + two PreToolUse hooks + a Claude Code mod + two skills: `atlas-slack`, `atlas-slack-setup`)
- **Marketplace:** `atlas-slack` (`.claude-plugin/marketplace.json`) → plugin `plugins/atlas-slack`
- **MCP tools:** 2 — `slack_api` (gateway for every Web API method, plus the local pseudo-methods `_rights`, `_comm_id`, `_reconcile`) and `slack_download` (file bytes)
- **Stack:** Python 3.12+, FastMCP, slack_sdk, PyYAML · the mod in TypeScript (`hooks/approval.tsx`, bun tests)
- **Transport:** stdio
- **Version:** `pyproject.toml` = `plugins/atlas-slack/.claude-plugin/plugin.json` = `src/atlas_slack/__init__.py` (`tests/test_version.py`); history in `CHANGELOG.md`

## Architecture

Two tokens, two voices:

- `SLACK_USER_TOKEN` (`xoxp-`) — reads (search, history, profiles, DMs) and sends **in the person's voice**: the message carries a `💙 <bot>` context block so the recipient sees the agent took part.
- `SLACK_BOT_TOKEN` (`xoxb-`) — sends **in the bot's voice** (the bot is the author on record), reactions, pins.

The attributed bot id comes from the profile (`bot_user_id`) — there is no workspace literal in code.

The door (`src/atlas_slack/`): `policy.py` (effect registry, sender classes, invariants, verdicts) → `provenance.py` → `decision.py` (one-time token bound to the exact operation) → `journal.py` (SQLite write-ahead) → transport. Faces: `gateway.py` (MCP), `door.py` (library `Door` for jobs), `gate.py` (the hook's verdict + CLI). `identity.py` checks the tokens against the profile at the first write. `reconcile.py` resolves `unknown` outcomes by reading Slack. `shaping.py` / `formatters.py` are the transport layer (attribution, auto-split, output contract). Contracts: `docs/output-contract.md` (what the tools return), `docs/policy-schema.md` (profile, policy, provenance, decisions, journal).

## Development

```bash
# Run the server locally
python3 -m atlas_slack

# Tests (level 1 — no Slack, no secrets)
python3 -m pytest -q tests/
# From a worktree: the package is editable-installed from the main checkout — without PYTHONPATH
# the tests import the installed copy, not yours
PYTHONPATH=<worktree>/src python3 -m pytest -q <worktree>/tests

# The approval mod's tests (bun, via Claude Code)
claude plugin test plugins/atlas-slack

# Plugin manifest and layout
claude plugin validate plugins/atlas-slack
claude --plugin-dir plugins/atlas-slack plugin details atlas-slack   # → Hooks (1) · Skills (1) · MCP servers (1)

# Private-token scan (also in CI and in the pre-push hook)
python3 tests/test_no_private_tokens.py            # tracked files
git config core.hooksPath .githooks                # once per clone: pre-push scans the outgoing commit messages too
```

A marketplace added from a local path loads the source in place: a hook or skill edit is live at the next session start. **The hooks import `src/` directly** (`.mcp.json` sets `PYTHONPATH`; the gate hook prefers the checkout's `src/` when run from a source tree), so every hook invocation runs the checkout's code the moment it changes — develop in a worktree and merge atomically. The MCP server keeps its in-memory code until the session restarts; `/reload-plugins` is not enough (`version_skew` then refuses the send, by design).

This repository is public: no personal names, home paths, private project references or workspace ids in files or commit messages (`tests/test_no_private_tokens.py` enforces it; names and ids are kept as salted hashes in `tests/private-token-hashes.txt`). Test fixtures use synthetic people and ids.

## Plugin layout

`plugins/atlas-slack/.claude-plugin/plugin.json` — the single manifest, hooks inline · `hooks/mcp-slack-gate.py` and `hooks/bash-slack-gate.py` (the two PreToolUse gates) · `hooks/hooks.json` → `modules: [./approval.tsx]` — the function-hooks module (Claude Code mod, 2.1.314+): `tool.check` on `slack_api` turns the stock JSON permission dialog for a core send into one `$.ui.ask` card (to · format · when / from / text / the door line / checks declared / not checked · Send/No) plus a native notification; any failure falls back to the stock dialog · `.mcp.json` · `skills/atlas-slack/SKILL.md`.

Two loaders read plugins differently: a marketplace install reads `plugins/atlas-slack/.claude-plugin/plugin.json`; `--plugin-dir` reads the same file but ignores a root-level one. After any layout change check both commands above, and after install `claude plugin details atlas-slack@atlas-slack` → Hooks (1) · Skills (1).

## Token and profile setup

Tokens live in the **shell environment** (`~/.zshenv` or equivalent), never in `settings.json`: `${VAR}` in `.mcp.json` resolves from the shell environment only. The profile (`~/.config/atlas-slack/<name>.yaml`, written by `python3 -m atlas_slack init`) holds ids and token variable *names*, no secrets; `ATLAS_SLACK_PROFILE=<name>` selects it and must reach both the hook and the MCP server (a project's `.claude/settings.json` → `env`, or the shell). `python3 -m atlas_slack doctor` checks both tokens against the profile and prints the rights table. Schema: `docs/policy-schema.md`.

## Safety

Two PreToolUse hooks ship inside the plugin:

- **`mcp-slack-gate.py`** — matcher `mcp__plugin_atlas-slack.*`: a thin adapter over `atlas_slack.gate`. Reads → allow; a core send → ask with a one-time decision token in `updatedInput`; a transitional write → ask; refusals → deny with the reason. If the package cannot be imported (`slack_sdk`, `yaml` must be importable by plain `python3`), the hook **asks on every call, reads included** — it never silently allows. Hook and server must resolve the same profile (`profile_mismatch` otherwise).
- **`bash-slack-gate.py`** — matcher `Bash`: blocks raw `curl slack.com/api/*` and keychain reads of Slack tokens, pointing at `slack_api`; allows binary uploads to `files.slack.com`.

The trust posture is tamper-evident, not tamper-proof: the decision secret and the journal are readable by the same OS user.

## Current state — 0.7.1

The six message sends run through the core: `chat.postMessage`, `scheduleMessage`, `update`, `postEphemeral`, `meMessage`, `files.uploadV2` (`policy.CORE_METHODS`). Everything else is on the labelled *transitional* path (compact card, today's gate semantics, no journal op, no binding): reactions, pins, delete, deleteScheduledMessage, structure (`conversations.*`, usergroups), and the row-1 writes that are not message sends. Identity is checked at the first write per token (0.7.0). The library face executes reads and core sends only; `needs-human` there refuses (`no_decision_adapter`). Unknown future method names still fall to the transitional heuristic (`KNOWN_ISSUES.md` GATE-003); the effect registry grows as names appear (slack_sdk 3.46 added 16 rows).

Tests: `python3 -m pytest -q tests/` — level 1 (fake transports, no secrets) plus 8 e2e tests gated by `SLACK_E2E=1`. The version history with what each release changed: `CHANGELOG.md`.
