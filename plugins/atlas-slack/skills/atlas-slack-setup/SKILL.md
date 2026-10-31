---
name: atlas-slack-setup
description: Set up, check or repair the atlas-slack plugin for the person in this session — create the Slack app from the manifest, place the tokens, write the profile with `init`, verify with `doctor`, run the first send, read a refusal. Use when the person says "set up atlas-slack", "install the Slack door", "create the Slack app", "configure the profile", "doctor says …", "identity_mismatch", "missing_scope", "the gate asks on every call", "add a second workspace / profile", "update the plugin", or when any slack_api call fails before reaching Slack with a profile, token or identity code. Not for sending or reading Slack — that is the `atlas-slack` skill.
---

# atlas-slack — setup and repair

The person does the Slack side (a browser, their account); you do the local side (shell, files, checks). One command decides every step: `python3 -m atlas_slack doctor` ends with a line `next: <code> — <what to do>`. Do what that line says. Then run `doctor` again. When it says `next: send`, go to step 4. `init` prints its own `then:` hint; only `doctor`'s `next:` line is the verdict. The full guide for humans is `docs/setup.md` in the clone.

**The clone** is the repository checkout: `claude plugin list` prints `Read from: <clone>/plugins/atlas-slack`, so cut `/plugins/atlas-slack` off that path. Without an installed plugin the clone is wherever the person cloned the repository; without a clone, `git clone https://github.com/dkushnikov/atlas-slack.git` first.

**`<name>`** is the profile: what the person names («for acme», «the test app») when they name one; else `$ATLAS_SLACK_PROFILE` when it is set in this session; else `default`. Pass `--profile <name>` to `init` and `doctor` whenever it is not `default`.

## Rules that hold at every step

- Never ask for a token value and never print one. Tokens go into the person's shell start-up file by their own hand. If a token appears in the chat, say so; the person revokes it on the app's OAuth & Permissions page (as of 2026-10) and reinstalls the app for a fresh one.
- Never call Slack with `curl` or the SDK directly. The checks are `doctor` and `init`; a read or a send in a session goes through `slack_api`.
- Say only what a command printed. Every claim about the person's setup comes from `doctor`, `init`, `claude plugin list` or the import check.
- `init --force` rewrites the four identity fields (`workspace_id`, `person_user_id`, `bot_user_id`, `person_bot_dm_id`), rewrites `token_sources` when `--user-var`/`--bot-var` are given, adds `language: en` when absent, and keeps every other key; comments in the file are not kept. Before you run it, say what it rewrites. Run it only when `doctor`'s `next:` line names it, when step 4 names it, or when the person asks.

## Step 0 — the environment

```bash
python3 --version && python3 -c "import atlas_slack, slack_sdk, yaml; print(atlas_slack.__version__)"
claude plugin list 2>/dev/null | grep -A4 "atlas-slack@"
```

- `python3` below 3.12 → the person installs Python 3.12+ (python.org or Homebrew) and starts Claude Code from a shell where it is `python3`; then come back here.
- The import fails → `python3 -m pip install -e <clone>` with that same `python3`. If pip answers `externally-managed-environment` (Homebrew or Debian Python): `python3 -m pip install --break-system-packages -e <clone>` (the interpreter is the person's own), or a virtualenv whose `bin` is first on the PATH of the shell that starts Claude Code. Re-run the import line.
- No `atlas-slack@` block → `claude plugin marketplace add <clone>` then `claude plugin install atlas-slack@atlas-slack`; a session restart will be needed (remember it for step 4).
- A block with `Status: ✘ disabled` → `claude plugin enable atlas-slack@atlas-slack`; a restart will be needed (remember it for step 4).

If the person reports that the gate asks on **every** call, reads included («gate unavailable»): the import fails under plain `python3` — the pip line above, with the interpreter Claude Code's shell uses.

## Step 1 — `doctor`, and the `next:` line

```bash
python3 -m atlas_slack doctor --profile <name>
```

Read the whole output to the person in one or two sentences (which profile, both tokens' state, the rights table's gist), then do what the last line says:

| `next:` | What you do |
|---|---|
| `init — …` | no profile file for `<name>` (for `default`: and no `ATLAS_BOT_USER_ID` in the environment): step 2 (`<VAR>` placeholders in the line: the person names the two variables, step 3) |
| `fix-profile — …` | the line quotes the problem and the one fix: a bad `ATLAS_SLACK_PROFILE` → the person fixes it; an unknown key → remove it from the file (`--force` would keep it); another bad value or an unparsable file → the hand edit or the `init … --force` the line names (`<VAR>` placeholders: the person names the two variables, step 3) |
| `set-token — <VAR> …` | step 3 for that variable |
| `new-token — <VAR>: …` | Slack rejected the token: the person first checks the variable's value (the whole token, no quotes, no newline); if it is clean, reinstalls the app (OAuth & Permissions → Reinstall) and puts the fresh token into the variable → step 3's «new terminal» part |
| `retry — …` | transient (network, rate limit): `doctor` again in a minute; if it persists, the person checks the variable's value for stray characters → `doctor` again |
| `swap-tokens — …` | the variables hold the wrong kind of token: the line says which — swapped values → the person swaps them; the same token in both → the person pastes the missing one (`xoxp-` user, `xoxb-` bot) → step 3's «new terminal» part |
| `decide — …` | the token and the profile disagree: ask the person which is right, then do the half of the line that applies (the `init … --force` the line names for the token — `<VAR>` placeholders: the person names the two variables, step 3; a fresh variable for the profile) |
| `frozen — …` | the person's emergency stop. Say so. If they want it lifted, the person sets `freeze: false` in the file; otherwise stop here |
| `fix-policy — …` / `fix-journal — …` | quote the line; the fix is in the file or directory it names |
| `send — …` | consistent, with the channel for the first message: step 4 — unless the hello was already sent and the line names the bot DM: then the setup is done, no second hello. If `·` lines under the token lines said fields are unset (an env-only profile), the identity check compares only what is set — say so |

After every fix, `doctor` again. A `✗` or `·` line that is not the `next:` line is context, not an instruction — mention it once (the `·` hygiene scopes `chat:write.customize` / `incoming-webhook`: suggest dropping them from the app).

## Step 2 — `init`

```bash
python3 -m atlas_slack init                                                       # the default profile: SLACK_USER_TOKEN / SLACK_BOT_TOKEN
python3 -m atlas_slack init --profile acme --user-var ACME_SLACK_USER --bot-var ACME_SLACK_BOT   # a second workspace or a test app
```

A non-default profile must name **both** its variables (upper-case letters, digits, underscores) — in the options, or in the file's `token_sources` for an existing file; without them `init` refuses (`names no token variables`), with one alone it refuses (`name both variables`) — never a copy of the first workspace's identities or of its other variable. A named profile that really shares the default tokens says so: `--user-var SLACK_USER_TOKEN --bot-var SLACK_BOT_TOKEN` (the file then declares them, so later `init --force` runs need no flags).

`init` prints either `wrote <path> …` or one refusal on stderr. If it printed a line starting with `note:`, that existing file's content was not kept — tell the person. Then:

- `wrote …` → for a non-default `<name>`, first the selection below; then step 1 again with `--profile <name>` (init's own `then:` hint says the same; `doctor` decides). A trailing `bot DM not found: …` is fine; step 4 opens the DM.
- `cannot read the identities behind the tokens:` → step 3 (the lines under it name the variable and the reason; `… is not set` = unset, a Slack error code = reinstall for a fresh token, `<SomethingError>:` = network).
- `is a bot token` / `is not a bot token` / `different workspaces` → the variables hold the wrong tokens → step 3.
- `profile name … is not valid` / `is not an environment variable name` / `names no token variables` / `name both variables` → fix the command, run again.
- `… exists — edit it, or run init --force` → step 1 (`doctor` says what, if anything, is wrong).

**Selection (non-default `<name>` only).** Put `"ATLAS_SLACK_PROFILE": "<name>"` into the project's `.claude/settings.json` → `"env"` (show the JSON) — that reaches the hook and the MCP server after a restart (remember it for step 4). Without it the session keeps using `default`; `slack_api("_rights")`'s first line shows which profile a session runs.

## Step 3 — the Slack app and the tokens (the person's hands)

Point the person to `docs/setup.md` § 1 and § 2 and say the gist: create the app from `slack-app-manifest.json` (in the clone), install it to the workspace, copy the two tokens, put the two `export` lines into the start-up file of the shell they launch Claude Code from. Before they start: the app and its bot user are named Atlas and can be renamed; a workspace with app approval makes the install wait for the admin. A second workspace or a test app gets its own variable names (`ACME_SLACK_USER`, `ACME_SLACK_BOT`); step 2 records them. Tokens stay in the shell file, never in `settings.json` (it is often committed).

**New terminal.** Variables set now are not in this session's environment; no command from inside the session can add them. When the person says the file is saved, they do three things. They open a new terminal. They restart Claude Code from it, in the same project. They type «set up atlas-slack: run doctor --profile <name>, variables <USER_VAR> and <BOT_VAR>» (the real names; `default` and the variables may be omitted when they are the defaults) — that re-enters this skill at step 1 with the same profile and the same variables.

## Step 4 — the first send, then the DM id

If the plugin, the tokens, the profile or its selection changed in this session, a restart comes first; if nothing changed, the send happens in this session. Either way, give the person this sentence — it carries the step across a restart:

> «Read the last ten messages of a channel I am in, then post "hello from the door" to me in the bot's voice, then record the bot DM (atlas-slack setup).»

The `atlas-slack` skill handles the two Slack calls. Before the send, `slack_api("_rights")`: its first line must name `<name>` as the profile (if it names another, the Selection did not reach this session — step 2's Selection, restart); its `identities:` line names the person, the bot and the bot DM. `bot DM D…` → that is the post's channel. `bot DM none yet` → the post's channel is the person's **user id** (from that line, or from `slack_api("auth.test")` under the user token when it shows `?`), and Slack opens the DM. One card; the person presses Send; the result line reads `journal: op … ok · comm …` (the journal row records the DM Slack opened, so an edit of that post finds it).

**Record the bot DM — you run it, not the person.** Only when `identities:` said `none yet`. `_rights`'s first line says `(file)` or `(builtin)`. `(file)` → run `python3 -m atlas_slack init --profile <name> --force`: it records the DM id (the bot's reports to the person need it). Then `doctor` once more → `next: send` naming the DM. `(builtin)`, an env-only profile → the person sets `ATLAS_SLACK_PERSON_BOT_DM_ID` to the `D…` id from the send's result (its `channel:` line) and restarts; `doctor` in the new session names the DM, and the setup is done without another hello. That is the setup done.

## Refusals in a session — read the code, then act

| Code | Cause | Fix |
|---|---|---|
| `token_unavailable` | the server's environment lacks the variable | step 3, then restart |
| `profile_missing` / `profile_invalid` | `ATLAS_SLACK_PROFILE` names a missing file, an invalid name, or the file has a bad key / a token-shaped value | `doctor --profile <name>` → its `next:` line |
| `profile_mismatch` | the hook and the server see different `ATLAS_SLACK_PROFILE` | one place for the variable; restart |
| `identity_mismatch` | a token is not the workspace / person / bot of the profile | `doctor` → `decide` or `swap-tokens`; then restart the session |
| `identity_unverified` | no usable `auth.test` answer | call once more; if it persists, `doctor` (revoked token, network) |
| `missing_scope: <name>` | the app lacks a scope | the person adds it under OAuth & Permissions and **reinstalls**; the tokens change (as of 2026-10) → step 3 |
| `attribution_unavailable` | no `bot_user_id` in the profile | `init --profile <name> --force` (identity fields only) |
| `frozen` / `policy_invalid` / `journal_unavailable` | the profile's emergency stop / a broken or mis-pinned policy file / an unwritable journal directory | `doctor` → `frozen` / `fix-policy` / `fix-journal` |
| `version_skew` | the MCP server runs another plugin version than the hook (after an update) | restart the session |
| `decision_expired` | the card waited more than 30 minutes | the agent asks again — nothing was sent |
| `outcome_unknown` | Slack's answer was lost | the agent runs `_reconcile`; never a blind retry (the `atlas-slack` skill) |

## Updating

`git pull` in the clone (the directory marketplace reads it in place), then a session restart: until then the live MCP server runs the old code (when the version changed, it refuses sends with `version_skew`). Say what `CHANGELOG.md` lists for the new version before the person restarts: a version bump can change what the card does.
