# Atlas Slack — setup guide

The full procedure, from an empty workspace to the first card. The README has the short form; the plugin's `atlas-slack-setup` skill walks a Claude Code session through the same steps and runs the checks for you. Slack's UI labels are as of 2026-10; if a button has moved, the menu names below are what to look for.

## 0. Before you start

- **Python 3.12+** — the one on the PATH of the shell you start Claude Code from. The MCP server, the hooks and the CLI all run as `python3`; `pip install` into that interpreter.
- **Claude Code 2.1.314 or newer** for the approval card. Older versions work; you get Claude Code's stock permission dialog with the same question text inside.
- **A Slack workspace where you may create an app.** Slack lets members create apps for their own workspace by default. If your admin turned on *app approval*, the install (step 1.4) waits for their one-time approval; creation itself usually does not. One app per person is the path this guide takes; see § Teams for why.

## 1. Create the Slack app (≈ 5 minutes)

1. Open https://api.slack.com/apps → **Create New App** → **From a manifest**.
2. Pick the workspace, then paste the contents of [`slack-app-manifest.json`](../slack-app-manifest.json) (switch the editor to JSON if it shows YAML) → **Next** → **Create**.
3. The app and its bot user are named **Atlas**. To rename: **Basic Information → Display Information** (app name) and **App Home → Your App's Presence** (bot display name). An icon goes in Display Information too.
4. **Install App → Install to Workspace** (or **Request to Install**, when the admin must approve). Allow the requested scopes.
   If the button says **Request to Install**, the workspace has app approval on: Slack sends your request to the admins and the page shows *pending* until one of them approves. Meanwhile do step 3 (the package and the plugin need no token); steps 2, 4 and 5 wait — there are no tokens yet, so `init` has nothing to read and `doctor` cannot pass. What to tell the admin when they ask: it is an internal app for one person (you), created from this manifest, no distribution, no events, no webhook; the scope list is in `slack-app-manifest.json`. Slack approves each app, so a colleague's copy is a separate request; an admin who expects several can set an automation rule for these scopes (§ Teams).
5. **OAuth & Permissions** now shows two tokens. Copy both: **User OAuth Token** (`xoxp-…`) — your voice and your reads — and **Bot User OAuth Token** (`xoxb-…`) — the bot's voice.

What the manifest grants, and what it deliberately does not: the scopes for reads, the six core sends, opening a DM, reactions, pins, reminders, your status and DND; no `chat:write.customize` (the door refuses sender overrides anyway), no incoming webhook (a send path outside the door), no slash commands, no events, no Socket Mode. When the agent later needs a method outside these scopes, Slack answers `missing_scope: <name>`: add that scope under **OAuth & Permissions → Scopes**, then **Reinstall to Workspace** (as of 2026-10 the tokens change when scopes change — update step 2).

## 2. Tokens into the shell environment

```bash
# ~/.zshenv (zsh) — or ~/.bash_profile, or whatever your Claude Code sessions inherit
export SLACK_USER_TOKEN="xoxp-..."
export SLACK_BOT_TOKEN="xoxb-..."
```

Rules: never in `settings.json` (`${VAR}` in the plugin's `.mcp.json` resolves from the shell environment only); never paste a token into a Claude Code chat (the transcript is a record). Then **open a new terminal** or `source` the file: the shell you run steps 3–5 in, and the one you start Claude Code from, must have the variables.

Two workspaces, or a test app next to the real one: give its tokens other variable names (`ACME_SLACK_USER`, `ACME_SLACK_BOT`) and name them in step 4 (`init --profile acme --user-var ACME_SLACK_USER --bot-var ACME_SLACK_BOT`).

## 3. Install the package and the plugin

```bash
git clone https://github.com/dkushnikov/atlas-slack.git
python3 -m pip install -e ./atlas-slack          # the same python3 your Claude Code shell runs

claude plugin marketplace add ./atlas-slack          # a directory marketplace: the clone is read in place
claude plugin install atlas-slack@atlas-slack
claude plugin list                                   # a block: ❯ atlas-slack@atlas-slack / Version: 0.7.x / Read from: … / Status: ✔ enabled
```

`pip install -e` matters: the hooks and the MCP server import the clone's `src/` directly. If pip answers `externally-managed-environment` (Homebrew or Debian Python), the clean path is a virtualenv that becomes the `python3` of the shell Claude Code starts from:

```bash
python3 -m venv ~/.atlas-slack-venv            # Debian/Ubuntu: apt install python3-venv first if this complains about ensurepip
~/.atlas-slack-venv/bin/python -m pip install -e ./atlas-slack
# in the same start-up file as the tokens (step 2), before Claude Code starts:
export PATH="$HOME/.atlas-slack-venv/bin:$PATH"
```

`python3 -c "import atlas_slack, slack_sdk, yaml; print(atlas_slack.__file__)"` from a new terminal proves it: the path printed is inside the clone. `--break-system-packages` on the system interpreter also works when that interpreter is your own install (your Homebrew, your python.org Python) — your call, not a managed machine's. Updating later is `git pull` in the clone plus a **restart of the Claude Code session** (a live session keeps the old MCP server in memory; when the plugin version changed, that server refuses sends from the newer hook with `version_skew` until the restart — by design). A block that says `Status: ✘ disabled` needs `claude plugin enable atlas-slack@atlas-slack` and a restart.

## 4. Write the profile

```bash
python3 -m atlas_slack init                          # → ~/.config/atlas-slack/default.yaml
```

`init` calls Slack's `auth.test` with each token and writes: `workspace_id`, `person_user_id` (you), `bot_user_id` (the bot — the attribution target of the `💙` mark), `person_bot_dm_id` (the bot's DM with you, when one exists). The file holds token variable *names*, never tokens. It refuses to overwrite an existing profile; `--force` rewrites the four identity fields (and `token_sources` when you pass the variable options), adds `language: en` when absent, and keeps every other key you added (`freeze`, policy, journal, names, language — the file's comments are not kept). It refuses tokens that are not one person + one bot in one workspace — the message says which slot is wrong — and says `cannot read the identities` when a variable is unset or a token is invalid.

Several profiles: `init --profile acme --user-var ACME_SLACK_USER --bot-var ACME_SLACK_BOT` writes `acme.yaml` with those variable names as its `token_sources` (a non-default profile that names no variables — in the options or in its file's `token_sources` — is refused, and so is one option alone: it would read the other token from the first app's variable; a profile that really shares the default tokens says so with `--user-var SLACK_USER_TOKEN --bot-var SLACK_BOT_TOKEN`, and the file then declares them). Variable names are upper-case letters, digits and underscores. Check it with `doctor --profile acme`, then select it with `ATLAS_SLACK_PROFILE=acme` in the environment Claude Code runs in — a project's `.claude/settings.json` → `"env"` is the usual place. The hook and the MCP server must see the same value; a divergence refuses `profile_mismatch`. Everything else the profile can hold — a policy file and its sha256 pin, `freeze`, `language`, display names — is in [`policy-schema.md`](policy-schema.md).

## 5. Check

```bash
python3 -m atlas_slack doctor
```

Expected shape:

```
profile: default (file: ~/.config/atlas-slack/default.yaml)
person token (SLACK_USER_TOKEN): ✓ team T… (acme) · user U… (ann) · 27 scopes
bot token (SLACK_BOT_TOKEN): ✓ team T… (acme) · user U… (atlas) · bot_id B… · 20 scopes

atlas-slack rights — profile default (file), policy builtin-strict-1 (builtin) …
```

What the lines mean: ✓ — no field the profile sets disagrees with the token (with no profile file every field is unset, so ✓ then means only that each slot holds the right kind of token); ✗ with a reason — a variable is not set, Slack rejected the token (`invalid_auth`, `token_revoked`), or the token is not the workspace / person / bot the profile names (the indented `✗ workspace: …` / `person: …` / `bot: …` lines say which); an indented ✗ under `profile:` is a problem with the file itself; `· <advice>` — a profile field is unset and therefore not checked; `· scope chat:write.customize is granted` — a hygiene note, drop the scope from the app. The rights table below is what the door allows right now: with no policy file, reads are allowed, a message in your voice or the bot's asks you every time, autonomous classes are off. `freeze: YES` on the second line means every write refuses until you set `freeze: false` in the profile.

The last line is the verdict: `next: <code> — <what to do>`. `send` means everything is consistent (exit code 0) and names the channel for your first message; any other code names the one thing to fix first — `init` (no profile file for the name — for `default`, also no `ATLAS_BOT_USER_ID` in the environment), `fix-profile` (a bad key, or identity fields missing — the line names the fix), `set-token`, `new-token` (Slack rejected a token: check the variable's value; if it is clean, reinstall the app), `retry` (transient: network, rate limit), `swap-tokens` (a bot token in the user slot or the reverse, or the same token in both), `decide` (the token and the profile disagree: you say which is right), `add-scope` (the app lacks one of the four scopes the first session uses — `channels:history`, `chat:write` for you; `chat:write`, `im:write` for the agent: add it under OAuth & Permissions → Scopes, reinstall, update the variable; `scopes unknown` on a token line means Slack sent no scope header and nothing was checked), `frozen`, `fix-policy`, `fix-journal`. Fix it, run `doctor` again.

## 6. The first session

Start Claude Code from a shell that has the tokens. Then:

1. «Read the last ten messages in #general.» — a read; no card.
2. «Post "hello from the door" to me, in the bot's voice.» — the target is the bot's DM with you (`init` recorded it when it existed; otherwise the agent posts to your **user id** as the channel, Slack opens the DM on that first post, and `init --force` later records its id). You see the card: to · format · when / from / the text / the door line / what was checked / *Send?* Press **Send** → the result line reads `journal: op <id> ok · comm <id>`. Type a correction instead → nothing is sent, the agent gets your words back.
3. Optional: «Reply in that thread in my voice» — your user token, the `💙 Atlas` mark under the message.
4. Before the agent's voice goes to a channel: invite the agent there first (`/invite @Atlas`, or the name you gave it). The manifest has no `chat:write.public` — each voice posts only where it is a member (the agent where it was invited, you where you joined), and Slack answers `not_in_channel` otherwise. A test channel with the agent invited, one message in each voice, is the check that the channel path works; the DM alone does not prove it.

Your journal is now `~/.local/share/atlas-slack/default/journal.sqlite`; `slack_api("_rights")` in a session prints the rights table.

## 7. When something refuses

| You see | It means | Do |
|---|---|---|
| the hook asks on **every** call, reads included, «gate unavailable» | plain `python3` cannot import `slack_sdk` or `yaml` | `pip install -e` into the interpreter Claude Code's shell uses |
| `token_unavailable` | the variable is not set in the environment the MCP server inherited | step 2; restart Claude Code from a shell that has it |
| `profile_missing` / `profile_invalid` | `ATLAS_SLACK_PROFILE` names a file that does not exist / an invalid name, or the file has an unknown key or a token-shaped value | `doctor --profile <name>` says which: `init --profile <name> --user-var … --bot-var …` for a missing second-workspace profile, a fix of the variable or the file otherwise |
| `profile_mismatch` | the hook and the server see different `ATLAS_SLACK_PROFILE` | set it in one place (shell or project `env`), restart |
| `permission_mode_unsafe` | the session runs in a permission mode where the card cannot reach you (`bypassPermissions`, `dontAsk` — e.g. `claude --dangerously-skip-permissions`) | start the session in the default mode (or `auto`). The refusal also names the other way: listing the mode in the profile's `decision_permission_modes` — then the card is whatever that mode shows you, in `bypassPermissions` nothing; your hand in the profile, your call |
| `no_decision` | a send reached the server without the hook's decision: the plugin's hooks did not run (`disableAllHooks` in your own settings, or a managed Claude Code with `allowManagedHooksOnly`), or the hook ran and failed — then the prompt before it said «gate error» or «gate unavailable» | a «gate …» prompt: the pip line of § 3 (the hook cannot import the package); otherwise check your settings for `disableAllHooks`, then ask your Claude Code admin; nothing was sent |
| `identity_mismatch` | a token is not the workspace / person / bot the profile names | `doctor` says which; fix the profile or the token; restart the session |
| `identity_unverified` | Slack gave no usable answer to `auth.test` (unreachable, revoked token) | the next call checks again; if it persists, `doctor` |
| `missing_scope` | the method needs a scope the app does not have | add it under OAuth & Permissions, reinstall, update the tokens |
| `attribution_unavailable` | the profile has no `bot_user_id` | `init --force` (identity fields only), or set it by hand |
| `version_skew` | the MCP server runs another plugin version than the hook that approved the send (after an update) | restart the session |
| `frozen` | the profile says `freeze: true` | your emergency stop; `freeze: false` lifts it |
| `policy_invalid` | the policy file does not parse, breaks the schema, or its sha256 differs from the profile's pin | fix the file or re-pin it (`docs/policy-schema.md`); `doctor` says `fix-policy` |
| `journal_unavailable` | the journal directory is unwritable | fix permissions on `~/.local/share/atlas-slack/<profile>/` |
| `decision_expired` | the card waited more than 30 minutes | ask again — nothing was sent |
| `outcome_unknown` | Slack's answer was lost | the agent runs `_reconcile`; never a blind retry |
| `not_in_channel` | Slack: the voice that posted is not a member of that channel | the agent's voice: `/invite @<agent name>` in Slack (the door's `conversations.invite` needs `channels:manage`, not in the manifest); your voice: join the channel; then the same call, same voice |

## Teams

One app per person is the path above: your tokens, your app, your agent. Separate apps are separate agent users in Slack — each with its own name, its own channel memberships, its own DM with its person; that is what the two voices and the agent's own visibility rest on. A single app shared by a team would be **one** agent user for everyone: the same name under every colleague's messages, one bot token on every laptop, and the channels any one person invites it to readable by all of them; each person's **user** token would also have to come from an OAuth sign-in flow (a redirect URL and a small handler) that this plugin does not ship. Not this door's model. Each colleague creates their own app from the same manifest.

Where the workspace has app approval on: Slack approves **apps**, not manifests — each person's app is its own request, approved once. An admin who wants to approve the template once sets an [automation rule](https://slack.com/help/articles/9978438318227-Guide-to-automation-rules-for-app-approval) that approves apps requesting these scopes; ask them before a team starts. A Claude Code managed by the company may also restrict plugins or hooks, in two different shapes. `disableSideloadFlags` refuses the plugin's launch: no `slack_api` tool at all. `allowManagedHooksOnly` (like `disableAllHooks` in your own settings) drops the plugin's hooks while its server still runs: no card, and every core send refuses `no_decision` — nothing is sent; the writes outside the core (reactions, pins, delete, channel structure) then run behind Claude Code's stock prompt only.

## Your own copy

The clone you `pip install -e` and `claude plugin marketplace add` is the plugin: the hook imports `src/` of its own checkout, the MCP server runs from the same `src/`, and `claude plugin list` → `Read from:` names it. To work on a fork, point both at the fork: `claude plugin uninstall atlas-slack@atlas-slack` and `claude plugin marketplace remove atlas-slack` first (the fork's marketplace carries the same name), then `marketplace add` and `install` from the fork, and `pip install -e` the fork — one install, one hook, one server. A gate change must keep the server's side: the core verifies the decision token, the profile and the hook's version itself, so a hook that is switched off does not turn a send into an allowed one — it turns it into `no_decision`.

## Uninstall

`claude plugin uninstall atlas-slack@atlas-slack`, `claude plugin marketplace remove atlas-slack`, `pip uninstall atlas-slack`; delete `~/.config/atlas-slack/` (profiles, no secrets) and `~/.local/share/atlas-slack/` (journals — they hold the text of everything sent; keep or shred per your own retention); remove the two `export` lines; in Slack, **Basic Information → Delete App** revokes both tokens.
