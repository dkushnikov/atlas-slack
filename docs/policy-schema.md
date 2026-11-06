---
title: atlas-slack — profile, policy and provenance schema
status: normative — the reference for profile, policy, provenance, decisions, journal (plugin 0.8.1)
created: 2026-10-28
---

# atlas-slack — profile, policy and provenance schema

Three inputs decide what the door does with a write:

- the **profile**: per host, machine-local, no secrets. It says which workspace, which identities, where the policy and the journal live, and whether the door is frozen.
- the **policy**: the context's rules, versioned. It says which sender class may speak where, and how.
- the **provenance**: per message. It says who prepared it, for whom, which checks the context declares, and which decision covers it.

```
effective_policy = context policy, if configured and valid; else the built-in strict policy
rights           = immutable invariants ∩ capabilities ∩ effective_policy
```

A *missing* policy file and an *invalid* one are different states. Missing → built-in strict. Invalid → every write refuses `policy_invalid` and reads continue. The door never falls back to a friendlier default.

## Sender classes

| Class | Voice | Who decides | Built-in strict policy |
|---|---|---|---|
| `A` | the person (user token, 💙 attribution to the bot) | the person, per message | human decision |
| `B-h` | the bot | the person, per message | human decision |
| `B-a` | the bot, on its own initiative, in a process without the person's token | the process + a notary stamp | disabled |
| `C1` | the bot, an autonomous job talking to others | job policy + a notary stamp | disabled |
| `C2` | reads as the person, reports in the bot voice to the person only | job policy | disabled |

B-a / C1 / C2 are disabled by the *built-in strict policy*, not by code. A valid context policy can set their modes, and the invariants still hold. In this slice no notary exists, so B-a/C1 refuse `no_notary` even under a permissive policy. They also refuse `user_token_in_process` when the person's token is in the process.

## Profile — `~/.config/atlas-slack/<ctx>.yaml`

Selected by `ATLAS_SLACK_PROFILE=<ctx>` (default `default`). Put it in the environment of the Claude Code session, for example a project's `.claude/settings.json` → `env`. The hook and the MCP server must see the same value.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `workspace_id` | string | — | Slack team id (`T…`); `Door.doctor()` checks `auth.test` against it |
| `person_user_id` | string | — | the person's user id (`U…`); a bot send addressed to it lands in the person's bot DM, so it counts as row 2 "report to the person" too (C2, the `required_checks` exemption) |
| `bot_user_id` | string | — | the bot's user id — the 💙 attribution target; **required for person-voice sends** |
| `person_bot_dm_id` | string | — | the bot-side DM with the person (`D…`) — row 2 "report to the person" (with `person_user_id`, the two ids that reach the person) |
| `token_sources` | `{user, bot}` → env var **names** | `SLACK_USER_TOKEN`, `SLACK_BOT_TOKEN` | where the tokens are; never the tokens themselves |
| `policy_path` | path | — | the context policy yaml; absolute or `~/…` (the loader expands `~`); `init --policy-path` writes it (0.7.3) |
| `policy_sha256` | 64 hex | — | pin: the policy file's sha256 must match, else `policy_invalid`; `init --policy-path … --pin` writes it, `--no-pin` removes it |
| `journal_dir` | path | `~/.local/share/atlas-slack/<ctx>/` | journal + decision secret |
| `ledger_dir` | path list (`os.pathsep`) | none (env `ATLAS_SLACK_LEDGER_DIR` overrides) | the `/comms` ledger folder(s) of this context (`comm_id:` in each file's frontmatter; newest file wins across folders); env `ATLAS_SLACK_LEDGER_DIR` overrides. Optional: without it the card has no ledger line. With it the approval prompt reads the file behind `provenance.comm_id` and shows `ledger: ✅ аудит Cicero <audit_at>` only when `audit: pass` and the text being sent equals the file's last `## Draft vN` section; or `✅ … (текст в blocks — не сверен)` for a blocks-only send; otherwise `⚠ аудит был для другого текста` / `⚠ аудит, но в ledger нет текста (## Draft)` / `☑ аудит пропущен` (`audit: skip`) / `⚠ аудит: <other value>` / `⚠ аудита нет` / `⚠ не найден` / `⚠ помечен dropped` / `⚠ папка ledger недоступна` / `⚠ не прочитан`; a second file with the same `comm_id` is flagged `(+N файл с тем же comm_id)`; plus `intention: …` — read, never judged, never a refusal by itself (0.5.2; a policy that requires the check `cicero` accepts the plain ✅ as that check — § Required checks); the same lines in English for `language: en` (`ledger.py`, e.g. `ledger: ✅ Cicero audit <audit_at>`). «Cicero» is the name of the author's text-audit ritual: any reviewer that writes `audit: pass` and the drafts into the file works the same |
| `decision_adapters` | list | `["cc-hook"]` | adapters that may carry a human decision; empty → human-decided sends refuse |
| `freeze` | bool | `false` | emergency stop: every write refuses, reads continue |
| `decision_fallback` | bool | `false` | use the pre-registered decision row instead of the `updatedInput` token (see Decisions); the live check 2026-10-28 proved the token path on Claude Code 2.1.296 |
| `decision_permission_modes` | list | `[default, acceptEdits, plan, auto]` | Claude Code permission modes in which the hook may ask for a decision; any other mode (`bypassPermissions`, `dontAsk`, unknown) → `deny permission_mode_unsafe` |
| `language` | `en` \| `ru` | `en` | language of the approval prompt |
| `names` | map id → label | `{}` | display labels that win over resolution, e.g. `U…: Ann`, `D…: "DM Ann↔Atlas"` |
| `resolve_names` | bool | `true` | resolve ids for the prompt with `conversations.info` / `users.info` (reads), cached 24 h in `<journal_dir>/names.json`; env `ATLAS_SLACK_RESOLVE_NAMES=0` switches it off |
| `attribution_label` | string | `Sent via` | the label of the person-voice footer, `:blue_heart: <label> <@bot_user_id>` — the bot is the channel, not the author; `""` = the mention alone; one line, at most 60 characters, no surrounding whitespace (0.8.0) |
| `cc_person` | bool | `false` | bot-voice message sends to others end with `<cc_label> <@person_user_id>` — the mention subscribes the person to the thread the recipients reply in; a thread reply only while the person does not follow the thread (the parent's author, its `reply_users`, a mention in it, or an earlier cc of the door in the thread — one read of the parent; a parent that cannot be read → the cc goes; 0.8.1); an edit keeps what the original had; never in the person's own conversations (their bot DM and their user id as the profile records them — without `person_bot_dm_id` a report into that DM carries the cc until `init --force` records it; `channels` on `files.completeUploadExternal` counts too), never on `chat.postEphemeral` or a stream, not when the text or blocks already mention them; `true` needs `person_user_id` (0.8.0) |
| `cc_label` | string | `cc` | the label before the cc mention; `""` = the mention alone; one line, at most 60 characters, no surrounding whitespace (0.8.0) |

Rules:

- **No file and no `ATLAS_SLACK_PROFILE`** → the built-in profile, derived from env. It reads `SLACK_USER_TOKEN`, `SLACK_BOT_TOKEN`, `ATLAS_BOT_USER_ID`, and optionally `ATLAS_SLACK_WORKSPACE_ID`, `ATLAS_SLACK_PERSON_USER_ID` and `ATLAS_SLACK_PERSON_BOT_DM_ID`. Verdicts say `profile_source: builtin`.
- **`ATLAS_SLACK_PROFILE` names a file that does not exist** → writes refuse `profile_missing`. Falling back to whatever tokens sit in the shell could attribute a send to another workspace's bot.
- Unknown keys, wrong types or a token-shaped value (`xox…-`) → `profile_invalid`, and writes refuse.
- Overrides for tests and relocation: `ATLAS_SLACK_CONFIG_DIR` (profile dir) and `ATLAS_SLACK_DATA_DIR` (journal root). `ATLAS_SLACK_IDENTITY_CHECK=0` switches the identity check off — the level-1 test suite sets it; a host never should.
- **Identity at connect (0.7.0).** Before the first write under each token in a process — in `core.execute_send` after the policy verdict and the decision checks and before the intent is journalled, so a frozen profile or an unapproved send never reaches the network — the door asks Slack `auth.test` once and compares the answer with the profile (`src/atlas_slack/identity.py`): `team_id` vs `workspace_id`, `user_id` vs `person_user_id` / `bot_user_id`, and that the bot slot holds a bot token (`bot_id` present) and the user slot does not. The answer is cached per process by profile, token variable and a fingerprint of the token value (a rotated token is checked again). A positive mismatch refuses `identity_mismatch`; no answer refuses `identity_unverified` (retried at the next write); nothing is sent and no `ops` row is written in either case — the refusal is in `calls`. Fields the profile leaves unset are not checked. The MCP face runs it under the client of the send, and also before a transitional write (reactions, pins, delete, …); reads are not checked. `Door` runs it only under its default transport (an explicit `transport=` is the caller's contract — tests, embedding). `Door.doctor(auth_test, as_bot)` is the same comparison with the unset fields reported as advice.
- **`python3 -m atlas_slack init [--profile NAME] [--user-var VAR --bot-var VAR] [--force]`** writes the profile from the two tokens (`auth.test` for each; the bot's DM with the person via `conversations.open` with `prevent_creation`, optional — when none exists, the first bot send with `channel` = the person's user id opens it and `init --force` records it). A non-default profile must name both its token variables — in the options or, for an existing file, in its `token_sources` with both keys — or name the default pair explicitly (the file then declares them); one option alone is refused; variable names follow the `token_sources` rule. `--force` rewrites the four identity fields (and `token_sources` when the options are given), adds `language: en` when absent, and keeps every other key. It refuses tokens that are not one person + one bot in one workspace. **`init [--profile NAME] --policy-path PATH [--pin | --no-pin]`** (0.7.3; NAME is letters, digits, `_ . -`, a letter or digit first, at most 64 characters — the rule `load_profile` applies) attaches a context policy to an existing profile — the door owns the profile directory, so a session never needs to edit it by hand: no tokens, no network, no `--force`. It loads and validates the file with the door's own loader first (`yaml.safe_load` + the schema below + the sha256 a send compares); a missing or invalid file exits non-zero with the loader's message and writes nothing. It then writes `policy_path` as given (absolute or `~/…`; a relative path is refused — the hook and the server run from different directories) and no other key: the two key lines are replaced in place (a quoted key too) or added at the end, as plain `key: value` lines (a comment on a replaced line is not kept), every other line stays; a layout a line edit cannot handle (a flow mapping, an explicit `? key`) is rewritten as a whole, header comments kept, and the command says so. A symlinked profile is written through to its target. The write is atomic and read back. `--pin` also writes `policy_sha256` (the file's sha256); `--no-pin` removes it; with neither, a pin that still matches stays, and a pin that no longer matches refuses (exit non-zero, nothing written — a stale pin would refuse every send) until `--pin` or `--no-pin` says which. `--no-pin` on the profile's own policy file works even when that file no longer parses, breaks the schema or is gone — removing a bad pin is the recovery path; it prints `policy: ✗ invalid — <error> · unpinned <path>` (or `built-in strict — …`) and says writes keep refusing until the file is fixed. A new path always has to validate. A missing profile is refused (`profile not found — run init (identity) first`), and so is the combination with `--force` / `--user-var` / `--bot-var`: identity first, then the policy, two commands. It prints one line: `policy: <version> sha256 <12 hex> pinned|unpinned <path>`; a rerun with the same arguments changes nothing. **`init [--profile NAME] --set KEY=VALUE [--set …]`** (0.8.0) writes a setting — `attribution_label`, `cc_person`, `cc_label`, nothing else (identity fields: `init --force`; the policy: `init --policy-path`) — into an existing profile through the same line edit: offline, no tokens; the merged mapping is validated by the door's loader before anything is written (a value the loader refuses — a label over 60 characters, `cc_person: true` without `person_user_id` — writes nothing); a label is taken as given (`""` = none), `cc_person` is `true` or `false`; the write is atomic, read back and restored on a mismatch; it does not combine with the identity or policy options. It prints one line, `marks: person voice <footer> · bot voice cc <line> on bot-voice sends to others (not in the person's own DM)` or `… · bot voice cc off` — the footer part reads `unavailable (no bot_user_id — person-voice sends refuse)` when the profile has no `bot_user_id`, and the cc part says `the bot DM is not in the profile — init --force records it …` when it has no `person_bot_dm_id`; a rerun changes nothing. **`doctor [--profile NAME]`** prints the profile line, one line per token with the identity comparison, the policy line (the same `policy: …` line with `· valid`, `policy: ✗ invalid — <loader error> · pinned|unpinned <path>`, or `policy: built-in strict — …`), the `marks:` line (the footer and the cc as this profile sends them, 0.8.0), the rights table, and ends with one `next: <code> — …` line (`init` · `fix-profile` · `set-token` · `new-token` · `retry` · `swap-tokens` · `decide` · `add-scope` · `frozen` · `fix-policy` · `fix-journal` · `send`; exit 0 only on `send`; `add-scope` covers the four scopes the first session uses — `channels:history`, `chat:write` for the person, `chat:write`, `im:write` for the bot — and is skipped, said aloud, when Slack's answer carries no scope header) — the setup skill follows that line.
- With non-default `token_sources`, those env vars must reach the MCP server process. The plugin's `.mcp.json` forwards only `SLACK_USER_TOKEN` / `SLACK_BOT_TOKEN` explicitly. Other names rely on the server inheriting the session environment.

### Example — a standalone profile (`~/.config/atlas-slack/acme.yaml`)

```yaml
workspace_id: TACME00001
person_user_id: UACMEPRSN
bot_user_id: UACMEBOT1
person_bot_dm_id: DACMEBOTDM
token_sources:
  user: ACME_SLACK_USER
  bot: ACME_SLACK_BOT
policy_path: ~/work/acme/.claude/atlas-slack.policy.yaml
policy_sha256: 3b6c…(64 hex)…   # the policy file's sha256; init --profile acme --policy-path <path> --pin writes it
decision_adapters: [cc-hook]
freeze: false
```

## Policy — `<project>/.claude/atlas-slack.policy.yaml`

```yaml
version: "2026-10-28.1"            # required; recorded in every journal row and verdict
ratified_by: "Pat Example"         # who approved this version
ratified_at: "2026-10-28"
channels:
  C0ACMEGEN1:
    name: general
    class: announce                # governance | leadership | working | announce | dm | external
    voices: [person]               # which voices may speak here
    approval: explicit-greenlight  # self-send-preferred | explicit-greenlight | one-word (shown in the prompt)
    modes: {A: human, B-h: deny}
  C0ACMETEAM:
    name: team-platform
    class: working
    voices: [person, bot]
    approval: one-word
    modes: {A: human, B-h: human, B-a: stamp, C1: stamp, C2: deny}
recipients_deny_autonomous: [UACMECEO1]   # never spoken to by B-a / C1
recipients_deny:                          # per-class deny lists — hold even against a human decision
  A: [UACMELEGAL]
required_checks:                          # checks a send of the class to others must carry (0.7.3)
  A: [cicero]                             # a message in the person's voice: the text audit, declared or from the ledger
grants: []                         # parsed, no semantics yet
report_channels: {}                # parsed, no semantics yet
defaults:
  unknown_channel: strictest       # the only supported value
```

Allowed `modes` per class. Anything else makes the policy invalid:

| Class | Modes | Meaning |
|---|---|---|
| `A`, `B-h` | `human`, `deny` | a human decides each send, or the class is off here |
| `B-a`, `C1` | `stamp`, `deny` | needs a notary stamp (none exists yet → refuse), or off |
| `C2` | `auto`, `deny` | report without a decision (only to the person: their bot DM or their user id), or off |

A channel not listed gets the strictest row: `A: human, B-h: human, B-a: deny, C1: deny, C2: deny`. A listed channel inherits the strictest mode for every class it does not name.

### Required checks (0.7.3)

`required_checks: {<class>: [<check name>, …]}` names, per sender class, the checks a core send (the six methods of § Provenance) of that class **to others** must carry. "To others" means the target channel is neither the person's bot DM (`person_bot_dm_id`) nor the person's user id (`person_user_id` — a bot send addressed to it opens their DM), each only when the profile sets it; a profile with neither exempts nothing. The key is optional and defaults to `{}`: a policy without it behaves exactly as before, and so does the built-in strict policy. Class keys are the five sender classes (`A`, `B-h`, `B-a`, `C1`, `C2`); a value is a list of check names — letters, digits, `_ . : -`, no spaces or commas (duplicates collapse; quote a name YAML would read as a boolean — `yes`, `no`, `on`, `off` — or the loader rejects the list); an empty list requires nothing. Any other shape makes the policy invalid.

A name is **satisfied** when `provenance.checks.<name>` is `true`; a non-empty string that is not one of `false`, `no`, `none`, `null`, `0`, `fail`, `failed`, `skip`, `skipped`, `n/a` (compared trimmed, in any case); or a non-empty object or list. That list is exhaustive: nothing else counts — numbers, `false`, `null`, an empty string, object or list do not. The door does not judge the content (A2): a declaration with no check behind it is the caller's false claim, tamper-evident through the journal — `provenance.checks` is stored on the op row of every send; the card shows the declaration as the context's claim, never as a ✅. The name **`cicero`** is also satisfied by the door's own ledger lookup of `provenance.comm_id` (profile `ledger_dir` / env `ATLAS_SLACK_LEDGER_DIR`, § Profile) when the card would read the plain `✅ Cicero audit`: the newest file with that `comm_id` says `audit: pass`, is not `status: dropped`, and the text being sent (`text`; an upload's `initial_comment`) equals its last `## Draft` section. The «not compared» ✅ of a blocks-only or text-less send does not count — the ledger cannot vouch for a text it did not compare; declare the check instead. So a send prepared through the `/comms` ledger passes without the caller restating it, and a host without a ledger is never stuck.

An unsatisfied name refuses **`check_missing`** in the verdict, next to `recipient_denied`: before any human decision is asked, the same on the hook and on the server (a valid approval does not override it — invariant iii), with no Slack call and a `calls` row `status=refused`. The refusal carries `needed` (the unsatisfied names, in the policy's order) and `provided` (the satisfied names — declared, or `cicero` from the ledger — sorted, only keys that match the check-name rule, at most 12), rendered like Slack's own detail fields: `Error: check_missing — needed: cicero · provided: recipient — <message>` (output contract v1.12). When an earlier reason refuses the same send (a deny list, a frozen profile), that reason is the code and no detail fields are shown. `provenance.checks` is not part of the decision binding, and the asymmetry is deliberate: a check added after approval is harmless (the approved operation is unchanged), a check stripped after approval refuses `check_missing` at the server. `_rights` and `doctor` list the policy's required checks.

### Deny lists are per class, and a human decision does not override them

`recipients_deny_autonomous` applies to the autonomous speaking classes B-a and C1. A person who must never hear from the bot on its own can still receive a message the human approved in class A or B-h. `recipients_deny: {<class>: [ids]}` is a deterministic prohibition for that class. **What is matched:** the `channel` of the send and the user ids the caller declares in `provenance.target.users` (plus the viewer of an ephemeral, which the door adds) — the door does not resolve a DM channel to its members, so to deny a person list their DM channel id (`D…`) next to their user id. It refuses even when a valid human decision is attached (invariant iii: "human" always reads "human + policy"). The checked recipients are the channel id plus `provenance.target.users`.

## Provenance (tool parameter `provenance`, every core send: `chat.postMessage`, `chat.scheduleMessage`, `chat.update`, `chat.postEphemeral`, `chat.meMessage`, `files.uploadV2`)

| Field | Type | Notes |
|---|---|---|
| `cls` | `A` \| `B-h` \| `B-a` \| `C1` \| `C2` | derived from `as_bot` (`A` / `B-h`) when omitted (0.5.1) |
| `voice` | `person` \| `bot` | must match `as_bot`; derived from it when omitted |
| `comm_id` | string | the communication item (may have edits, several messages, resends); minted by the door (`YYYYMMDD-<4 hex>`, OS entropy, journal-unique) when absent — returned in the result line, `comm_id_source: door` on the op row (0.5.1); `slack_api("_comm_id")` mints one ahead of the draft and notes the first sighting |
| `op_id` | string ≤128 | one write attempt; generated by the hook when absent; idempotency key |
| `prepared_by` | string | session / job id; the hook fills `cc-session:<id>` |
| `context` | string | free text; the profile name is recorded separately |
| `target` | `{channel, users, is_mpim}` | `channel` must equal `params.channel` |
| `checks` | object | declared by the context: `voice`, `recipient`, `policy_row`, `mark`, `disclosure`, `cicero`, … — shown, not judged; a policy's `required_checks` may require names here (§ Required checks) |
| `sources` | list | own visibility / the person's context / handed over from run X |
| `origin` | `live` \| `prepared-as-human` | prepared-as-human leaves only in the person's voice, needs `ttl_s` and `comm_id` (`comm_id_required`, 0.5.1) |
| `ttl_s` | seconds | measured from the **journal's first sighting** of `comm_id`, not from the caller |
| `prepared_at` | epoch / ISO | caller's claim; > 60 s in the future → refuse; otherwise informational |
| `scheduled_for` | epoch / ISO | bound into the decision |
| `file_sha256` | list of 64-hex | `files.uploadV2`: sha256 of each uploaded file (`file`, then `file_uploads[*].file`), filled by the door and bound into the decision; a caller value that differs from the bytes on disk refuses `file_mismatch` (0.6.0) |
| `resend` | bool | required to write a `comm_id` that already has a write |
| `decision` | `{adapter, token, at}` | **set by the decision adapter**, never by the caller (the hook overwrites it) |

**Migration rule (A12.2).** A core send without provenance is treated as class A (`as_bot=False`) or B-h (`as_bot=True`). It still needs a human decision, and the prompt warns `provenance missing`. Autonomous classes have no migration path.

**Binding.** `op_hash = sha256(canonical JSON {method, as_bot, params, cls, context, scheduled_for, file_sha256, target.users, comm_id, resend})`. Every caller param is bound, so changing `reply_broadcast` after approval also invalidates the decision; so does another `ts` on an edit, another `user` on an ephemeral, or a rewritten upload file. Only `_raw`, a render flag, is excluded. `chat.postEphemeral` adds `params.user` to `target.users` before hashing (the viewer is the recipient).

## Decisions

The Claude Code hook is the decision adapter (`cc-hook`).

- **Token path (default).** On `needs-human` the hook issues `token = <16 hex nonce>.<first 32 hex of HMAC-SHA256(secret, op_hash:nonce)>` (49 chars; the 128-bit MAC prefix is far beyond online guessing — each guess is a tool call and the row must also exist unconsumed). It records `decisions(token_hash, op_hash, adapter="cc-hook session=<id>", issued_at)` and answers `ask` with `updatedInput` carrying a **compact** provenance: the caller's own keys plus `decision: {t: <token>, p: <profile>}` — no echoed defaults. The server derives `op_id = sha256(op_hash:nonce)[:16]` (the prompt shows its first 8 chars) and takes `prepared_by` from the decision row. Claude Code documents that `updatedInput` combined with `ask` shows the modified input to the user and runs it on approval. The core recomputes the op hash and verifies the HMAC, then consumes the row atomically with the journal intent. A repeat refuses `decision_already_used`. A change refuses `decision_mismatch`. The token is valid for **30 minutes** from issue (0.5.1; 10 in 0.5.0 — 5 of 39 production approvals expired while the prompt sat in a background session). The clock starts when the prompt is offered, not when it is read; one-time use and the op-hash binding carry the safety.
- **Fallback (`decision_fallback: true`).** For a Claude Code build that drops `updatedInput` on `ask`. The hook only records the row. The core needs an unconsumed row for the exact op hash, issued within 30 minutes. The row's `adapter` text is `cc-hook v=<plugin version> session=<id>` (0.6.0): the version check (`version_skew`) and `prepared_by` read it on this path, since there is no wire token to carry them.
- The secret is `<journal_dir>/secret` (0600), generated once per profile. The trust posture is tamper-evident, not tamper-proof: the secret is readable by the same OS user.
- **Library (`Door`)** has no adapter, so `needs-human` refuses `no_decision_adapter`. It is never auto-allowed.

## Journal — `<journal_dir>/journal.sqlite`

SQLite in WAL mode, one file per host and profile, written only by the door. Several processes on a host serialise through SQLite locking.

- `ops`: one row per write that passed the gate. The intent is written before the Slack call (`pending`), the outcome after (`ok` / `error` / `unknown`). When Slack's response names a different conversation than the one addressed — a bot post to the person's **user id** lands in their DM — the `ok` outcome rewrites the row's `target.channel` to that conversation and keeps the addressed id as `target.addressed_to`, so an edit or a lookup by `ts` + channel finds the post (0.7.0). An `unknown` outcome never gets that rewrite: reconciliation of such a post reads the addressed id, which Slack may not accept as a conversation — then the person resolves it by hand. It holds the full payload, provenance with the token stripped, policy version/digest and the decision record. Refusals leave **no** `ops` row; they appear in `calls` as `status=refused` with the reason code. `slack_ts` is the write's Slack handle: message `ts`, `scheduled_message_id`, ephemeral `message_ts`, or the file id. A `chat.update` row carries `provenance.prior` (the message before the edit: `ts`, `user`, `bot_id`, `text`, or `error`). A composite upload (`attribution: block`) has a child row `<op>.c1` with `parent_op_id` and `provenance.composite: "comment"` — the same decision and `comm_id`, its own outcome.
- `calls`: every Slack call and every refusal, with method, token kind, caller, status, error code and latency. Never content.
- `decisions`: token hashes only. `state` holds the first sighting of each `comm_id`.
- A `pending` row older than 10 minutes becomes `unknown` on the next open. `unknown` means "may or may not have been posted": resolve it by checking Slack, never by retrying — `slack_api("_reconcile")` / `python3 -m atlas_slack.gate --reconcile` does that (0.6.0): bot sends are found by `{op_id}` in message metadata, person sends by text and author in the channel window, edits by the text at `ts`, scheduled sends in `chat.scheduledMessages.list`, uploads in `files.list`; an ephemeral is unobservable (`error reconciled_unobservable`). A `comm_id` with an `unknown` row refuses `reconcile_required` until then, `resend` or not.
- Export: `Journal.export_envelope(since)` yields JSONL lines in the outbound envelope shape: `kind: outbound_message`, `payload`, `sent_by`, `comm_id`, `captured_at`, `op_id`, `outcome`, `ts`.
- **Retention is yours, with one rule.** The journal holds the verbatim text of everything sent, and the door reads it: an edit finds its communication by `ts`, a `comm_id` with an `unknown` row refuses `reconcile_required`, reconciliation settles those rows. Blank `payload_json` whenever you like and keep the row; delete rows only when they are `ok` or `error` and older than anything you might still edit or resend — never a `pending` or `unknown` row. Nothing outside the door reads the file unless you export it.

## Job manifest — `Door(profile, manifest={…})`

A script or job passes a manifest with what it may touch; the policy engine checks every call against it (`manifest.get` in `policy.py`): `cls` — the one sender class the job speaks in (a provenance with another class refuses `class_not_in_manifest`); `allowed_methods` — the Web API methods it may call (`method_not_in_manifest`); `allowed_targets` — the channel ids it may write to (`target_not_in_manifest`); `read_scope` — `{channels: [...]}` it may read (`read_scope_violation`). A manifest never widens the policy: a class the policy sets to `deny` stays denied. Today the only autonomous class a policy can enable is `C2` (`modes: {C2: auto}` on the person's bot DM); `B-a` / `C1` need a notary stamp and no notary exists yet.

### Example — a scheduled job that reports to the person

A cron job on a headless host posts a finding to the person's bot DM in the bot's voice. Its profile (`decision_adapters: []` — nothing human-decided can leave this host) names a policy whose only entry is the DM:

```yaml
# ~/.config/atlas-slack/acme.policy.yaml
version: "2026-10-31.1"
ratified_by: "Pat Example"
channels:
  D0ACMEBOTDM:                       # the person's bot DM (person_bot_dm_id in the profile)
    class: dm
    voices: [bot]
    modes: {C2: auto}
```

```python
from atlas_slack.door import Door

channel = "D0ACMEBOTDM"
door = Door("acme", manifest={"cls": "C2", "allowed_methods": ["chat.postMessage"],
                              "allowed_targets": [channel]})
door.call("_reconcile")                      # settle unknown outcomes of earlier runs first; reads only
comm_id = f"nightly-{day}-{digest}"          # one id per communication: the same report twice in a day is one
res = door.call("chat.postMessage", {"channel": channel, "text": text}, as_bot=True, provenance={
    "cls": "C2", "comm_id": comm_id,                     # comm — a repeat answers duplicate_comm_id (see below)
    "prepared_by": "job:nightly-check@acme-node",
    "target": {"channel": channel},
    "sources": ["the job's own reads"],
})
if res.verdict.decision != "allow":
    if res.verdict.reason == "duplicate_comm_id":
        # the comm already has a blocking row: `ok` (an earlier run delivered it) — or `pending` (a send
        # in flight, or a process that died minutes ago), which is NOT delivery: ask the journal
        from atlas_slack import core
        rows = core.open_journal(door.profile.journal_dir).find_by_comm_id(comm_id)
        if not any(r["outcome"] == "ok" for r in rows):
            raise SystemExit("an earlier send is still pending — retry next run")
        pass                                 # delivered — record the finding as sent
    else:
        raise SystemExit(f"refused {res.verdict.reason}: {res.verdict.message}")   # exit non-zero, retry next run
elif res.error:
    raise SystemExit(f"send failed: {res.error}")   # error or unknown; the next run's _reconcile settles an unknown
```

Only the bot token is in the job's environment (invariant i: a job never holds the person's token). The same text to any other channel is class C1 under the door's own rule and refuses `no_notary`; a `comm_id` with an `unknown` row refuses `reconcile_required` until `_reconcile` resolves it, which is why the job calls it before every send. A failed send (`error`) does not block a retry: only `ok`, `pending` and `unknown` rows do. Two more rules a job's own state has to carry, since the door knows communications and not the job's windows: record the digest (text, window, `comm_id`) **before** the send and replay exactly that record first on the next run — a run that dies after the send must not rebuild the window, or new findings fold into a communication the journal already holds as delivered; and keep the `comm_id` in that record, so a date-stamped id survives the date change.
