"""The door's send pipeline — one implementation for every face (A1, A6).

    policy → provenance → decision → journal.reserve (write-ahead) → transport → journal.outcome

`execute_send` is called by the MCP server (`gateway.slack_api`) and by the
library (`door.Door`). Faces differ only in what `needs-human` means:
MCP → a decision from the Claude Code hook must be attached; library → refuse.

Slice 2 (2026-10-31): the pipeline carries every core method (`policy.CORE_METHODS`).
`chat.update` reads the message first (prior text journalled, author = voice);
`files.uploadV2` in block mode is a composite — the upload, then the comment as a
child op (`<op>.c1`, `parent_op_id`) under the same decision; bot-voice sends carry
`{op_id, comm_id}` in message metadata so an `unknown` can be reconciled (reconcile.py).

Outcome semantics (invariant viii):
- Slack answered `ok=false` / `SlackApiError` with a code → `error` (nothing posted).
- Any other exception from the transport (timeout, connection reset) → `unknown`:
  the post may or may not exist; resolved by reconciliation, never by a retry.
- The process dies between the call and the outcome → the row stays `pending`
  and becomes `unknown` on the next journal open (`stale_after`).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable

from slack_sdk.errors import SlackApiError

from atlas_slack import decision as decision_mod
from atlas_slack import prior as prior_mod
from atlas_slack import identity, shaping
from atlas_slack.journal import (
    DecisionRejected, DuplicateComm, DuplicateOp, JournalUnavailable, OpIntent, open_journal, token_hash,
)
from atlas_slack.policy import NEEDS_HUMAN, REFUSE, Verdict, evaluate, load_policy
from atlas_slack.provenance import Provenance, ensure_op_id, mint_comm_id

Transport = Callable[[str, dict, bool], dict]

# Slack documents these as "the request may have partially completed" — an outcome the door cannot
# call `error` (nothing posted): it is `unknown`, resolved by reconciliation (Codex review 2026-10-31).
AMBIGUOUS_SLACK_ERRORS = frozenset({"internal_error", "fatal_error"})


IDENTITY_HINTS = {
    "identity_mismatch": " — nothing was sent; fix the profile or the token, then restart the session "
                         "(`python3 -m atlas_slack doctor` shows both tokens against the profile)",
    "identity_unverified": " — nothing was sent; the check runs again at the next call — call once more; "
                           "if it persists, `python3 -m atlas_slack doctor`",
}


class Refused(Exception):
    """The door refused before any Slack call. `.verdict` carries the full verdict."""

    def __init__(self, verdict: Verdict):
        super().__init__(f"{verdict.reason}: {verdict.message}")
        self.verdict = verdict
        self.code = verdict.reason
        self.message = verdict.message


class SendFailed(Exception):
    """The Slack call was made (intent journalled) and did not succeed."""

    def __init__(self, code: str, detail: str, op_id: str, outcome: str, verdict: Verdict | None = None):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.op_id = op_id
        self.outcome = outcome   # error | unknown
        self.verdict = verdict


@dataclass
class SendResult:
    verdict: Verdict
    data: dict
    op_id: str
    final_params: dict
    journal_error: str | None = None   # set when Slack accepted the send but the outcome row failed
    comm_id: str | None = None
    comm_minted: bool = False          # the door minted comm_id because the caller passed none
    prior: dict | None = None          # chat.update: the message as it was before the edit (or {error})
    child_op_id: str | None = None     # files.uploadV2 in block mode: the comment post's op (composite, A4)
    child_data: dict | None = None
    child_outcome: str | None = None   # ok · error (Slack refused — nothing posted) · unknown (reconcile, never repost)
                                       # · journal (posted, outcome row failed) · skipped (not attempted — nothing posted)
    child_error: str | None = None     # the comment failed after the upload succeeded: never re-upload


def outcome_id(data: dict) -> str | None:
    """The Slack handle of a successful write: every file id of an upload (`F1,F2` — reconciliation must know
    all of them, verifier 2026-10-31) · message ts · scheduled_message_id · ephemeral message_ts."""
    if isinstance(data.get("files"), list) and data["files"]:
        ids = [str(f["id"]) for f in data["files"] if isinstance(f, dict) and f.get("id")]
        if ids:
            return ",".join(ids)
    if isinstance(data.get("file"), dict) and data["file"].get("id"):
        return str(data["file"]["id"])
    if data.get("ts"):
        return data["ts"]
    if data.get("scheduled_message_id"):
        return data["scheduled_message_id"]
    if data.get("message_ts"):
        return data["message_ts"]
    return None


def _journal(profile):
    try:
        return open_journal(profile.journal_dir), None
    except JournalUnavailable as e:
        return None, str(e)


def record_call(profile, method: str, status: str, *, as_bot: bool, caller: str | None,
                error_code: str | None = None, latency_ms: int | None = None, op_id: str | None = None,
                journal=None) -> None:
    """Call-journal row, best effort for reads (never content). Write paths pass their open journal."""
    try:
        j = journal or open_journal(profile.journal_dir)
        j.record_call(method, status, token_kind="bot" if as_bot else "user", caller=caller,
                      error_code=error_code, latency_ms=latency_ms, op_id=op_id)
    except JournalUnavailable:
        pass


def _quiet(fn, *args, **kw) -> None:
    """Outcome bookkeeping after a failed call: a journal error must not mask the Slack error."""
    try:
        fn(*args, **kw)
    except JournalUnavailable:
        pass


def _refuse(verdict: Verdict, code: str, message: str) -> Refused:
    verdict.decision = REFUSE
    verdict.reason = code
    verdict.message = message
    if code not in verdict.reasons:
        verdict.reasons.append(code)
    return Refused(verdict)


def evaluate_with_journal(method, params, as_bot, provenance, profile, *, face="mcp", manifest=None,
                          record_sighting=True) -> tuple[Verdict, object]:
    journal, jerr = _journal(profile)
    v = evaluate(method, params, as_bot, provenance, profile, load_policy(profile), face=face,
                 manifest=manifest, journal=journal, journal_error=jerr, record_sighting=record_sighting)
    return v, journal


def execute_send(method: str, params: dict, as_bot: bool, provenance: dict | None, *, profile,
                 transport: Transport, face: str = "mcp", manifest: dict | None = None,
                 caller: str | None = None, identity_check: bool = True) -> SendResult:
    params = dict(params or {})
    verdict, journal = evaluate_with_journal(method, params, as_bot, provenance, profile, face=face,
                                             manifest=manifest)
    caller = caller or (verdict.provenance or {}).get("prepared_by") or face

    def refused(exc: Refused) -> Refused:
        if journal is not None:
            record_call(profile, method, "refused", as_bot=as_bot, caller=caller,
                        error_code=exc.code, journal=journal)
        return exc

    if verdict.decision == REFUSE:
        raise refused(Refused(verdict))

    prov = Provenance.from_dict(verdict.provenance, as_bot=as_bot)
    tok = decision_mod.token_of(prov.decision)
    if not prov.op_id and tok and verdict.op_hash:
        prov.op_id = decision_mod.derive_op_id(verdict.op_hash, tok)
    prov = ensure_op_id(prov)
    dc = None
    if verdict.decision == NEEDS_HUMAN:
        hook_profile = (prov.decision or {}).get("p") or (prov.decision or {}).get("profile")
        if hook_profile and hook_profile != profile.name:
            raise refused(_refuse(
                verdict, "profile_mismatch",
                f"the decision was issued under profile {hook_profile!r} but this server runs profile "
                f"{profile.name!r} — ATLAS_SLACK_PROFILE differs between the hook and the MCP server environment "
                "(set it in the shell or the project settings env, not only one of them)"))
        dc = decision_mod.check(prov.decision, verdict.op_hash, journal, profile.journal_dir,
                                profile.decision_fallback)
        if not dc.ok:
            raise refused(_refuse(verdict, dc.code, dc.detail))
        # The hook and this server must run the same code: after /reload-plugins the hook runs main's
        # src/ at once while a live server keeps its old module — a token the server does not
        # understand, or a method the two classify differently, must not slip through (MC session,
        # 2026-10-31: a 0.5.3 hook issued a token, a 0.5.2 server sent chat.scheduleMessage transitionally).
        from atlas_slack import __version__
        adapter = dc.adapter
        if adapter is None and dc.fallback and journal is not None:
            # fallback path: no wire token — the row that reserve() will consume names the hook's version and session
            try:
                row = journal.latest_unconsumed_decision(verdict.op_hash)
                adapter = row["adapter"] if row else None
            except JournalUnavailable:
                adapter = None
            if adapter is None:
                # no row at all: the send was never approved — say so, not «restart the session» (verifier)
                raise refused(_refuse(verdict, "no_decision",
                                      "no unconsumed decision recorded for this exact operation (decision_fallback profile)"))
        hook_v = (prov.decision or {}).get("v") if isinstance(prov.decision, dict) else None
        if hook_v is None:
            m = re.search(r"\bv=(\S+)", adapter or "")
            hook_v = m.group(1) if m else None
        if hook_v != __version__:   # a valid token from a hook of another version
            raise refused(_refuse(
                verdict, "version_skew",
                f"the hook runs atlas-slack {hook_v or '< 0.5.4'} but this MCP server runs {__version__} — restart the "
                "session (a /reload-plugins or /mcp Reconnect alone leaves them apart); nothing was sent"))
        if not prov.prepared_by and adapter and "session=" in adapter:
            prov.prepared_by = "cc-session:" + adapter.split("session=", 1)[1]
            caller = caller if caller not in ("mcp", face) else prov.prepared_by

    if identity_check:
        # Identity at connect (0.7.0): after policy and the decision checks — a frozen profile or a bad
        # token never reaches the network — and before the intent: one `auth.test` per process and token,
        # the answer compared with the profile. A mismatch is a refusal: nothing sent, no ops row.
        problems = identity.verify(profile, as_bot, transport)
        if problems:
            code = identity.code_for(problems)
            raise refused(_refuse(verdict, code, "; ".join(problems) + IDENTITY_HINTS[code]))

    problem = shaping.caller_blocks_problem(params)
    if problem:
        raise refused(_refuse(verdict, "invalid_blocks", problem))
    prior = None
    if method == "chat.update":
        # «was → becomes»: the message before the edit, journalled verbatim; the original's author
        # must be the voice behind the token (A3: edits inherit the original's voice).
        prior = prior_mod.fetch_prior(transport, params.get("channel"), params.get("ts"), as_bot)
        why = prior_mod.author_problem(prior, as_bot, profile)
        if why:
            raise refused(_refuse(verdict, "author_mismatch", why))
        if prior.get("error"):
            verdict.warnings.append(f"prior text not read ({prior['error']}) — Slack decides the author check")
    try:
        send_params = params
        if method == shaping.UPLOAD_METHOD:
            send_params = shaping.prepare_upload(params)          # resolved paths; already validated by the verdict
        final = shaping.shape_send(method, send_params, as_bot, profile.bot_user_id)
        final = shaping.with_op_metadata(method, final, as_bot, prov.op_id, prov.comm_id)   # never on chat.update
    except shaping.ShapingError as e:
        raise refused(_refuse(verdict, e.code, e.detail))
    block_mode = method == shaping.UPLOAD_METHOD and shaping.upload_block_mode(params, as_bot)

    decision_rec = None
    if dc is not None:
        decision_rec = {"adapter": (prov.decision or {}).get("adapter", decision_mod.ADAPTER_CC_HOOK),
                        "at": (prov.decision or {}).get("at"),
                        "path": "fallback" if dc.fallback else "token",
                        "token_hash": token_hash(dc.token) if dc.token else None}
    comm_minted = False
    if not prov.comm_id:
        # Outside the decision binding (the hash had no comm_id on either side), inside the
        # journal: every op row carries a comm_id, and the result line hands it to the ledger.
        def _taken(cid: str) -> bool:
            return journal.comm_first_seen(cid) is not None or journal.has_comm(cid)
        prov.comm_id = mint_comm_id(_taken)
        comm_minted = True
        _quiet(journal.note_comm_seen, prov.comm_id)
    stored_prov = prov.to_dict()
    if comm_minted:
        stored_prov["comm_id_source"] = "door"
    # The ledger line (✅/☑/⚠, intention) recomputed at send from the same inputs the hook used for the
    # card — within the decision window (≤ 30 min) this is what the card showed, but it is the state at
    # send, hence the name (MC session finding 2026-10-31; verifier). A failed recompute is recorded too.
    try:
        from atlas_slack import ledger as _ledger
        compared = "" if isinstance(params.get("blocks"), list) and params["blocks"] else (
            params.get("text") if isinstance(params.get("text"), str) else None)
        shown = _ledger.prompt_lines(profile, prov.comm_id, compared)
        if shown:
            stored_prov["ledger_at_send"] = shown
    except Exception as e:  # noqa: BLE001
        stored_prov["ledger_at_send_error"] = f"{type(e).__name__}: {e}"[:200]
    if stored_prov.get("decision"):
        stored_prov["decision"] = {k: v for k, v in stored_prov["decision"].items() if k not in ("token", "t")}
    parent_op = None
    if prior is not None:
        stored_prov["prior"] = prior                              # chat.update: what the message said before
        try:
            origin = journal.find_by_slack_ts(params.get("ts"), params.get("channel"))
            parent_op = origin["op_id"] if origin else None       # the post this edit is a version of
        except Exception:  # noqa: BLE001
            parent_op = None
    intent = OpIntent(
        op_id=prov.op_id, method=method, payload_hash=verdict.op_hash,
        payload={"params": params, "final_params": final, "as_bot": bool(as_bot)},
        comm_id=prov.comm_id, cls=prov.cls, voice=prov.voice, context=profile.name,
        prepared_by=prov.prepared_by, target={"channel": verdict.channel, **prov.target},
        provenance=stored_prov, policy_version=verdict.policy_version, policy_digest=verdict.policy_digest,
        decision=decision_rec, parent_op_id=parent_op,
    )
    try:
        journal.reserve(
            intent,
            consume_token_hash=token_hash(dc.token) if dc and dc.token else None,
            consume_op_hash=verdict.op_hash if dc and dc.fallback else None,
            decision_max_age_s=decision_mod.DECISION_MAX_AGE_S if dc else None,
            forbid_comm_duplicate=not prov.resend and method != "chat.update",   # an edit is the same comm's new version
        )
    except DuplicateOp:
        raise refused(_refuse(verdict, "duplicate_op_id", f"op_id {prov.op_id} is already reserved (invariant ix)"))
    except DuplicateComm:
        raise refused(_refuse(verdict, "duplicate_comm_id",
                              f"comm_id {prov.comm_id} already has a write; resend=true needs a fresh decision"))
    except DecisionRejected as e:
        raise refused(_refuse(verdict, e.code, e.detail))
    except JournalUnavailable as e:
        raise _refuse(verdict, "journal_unavailable", f"journal could not record the intent: {e}")

    # ---- the one network call ------------------------------------------------
    wire = final
    if method == shaping.UPLOAD_METHOD and prov.file_sha256:
        # The bytes were hashed for the verdict. Read them once now, hash *these* bytes and hand the same
        # bytes to the SDK — the journal keeps the paths, the wire carries the bytes. A swap since the
        # approval is an outcome `error` on the reserved row (nothing uploaded), never other bytes.
        try:
            wire, now_hashes = shaping.upload_with_bytes(final)
        except OSError as e:
            wire, now_hashes = final, [f"unreadable: {e}"]
        if now_hashes != prov.file_sha256:
            _quiet(journal.record_outcome, prov.op_id, "error", error_code="file_changed")
            record_call(profile, method, "error", as_bot=as_bot, caller=caller, error_code="file_changed",
                        op_id=prov.op_id, journal=journal)
            raise SendFailed("file_changed", "the file changed between the approval and the upload — nothing was uploaded; "
                                             "approve the new file", prov.op_id, "error", verdict)
    data, latency = _call(journal, transport, profile, method, wire, as_bot, prov.op_id, caller, verdict)
    # The message is in Slack now. A journal failure here must never read as a failed
    # send — a caller that retried would post it twice. The intent row stays `pending`
    # and becomes `unknown` at the next open; the result says so explicitly.
    result = SendResult(verdict=verdict, data=data, op_id=prov.op_id, final_params=final,
                        comm_id=prov.comm_id, comm_minted=comm_minted, prior=prior)
    try:
        journal.record_outcome(prov.op_id, "ok", slack_ts=outcome_id(data),
                               channel=data.get("channel") if isinstance(data.get("channel"), str) else None)
    except JournalUnavailable as e:
        result.journal_error = str(e)
        if block_mode:
            # no second write without a working journal (invariant viii); the result says the comment is missing
            result.child_op_id, result.child_outcome = f"{prov.op_id}.c1", "skipped"
            result.child_error = "not attempted — the journal failed after the upload; the comment was NOT posted"
        return result
    record_call(profile, method, "ok", as_bot=as_bot, caller=caller, latency_ms=latency, op_id=prov.op_id,
                journal=journal)
    if block_mode:
        _composite_comment(result, params, data, prov, verdict, profile, journal, transport, caller, stored_prov)
    return result


def _call(journal, transport, profile, method, final, as_bot, op_id, caller, verdict) -> tuple[dict, int]:
    """The network call of one op: outcome `error` (Slack said no) or `unknown` (ambiguous) is journalled
    and raised as SendFailed; a dict and the latency come back on success."""
    t0 = time.monotonic()
    try:
        data = transport(method, final, as_bot)
    except SlackApiError as e:
        code = ""
        try:
            code = (e.response or {}).get("error") or ""
        except Exception:
            code = ""
        latency = int((time.monotonic() - t0) * 1000)
        if code and code not in AMBIGUOUS_SLACK_ERRORS:
            _quiet(journal.record_outcome, op_id, "error", error_code=code)
            record_call(profile, method, "error", as_bot=as_bot, caller=caller, error_code=code,
                        latency_ms=latency, op_id=op_id, journal=journal)
            raise SendFailed(code, "", op_id, "error", verdict) from e
        if code:
            _quiet(journal.mark_unknown, op_id, error_code=code)
            record_call(profile, method, "unknown", as_bot=as_bot, caller=caller, error_code=code,
                        latency_ms=latency, op_id=op_id, journal=journal)
            raise SendFailed(code, f"Slack answered {code} — the write may have partially completed; outcome unknown "
                                   f"(op {op_id}); reconcile before any resend", op_id, "unknown", verdict) from e
        _quiet(journal.mark_unknown, op_id, error_code="exception")
        record_call(profile, method, "unknown", as_bot=as_bot, caller=caller, error_code="exception",
                    latency_ms=latency, op_id=op_id, journal=journal)
        raise SendFailed("exception", f"{e} — outcome unknown, not retried", op_id, "unknown", verdict) from e
    except Exception as e:
        latency = int((time.monotonic() - t0) * 1000)
        _quiet(journal.mark_unknown, op_id, error_code="exception")
        record_call(profile, method, "unknown", as_bot=as_bot, caller=caller, error_code="exception",
                    latency_ms=latency, op_id=op_id, journal=journal)
        raise SendFailed("exception", f"{e} — outcome unknown (op {op_id}); reconcile before any "
                                      "resend, never retry blindly", op_id, "unknown", verdict) from e
    latency = int((time.monotonic() - t0) * 1000)
    if not isinstance(data, dict):
        data = {"ok": True}
    if not data.get("ok", True):
        code = data.get("error") or "unknown_error"
        if code in AMBIGUOUS_SLACK_ERRORS:
            _quiet(journal.mark_unknown, op_id, error_code=code)
            record_call(profile, method, "unknown", as_bot=as_bot, caller=caller, error_code=code,
                        latency_ms=latency, op_id=op_id, journal=journal)
            raise SendFailed(code, f"Slack answered {code} — the write may have partially completed; outcome unknown "
                                   f"(op {op_id}); reconcile before any resend", op_id, "unknown", verdict)
        _quiet(journal.record_outcome, op_id, "error", error_code=code)
        record_call(profile, method, "error", as_bot=as_bot, caller=caller, error_code=code,
                    latency_ms=latency, op_id=op_id, journal=journal)
        raise SendFailed(code, "", op_id, "error", verdict)
    return data, latency


def _composite_comment(result: SendResult, params: dict, upload_data: dict, prov, verdict, profile, journal,
                       transport, caller, stored_prov: dict) -> None:
    """files.uploadV2 with `attribution: block` — the comment as a separate attributed chat.postMessage.

    A distinct child op (`<parent>.c1`, `parent_op_id`) under the same decision and comm_id (A4:
    «составные действия — различимые дочерние op_id, чтобы повтор не загрузил файл снова»). The
    child's failure never undoes the parent: the file IS in Slack; the result says what to do.
    """
    f = upload_data.get("file") if isinstance(upload_data.get("file"), dict) else None
    if f is None and isinstance(upload_data.get("files"), list) and upload_data["files"]:
        f = upload_data["files"][0] if isinstance(upload_data["files"][0], dict) else None
    f = f or {}
    title = f.get("title") or f.get("name") or params.get("title") or "file"
    if not f.get("permalink") and f.get("id"):
        # files.completeUploadExternal answers with id + title only; one read gets the link (verifier)
        try:
            info = transport("files.info", {"file": f["id"]}, False)
            got = (info or {}).get("file") if isinstance(info, dict) else None
            if isinstance(got, dict) and got.get("permalink"):
                f = {**f, "permalink": got["permalink"], "title": f.get("title") or got.get("title")}
                title = f.get("title") or title
        except Exception:  # noqa: BLE001 — the link is a courtesy; the comment still goes
            pass
    link = f"<{f['permalink']}|{title}>" if f.get("permalink") else title
    child_params = {"channel": params.get("channel"), "text": f"{params['initial_comment']}\n{link}"}
    if params.get("thread_ts"):
        child_params["thread_ts"] = params["thread_ts"]
    child_id = f"{prov.op_id}.c1"
    try:
        child_final = shaping.shape_send("chat.postMessage", child_params, False, profile.bot_user_id)
    except shaping.ShapingError as e:
        result.child_op_id, result.child_outcome, result.child_error = child_id, "error", f"{e.code}: {e.detail}"
        return
    intent = OpIntent(
        op_id=child_id, method="chat.postMessage", payload_hash=verdict.op_hash,
        payload={"params": child_params, "final_params": child_final, "as_bot": False},
        comm_id=prov.comm_id, cls=prov.cls, voice=prov.voice, context=profile.name, prepared_by=prov.prepared_by,
        target={"channel": verdict.channel, **prov.target},
        provenance={**stored_prov, "composite": "comment", "parent_op_id": prov.op_id},
        policy_version=verdict.policy_version, policy_digest=verdict.policy_digest, parent_op_id=prov.op_id,
    )
    try:
        journal.reserve(intent)                 # same decision (consumed by the parent), same comm_id
    except (DuplicateOp, DuplicateComm, DecisionRejected, JournalUnavailable) as e:
        # nothing was posted: the intent could not be reserved
        result.child_op_id, result.child_outcome, result.child_error = child_id, "skipped", f"journal: {type(e).__name__}: {e} — not attempted, not posted"
        return
    result.child_op_id = child_id
    try:
        data, latency = _call(journal, transport, profile, "chat.postMessage", child_final, False, child_id, caller,
                              verdict)
    except SendFailed as e:
        result.child_outcome = e.outcome
        result.child_error = f"{e.code} ({e.outcome})" + (f": {e.detail}" if e.detail else "")
        return
    result.child_data = data
    result.child_outcome = "ok"
    try:
        journal.record_outcome(child_id, "ok", slack_ts=outcome_id(data),
                               channel=data.get("channel") if isinstance(data.get("channel"), str) else None)
    except JournalUnavailable as e:
        result.child_outcome = "journal"
        result.child_error = f"posted (ts={data.get('ts')}) but not journaled: {e}"
        return
    record_call(profile, "chat.postMessage", "ok", as_bot=False, caller=caller, latency_ms=latency, op_id=child_id,
                journal=journal)
