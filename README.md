# Atlas Slack

**Slack for Claude Code, with checks you set for yourself. The same access an agent gets anyway — plus a declared voice on every message, a card before anything leaves, a local record, and recovery instead of blind retries.**

> **Status: 0.7.0.** Works with a plain Claude Code setup and a Slack workspace of your own — no AI-partner framework, no other service. The door core has run the author's Slack since 2026-10-28; the six message sends go through it; identity is checked at connect. Named after [Atlas Seed](https://github.com/dkushnikov/atlas-seed) (an identity layer for an AI partner) because it was built for that partner; it does not need it.

---

## What this is

You already let an agent read and write your Slack — or you want to, and something stops you. What stops most people is not access; it is the moment the agent posts the wrong thing, in the wrong voice, to the wrong person, and you find out from a colleague. Atlas Slack keeps the access and adds the checks:

- **Voice.** Every message is either **yours** (your user token, with a visible `💙 Atlas` mark so the reader knows the agent took part) or **the bot's** (the app's own user). The agent chooses per message; the reader always sees which. No fake sender names or icons, ever.
- **A card before it leaves.** To whom, in which voice, the exact text, what was checked and what was not — *Send* or *No*. The approval binds that exact operation: change a word, the thread, the time or the file and the question is asked again. One approval = one operation, 30 minutes to answer.
- **Rules you write once.** A small policy file says which voice and class may speak in which channel and who must never be written to — and the door enforces it even when you click *Send*. Nothing autonomous unless you turn it on.
- **A record and a way back.** Every send is written to a local journal *before* the network call and settled after. When Slack's answer is lost (timeout, `internal_error`), nothing is retried blindly: the agent is told to *reconcile* — read Slack, settle what happened — and cannot re-send that communication until it has.
- **The same door for scripts.** A job uses the library face with a manifest of what it may touch. Same policy, same journal; what needs a human is refused there, never auto-allowed.

### Three situations

**1. «Answer Anna in the thread, in my voice.»** The agent reads the thread, drafts, calls `chat.postMessage` with `as_bot=False`. You see:

```
To:      #team-platform · in thread · now
From:    you (your voice, marked 💙 Atlas)
Text:
Thanks — merged, deploying after lunch. I'll ping you when it is live.

Door:   👤 recipient ✅ · 🎙 voice ✅ you + 💙 · 🔏 binding ✅ · 📓 journal ✅ · 📜 policy ✅
Context checked:  recipient and register — Anna, platform lead · disclosure of the assistant — the 💙 mark
Send?                                                   [Send] [No]
```

Type a correction instead of pressing a button — «shorter, drop the second sentence» — and nothing is sent: the agent gets your words back as the refusal reason and comes back with a new card.

**2. «Post the weekly summary to #announce as the bot — never to the CEO's DM.»** A policy file names the channel and the rule:

```yaml
version: "2026-10-31.1"
channels:
  C0ANNOUNCE:
    voices: [bot]                 # only the bot speaks here
    modes: {A: deny, B-h: human}  # your voice is off; the bot's needs your decision
recipients_deny:
  B-h: [D0CEODM001]               # the bot never writes there, even if you click Send
```

The door refuses `voice_not_allowed`, `not_enabled_by_policy` or `recipient_denied` before any Slack call — and before the first write of the session it has already checked that the bot token belongs to *your* workspace and *your* app (`identity_mismatch` otherwise).

**3. «The session died while sending. Did it go?»** The journal row says `unknown`. The agent runs `slack_api("_reconcile")` — no message is sent; Slack is read and the row settled:

```
reconcile: op 7c3e9a21 chat.postMessage #team-platform → ok (found by text and author, ts 1761912004.118)
```

If it cannot be settled by reading, the agent asks you to look and records your word. Until then a resend of that communication is refused — that is the double post that never happens.

**And in the background:** a script reports to you in the bot's DM every morning through the same door, with a manifest (`cls: C2`, the DM as the only target, `conversations.history` as the only read). Policy enables that one class; everything else a job might try is refused.

## Two questions people ask first

**Does it need an AI partner, Atlas Seed, anything else?** No. Python 3.12+, Claude Code, Slack. It was built for a partner with an identity of its own, and the card reads naturally when the agent *is* such a partner — but a plain Claude Code session gets the same checks.

**Does it need a Slack app?** Yes — yours, created from the manifest in this repo in about five minutes. Every Slack integration runs under some app's tokens; the official MCP hides that behind Slack's own app, this one gives you your own. That is what makes the two voices possible (a user token and a bot token of one app), keeps the tokens on your machine, and keeps the app *internal* in Slack's terms — outside the rate limits Slack applies to distributed apps.

**Can I create one? Why not use the official Slack MCP as the transport?** In a normal workspace, yes: Slack lets members create apps for their own workspace by default; where an admin has turned on *app approval*, the install needs their one-time approval, creation itself usually does not. One app per person is the simple path. One shared app per team would give everyone the same bot token, but each person's *user* token then needs an OAuth sign-in flow that this plugin does not ship yet — a team install is on the list, not in 0.7.0.

The official Slack MCP cannot be the transport because it runs under one identity, the OAuth user's. One identity means no bot voice, no `💙` mark as a choice, no `op_id` in message metadata for reconciliation, and nothing a script can import. Your own app is what the two voices cost. A lighter mode — your voice only, the card and the journal as hooks over the official MCP — is conceivable and not built.

## What this is NOT

- **Not a replacement for the official Slack MCP.** That one is about access — OAuth, search, canvas, reading and posting. This one is about *managed communication*. They coexist; when both could perform a write, the agent is told to use `slack_api`, because the guarantees above apply only to it.
- **Not an autonomous poster.** Out of the box nothing leaves without your card. Autonomous classes exist in the policy model (reports to you, limited jobs) and are off until you enable them per context.
- **Not a Slack bot framework.** No events, no slash commands, no Socket Mode. One person's agent reads and writes; listening to a workspace is another tool's job.
- **Not finished.** The full guarantees cover the **six message sends** (`chat.postMessage`, `scheduleMessage`, `update`, `postEphemeral`, `meMessage`, `files.uploadV2`). Everything else — reactions, pins, deletes, channel structure, streams — runs on a labelled *transitional* path: a compact card, today's gate semantics, no journal row.

## What you get

| | |
|---|---|
| `slack_api(method, params, as_bot)` | every Slack Web API method, plus `_rights` (what this host may do now), `_comm_id` (a journal-unique id for a communication), `_reconcile` (settle unknown outcomes) |
| `slack_download(file_id)` | file bytes to local disk — the JSON API never serves them |
| the approval card | a Claude Code mod (2.1.314+) that replaces the stock JSON permission dialog for a send with one readable card; older Claude Code: the stock dialog with the same question inside |
| two PreToolUse hooks | the write gate (a thin adapter over the door's policy engine) and a Bash gate that blocks `curl slack.com/api/…` and keychain reads |
| the skill | what the agent reads on every Slack task: method table, voices, the sending contract |
| `Door` | the library face for scripts and jobs, with the same policy and journal |

## How to use this

You need Python 3.12+, Claude Code (2.1.314 or newer for the card), and the right to create a Slack app in your workspace. One interpreter for everything: the `python3` on the PATH of the shell Claude Code starts from runs the MCP server and the hooks, so `pip install` into that one.

**1. Create your Slack app.** https://api.slack.com/apps → **Create New App** → **From a manifest** → paste [`slack-app-manifest.json`](slack-app-manifest.json). The app and its bot user are named **Atlas**; rename them if you like. Install it to the workspace and copy both tokens: **User OAuth Token** (`xoxp-…`, your voice and your reads) and **Bot User OAuth Token** (`xoxb-…`, the bot's voice). The manifest carries the scopes the door's reads and its six sends use — and deliberately no `chat:write.customize`, no incoming webhook, no commands, no events. A method outside the granted scopes fails with `missing_scope`, which names the scope to add.

**2. Tokens in the shell environment** — not in `settings.json` (`${VAR}` in the plugin's `.mcp.json` resolves from the shell only). Open a new terminal, or `source` the file, before step 4, and start Claude Code from a shell that has them:

```bash
# ~/.zshenv (or the profile your Claude Code sessions inherit)
export SLACK_USER_TOKEN="xoxp-..."
export SLACK_BOT_TOKEN="xoxb-..."
```

**3. Install the package and the plugin.**

```bash
git clone https://github.com/dkushnikov/atlas-slack.git
pip install -e ./atlas-slack          # the hooks and the server import the checkout's src/

claude plugin marketplace add ./atlas-slack
claude plugin install atlas-slack@atlas-slack
```

**4. Write the profile — one step.**

```bash
python3 -m atlas_slack init           # → ~/.config/atlas-slack/default.yaml
```

It asks Slack who the two tokens are and writes the profile: `workspace_id`, `person_user_id` (you), `bot_user_id` (the bot — the attribution target), the bot's DM with you. No secrets in the file — token variable *names* only. It refuses to overwrite a profile and refuses tokens that are not one person + one bot in one workspace. Several profiles (two workspaces, a test app): `init --profile <name>` and `ATLAS_SLACK_PROFILE=<name>` in the environment Claude Code runs in — a project's `.claude/settings.json` → `env` works; the hook and the MCP server must see the same value.

**5. Check, then send one message.**

```bash
python3 -m atlas_slack doctor
```

Both tokens against the profile, the scope count, hygiene flags, and the rights table. Then start a Claude Code session, ask it to read a channel, then to post one message **to you, in the bot's voice** — the bot's DM with you is the one channel the bot is always in (`init` recorded it); you will see the card.

## Defaults, and what is yours to set

- **Policy.** Without a policy file the built-in strict policy applies: reads are allowed; a message in your voice or the bot's asks you every time; autonomous sending is off. A policy file (`docs/policy-schema.md`) widens or narrows that per context — which voice and class may speak in which channel, deny lists of recipients — and the profile pins its sha256. What a job may read is set in the job's manifest.
- **Journal.** `~/.local/share/atlas-slack/<profile>/journal.sqlite`, written only by the door — and read by it: an edit finds its communication there, a resend is refused while a row is `unknown`, reconciliation settles those rows. It is operational state, not an archive. Retention is still yours, with one rule: blank `payload_json` (the message text) whenever you like and keep the row; delete rows only when they are `ok` or `error` and older than anything you might edit or resend. Nothing outside the door reads the file unless you export it (`Journal.export_envelope()`). `freeze: true` in the profile stops all writes at once.
- **Language.** English by default; `language: ru` in the profile switches the card and the prompt.
- **Optional layers.** The card can show a *ledger* mark when the profile names a `ledger_dir`: a folder of per-communication Markdown files (one per `comm_id`) in which some reviewer — in the author's setup a text-audit ritual called Cicero — records `audit: pass` and the drafts. The mark says exactly this: the file exists, its audit field says pass, and the text being sent equals the file's last draft (for Block Kit sends: not compared, and the mark says so). Without the folder there is no ledger line and never a refusal.
- **Known limit.** Recipient deny lists match the channel id and the user ids the agent declares (plus an ephemeral's viewer); to deny a person, list their DM channel id as well.

## Now and next

**In 0.7.0:** the six sends through the core; the card; the policy file (channels, voices, classes, deny lists); the journal and `_reconcile`; identity at connect; `init` / `doctor`; the `Door` library with manifests, with one autonomous class (C2, reports to you) enableable by policy.

**Next, in order:** the rest of the writes into the core (reactions, pins, deletes, structure — a journal row and a binding for each); the approval outside the terminal (a card in the bot's DM or on the phone, answered by a reaction — for jobs and for the moment you are away from the keyboard); a *notary* — a check before autonomous speech — so the remaining autonomous classes can be enabled; reconciliation that resolves a DM to its member for the deny lists. `CHANGELOG.md` says what each version changed; a session needs a restart after a plugin update (`version_skew` refuses sends until then — by design).

## Compared with the official Slack MCP (as of 2026-10-31)

| | Official Slack MCP | Atlas Slack |
|---|---|---|
| What it is for | access: OAuth, search, read, post, canvas | managed communication: who speaks, with what approval, with what record |
| Install and maintenance | Slack-hosted, admin-approved OAuth, nothing to run | your own Slack app (manifest), a local Python package and a Claude Code plugin to keep updated |
| Identity | one app identity | two declared voices: yours with a visible mark, or the bot's; sender overrides refused |
| Approval | the host's generic tool permission | a card per operation bound to its exact content; one approval = one operation; 30-min window |
| Rules | Slack admin controls | your policy file: voices and classes per channel, deny lists that hold against a click |
| Record | Slack-side audit of API use | a local write-ahead journal with the text, the decision and the outcome; reconciliation of unknown outcomes |
| Scripts and jobs | n/a | the same policy and journal through `Door`; nothing autonomous unless a policy says so |
| Operation coverage | Slack's curated set | every Web API method; six sends under the full guarantees, the rest on a transitional gate |

If you need search and the occasional post, the official MCP is enough. If an agent writes to your colleagues regularly, in your name or its own, this is the difference between hoping it goes well and deciding each time it does.

## Relationship to other projects

- **[Atlas Seed](https://github.com/dkushnikov/atlas-seed)** — an identity layer for an AI partner. Atlas Slack was built as one of that partner's doors and runs without it.
- **[Obsidian Seed](https://github.com/dkushnikov/obsidian-seed)** — a substrate for notes and memory; a ledger of communications (the optional layer above) is the kind of thing that lives there.
- **[Nestor](https://github.com/dkushnikov/nestor-plugin)** — a multi-model deliberation plugin by the same author; shares the repository practices, nothing else.

## Philosophy (the short version)

Delegating communication is not the same as delegating a task. When an agent writes to your colleagues, three things must stay true at every step: *whose voice this is* (and that the reader can tell), *that you decided* (this message, not a category of messages), and *that there is a record you can trust after a crash*. Most Slack integrations for agents solve access and leave these three to the prompt. Atlas Slack moves them into the door — a policy engine, a one-time decision bound to the exact operation, a journal written before the network call — so the prompt can be wrong and the door still holds.

## Documentation

- **`docs/output-contract.md`** — what `slack_api` returns, per shape; error codes; the result line after one of the six sends.
- **`docs/policy-schema.md`** — profile, policy, provenance, decisions, journal and retention; the job manifest.
- **`CHANGELOG.md`** — versions; **`KNOWN_ISSUES.md`** — the bug history behind the current invariants.
- **`plugins/atlas-slack/skills/atlas-slack/SKILL.md`** — what the agent reads.

## Contributing

The most useful contribution now is **installing it**: create the app, run `init` and `doctor`, send one message to yourself, and open an issue with what was unclear, what refused you, and what you could not find afterwards in the journal. PRs on documentation and tests are welcome; the policy model and the contract change through issues first.

## License

MIT.
