# Changelog

Plugin version = `pyproject.toml` = `plugins/atlas-slack/.claude-plugin/plugin.json` = `atlas_slack.__version__` (`tests/test_version.py`). The hook stamps its version into every decision; a server of another version refuses the send (`version_skew`), so a live session needs a restart after an update, not `/reload-plugins`. Output and provenance contract versions live in `docs/output-contract.md`.

## 0.7.0 — 2026-10-31 — public-release preparation

- **Identity at connect.** Before the first write under each token in a process — after the policy verdict and the decision check, before the journal intent (`core.execute_send`); transitional writes too, reads not — the door asks Slack `auth.test` once and compares the answer with the profile: another workspace, another person, a bot token in the user slot (or the reverse) refuse `identity_mismatch`; an answer that cannot be obtained refuses `identity_unverified`. Nothing is sent and no op row is written in either case. Fields the profile leaves unset are not checked. `Door.doctor()` delegates to the same check (`src/atlas_slack/identity.py`). `ATLAS_SLACK_IDENTITY_CHECK=0` switches it off — the level-1 test suite only.
- **`python3 -m atlas_slack init [--profile NAME]`** writes `~/.config/atlas-slack/<name>.yaml` from the two tokens in the environment (workspace, person, bot, the bot's DM when it exists); refuses to overwrite, refuses tokens that are not one person + one bot in one workspace. **`python3 -m atlas_slack doctor`** checks both tokens against the profile, flags `chat:write.customize` / `incoming-webhook` on the app, prints the rights table.
- **`slack-app-manifest.json`** — the Slack app manifest to create your own app from: the scopes the door's reads and six core sends use, no `chat:write.customize`, no webhook, no commands, no events, no Socket Mode. Replaces the maintainer's app manifest, which was not a template.
- **Profile-neutral texts.** The card's deny texts name no person (`правка с карточки` / `edit from the person`); the card and the prompt default to English when the profile sets no `language` (`en` was already the profile default; the mod assumed `ru`); a transitional ask carries a non-default profile language on a trailing `lang:` line so the compact card follows the profile before any core card. The card expects a ledger audit (`🎯 Cicero`) only where the gate printed a ledger line — a host without a ledger is never told it skipped one. The skill explains voices as *you* and *the bot*, not as the maintainer's names. The `/comms` ledger (Cicero) stays an optional layer: no ledger folder → no ledger line, never a refusal.
- **Repository hygiene** (practices from `nestor-plugin`): `tests/test_no_private_tokens.py` + `tests/private-token-hashes.txt` (home paths, private trees, session trailers, workspace ids and personal names as salted hashes; `--commits` for messages; `.githooks/pre-push`), GitHub Actions `checks.yml` (unit tests, commit-message scan, manifest JSON), `homepage`/`repository` in the plugin manifest, this changelog. The working plans (`docs/plans/`) moved to the maintainer's vault; `KNOWN_ISSUES.md` and `docs/decisions/` are scrubbed and scanned like everything else.
- The `not_in_channel` hint no longer names a bot; the `slack_api` tool description says «the bot», not a name. The identity refusals carry their own next step in the message (`core.IDENTITY_HINTS`): mismatch → fix and restart; unverified → the check runs again at the next call.
- Contract v1.10 (`docs/output-contract.md`): the two new refusal codes; `init` / `doctor`.

## 0.6.0 — 2026-10-31 — slice 2: the six message sends through the core

`chat.update`, `files.uploadV2`, `chat.postEphemeral`, `chat.meMessage` join `chat.postMessage` / `chat.scheduleMessage` in the core (`policy.CORE_METHODS`). An edit binds `ts`, reads the message first (prior text on the op row and on the card as «was / becomes»), and must be in the original author's voice (`author_mismatch`). An upload binds the file's bytes by sha256; `attribution: block` posts the comment as a child op. An ephemeral's viewer is a bound recipient. Bot `chat.postMessage` carries `{op_id, comm_id}` in message metadata; `_reconcile` resolves `unknown` rows by reading Slack (pagination ≤ 5 pages, incomplete scan = still unknown; an upload is proven only by files created after the intent, one per name). Writes use one HTTP attempt (no SDK retry); `internal_error` / `fatal_error` → `unknown`. A `Door` built by name re-reads its profile before every call (`freeze`). The decision row carries the plugin version. Contract v1.9.

## 0.5.4 — 2026-10-31 — `version_skew`

The hook stamps `__version__` into the wire decision; a server of another version refuses the send.

## 0.5.3 — 2026-10-31 — `chat.scheduleMessage` in the core

`post_at` bound as `scheduled_for`; codes `schedule_in_past` / `schedule_too_far` / `schedule_mismatch`; the prompt and the card show the time.

## 0.5.2 — 2026-10-31 — approval card (Claude Code mod) and the ledger line

`hooks/approval.tsx` (Claude Code 2.1.314+): `tool.check` on `slack_api` turns the stock JSON permission dialog for a core send into one `$.ui.ask` card — to · format · when / from / text / the door line (recipient · voice · binding · journal · policy · ledger) / checks declared / not checked; text typed on the card comes back as the deny reason; transitional writes get a compact card. Optional ledger line from the profile's `ledger_dir` (`src/atlas_slack/ledger.py`), informational only.

## 0.5.1 — 2026-10-31 — door contract after three days of 0.5.0

Decision window 30 min with an actionable `decision_expired` text; `comm_id` minted by the door when absent and returned in the result line; `cls` derived from `as_bot`; hook-side refusals journaled; WAL checkpoint on open. Contract v1.5.

## 0.5.0 — 2026-10-28 — the door core

`chat.postMessage` through profile → policy → provenance → one-time decision (`updatedInput` token) → write-ahead SQLite journal → Slack. Reads through the effect registry. `atlas_slack.door.Door` library face, `python3 -m atlas_slack.gate` CLI, `slack_api("_rights")`. No workspace literal in code: `bot_user_id` comes from the profile. Contract v1.4.

## 0.4.0 — 2026-10-22

`files.uploadV2` as one `slack_api` call (SDK `files_upload_v2`), `attribution: "block"`, bot auto-split of long text, `_raw: true`, Slack transcripts (`files.info`, `slack_download(vtt=True)`). Contract v1.3.

## v1.2 — 2026-09-01

Structural errors (`is_error=true` + hint catalogue), unread state in channel info, default `limit=100` on history/replies, args echo on list shapes, caller-blocks pre-validation, convenience wrappers removed (surface = `slack_api` + `slack_download`). Contract v1.2.

## v1.1 — 2026-08-21

History output renders attachments and file stubs; streams (`chat.*Stream`) bot-only. Contract v1.1.

## v1.0 — 2026-04-30

Output contract locked. Dual tokens, attribution footer, the two gate hooks inside the plugin.
