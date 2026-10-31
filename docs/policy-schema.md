---
title: atlas-slack — profile, policy and provenance schema
status: draft (v2 core slice, branch feat/v2-core)
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
| `person_user_id` | string | — | the person's user id (`U…`) |
| `bot_user_id` | string | — | the bot's user id — the 💙 attribution target; **required for person-voice sends** |
| `person_bot_dm_id` | string | — | the bot-side DM with the person (`D…`) — row 2 "report to the person" |
| `token_sources` | `{user, bot}` → env var **names** | `SLACK_USER_TOKEN`, `SLACK_BOT_TOKEN` | where the tokens are; never the tokens themselves |
| `policy_path` | path | — | the context policy yaml |
| `policy_sha256` | 64 hex | — | pin: the policy file's sha256 must match, else `policy_invalid` |
| `journal_dir` | path | `~/.local/share/atlas-slack/<ctx>/` | journal + decision secret |
| `ledger_dir` | path list (`os.pathsep`) | none (env `ATLAS_SLACK_LEDGER_DIR` overrides) | the `/comms` ledger folder(s) of this context (`comm_id:` in each file's frontmatter; newest file wins across folders); env `ATLAS_SLACK_LEDGER_DIR` overrides. Optional: without it the card has no ledger line. With it the approval prompt reads the file behind `provenance.comm_id` and shows `ledger: ✅ аудит Cicero <audit_at>` only when `audit: pass` and the text being sent equals the file's last `## Draft vN` section; or `✅ … (текст в blocks — не сверен)` for a blocks-only send; otherwise `⚠ аудит был для другого текста` / `⚠ аудит, но в ledger нет текста (## Draft)` / `☑ аудит пропущен` (`audit: skip`) / `⚠ аудит: <other value>` / `⚠ аудита нет` / `⚠ не найден` / `⚠ помечен dropped` / `⚠ папка ledger недоступна` / `⚠ не прочитан`; a second file with the same `comm_id` is flagged `(+N файл с тем же comm_id)`; plus `intention: …` — read, never judged, never a refusal (0.5.2) |
| `decision_adapters` | list | `["cc-hook"]` | adapters that may carry a human decision; empty → human-decided sends refuse |
| `freeze` | bool | `false` | emergency stop: every write refuses, reads continue |
| `decision_fallback` | bool | `false` | use the pre-registered decision row instead of the `updatedInput` token (see Decisions); the live check 2026-10-28 proved the token path on Claude Code 2.1.296 |
| `decision_permission_modes` | list | `[default, acceptEdits, plan, auto]` | Claude Code permission modes in which the hook may ask for a decision; any other mode (`bypassPermissions`, `dontAsk`, unknown) → `deny permission_mode_unsafe` |
| `language` | `en` \| `ru` | `en` | language of the approval prompt |
| `names` | map id → label | `{}` | display labels that win over resolution, e.g. `U…: владелец`, `D…: "DM владелец↔Atlas"` |
| `resolve_names` | bool | `true` | resolve ids for the prompt with `conversations.info` / `users.info` (reads), cached 24 h in `<journal_dir>/names.json`; env `ATLAS_SLACK_RESOLVE_NAMES=0` switches it off |

Rules:

- **No file and no `ATLAS_SLACK_PROFILE`** → the built-in profile, derived from env. It reads `SLACK_USER_TOKEN`, `SLACK_BOT_TOKEN`, `ATLAS_BOT_USER_ID`, and optionally `ATLAS_SLACK_WORKSPACE_ID`, `ATLAS_SLACK_PERSON_USER_ID` and `ATLAS_SLACK_PERSON_BOT_DM_ID`. Verdicts say `profile_source: builtin`.
- **`ATLAS_SLACK_PROFILE` names a file that does not exist** → writes refuse `profile_missing`. Falling back to whatever tokens sit in the shell could attribute a send to another workspace's bot.
- Unknown keys, wrong types or a token-shaped value (`xox…-`) → `profile_invalid`, and writes refuse.
- Overrides for tests and relocation: `ATLAS_SLACK_CONFIG_DIR` (profile dir) and `ATLAS_SLACK_DATA_DIR` (journal root).
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
policy_sha256: 3b6c…(64 hex)…   # sha256sum of the policy file; update it when you ratify a new version
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
| `C2` | `auto`, `deny` | report without a decision (only to the person's bot DM), or off |

A channel not listed gets the strictest row: `A: human, B-h: human, B-a: deny, C1: deny, C2: deny`. A listed channel inherits the strictest mode for every class it does not name.

### Deny lists are per class, and a human decision does not override them

`recipients_deny_autonomous` applies to the autonomous speaking classes B-a and C1. A person who must never hear from the bot on its own can still receive a message the human approved in class A or B-h. `recipients_deny: {<class>: [ids]}` is a deterministic prohibition for that class. It refuses even when a valid human decision is attached (invariant iii: "human" always reads "human + policy"). The checked recipients are the channel id plus `provenance.target.users`.

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
| `checks` | object | declared by the context: `voice`, `recipient`, `policy_row`, `mark`, `disclosure`, … — shown, not judged |
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

- `ops`: one row per write that passed the gate. The intent is written before the Slack call (`pending`), the outcome after (`ok` / `error` / `unknown`). It holds the full payload, provenance with the token stripped, policy version/digest and the decision record. Refusals leave **no** `ops` row; they appear in `calls` as `status=refused` with the reason code. `slack_ts` is the write's Slack handle: message `ts`, `scheduled_message_id`, ephemeral `message_ts`, or the file id. A `chat.update` row carries `provenance.prior` (the message before the edit: `ts`, `user`, `bot_id`, `text`, or `error`). A composite upload (`attribution: block`) has a child row `<op>.c1` with `parent_op_id` and `provenance.composite: "comment"` — the same decision and `comm_id`, its own outcome.
- `calls`: every Slack call and every refusal, with method, token kind, caller, status, error code and latency. Never content.
- `decisions`: token hashes only. `state` holds the first sighting of each `comm_id`.
- A `pending` row older than 10 minutes becomes `unknown` on the next open. `unknown` means "may or may not have been posted": resolve it by checking Slack, never by retrying — `slack_api("_reconcile")` / `python3 -m atlas_slack.gate --reconcile` does that (0.6.0): bot sends are found by `{op_id}` in message metadata, person sends by text and author in the channel window, edits by the text at `ts`, scheduled sends in `chat.scheduledMessages.list`, uploads in `files.list`; an ephemeral is unobservable (`error reconciled_unobservable`). A `comm_id` with an `unknown` row refuses `reconcile_required` until then, `resend` or not.
- Export: `Journal.export_envelope(since)` yields JSONL lines in the outbound envelope shape: `kind: outbound_message`, `payload`, `sent_by`, `comm_id`, `captured_at`, `op_id`, `outcome`, `ts`.
- **Retention is yours.** The journal holds the verbatim text of everything sent. Delete rows or blank `payload_json` per your own policy; keep the row as the record that the send happened. Nothing reads the file unless you export it.
