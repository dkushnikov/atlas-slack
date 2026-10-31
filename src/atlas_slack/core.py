"""The door's send pipeline — one implementation for every face (A1, A6).

    policy → provenance → decision → journal.reserve (write-ahead) → transport → journal.outcome

`execute_send` is called by the MCP server (`gateway.slack_api`) and by the
library (`door.Door`). Faces differ only in what `needs-human` means:
MCP → a decision from the Claude Code hook must be attached; library → refuse.

Outcome semantics (invariant viii):
- Slack answered `ok=false` / `SlackApiError` with a code → `error` (nothing posted).
- Any other exception from the transport (timeout, connection reset) → `unknown`:
  the post may or may not exist; resolved by reconciliation, never by a retry.
- The process dies between the call and the outcome → the row stays `pending`
  and becomes `unknown` on the next journal open (`stale_after`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from slack_sdk.errors import SlackApiError

from atlas_slack import decision as decision_mod
from atlas_slack import shaping
from atlas_slack.journal import (
    DecisionRejected, DuplicateComm, DuplicateOp, JournalUnavailable, OpIntent, open_journal, token_hash,
)
from atlas_slack.policy import NEEDS_HUMAN, REFUSE, Verdict, evaluate, load_policy
from atlas_slack.provenance import Provenance, ensure_op_id, mint_comm_id

Transport = Callable[[str, dict, bool], dict]


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
                 caller: str | None = None) -> SendResult:
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
        if not prov.prepared_by and dc.adapter and "session=" in dc.adapter:
            prov.prepared_by = "cc-session:" + dc.adapter.split("session=", 1)[1]
            caller = caller if caller not in ("mcp", face) else prov.prepared_by

    problem = shaping.caller_blocks_problem(params)
    if problem:
        raise refused(_refuse(verdict, "invalid_blocks", problem))
    try:
        final = shaping.shape_send(method, params, as_bot, profile.bot_user_id)
    except shaping.ShapingError as e:
        raise refused(_refuse(verdict, e.code, e.detail))

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
    if stored_prov.get("decision"):
        stored_prov["decision"] = {k: v for k, v in stored_prov["decision"].items() if k not in ("token", "t")}
    intent = OpIntent(
        op_id=prov.op_id, method=method, payload_hash=verdict.op_hash,
        payload={"params": params, "final_params": final, "as_bot": bool(as_bot)},
        comm_id=prov.comm_id, cls=prov.cls, voice=prov.voice, context=profile.name,
        prepared_by=prov.prepared_by, target={"channel": verdict.channel, **prov.target},
        provenance=stored_prov, policy_version=verdict.policy_version, policy_digest=verdict.policy_digest,
        decision=decision_rec,
    )
    try:
        journal.reserve(
            intent,
            consume_token_hash=token_hash(dc.token) if dc and dc.token else None,
            consume_op_hash=verdict.op_hash if dc and dc.fallback else None,
            decision_max_age_s=decision_mod.DECISION_MAX_AGE_S if dc else None,
            forbid_comm_duplicate=not prov.resend,
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
        if code:
            _quiet(journal.record_outcome, prov.op_id, "error", error_code=code)
            record_call(profile, method, "error", as_bot=as_bot, caller=caller, error_code=code,
                        latency_ms=latency, op_id=prov.op_id, journal=journal)
            raise SendFailed(code, "", prov.op_id, "error", verdict) from e
        _quiet(journal.mark_unknown, prov.op_id, error_code="exception")
        record_call(profile, method, "unknown", as_bot=as_bot, caller=caller, error_code="exception",
                    latency_ms=latency, op_id=prov.op_id, journal=journal)
        raise SendFailed("exception", f"{e} — outcome unknown, not retried", prov.op_id, "unknown", verdict) from e
    except Exception as e:
        latency = int((time.monotonic() - t0) * 1000)
        _quiet(journal.mark_unknown, prov.op_id, error_code="exception")
        record_call(profile, method, "unknown", as_bot=as_bot, caller=caller, error_code="exception",
                    latency_ms=latency, op_id=prov.op_id, journal=journal)
        raise SendFailed("exception", f"{e} — outcome unknown (op {prov.op_id}); reconcile before any "
                                      "resend, never retry blindly", prov.op_id, "unknown", verdict) from e
    latency = int((time.monotonic() - t0) * 1000)
    if not isinstance(data, dict):
        data = {"ok": True}
    if not data.get("ok", True):
        code = data.get("error") or "unknown_error"
        _quiet(journal.record_outcome, prov.op_id, "error", error_code=code)
        record_call(profile, method, "error", as_bot=as_bot, caller=caller, error_code=code,
                    latency_ms=latency, op_id=prov.op_id, journal=journal)
        raise SendFailed(code, "", prov.op_id, "error", verdict)
    # The message is in Slack now. A journal failure here must never read as a failed
    # send — a caller that retried would post it twice. The intent row stays `pending`
    # and becomes `unknown` at the next open; the result says so explicitly.
    try:
        journal.record_outcome(prov.op_id, "ok", slack_ts=data.get("ts"))
    except JournalUnavailable as e:
        return SendResult(verdict=verdict, data=data, op_id=prov.op_id, final_params=final,
                          journal_error=str(e), comm_id=prov.comm_id, comm_minted=comm_minted)
    record_call(profile, method, "ok", as_bot=as_bot, caller=caller, latency_ms=latency, op_id=prov.op_id,
                journal=journal)
    return SendResult(verdict=verdict, data=data, op_id=prov.op_id, final_params=final,
                      comm_id=prov.comm_id, comm_minted=comm_minted)
