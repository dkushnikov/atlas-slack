# Atlas Slack

**Slack for Claude Code, with checks you set for yourself. The access an agent gets anyway — plus a declared voice on every message, a card before anything leaves, a local record, and recovery instead of blind retries.**

> **Status: 0.7.1.** Works with a plain Claude Code setup and a Slack workspace of your own — no AI-partner framework, no other service. The door core has run the author's Slack since 2026-10-28; the six message sends go through it; identity is checked at connect. Named after [Atlas Seed](https://github.com/dkushnikov/atlas-seed) (an identity layer for an AI partner) because it was built for that partner; it does not need it.

---

## What this is

You already let an agent read and write your Slack, or you want to and something stops you. What stops most people is not access. It is the moment the agent posts the wrong thing, in the wrong voice, to the wrong person, and you find out from a colleague. Atlas Slack keeps the access and adds the checks.

- **A declared voice.** Every message goes out either as **you** (your user token, with a small mark under the message that carries your app's name — ours reads `💙 Atlas`, yours reads whatever you named the app) or as **the agent itself** (the app's own Slack user, which Slack now calls an agent). The reader always sees which. No fake sender names or icons, ever.
- **A card before it leaves.** To whom, in which voice, the exact text, what was checked and what was not. You press *Send* or *No*, or type a correction and nothing goes out. The approval covers that exact message: change a word, the thread, the time or the file and the question is asked again. One approval, one message, thirty minutes to answer.
- **Rules you write once.** A small policy file says which voice may speak in which channel and who must never be written to, and the door enforces it even when you click *Send*. Nothing autonomous unless you turn it on.
- **A record, and a way back.** Every message is written to a local journal before the network call and settled after. When Slack's answer is lost (a timeout, an `internal_error`), nothing is retried blindly: the agent is told to look at Slack and settle what happened, and cannot re-send that message until it has.
- **The same door for scripts.** A job uses the library with a list of what it may touch. Same policy, same journal. What needs a human is refused there, never auto-allowed.

### What you can ask it, and what holds

**«Answer the three threads waiting on me, in my name. I'm in meetings.»** The agent drafts each reply and shows a card per message. You approve from the terminal, or type «shorter» and get a new card. Each reply carries your name and the mark; nobody has to guess who wrote it.

```
To:      #team-platform · in thread · now
From:    you (your voice, marked 💙 Atlas)
Text:
Thanks — merged, deploying after lunch. I'll ping you when it is live.

Door:   👤 recipient ✅ · 🎙 voice ✅ you + 💙 · 🔏 binding ✅ · 📓 journal ✅ · 📜 policy ✅
Context checked:  recipient and register — Anna, platform lead · disclosure of the assistant — the mark
Send?                                                   [Send] [No]
```

**«Fix the typo in what you just posted.»** The card shows *was* and *becomes*. The agent can only edit what was posted in the same voice: it never rewrites a colleague's message, and never yours in the agent's voice.

**«Send the one-pager to #product with a two-line comment, as yourself.»** The file you approved is the file that goes: its bytes are bound into the approval. Rewrite the PDF after the card and the door asks again.

**«Schedule the announcement for Monday 9:00.»** The time is part of the approval. Move it and the card comes back. The message leaves in the agent's voice at 9:00 with nobody at the keyboard, because you decided that on Friday.

**«Never write to the CEO's DM in my name. In #announce only the agent speaks, and only with my approval.»** That is a policy file, not a prompt (the file spells the agent's voice `bot`, Slack's older word):

```yaml
version: "2026-10-31.1"
channels:
  C0ANNOUNCE:
    voices: [bot]                  # only the agent speaks here
    modes: {A: deny, B-h: human}   # your voice is off; the agent's needs your decision
recipients_deny:
  A: [D0CEODM001]                  # never in your name, even if you click Send
```

**«The laptop went to sleep mid-send. Did it go?»** The journal row says `unknown`. The agent reads Slack, finds the message or does not, and settles the row. Until then a second attempt is refused. That is the double post that never happens.

**«Every morning at eight, a digest in your DM to me.»** A script, not a session: it goes through the same door with a manifest (one target, two channels to read) and a policy line that allows exactly that. Everything else a job might try is refused.

The last one needs the journal and the policy; the first six work with the defaults.

## Two questions people ask first

**Does it need an AI partner, Atlas Seed, anything else?** No. Python 3.12+, Claude Code, Slack. It was built for a partner with an identity of its own, and the card reads naturally when the agent *is* such a partner. A plain Claude Code session gets the same checks.

**Does it need a Slack app? Can I even create one?** Yes, and usually yes. Every Slack integration runs under some app's tokens; the official MCP hides that behind Slack's own app, this one gives you your own, created from the manifest in this repo in about five minutes. That is what makes the two voices possible (a user token and an agent token of one app), keeps the tokens on your machine, and keeps the app *internal* in Slack's terms, outside the rate limits Slack applies to distributed apps. Slack lets workspace members create apps by default; where an admin has turned on app approval, the install waits for their one-time approval, creation itself usually does not. One app per person is the path this release takes, and it is what gives each person their own agent: its name, the channels it was invited to, its DM with you. A single app shared by a team would be one agent user for everyone — the same name under every colleague's messages, one set of channel memberships read by every laptop — and would need an OAuth sign-in flow for each person's user token, which is not shipped. Not the same product.

**Why not the official Slack MCP as the transport?** It runs under one identity, the OAuth user's. One identity means no second voice, no mark as a choice, nothing in message metadata for recovery, and nothing a script can import. Your own app is what the two voices cost.

## What this is NOT

- **Not a replacement for the official Slack MCP.** That one is about access: OAuth, search, canvas, reading and posting. This one is about who speaks, with what approval, with what record. They coexist; when both could perform a write, the agent is told to use `slack_api`, because the checks above apply only to it.
- **Not an autonomous poster.** Out of the box nothing leaves without your card. Autonomous classes exist in the policy model (reports to you, limited jobs) and stay off until you enable them per context.
- **Not a Slack bot framework.** No events, no slash commands, no Socket Mode. One person's agent reads and writes; listening to a workspace is another tool's job.
- **Not finished.** The full checks cover the **six message sends**: `chat.postMessage`, `scheduleMessage`, `update`, `postEphemeral`, `meMessage`, `files.uploadV2`. Everything else, reactions, pins, deletes, channel structure, streams, runs on a labelled *transitional* path: a compact card, today's gate semantics, no journal row.

## What leaves your machine

Slack API calls under your two tokens, and nothing else. The journal, the profile, the policy and the decisions stay in your home directory. The agent's reasoning goes wherever your Claude Code sends it; the door adds no service of its own.

## What you get

| | |
|---|---|
| `slack_api(method, params, as_bot)` | every Slack Web API method, plus `_rights` (what this host may do now), `_comm_id` (a journal-unique id for a communication), `_reconcile` (settle unknown outcomes). The flag is still called `as_bot`: `True` = the agent's own voice, `False` = yours |
| `slack_download(file_id)` | file bytes to local disk — the JSON API never serves them |
| the approval card | a Claude Code mod (2.1.314+) that replaces the stock JSON permission dialog for a send with one readable card; older Claude Code: the stock dialog with the same question inside |
| two PreToolUse hooks | the write gate (a thin adapter over the door's policy engine) and a Bash gate that blocks `curl slack.com/api/…` and keychain reads |
| two skills | `atlas-slack` — what the agent reads on every Slack task: method table, voices, the sending contract; `atlas-slack-setup` — installs, checks and repairs the setup from a session |
| `Door` | the library face for scripts and jobs, with the same policy and journal |

## How to use this

Short form; the full procedure with Slack's menu names, the `doctor` output explained and a refusal table is [`docs/setup.md`](docs/setup.md), and, once the plugin is installed (step 3), the `atlas-slack-setup` skill walks a Claude Code session through the rest («set up atlas-slack»). You need Python 3.12+, Claude Code (2.1.314 or newer for the card), and the right to create a Slack app in your workspace. One interpreter for everything: the `python3` on the PATH of the shell Claude Code starts from runs the MCP server and the hooks, so `pip install` into that one.

**1. Create your Slack app.** https://api.slack.com/apps → **Create New App** → **From a manifest** → paste [`slack-app-manifest.json`](slack-app-manifest.json). The app and its agent user are named **Atlas**; rename them if you like — the name you choose is what the mark under your messages will say. Install it to the workspace and copy both tokens: **User OAuth Token** (`xoxp-…`, your voice and your reads) and **Bot User OAuth Token** (`xoxb-…`, the agent's own voice; Slack still labels it *bot* on that page). The manifest carries the scopes for the door's reads, its six sends, opening a DM, reactions, pins, reminders, your status and DND — and deliberately no `chat:write.customize`, no incoming webhook, no commands, no events. A method outside the granted scopes fails with `missing_scope`, which names the scope to add.

**2. Tokens in the shell environment** — not in `settings.json` (`${VAR}` in the plugin's `.mcp.json` resolves from the shell only). Open a new terminal, or `source` the file, before step 4, and start Claude Code from a shell that has them:

```bash
# ~/.zshenv — or the start-up file of the shell your Claude Code sessions inherit
export SLACK_USER_TOKEN="xoxp-..."
export SLACK_BOT_TOKEN="xoxb-..."
```

**3. Install the package and the plugin.**

```bash
git clone https://github.com/dkushnikov/atlas-slack.git
python3 -m pip install -e ./atlas-slack   # the hooks and the server import the checkout's src/

claude plugin marketplace add ./atlas-slack
claude plugin install atlas-slack@atlas-slack
```

**4. Write the profile — one step.**

```bash
python3 -m atlas_slack init           # → ~/.config/atlas-slack/default.yaml
```

It asks Slack who the two tokens are and writes the profile: `workspace_id`, `person_user_id` (you), `bot_user_id` (the agent — the attribution target), and the agent's DM with you when one exists. No secrets in the file — token variable *names* only. It refuses to overwrite a profile (`--force` rewrites the identity fields, and the token variable names when you pass them, and keeps the other keys) and refuses tokens that are not one person + one agent in one workspace. A second workspace or a test app has its own token variables: `init --profile acme --user-var ACME_SLACK_USER --bot-var ACME_SLACK_BOT` (a non-default profile that names fewer than both is refused — it would copy the first workspace's identities or one of its variables), then `ATLAS_SLACK_PROFILE=acme` in the environment Claude Code runs in — a project's `.claude/settings.json` → `env` works; the hook and the MCP server must see the same value.

**5. Check, then send one message.**

```bash
python3 -m atlas_slack doctor
```

Both tokens against the profile, the scope count, hygiene flags, the rights table — and one last line, `next: send — …`, that names the channel for your first message (the agent's DM with you, or your own user id when no DM exists yet: Slack opens it on the first post). Any other `next:` code names the one thing to fix first. Then start a Claude Code session, ask it to read a channel, then to post one message **to you, in the agent's voice** — you will see the card. The door checks identity by itself too: before the first write under each token in a session (sends, reactions, pins — reads are not attributed) it asks Slack `auth.test` once and refuses `identity_mismatch` when a token is not the workspace, person or agent the profile names.

## Defaults, and what is yours to set

- **Policy.** Without a policy file the built-in strict policy applies: reads are allowed; a message in your voice or the agent's asks you every time; autonomous sending is off. A policy file (`docs/policy-schema.md`) widens or narrows that per context — which voice and class may speak in which channel, deny lists of recipients — and the profile pins its sha256. What a job may read is set in the job's manifest. (In the policy file the agent's voice is still spelled `bot`.)
- **Journal.** `~/.local/share/atlas-slack/<profile>/journal.sqlite`, written only by the door — and read by it: an edit finds its communication there, a resend is refused while a row is `unknown`, reconciliation settles those rows. It is operational state, not an archive. Retention is still yours, with one rule: blank `payload_json` (the message text) whenever you like and keep the row; delete rows only when they are `ok` or `error` and older than anything you might edit or resend. Nothing outside the door reads the file unless you export it (`Journal.export_envelope()`). `freeze: true` in the profile stops all writes at once.
- **Language.** English by default; `language: ru` in the profile switches the card and the prompt.
- **Optional layers.** The card can show a *ledger* mark when the profile names a `ledger_dir`: a folder of per-communication Markdown files (one per `comm_id`) in which some reviewer — in the author's setup a text-audit ritual called Cicero — records `audit: pass` and the drafts. The mark says exactly this: the file exists, its audit field says pass, and the text being sent equals the file's last draft (for Block Kit sends: not compared, and the mark says so). Without the folder there is no ledger line and never a refusal.
- **Known limit.** Recipient deny lists match the channel id and the user ids the agent declares (plus an ephemeral's viewer); to deny a person, list their DM channel id as well.

## Now and next

**In 0.7.0:** the six sends through the core; the card; the policy file (channels, voices, classes, deny lists); the journal and `_reconcile`; identity at connect; `init` / `doctor`; the `Door` library with manifests, with one autonomous class (C2, reports to you) enableable by policy.

**Next, in order:** the rest of the writes into the core (reactions, pins, deletes, structure — a journal row and a binding for each); the approval outside the terminal (a card in the agent's DM or on the phone, answered by a reaction — for jobs and for the moment you are away from the keyboard); a *notary* — a check before autonomous speech — so the remaining autonomous classes can be enabled; reconciliation that resolves a DM to its member for the deny lists. `CHANGELOG.md` says what each version changed; a session needs a restart after a plugin update (`version_skew` refuses sends until then — by design).

## Compared with the official Slack MCP (as of 2026-10-31)

| | Official Slack MCP | Atlas Slack |
|---|---|---|
| What it is for | access: OAuth, search, read, post, canvas | who speaks, with what approval, with what record |
| Install and maintenance | Slack-hosted, admin-approved OAuth, nothing to run | your own Slack app (manifest), a local Python package and a Claude Code plugin to keep updated |
| Identity | one app identity | two declared voices: yours with a visible mark, or the agent's; sender overrides refused |
| Approval | the host's generic tool permission | a card per message bound to its exact content; one approval = one message; 30-min window |
| Rules | Slack admin controls | your policy file: voices and classes per channel, deny lists that hold against a click |
| Record | Slack-side audit of API use | a local write-ahead journal with the text, the decision and the outcome; recovery of unknown outcomes |
| Scripts and jobs | n/a | the same policy and journal through `Door`; nothing autonomous unless a policy says so |
| Operation coverage | Slack's curated set | every Web API method; six sends under the full checks, the rest on a transitional gate |

If you need search and the occasional post, the official MCP is enough. If an agent writes to your colleagues every day, in your name or its own, you want each of those messages decided and on record. That is what the door is for.

## Relationship to other projects

- **[Atlas Seed](https://github.com/dkushnikov/atlas-seed)** — an identity layer for an AI partner. Atlas Slack was built as one of that partner's doors and runs without it.
- **[Obsidian Seed](https://github.com/dkushnikov/obsidian-seed)** — a substrate for notes and memory; a ledger of communications (the optional layer above) is the kind of thing that lives there.
- **[Nestor](https://github.com/dkushnikov/nestor-plugin)** — a multi-model deliberation plugin by the same author; shares the repository practices, nothing else.

## Philosophy (the short version)

When an agent writes to your colleagues, three things have to stay true at every step: whose voice this is, and that the reader can tell; that you decided this message, not a category of messages; and that there is a record you can trust after a crash. Most Slack integrations for agents solve access and leave those three to the prompt. Atlas Slack puts them into the door: a policy engine, a one-time decision bound to the exact message, a journal written before the network call. The prompt can be wrong and the door still holds.

## Documentation

- **`docs/output-contract.md`** — what `slack_api` returns, per shape; error codes; the result line after one of the six sends.
- **`docs/policy-schema.md`** — profile, policy, provenance, decisions, journal and retention; the job manifest.
- **`CHANGELOG.md`** — versions; **`KNOWN_ISSUES.md`** — the bug history behind the current invariants.
- **`plugins/atlas-slack/skills/atlas-slack/SKILL.md`** — what the agent reads.

## Contributing

The most useful contribution now is **installing it**: create the app, run `init` and `doctor`, send one message to yourself, and open an issue with what was unclear, what refused you, and what you could not find afterwards in the journal. PRs on documentation and tests are welcome; the policy model and the contract change through issues first. Working on your own copy: `docs/setup.md` § Your own copy — one checkout is the plugin.

## License

MIT.
