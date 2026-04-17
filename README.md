# atlas-slack

Full Slack Web API access via MCP for Claude Code.

## What

One generic gateway tool (`slack_api`) for all 200+ Slack Web API methods, plus 13 convenience wrappers for common operations.

Complements the official Slack plugin (which handles canvas and semantic search).

## Setup

1. Create a Slack App at https://api.slack.com/apps
2. Add User Token Scopes (see docs/superpowers/specs/2026-04-08-atlas-slack-mcp-design.md for full list)
3. Install to workspace, copy the `xoxp-...` token
4. Set environment variable: `export SLACK_USER_TOKEN=xoxp-...`
5. Install plugin in Claude Code settings

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
