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
    "path": "/path/to/atlas-slack"
  }
}
```

Then enable `atlas-slack@atlas-slack` in `enabledPlugins`.

## Standalone profile (v2 core, branch `feat/v2-core`)

The door works without any Atlas environment. All you need is a profile file and the two tokens in the shell environment.

1. Create `~/.config/atlas-slack/<name>.yaml`. It holds no secrets: token env var *names* only.

   ```yaml
   workspace_id: T0123ABCD        # your Slack team id
   person_user_id: U0123ABCD      # you
   bot_user_id: U0456EFGH         # your app's bot user — the 💙 attribution target
   person_bot_dm_id: D0789IJKL    # the bot's DM with you (optional)
   ```

2. Export `ATLAS_SLACK_PROFILE=<name>` in the environment Claude Code runs in. A project's `.claude/settings.json` → `env` works, and so does your shell profile.
3. Run `slack_api("_rights")` to see what this host may do. Without a policy file the built-in strict policy applies: reads are allowed, a message in your voice (class A) or the bot's voice (class B-h) asks you every time, and autonomous sending is off.
4. Optional: write a policy file (`docs/policy-schema.md`), set `policy_path`, and pin its `policy_sha256`.

**Journal.** Every send is recorded in `~/.local/share/atlas-slack/<name>/journal.sqlite` before it leaves. That covers the full text, the target, the voice, the policy version and your decision. Every Slack call is recorded as well, without content. The file is yours. Nothing else reads it unless you export it with `Journal.export_envelope()`. Retention is your call: delete rows or blank `payload_json` per your own policy, and keep the row as the record that a send happened. `freeze: true` in the profile stops all writes at once.

**Two Slack MCPs.** This plugin coexists with the official Slack MCP (decision 2026-08-21). The guarantees above — two voices, per-operation decisions, the journal — apply only to calls made through `slack_api`.

## Documentation

- **`docs/output-contract.md`** — output format contract: per-shape field preservation, truncation rules, list caps, error hint catalogue. Source of truth for what `slack_api` returns. Code changes that alter output go via spec change first.
- **`docs/policy-schema.md`** — profile, policy and provenance schema, decisions, journal and retention (v2 core).
- **`KNOWN_ISSUES.md`** — historical record of bugs and their resolutions, useful for understanding why current invariants exist.
