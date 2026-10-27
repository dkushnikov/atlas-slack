# atlas-slack

Full Slack Web API access via MCP for Claude Code.

## What

One generic gateway tool (`slack_api`) for all 200+ Slack Web API methods, plus `slack_download` for fetching file bytes to local disk (v1.2 removed the typed convenience wrappers — unused; everything routes through `slack_api`).

Complements the official Slack plugin (canvas, semantic search) — coexistence decision: `docs/decisions/2026-08-21-agent-sessions-and-official-slack-mcp.md`. Project Vision and Architecture live in the Atlas vault (`the project file/project.md`); the original April design spec is superseded (`<atlas>/docs/superpowers/specs/2026-04-08-atlas-slack-mcp-design.md`, historical).

## Setup

1. Create a Slack App at https://api.slack.com/apps
2. Add a bot user and the User/Bot Token Scopes — `manifest.json` in this repo is a snapshot of the app config (it has drifted from the live app, see CLAUDE.md § Current State); the live scopes are what `auth.test` reports in `x-oauth-scopes`
3. Install to workspace, copy both tokens: `xoxp-...` (user — reads, sends as the person with the 💙 footer) and `xoxb-...` (bot — sends as Atlas)
4. Put them in the shell environment (`~/.zshenv`): `export SLACK_USER_TOKEN=xoxp-... ; export SLACK_BOT_TOKEN=xoxb-...` — `settings.json` env does not reach MCP servers
5. Install the plugin (see Plugin below)

## Install

```bash
pip install -e .
```

## Plugin

Add to Claude Code `settings.json` `extraKnownMarketplaces`:
```json
"atlas-slack": {
  "source": {
    "source": "directory",
    "path": "<repo>"
  }
}
```

Then enable `atlas-slack@atlas-slack` in `enabledPlugins`.

## Documentation

- **`docs/output-contract.md`** — output format contract: per-shape field preservation, truncation rules, list caps, error hint catalogue. Source of truth for what `slack_api` returns. Code changes that alter output go via spec change first.
- **`KNOWN_ISSUES.md`** — historical record of bugs and their resolutions, useful for understanding why current invariants exist.
