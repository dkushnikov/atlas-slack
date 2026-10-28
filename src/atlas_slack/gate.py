"""Gate face — the core's verdict for decision adapters (A6).

    python3 -m atlas_slack.gate            stdin: hook JSON or {method, params, as_bot, provenance}
                                           stdout: the verdict JSON (+ `decision_check` when a token is attached)
    python3 -m atlas_slack.gate --hook     stdout: Claude Code PreToolUse hookSpecificOutput
    python3 -m atlas_slack.gate --rights   stdout: the rights table (same as slack_api("_rights"))

The Claude Code hook (`plugins/atlas-slack/hooks/mcp-slack-gate.py`) is a thin
wrapper around `run_cc_hook()`. Mapping: allow → allow; refuse → deny (reason
shown to Claude); needs-human → ask with the full provenance as the reason —
and, for the core path, a one-time decision token in `updatedInput`.
"""

from __future__ import annotations

import json
import sys

from atlas_slack import decision as decision_mod
from atlas_slack.core import evaluate_with_journal
from atlas_slack.journal import JournalUnavailable, open_journal
from atlas_slack.policy import ALLOW, NEEDS_HUMAN, REFUSE, Verdict, load_policy, rights_table
from atlas_slack.profile import load_profile

TEXT_PREVIEW = 700


def _parts(tool_input: dict):
    method = tool_input.get("method", "") or ""
    params = tool_input.get("params")
    if not isinstance(params, dict):
        params = {}
    as_bot = bool(tool_input.get("as_bot", False))
    prov = tool_input.get("provenance")
    return method, params, as_bot, prov


def rights(profile=None) -> str:
    profile = profile or load_profile()
    try:
        j = open_journal(profile.journal_dir)
        ok, path = True, str(j.path)
    except JournalUnavailable as e:
        ok, path = False, f"unavailable: {e}"
    return rights_table(profile, load_policy(profile), journal_ok=ok, journal_path=path)


def verdict_for(tool_input: dict, profile=None, record_sighting: bool = True) -> tuple[Verdict, object]:
    profile = profile or load_profile()
    method, params, as_bot, prov = _parts(tool_input)
    return evaluate_with_journal(method, params, as_bot, prov, profile, face="mcp",
                                 record_sighting=record_sighting)


TEXT_INLINE = 120     # quoted inside the sentence when this short and single-line
TEXT_BLOCK = 400      # shown in full below the sentence up to this length

_T = {
    "en": {"bot_voice": "Bot voice, on your request.", "person_voice": "In your voice, marked 💙 {bot}.",
           "send": "Send?", "resend": "Resend.", "thread": " (in thread)", "broadcast": " + also to the channel",
           "below": "message below", "chars": "chars", "you": "You", "bot": "the bot",
           "no_policy": "⚠ no context policy — the built-in strict policy applies"},
    "ru": {"bot_voice": "Голос бота, по твоему запросу.", "person_voice": "Твоим голосом, с пометкой 💙 {bot}.",
           "send": "Отправить?", "resend": "Повторная отправка.", "thread": " (в тред)",
           "broadcast": " + в канал", "below": "сообщение ниже", "chars": "симв.", "you": "Ты", "bot": "бот",
           "no_policy": "⚠ политики контекста нет — действует встроенная строгая"},
}


def describe_send(v: Verdict, params: dict, as_bot: bool, profile=None, names=None, op_short: str = "") -> str:
    """The approval prompt: one human sentence, the text, one technical line (A4 "the adapter shows these fields").

    Recipient by name (names.py), language from the profile. Machine detail — row, mode,
    profile, origin, empty checks — lives in the journal, not in the prompt.
    """
    from atlas_slack.names import Names
    profile = profile or load_profile()
    t = _T.get(getattr(profile, "language", "en"), _T["en"])
    p = v.provenance or {}
    names = names or Names(profile)
    bot = names.user(profile.bot_user_id, prefer_bot=True) or t["bot"]
    sender = bot if as_bot else (names.user(profile.person_user_id) or t["you"])
    target, _ = names.channel(v.channel, as_bot, (p.get("target") or {}).get("users"))
    if params.get("thread_ts"):
        target += t["thread"]
    if str(params.get("reply_broadcast", "")).lower() in ("true", "1"):
        target += t["broadcast"]
    text = params.get("text") if isinstance(params.get("text"), str) else ""
    inline = text and len(text) <= TEXT_INLINE and "\n" not in text
    what = f"“{text}”" if inline else f"{t['below']} ({len(text)} {t['chars']})" if text else "blocks"
    voice = t["bot_voice"] if as_bot else t["person_voice"].format(bot=bot)
    parts = [f"{sender} → {target}: {what}.", voice]
    if p.get("resend"):
        parts.append(t["resend"])
    parts.append(t["send"])
    lines = [" ".join(parts)]
    if text and not inline:
        lines.append(text if len(text) <= TEXT_BLOCK else text[:TEXT_BLOCK] + f"… ({len(text)} {t['chars']})")
    checks = p.get("checks") or {}
    if checks:
        lines.append("checks: " + ", ".join(f"{k}={checks[k]}" for k in sorted(checks)))
    if v.policy_state == "builtin":
        lines.append(t["no_policy"])
    names.save()
    tech = [f"class {v.cls}", f"policy {v.policy_version}"]
    if p.get("comm_id"):
        tech.append(f"comm {p['comm_id']}")
    if op_short:
        tech.append(f"op {op_short}")
    if isinstance(params.get("blocks"), list):
        tech.append(f"blocks {len(params['blocks'])}")
    extra = sorted(k for k in params if k not in ("channel", "text", "blocks", "thread_ts", "_raw"))
    if extra:
        tech.append("params " + ",".join(extra))
    lines.append(" · ".join(tech))
    return "\n".join(lines)


def _emit(decision: str, reason: str = "", updated_input: dict | None = None) -> dict:
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision}}
    if reason:
        out["hookSpecificOutput"]["permissionDecisionReason"] = reason
    if updated_input is not None:
        out["hookSpecificOutput"]["updatedInput"] = updated_input
    return out


def hook_output(data: dict, profile=None) -> dict:
    """Claude Code PreToolUse decision for one hook payload."""
    tool_name = data.get("tool_name", "") or ""
    tool_input = data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    short = tool_name.split("__")[-1]

    if short == "slack_download":
        # Slack-read-only (files.info + authenticated GET); local effect = the file in out_dir.
        return _emit("allow")
    if short != "slack_api":
        return _emit("ask", f"Slack write: {short}")

    profile = profile or load_profile()
    v, journal = verdict_for(tool_input, profile)
    method, params, as_bot, prov_in = _parts(tool_input)

    if v.decision == ALLOW:
        return _emit("allow", f"atlas-slack: {v.message}")
    if v.decision == REFUSE:
        tag = " (transitional)" if v.transitional else ""
        return _emit("deny", f"atlas-slack refused{tag} — {v.reason}: {v.message}")
    if v.route != "core":
        return _emit("ask", v.message)

    # needs-human on the core path → one-time decision bound to the op hash — but only
    # where Claude Code's `ask` reaches a human (permission_mode in the hook input).
    mode = data.get("permission_mode")
    if mode is not None and mode not in profile.decision_permission_modes:
        return _emit("deny", f"atlas-slack refused — permission_mode_unsafe: permission mode {mode!r} cannot "
                             "guarantee that a human sees this prompt; switch to default (or auto) mode, or list "
                             "the mode in the profile's decision_permission_modes")
    sid = str(data.get("session_id") or "")[:36]
    try:
        secret = decision_mod.load_secret(profile.journal_dir)
        token = decision_mod.issue_token(secret, v.op_hash)
        journal.store_decision(token, v.op_hash,
                               decision_mod.ADAPTER_CC_HOOK + (f" session={sid}" if sid else ""))
    except (OSError, JournalUnavailable, AttributeError) as e:
        return _emit("deny", f"atlas-slack refused — journal_unavailable: cannot record the decision ({e})")
    caller_prov = prov_in if isinstance(prov_in, dict) else {}
    op_short = (caller_prov.get("op_id") or decision_mod.derive_op_id(v.op_hash, token))[:8]
    reason = describe_send(v, params, as_bot, profile, op_short=op_short)
    if profile.decision_fallback:
        return _emit("ask", reason)
    # Compact wire form: only what the caller supplied + the decision. Defaults (origin,
    # voice, class for a provenance-less send) and op_id are derived again by the server.
    wire = {k: val for k, val in caller_prov.items() if k != "decision"}
    wire["decision"] = {"t": token, "p": profile.name}
    updated = {**tool_input, "method": method, "params": params, "as_bot": as_bot, "provenance": wire}
    return _emit("ask", reason, updated)


def run_cc_hook() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception as e:
        print(json.dumps(_emit("ask", f"slack-gate parse error: {e}")))
        return
    try:
        out = hook_output(data if isinstance(data, dict) else {})
    except Exception as e:  # never crash the hook: a crash would let the call through the normal flow
        out = _emit("ask", f"atlas-slack gate error ({type(e).__name__}: {e}) — confirm manually")
    print(json.dumps(out, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--rights" in argv:
        print(rights())
        return 0
    if "--hook" in argv:
        run_cc_hook()
        return 0
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        print(json.dumps({"decision": REFUSE, "reason": "parse_error", "message": str(e)}))
        return 2
    tool_input = data.get("tool_input", data) if isinstance(data, dict) else {}
    profile = load_profile()
    v, journal = verdict_for(tool_input, profile, record_sighting=False)   # dry run: no state written
    out = v.to_dict()
    prov = tool_input.get("provenance") if isinstance(tool_input, dict) else None
    if v.decision == NEEDS_HUMAN and v.route == "core":
        dc = decision_mod.check((prov or {}).get("decision") if isinstance(prov, dict) else None, v.op_hash,
                                journal, profile.journal_dir, profile.decision_fallback)
        out["decision_check"] = {"ok": dc.ok, "code": dc.code, "detail": dc.detail}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
