---
title: Agent Sessions API + official Slack MCP — coexistence decision
status: decided (Atlas recommendation, the owner review on merge)
date: 2026-08-21
issue: "#2"
---

# Agent Sessions API + official Slack MCP — decision note

Two platform shifts evaluated (issue #2). Both get an explicit call here so we stop
re-litigating them every changelog.

## Decision 1 — Agent Sessions API: park until the Socket Mode phase

**No-move now.** Atlas's Slack presence today is *outbound-only*: every call originates
in a Claude Code session and goes out through this MCP gateway. Agent Sessions
(`agents.sessions.*`, sidebar sessions, native stop buttons, streaming UX, June 30 2026
agent messaging experience) upgrade the *inbound*, Slack-native agent surface — their
value materializes only when Atlas answers people inside Slack. That is exactly the
"Socket Mode listener" phase already scoped as separate work (CLAUDE.md → What's Next),
and this decision folds into it.

What we did adopt now (shipped with issue #1, same day):

- Gate classification: `agents.sessions.setStatus` / `rename` → auto-allow (session
  chrome only); any other `agents.sessions.*` → ask by default.
- Streams `chat.startStream/appendStream/stopStream`: **bot-only** (user-token calls
  refused pre-flight); gate asks on start, allows append/stop as continuations.

So the new surface is safely callable today — without migrating our presence model.

**Manifest migration (`assistant_view` → `agent_view`).** `manifest.json` still carries
`features.assistant_view`. Platform state: new apps can only use the agent messaging
experience; existing `assistant_view` apps "continue temporarily", deprecation announced
(hard date circulating: Feb 2027). Plan: flip the manifest to `agent_view` **when the
Socket Mode phase starts, and in any case before 2027** — it's a manual apply in the
Slack app config (api.slack.com), not just this repo's mirror file. Until then, leaving
`assistant_view` in place changes nothing for the outbound gateway.

**Revisit triggers:** Slack hard-deprecates `assistant_view` for existing apps earlier;
or the Socket Mode phase gets scheduled; or the owner wants Atlas answering in Slack sooner.

## Decision 2 — Official Slack MCP server: coexist, no migration

**No-move, deliberately.** The official Slack MCP server tools (Apr–Jun 2026: add
reactions, create channel, list members, list emoji, read files) are a thin generic
subset. Everything that makes atlas-slack worth maintaining is exactly what an official
server will not ship:

| atlas-slack differentiator | Official Slack MCP |
|---|---|
| Dual-token identity routing (as-person / as-Atlas) | Single app identity |
| `:blue_heart: @Atlas` attribution middleware on user-token sends | None |
| PreToolUse read/write gate with conscious per-method classification | None |
| Locked LLM-facing output contract (docs/output-contract.md) | Raw-ish tool output |
| `slack_download` to local disk | Read-only file content |

The **Slackbot MCP client** (`settings.is_mcp_enabled`) solves the inverse problem —
Slack calling *external* MCP servers from Slackbot. Not relevant to the outbound
gateway; noted as a possible future door for exposing Atlas capabilities *into* Slack
at the Socket Mode phase. `is_mcp_enabled` stays unset.

**Revisit triggers:** official MCP gains attributed user-token posting or per-method
permissioning; or atlas-slack maintenance cost stops paying for the differentiators.

## Probe findings (2026-08-21, Acme workspace)

- `blocks.validate` — **live**, validates payloads. Adopted: gate-classified as read
  (auto-allow); `invalid_blocks` error hint added; attribution auto-split makes routine
  pre-validation unnecessary (contract v1.1 §9).
- New Block Kit blocks — a `table` block with `raw_text` cells **passes**
  `blocks.validate` on this workspace → schema-accepted. Rendering in regular channels
  (outside agent surfaces) unverified — validation ≠ rendering. Next probe when a digest
  use case appears: post one table block to a test DM. Native-table digests: parked
  until rendering confirmed.

## Related

- Issue #1 (agent-grade posting) — shipped 2026-08-21, same batch.
- Slack changelog recap Apr–Jun 2026: https://slack.dev/slack-developer-changelog-recap-april-june-2026/
- CLAUDE.md → What's Next → Socket Mode listener (the phase this folds into).
