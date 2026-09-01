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
- Bot scopes: 48, User scopes: 29
- Tests: 166 passing (+6 e2e, gated by SLACK_E2E=1)
- Streams (`chat.*Stream`): bot-only policy; gate allows append/stop, asks on start (2026-08-21)
- History output renders attachments (message shares) + file stubs — contract v1.1 (2026-08-21)
- **v1.2 (2026-09-01):** structural errors (`is_error=true` + hint-catalogue fix), unread state in channel info, default limit=100 on history/replies, args-echo on list shapes, caller-blocks pre-validation, conversations.open gate hint, convenience wrappers removed (surface = slack_api + slack_download). See output-contract v1.2 changelog + KNOWN_ISSUES ERR-001/FMT-004..006/UPL-001/GATE-001. **MCP restart pending** — next interactive session picks up plugin 0.2.0.

## What's Next

See project file for full roadmap. Immediate:
1. Socket Mode listener (separate project phase)
2. Migrate official Slack commands to atlas-slack tools
