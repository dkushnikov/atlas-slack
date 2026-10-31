# atlas-slack

Slack for one person and their Claude Code agent — with a decision per operation.

## What it is

A Claude Code plugin that gives an agent the whole Slack Web API through one tool (`slack_api`, 200+ methods, plus `slack_download` for file bytes) **and makes every send a governed act**:

- **Two voices.** A message goes out either in **your voice** (your user token, with a visible `💙 <bot>` mark so the recipient knows the agent took part) or in **the bot's voice** (your app's bot user). The agent picks the voice per message; the recipient always sees which it was. Nothing can set a fake sender name or icon.
- **A decision per operation.** Before a message leaves, you see a card — to whom, in which voice, the exact text, what was checked and what was not — and say *Send* or *No*. The approval binds that exact operation: a changed text, channel, thread, time or file is a new question. One approval = one send; it expires after 30 minutes.
- **A write-ahead journal.** Every send is recorded locally *before* it leaves (the text, the target, the voice, the policy version, your decision) and its outcome after. Every Slack call is recorded without content. The file is yours.
- **Reconciliation, never blind retries.** When Slack's answer is ambiguous (timeout, `internal_error`), the row stays `unknown` and the agent is told to *reconcile* — read Slack and settle what happened — instead of posting again. A communication with an unknown outcome cannot be re-sent until that is settled.
- **Background processes through the same door.** A script or job uses the library face (`Door`) with a manifest of what it may touch; the same policy and journal apply. What needs a human is refused there, never auto-allowed.

This is what differs from the official Slack MCP, which is about access (OAuth, search, read, post). atlas-slack is about *managed communication*: who speaks, with what approval, with what record. The two coexist (the official one covers canvas and semantic search).

## Install

You need Python 3.12+, Claude Code, and the right to create a Slack app in your workspace.

### 1. Create your Slack app

Go to https://api.slack.com/apps → **Create New App** → **From a manifest**, paste [`slack-app-manifest.json`](slack-app-manifest.json), rename the app and its bot user, and install it to the workspace. Copy both tokens: **User OAuth Token** (`xoxp-…`, your voice and your reads) and **Bot User OAuth Token** (`xoxb-…`, the bot's voice).

The manifest holds the scopes the door's reads and six core sends use — and deliberately no `chat:write.customize`, no incoming webhook, no commands, no events. A method outside the granted scopes fails with `missing_scope`, which names the scope to add.

An app you create this way is an *internal* app in Slack's terms: the 1-request-per-minute limit Slack applies to commercially distributed non-Marketplace apps does not apply to it.

### 2. Tokens in the shell environment

```bash
# ~/.zshenv (or the profile your Claude Code sessions inherit)
export SLACK_USER_TOKEN="xoxp-..."
export SLACK_BOT_TOKEN="xoxb-..."
```

Not in `settings.json`: `${VAR}` in the plugin's `.mcp.json` resolves from the shell environment only.

### 3. Install the package and the plugin

```bash
git clone https://github.com/dkushnikov/atlas-slack.git
pip install -e ./atlas-slack          # the hooks and the server import the checkout's src/

claude plugin marketplace add ./atlas-slack
claude plugin install atlas-slack@atlas-slack
```

### 4. Write the profile — one step

```bash
python3 -m atlas_slack init           # → ~/.config/atlas-slack/default.yaml
```

It asks Slack who the two tokens are and writes the profile: `workspace_id`, `person_user_id` (you), `bot_user_id` (the bot, the attribution target), the bot's DM with you when it exists. No secrets in the file — token variable *names* only. It refuses to overwrite a profile and refuses tokens that are not one person + one bot in one workspace.

Several profiles (two workspaces, a test app): `init --profile <name>` and `ATLAS_SLACK_PROFILE=<name>` in the environment Claude Code runs in — a project's `.claude/settings.json` → `env` works. The hook and the MCP server must see the same value.

### 5. Check

```bash
python3 -m atlas_slack doctor
```

Both tokens against the profile (workspace, person, bot), the scopes count, hygiene flags, and the rights table. Then start a Claude Code session and ask it to read a channel; then to post a message — you will see the card.

The door also checks identity by itself: before the first write under each token in a session (sends, reactions, pins — reads are not attributed) it asks Slack `auth.test` once and refuses `identity_mismatch` when a token is not the workspace, person or bot the profile names.

## What you get by default

Without a policy file the built-in strict policy applies: reads are allowed; a message in your voice or the bot's asks you every time; autonomous sending (no human in the loop) is off. A policy file (`docs/policy-schema.md`) can widen or narrow that per context — deny lists of recipients, read scope, which classes a job may use — and the profile pins its sha256.

**Journal.** `~/.local/share/atlas-slack/<profile>/journal.sqlite`, written only by the door. Retention is your call: delete rows or blank `payload_json` per your own policy, and keep the row as the record that a send happened. Nothing reads the file unless you export it (`Journal.export_envelope()`). `freeze: true` in the profile stops all writes at once.

**Two Slack MCPs.** This plugin coexists with the official Slack MCP. The guarantees above apply only to calls made through `slack_api`.

**Optional layers.** The card can show a *ledger* mark — whether a per-communication Markdown file behind the `comm_id` records an audit of the text — when the profile names a `ledger_dir`. That is a workflow you may or may not have; without the folder there is no ledger line and never a refusal.

## Documentation

- **`docs/output-contract.md`** — what `slack_api` returns, per shape; error codes and hints; the result line after a core send. Code changes that alter output go via spec change first.
- **`docs/policy-schema.md`** — profile, policy, provenance, decisions, journal and retention.
- **`CHANGELOG.md`** — versions; `KNOWN_ISSUES.md` — the bug history behind the current invariants.
- **`plugins/atlas-slack/skills/atlas-slack/SKILL.md`** — what the agent reads: method table, voices, the sending contract.
- `docs/decisions/2026-08-21-agent-sessions-and-official-slack-mcp.md` — why coexistence with the official MCP, not migration.

## License

MIT.
