"""Gate face — the core's verdict for decision adapters (A6).

    python3 -m atlas_slack.gate            stdin: hook JSON or {method, params, as_bot, provenance}
                                           stdout: the verdict JSON (+ `decision_check` when a token is attached)
    python3 -m atlas_slack.gate --hook     stdout: Claude Code PreToolUse hookSpecificOutput
    python3 -m atlas_slack.gate --rights   stdout: the rights table (same as slack_api("_rights"))
    python3 -m atlas_slack.gate --reconcile  resolve `unknown` outcomes against Slack (same as slack_api("_reconcile"))

The Claude Code hook (`plugins/atlas-slack/hooks/mcp-slack-gate.py`) is a thin
wrapper around `run_cc_hook()`. Mapping: allow → allow; refuse → deny (reason
shown to Claude); needs-human → ask with the full provenance as the reason —
and, for the core path, a one-time decision token in `updatedInput`.
"""

from __future__ import annotations

import json
import sys

from atlas_slack import decision as decision_mod
from atlas_slack.core import evaluate_with_journal, record_call
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
           "below": "message below", "chars": "chars", "you": "You", "bot": "the bot", "scheduled": " · scheduled for {when}",
           "edit": " · edit of the message from {when}", "ephemeral": " · visible only to {user}", "me": " · /me",
           "was": "was: ", "was_unread": "was: ⚠ not read ({why})", "comment_below": "comment below",
           "no_policy": "⚠ no context policy — the built-in strict policy applies", "ledger_unread": "ledger: ⚠ not read"},
    "ru": {"bot_voice": "Голос бота, по твоему запросу.", "person_voice": "Твоим голосом, с пометкой 💙 {bot}.",
           "send": "Отправить?", "resend": "Повторная отправка.", "thread": " (в тред)",
           "broadcast": " + в канал", "below": "сообщение ниже", "chars": "симв.", "you": "Ты", "bot": "бот", "scheduled": " · отложено на {when}",
           "edit": " · правка сообщения от {when}", "ephemeral": " · видно только {user}", "me": " · /me",
           "was": "было: ", "was_unread": "было: ⚠ не прочитано ({why})", "comment_below": "комментарий ниже",
           "no_policy": "⚠ политики контекста нет — действует встроенная строгая", "ledger_unread": "ledger: ⚠ не прочитан"},
}
# Params the prompt already shows in words — not repeated in the technical line.
_SHOWN_PARAMS = ("channel", "text", "blocks", "thread_ts", "_raw", "ts", "user", "post_at", "file", "content",
                 "initial_comment", "title", "filename", "attribution", "file_uploads")


def _when(post_at) -> str:
    """Local time of a `post_at` epoch for the prompt; the raw value when it is not a number."""
    import time as _t
    try:
        return _t.strftime("%d.%m %H:%M %Z", _t.localtime(float(post_at)))      # the hook host's zone, named
    except (TypeError, ValueError, OverflowError):
        return str(post_at)


def describe_send(v: Verdict, params: dict, as_bot: bool, profile=None, names=None, op_short: str = "",
                  prior: dict | None = None) -> str:
    """The approval prompt: one human sentence, the text, one technical line (A4 "the adapter shows these fields").

    Recipient by name (names.py), language from the profile. Machine detail — row, mode,
    profile, origin, empty checks — lives in the journal, not in the prompt. `chat.update`
    adds a `was:` line (the message before the edit, one line, ≤ 400 chars); `files.uploadV2`
    says `📎 <name> (<size>)` and treats `initial_comment` as the text.
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
    if params.get("post_at") is not None:
        target += t["scheduled"].format(when=_when(params.get("post_at")))
    if v.method == "chat.update":
        target += t["edit"].format(when=_when(params.get("ts")))
    elif v.method == "chat.postEphemeral":
        target += t["ephemeral"].format(user=names.user(params.get("user"), prefer_bot=as_bot) or params.get("user"))
    elif v.method == "chat.meMessage":
        target += t["me"]
    upload = v.method == "files.uploadV2"
    text_key = "initial_comment" if upload else "text"
    text = params.get(text_key) if isinstance(params.get(text_key), str) else ""
    inline = text and len(text) <= TEXT_INLINE and "\n" not in text
    if upload:
        from atlas_slack import shaping as _shaping
        what = f"📎 {_shaping.upload_label(params)}"
        if text:
            what += f" + “{text}”" if inline else f" + {t['comment_below']} ({len(text)} {t['chars']})"
    else:
        what = f"“{text}”" if inline else f"{t['below']} ({len(text)} {t['chars']})" if text else "blocks"
    voice = t["bot_voice"] if as_bot else t["person_voice"].format(bot=bot)
    parts = [f"{sender} → {target}: {what}.", voice]
    if p.get("resend"):
        parts.append(t["resend"])
    parts.append(t["send"])
    lines = [" ".join(parts)]
    if text and not inline:
        lines.append(text if len(text) <= TEXT_BLOCK else text[:TEXT_BLOCK] + f"… ({len(text)} {t['chars']})")
    if v.method == "chat.update":
        from atlas_slack.prior import one_line
        if prior and not prior.get("error"):
            lines.append(t["was"] + one_line(prior.get("text") or "", TEXT_BLOCK))
        else:
            lines.append(t["was_unread"].format(why=(prior or {}).get("error") or "no read"))
    checks = p.get("checks") or {}
    if checks:
        lines.append("checks: " + ", ".join(f"{k}={checks[k]}" for k in sorted(checks)))
    if v.policy_state == "builtin":
        lines.append(t["no_policy"])
    # The ledger behind the comm_id: the audit pass as a fact the door read, not a claim (0.5.2).
    from atlas_slack import ledger
    try:
        # with caller blocks the recipient reads the blocks, not `text`: the ledger match is not a ✅
        compared = "" if isinstance(params.get("blocks"), list) and params["blocks"] else (text if isinstance(text, str) else None)
        lines.extend(ledger.prompt_lines(profile, p.get("comm_id"), compared))
    except Exception:            # the ledger line is a courtesy; the prompt must always be issued
        lines.append(t["ledger_unread"])
    names.save()
    tech = [f"class {v.cls}", f"policy {v.policy_version}"]
    if p.get("comm_id"):
        tech.append(f"comm {p['comm_id']}")
    if op_short:
        tech.append(f"op {op_short}")
    if isinstance(params.get("blocks"), list):
        tech.append(f"blocks {len(params['blocks'])}")
    if p.get("file_sha256"):
        tech.append("sha256 " + ",".join(h[:8] for h in p["file_sha256"]))
    extra = sorted(k for k in params if k not in _SHOWN_PARAMS)
    if extra:
        tech.append("params " + ",".join(extra))
    lines.append(" · ".join(tech))
    return "\n".join(lines)


def _journal_refusal(profile, journal, method: str, as_bot: bool, code: str) -> None:
    """Call-journal row for a hook-side deny (caller=hook, never content). Best effort."""
    if journal is None:
        return
    try:
        record_call(profile, method, "refused", as_bot=as_bot, caller="hook", error_code=code,
                    journal=journal)
    except Exception:
        pass


def _emit(decision: str, reason: str = "", updated_input: dict | None = None) -> dict:
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision}}
    if reason:
        out["hookSpecificOutput"]["permissionDecisionReason"] = reason
    if updated_input is not None:
        out["hookSpecificOutput"]["updatedInput"] = updated_input
    return out


def _prior_reader(profile):
    """The hook's read of a message before an edit — through Names' live-read switch and 3 s timeout
    (`resolve_names: false` / ATLAS_SLACK_RESOLVE_NAMES=0 → no read; the server still reads before the send)."""
    from atlas_slack.names import Names
    names = Names(profile)
    if not names.live:
        return None
    return lambda m, pr, b: names._read(m, pr, b) or {"ok": False, "error": "read failed"}


def hook_output(data: dict, profile=None, prior_reader=None) -> dict:
    """Claude Code PreToolUse decision for one hook payload. `prior_reader` overrides the live read (tests)."""
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
        if method == "_reconcile" and isinstance(params.get("resolve"), str):
            # the person's word on an unknown row — a decision, not a read: ask, naming what will be recorded.
            # Same guard as a core send: only where Claude Code's `ask` reaches a human (no token binds it —
            # the write is to the journal only, and the comm it unblocks still needs its own approval to send).
            mode = data.get("permission_mode")
            if mode is not None and mode not in profile.decision_permission_modes:
                _journal_refusal(profile, journal, method, as_bot, "permission_mode_unsafe")
                return _emit("deny", f"atlas-slack refused — permission_mode_unsafe: permission mode {mode!r} cannot "
                                     "guarantee that a human sees this question; switch to default (or auto) mode")
            ids = params.get("op_ids") if isinstance(params.get("op_ids"), list) else []
            if not ids or not all(isinstance(i, str) for i in ids) or params["resolve"] not in ("ok", "absent") or journal is None:
                return _emit("deny", "atlas-slack refused — invalid_arguments: _reconcile by hand needs op_ids (a non-empty "
                                     "list of op id strings) and resolve: \"ok\" | \"absent\"" + ("" if journal else " (journal unavailable)"))
            # a one-time decision bound to {these ops, this resolution, this profile}, like a send: the server
            # consumes it, so a call that never met the hook is refused `no_decision` (verifier, 2026-10-31)
            from atlas_slack import __version__ as _ver
            from atlas_slack import reconcile as _rec
            rhash = _rec.manual_hash(ids, params["resolve"], profile.name)
            try:
                secret = decision_mod.load_secret(profile.journal_dir)
                token = decision_mod.issue_token(secret, rhash)
                journal.store_decision(token, rhash, decision_mod.ADAPTER_CC_HOOK + f" v={_ver} manual-resolve"
                                       + (f" session={str(data.get('session_id') or '')[:36]}" if data.get("session_id") else ""))
            except (OSError, JournalUnavailable) as e:
                return _emit("deny", f"atlas-slack refused — journal_unavailable: cannot record the decision ({e})")
            rows = "\n".join(_rec.describe_rows(journal, ids))
            verdict_txt = "OK — it is in Slack" if params["resolve"] == "ok" else "ABSENT — it is not in Slack"
            updated = {**tool_input, "params": {**params, "decision": {"t": token, "p": profile.name, "v": _ver}}}
            reason = (f"atlas-slack: mark as {verdict_txt} by your word (you looked). Nothing is sent; the journal "
                      f"records your resolution and unblocks the comm.\n{rows}")
            return _emit("ask", reason) if profile.decision_fallback else _emit("ask", reason, updated)
        return _emit("allow", f"atlas-slack: {v.message}")
    if v.decision == REFUSE:
        # A refusal here never reaches the server, so the hook is the only place that can
        # journal it (3 days of 0.5.0: eight provenance_invalid refusals, none in the journal).
        _journal_refusal(profile, journal, method, as_bot, v.reason)
        tag = " (transitional)" if v.transitional else ""
        return _emit("deny", f"atlas-slack refused{tag} — {v.reason}: {v.message}")
    if v.route != "core":
        # the transitional reason carries no names and no language; a non-default profile language rides
        # on a trailing `lang:` line so the card (hooks/approval.tsx) can follow the profile before any core card
        lang = getattr(profile, "language", "en")
        return _emit("ask", v.message + (f"\nlang: {lang}" if lang != "en" else ""))

    # needs-human on the core path → one-time decision bound to the op hash — but only
    # where Claude Code's `ask` reaches a human (permission_mode in the hook input).
    mode = data.get("permission_mode")
    if mode is not None and mode not in profile.decision_permission_modes:
        _journal_refusal(profile, journal, method, as_bot, "permission_mode_unsafe")
        return _emit("deny", f"atlas-slack refused — permission_mode_unsafe: permission mode {mode!r} cannot "
                             "guarantee that a human sees this prompt; switch to default (or auto) mode, or list "
                             "the mode in the profile's decision_permission_modes")
    prior = None
    if method == "chat.update":
        # The message before the edit — for the prompt («было → станет») and the author check, before any
        # token is issued (a denied edit leaves no decision row). Unreadable → the prompt says so; the
        # server reads again before the send and Slack enforces the author in the last resort.
        from atlas_slack import prior as prior_mod
        reader = prior_reader or _prior_reader(profile)
        prior = (prior_mod.fetch_prior(reader, params.get("channel"), params.get("ts"), as_bot) if reader
                 else {"error": "live reads off"})
        why = prior_mod.author_problem(prior, as_bot, profile)
        if why:
            _journal_refusal(profile, journal, method, as_bot, "author_mismatch")
            return _emit("deny", f"atlas-slack refused — author_mismatch: {why}")
    sid = str(data.get("session_id") or "")[:36]
    try:
        secret = decision_mod.load_secret(profile.journal_dir)
        token = decision_mod.issue_token(secret, v.op_hash)
        from atlas_slack import __version__ as _ver
        # the row carries the hook's version too: the fallback path has no wire token to carry it
        journal.store_decision(token, v.op_hash,
                               decision_mod.ADAPTER_CC_HOOK + f" v={_ver}" + (f" session={sid}" if sid else ""))
    except (OSError, JournalUnavailable, AttributeError) as e:
        _journal_refusal(profile, journal, method, as_bot, "journal_unavailable")
        return _emit("deny", f"atlas-slack refused — journal_unavailable: cannot record the decision ({e})")
    caller_prov = prov_in if isinstance(prov_in, dict) else {}
    op_short = (caller_prov.get("op_id") or decision_mod.derive_op_id(v.op_hash, token))[:8]
    reason = describe_send(v, params, as_bot, profile, op_short=op_short, prior=prior)
    if profile.decision_fallback:
        return _emit("ask", reason)
    # Compact wire form: only what the caller supplied + the decision. Defaults (origin,
    # voice, class for a provenance-less send) and op_id are derived again by the server.
    wire = {k: val for k, val in caller_prov.items() if k != "decision"}
    from atlas_slack import __version__
    wire["decision"] = {"t": token, "p": profile.name, "v": __version__}   # the hook's code version
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
    if "--reconcile" in argv:
        # Resolve `unknown` outcomes against Slack (reads only; writes the journal). Same as slack_api("_reconcile").
        from atlas_slack.door import Door
        res = Door().call("_reconcile")
        print(res.data["report"] if res.ok else f"Error: {res.verdict.reason} — {res.verdict.message}")
        return 0 if res.ok else 2
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
