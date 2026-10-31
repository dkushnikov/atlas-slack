# atlas-slack — Atlas Plugin

Full Slack Web API access via MCP. Gateway for all 200+ methods + slack_download.

## Quick Reference

- **Type:** Plugin (MCP server + skills)
- **Marketplace:** atlas-slack
- **MCP tools:** 2 (slack_api gateway + slack_download; the 13 convenience wrappers were removed in v1.2 — 0 calls in 86 days)
- **Stack:** Python 3.12+, FastMCP, slack_sdk
- **Transport:** stdio

## Architecture

Two tokens, two roles:
- `SLACK_USER_TOKEN` (xoxp-) — read operations: search, history, profiles, DMs
- `SLACK_BOT_TOKEN` (xoxb-) — write operations: messages, reactions, pins (appears as "Atlas" 💙)

User token messages get `💙 @Atlas` Block Kit footer (context block). The attributed bot id comes from the profile (`bot_user_id`) or `ATLAS_BOT_USER_ID`; on branch `feat/v2-core` there is no default in code.

## Development

```bash
# Run server locally
cd <repo> && python3 -m atlas_slack

# Run tests
cd <repo> && python3 -m pytest tests/

# Run tests from a worktree (the package is editable-installed from the main
# checkout — without PYTHONPATH the tests import the installed copy, not yours)
PYTHONPATH=<worktree>/src python3 -m pytest <worktree>/tests -q

# Validate plugin
claude plugin validate <repo>

# Redeploy after editing source (install no-ops if already installed):
claude plugin marketplace update atlas-slack   # re-scan directory source
claude plugin update atlas-slack@atlas-slack   # then restart session to apply
```

## Plugin layout (feat/v2-core)

`plugins/atlas-slack/.claude-plugin/plugin.json` — the single manifest, hooks inline (bare event map) · `hooks/*.py` (the two gate scripts) · `hooks/hooks.json` → `modules: [./approval.tsx]` — the function-hooks module (Claude Code mod, 2.1.314+): `tool.check` on `slack_api` turns the stock JSON permission dialog for a core send into one `$.ui.ask` card (gate sentence · text · checks · technical line · Отправить/Нет) plus a native notification; any failure falls back to the stock dialog; `claude plugin test plugins/atlas-slack` runs `hooks/approval.test.ts` · `.mcp.json` · `skills/atlas-slack/SKILL.md`. Two loaders read plugins differently: the marketplace install loaded the hooks from the old root-level `plugin.json` (hook events in MC transcripts since July), the `--plugin-dir` loader ignored that file (`plugin details` → `Hooks (0)`, `Skills (0)` for the flat skill file). After any layout change check both: `claude plugin validate plugins/atlas-slack`, `claude --plugin-dir plugins/atlas-slack plugin details atlas-slack` → Hooks (1) · Skills (1) · MCP servers (1), and after install `claude plugin details atlas-slack@atlas-slack` from the MC vault → Hooks (1) · Skills (1).

## Plugin Process

See `<atlas>/docs/extending-atlas.md` for full registration checklist and DoD.

Quick reminder — three steps, all required:
1. `settings.json` → extraKnownMarketplaces + enabledPlugins
2. `claude plugin marketplace add <repo>`
3. `claude plugin install atlas-slack@atlas-slack`

## Token Setup

Tokens must be in **shell environment** (`~/.zshenv`), NOT in settings.json:

```bash
# ~/.zshenv
export SLACK_USER_TOKEN="xoxp-..."
export SLACK_BOT_TOKEN="xoxb-..."
```

`${VAR}` in `.mcp.json` resolves from shell env only. `settings.json` env is for Claude Code tools, not MCP servers.

Each machine needs its own `~/.zshenv`. For distributed setup, server gets only the tokens its profiles need.

## Safety

Two PreToolUse hooks, both shipped inside the plugin at `plugins/atlas-slack/hooks/`:

- **`mcp-slack-gate.py`** — matcher `mcp__plugin_atlas-slack`. Gates MCP tool calls. (On `feat/v2-core` it is a thin adapter over `atlas_slack.gate`; the text below describes 0.4.0 on main. The v2 hook imports the package — `slack_sdk` and `yaml` must be importable by plain `python3`; if the import fails, the hook **asks on every call, reads included** ("atlas-slack gate unavailable"), it never silently allows. It prefers the checkout's `src/` when it runs from a source tree. Profile resolution must match between hook and MCP server: both read `ATLAS_SLACK_PROFILE` from their inherited environment — set it in the shell or in the project's `.claude/settings.json` env, never in `.mcp.json` (`${VAR}` there reads only the shell and would override the inherited value with an empty one); a divergence refuses `profile_mismatch`.) Reads (search.*, auth.*, *.list, *.info, *.history, *.get, *.members, *.replies, *.test, dnd.info, reminders.list, users.conversations, etc.) → auto-allow. Writes (all non-read slack_api methods) → ask user confirmation; conversations.open asks with a creates-DM/MPIM warning.
- **`bash-slack-gate.py`** — matcher `Bash`. Blocks raw `curl slack.com/api/*` and `security find-generic-password ... slack` with message pointing at `slack_api`. Allows legitimate binary uploads to `files.slack.com`.

Ship atomically with the plugin — no user settings.json configuration required. The earlier external `<atlas>/bin/slack-gate.{sh,py}` was removed 2026-04-14 (replaced by these plugin-internal hooks).

## Project Context

- **Project file:** `<vaults>/Atlas/the project file/project.md`
- **Design spec:** `<atlas>/docs/superpowers/specs/2026-04-08-atlas-slack-mcp-design.md`
- **Slack App:** ID A0APPID001, Acme workspace
- **Manifest:** `manifest.json` in this repo
- **Icon:** `atlas-icon.jpg` in this repo

## Current State

- **0.5.3 (2026-10-31) — slice 2, part 1: `chat.scheduleMessage` in the core** (`post_at` bound as `scheduled_for`; outcome id = `scheduled_message_id`; codes `schedule_in_past` / `schedule_too_far` / `schedule_mismatch`; the prompt and the card show the time). Found live: a time edit from the card («отправь завтра») had no answer inside the door.
- **Approval card mod (2026-10-31, live-verified by the owner, four rounds):** `hooks/approval.tsx` — `tool.check` on `slack_api` turns the stock JSON permission dialog for a core send into one `$.ui.ask` card. The card is the *question text* (Кому · формат · когда / От кого / Текст ≤ 1000 / «Дверь» as one icon line 👤 🎙 🔏 📓 📜 / «Контекст проверил» / «Не проверено»; no comm/op ids; 0.5.2: `🎯 Cicero` on the door line from the ledger file behind comm_id — `src/atlas_slack/ledger.py`, profile `ledger_dir` / env `ATLAS_SLACK_LEDGER_DIR`, informational only; text typed on the card = edit request returned as the deny reason; text cap 2000; marks ✅ / 🟡 / 🔴; card language = profile `language`, the gate's `ledger:` lines too; transitional writes get a compact card — action · where · when · text — marked «переходный путь»). Harness facts learned: a mod may not call AskUserQuestion itself (only `$.ui.ask`, labels only); a `ui.render` rewrite of that dialog's option hints/previews is never drawn (two live rounds); module variables and `$.state` made no difference there. The PreToolUse `updatedInput` decision token survives the mod's `allow`. `claude plugin test plugins/atlas-slack` runs `hooks/approval.test.ts`; rollback = delete `hooks/hooks.json`.
- **0.5.1 (2026-10-31) — door contract after three days of 0.5.0 in production** (`docs/plans/2026-10-31-door-contract-after-0.5.0.md`): decision window 30 min with an actionable `decision_expired` text; `comm_id` minted by the door when absent and returned in the result line; `cls` derived from `as_bot`; hook-side refusals journaled (`caller=hook`); WAL checkpoint on open; the provenance schema is in the skill. Contract v1.5.
- **0.5.0 (2026-10-28, installed).** `chat.postMessage` runs through the core: profile, policy, provenance, a one-time decision via the hook's `updatedInput`, and a write-ahead SQLite journal. Reads go through the registry. **Live hooks import `<repo>/src` directly** (editable install; the plugin cache holds only the plugin dir) — every hook invocation runs the main checkout's code the moment it changes, so develop in a worktree and merge atomically; MCP servers keep their in-memory code until the session restarts. `conversations.open` is split by effect. Every other write is on the labelled transitional path. Also on the branch: the library `atlas_slack.door.Door`, the gate CLI `python3 -m atlas_slack.gate`, `slack_api("_rights")`, and fixes for GATE-002, FMT-007 and FMT-008. Plan, decisions and the morning live-check steps: `docs/plans/2026-10-28-v2-core-slice.md`. Schema: `docs/policy-schema.md`. Contract v1.4. **Before installing it**, give the default profile a `bot_user_id`, because the `U0BOTUSR01` fallback is gone. Either create `~/.config/atlas-slack/default.yaml` or export `ATLAS_BOT_USER_ID`; otherwise person-voice sends refuse `attribution_unavailable`.

- Dual tokens: working (verified 2026-04-09)
- MCP server: **dual-token** via `get_client(as_bot=...)`; write-as-Atlas tools route to bot, user-state tools (set_status, dnd, remind) stay user (2026-04-13)
- Attribution: `slack_api('chat.postMessage', ..., as_bot=False)` auto-injects `:blue_heart: <@U0BOTUSR01>` context block (2026-04-13)
- Plugin: registered and installed
- Hooks: `mcp-slack-gate.py` (MCP write gate) + `bash-slack-gate.py` (curl bypass blocker) — both inside plugin
- Live token scopes (auth.test `x-oauth-scopes`, 2026-10-27): bot 48, user 41. **`manifest.json` in this repo has drifted** (29 user scopes in the file); the manifest is a surface shared with Verba's Socket Mode capture — reconcile it with scope reduction as a deliberate step (project.md § Strategy п. 11), not as a drive-by edit
- slack_sdk 3.46.0 (2026-10-31; both system pythons + uv.lock). The 16 WebClient methods added since 3.41 all have a registry row: `agents.conversations.listViews/getCanvas` + `canvases.getContent` + `blocks.validate` read; `agents.conversations.setCanvasContent` row 1; `agents.conversations.create/archive/setView/removeView/setCommands/setProperties` row 4; `agents.sessions.*` transitional-allowed; `admin.users.getExpiration`, `apps.user.connection.*` refused (row 8). Unknown future names still fall to the transitional heuristic (GATE-003).
- Tests: 372 passing (+8 e2e, gated by SLACK_E2E=1)
- Streams (`chat.*Stream`): bot-only policy; gate allows append/stop, asks on start (2026-08-21)
- History output renders attachments (message shares) + file stubs — contract v1.1 (2026-08-21)
- **v1.2 (2026-09-01):** structural errors (`is_error=true` + hint-catalogue fix), unread state in channel info, default limit=100 on history/replies, args-echo on list shapes, caller-blocks pre-validation, conversations.open gate hint, convenience wrappers removed (surface = slack_api + slack_download). See output-contract v1.2 changelog + KNOWN_ISSUES ERR-001/FMT-004..006/UPL-001/GATE-001.
- **v1.3 (2026-10-22):** `files.uploadV2` mapped to slack_sdk `files_upload_v2` — file uploads are one `slack_api` call again (UPL-002, issue #4); `attribution: "block"` option for a context-block comment. Same day (MC ask after live checks): bot-token auto-split of long `text`, `_raw: true` flag for the JSON response, Slack transcription in `files.info` + `slack_download(…, vtt=True)`. Nested params JSON-encoded + `expand: true` on auto-split sections (2026-10-20). Live sessions pick up new gateway code only when the MCP process restarts: `/mcp` → atlas-slack → Reconnect, or a session restart — **`/reload-plugins` is not enough** (MC 2026-10-22: same PID after it, `_raw` silently forwarded to Slack by the old process). The package is installed editable; the plugin cache carries only hooks/skills/.mcp.json.

## What's Next

Roadmap = project file (`the project file/project.md` in the Atlas vault): Vision v2.3 and Architecture v2 (2026-10-27) — policy engine, provenance + journals, library for jobs, host profile/policy. Code work starts after Strategy v2; until then only hygiene (block `chat:write.customize` params, revoke the dead webhook URL, manifest reconcile). Socket Mode is not the door's function (events belong to the communication store); official-command migration is on demand.
