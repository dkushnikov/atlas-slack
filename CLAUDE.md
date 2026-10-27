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

User token messages get `💙 @Atlas` Block Kit footer (context block).

## Development

```bash
# Run server locally
cd <repo> && python3 -m atlas_slack

# Run tests
cd <repo> && python3 -m pytest tests/

# Validate plugin
claude plugin validate <repo>

# Redeploy after editing source (install no-ops if already installed):
claude plugin marketplace update atlas-slack   # re-scan directory source
claude plugin update atlas-slack@atlas-slack   # then restart session to apply
```

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

- **`mcp-slack-gate.py`** — matcher `mcp__plugin_atlas-slack`. Gates MCP tool calls. Reads (search.*, auth.*, *.list, *.info, *.history, *.get, *.members, *.replies, *.test, dnd.info, reminders.list, users.conversations, etc.) → auto-allow. Writes (all non-read slack_api methods) → ask user confirmation; conversations.open asks with a creates-DM/MPIM warning.
- **`bash-slack-gate.py`** — matcher `Bash`. Blocks raw `curl slack.com/api/*` and `security find-generic-password ... slack` with message pointing at `slack_api`. Allows legitimate binary uploads to `files.slack.com`.

Ship atomically with the plugin — no user settings.json configuration required. The earlier external `<atlas>/bin/slack-gate.{sh,py}` was removed 2026-04-14 (replaced by these plugin-internal hooks).

## Project Context

- **Project file:** `<vaults>/Atlas/the project file/project.md`
- **Design spec:** `<atlas>/docs/superpowers/specs/2026-04-08-atlas-slack-mcp-design.md`
- **Slack App:** ID A0APPID001, Acme workspace
- **Manifest:** `manifest.json` in this repo
- **Icon:** `atlas-icon.jpg` in this repo

## Current State

- Dual tokens: working (verified 2026-04-09)
- MCP server: **dual-token** via `get_client(as_bot=...)`; write-as-Atlas tools route to bot, user-state tools (set_status, dnd, remind) stay user (2026-04-13)
- Attribution: `slack_api('chat.postMessage', ..., as_bot=False)` auto-injects `:blue_heart: <@U0BOTUSR01>` context block (2026-04-13)
- Plugin: registered and installed
- Hooks: `mcp-slack-gate.py` (MCP write gate) + `bash-slack-gate.py` (curl bypass blocker) — both inside plugin
- Live token scopes (auth.test `x-oauth-scopes`, 2026-10-27): bot 48, user 41. **`manifest.json` in this repo has drifted** (29 user scopes in the file); the manifest is a surface shared with Verba's Socket Mode capture — reconcile it with scope reduction as a deliberate step (project.md § Strategy п. 11), not as a drive-by edit
- Tests: 189 passing (+8 e2e, gated by SLACK_E2E=1)
- Streams (`chat.*Stream`): bot-only policy; gate allows append/stop, asks on start (2026-08-21)
- History output renders attachments (message shares) + file stubs — contract v1.1 (2026-08-21)
- **v1.2 (2026-09-01):** structural errors (`is_error=true` + hint-catalogue fix), unread state in channel info, default limit=100 on history/replies, args-echo on list shapes, caller-blocks pre-validation, conversations.open gate hint, convenience wrappers removed (surface = slack_api + slack_download). See output-contract v1.2 changelog + KNOWN_ISSUES ERR-001/FMT-004..006/UPL-001/GATE-001.
- **v1.3 (2026-10-22):** `files.uploadV2` mapped to slack_sdk `files_upload_v2` — file uploads are one `slack_api` call again (UPL-002, issue #4); `attribution: "block"` option for a context-block comment. Same day (MC ask after live checks): bot-token auto-split of long `text`, `_raw: true` flag for the JSON response, Slack transcription in `files.info` + `slack_download(…, vtt=True)`. Nested params JSON-encoded + `expand: true` on auto-split sections (2026-10-20). Live sessions pick up new gateway code only when the MCP process restarts: `/mcp` → atlas-slack → Reconnect, or a session restart — **`/reload-plugins` is not enough** (MC 2026-10-22: same PID after it, `_raw` silently forwarded to Slack by the old process). The package is installed editable; the plugin cache carries only hooks/skills/.mcp.json.

## What's Next

Roadmap = project file (`the project file/project.md` in the Atlas vault): Vision v2.3 and Architecture v2 (2026-10-27) — policy engine, provenance + journals, library for jobs, host profile/policy. Code work starts after Strategy v2; until then only hygiene (block `chat:write.customize` params, revoke the dead webhook URL, manifest reconcile). Socket Mode is not the door's function (events belong to the communication store); official-command migration is on demand.
