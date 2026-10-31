"""Generic Slack API gateway — one tool for all 200+ methods."""

import json
import time
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from slack_sdk.errors import SlackApiError

from atlas_slack import core, shaping
from atlas_slack import identity
from atlas_slack.client import get_client
from atlas_slack.policy import REFUSE, classify, evaluate, load_policy
from atlas_slack.profile import load_profile
from atlas_slack.formatters import format_response, format_error, ERROR_HINTS

mcp = FastMCP(
    "atlas-slack",
    instructions=(
        "Full Slack Web API access. Use slack_api to call any Slack method "
        "(reactions.add, conversations.open, reminders.add, users.profile.set, "
        "pins.add, chat.update, chat.delete, dnd.setSnooze, etc — all 200+ methods). "
        "Use slack_download to save a Slack file to local disk (the JSON API never serves "
        "file bytes — never curl url_private). File uploads: slack_api('files.uploadV2', "
        "{'file': '/abs/path' | 'content': '...', 'channel': 'C…' (string), 'title', "
        "'initial_comment', 'thread_ts'}) — the gateway runs the 3-step "
        "getUploadURLExternal → PUT → completeUploadExternal flow itself; never PUT bytes by hand. "
        "Pass as_bot=True for posts/reactions/writes to appear as the bot; "
        "as_bot=False (default) posts as the person with :blue_heart: @bot attribution. "
        "chat.postMessage, chat.scheduleMessage, chat.update, chat.postEphemeral, chat.meMessage and "
        "files.uploadV2 run through the v2 core: the approval prompt shows the provenance, "
        "the human's decision is bound to that exact operation (change anything after approval → "
        "decision_mismatch; repeat → decision_already_used) and the write is journalled before it "
        "leaves. chat.update reads the message first (its prior text is journalled; the original's author "
        "must match the voice). Optional `provenance` = {cls: 'A' (person voice) | 'B-h' (bot voice), comm_id, "
        "target, checks, sources, ttl_s, resend}. username/icon_emoji/icon_url are refused. "
        "slack_api('_rights') shows what this host may do now (profile × policy × class); "
        "slack_api('_reconcile') resolves journal rows with an unknown outcome against Slack (reads only) — "
        "required before any resend of such a comm_id. "
        "Attribution is auto-applied to user-token sends on chat.postMessage, chat.update, "
        "chat.scheduleMessage, chat.postEphemeral (Block Kit context block) and "
        "files.uploadV2 / files.completeUploadExternal initial_comment (text suffix; "
        "uploadV2 with attribution='block' posts the comment as a separate attributed "
        "message — a child op under the same approval); long text is "
        "auto-split into ≤3000-char sections (user token: always; bot token: "
        "chat.postMessage/update/scheduleMessage when text > 3000 chars and no blocks). "
        "Pass _raw=true inside params to get the response as JSON instead of the shape (delivery checks "
        "and debugging only — use limit=1). Streaming methods (chat.startStream/"
        "appendStream/stopStream) are bot-only — always pass as_bot=True for them. "
        "Slack failures return is_error=true with 'Error: <code> — <hint>' text. "
        "Never fall back to raw curl or bash — slack_api covers the whole Web API."
    ),
)


# Send shaping (attribution, auto-split, block pre-checks, form encoding) lives
# in shaping.py since the v2 core slice — shared with the library Door.
BLOCKS_METHODS = shaping.BLOCKS_METHODS
SUFFIX_METHODS = shaping.SUFFIX_METHODS
SECTION_TEXT_LIMIT = shaping.SECTION_TEXT_LIMIT
BOT_SPLIT_METHODS = shaping.BOT_SPLIT_METHODS
# files.uploadV2 takes the same suffix on initial_comment — applied inside
# _upload_v2, which owns that method's whole dispatch.

# files.uploadV2 is a slack_sdk client method (WebClient.files_upload_v2), not a
# Web API endpoint: dispatched through api_call it returned `unknown_method`
# (UPL-002, 2026-09-03). The SDK runs getUploadURLExternal → binary POST →
# completeUploadExternal internally — the one flow the JSON gateway cannot
# express on its own (no tool for the binary step). Since slice 2 the upload is a
# core send: pre-flight and shaping live in shaping.py, the dispatch in _core_send.
UPLOAD_METHOD = shaping.UPLOAD_METHOD

# Streaming trio (agent surface, Apr 2026 platform additions). Policy: bot-only.
# A user-token stream would ship recipient-facing text with no reliable place to
# attach the :blue_heart: @<bot> footer (chunks arrive incrementally; stopStream
# finalization params vary) — so as_bot=False stream calls are refused pre-flight
# instead of silently posting unattributed. Streams post as the bot; attribution
# is never injected for bot-token sends anyway.
STREAM_METHODS = frozenset({
    "chat.startStream",
    "chat.appendStream",
    "chat.stopStream",
})

# Slack's default for these is 100 messages but historically the tool passed no
# limit and Slack served up to 1000 — three oversized responses (78–98K chars)
# hit sessions on 08-04/13/15. Inject an explicit default; callers override freely.
DEFAULT_LIMIT_METHODS = frozenset({
    "conversations.history",
    "conversations.replies",
})
DEFAULT_HISTORY_LIMIT = 100

# Reserved caller flag: `_raw: true` in params returns the response as JSON
# (formatters §4.16) instead of the method's shape — the only way to see the
# `blocks` of a message you just sent. Stripped before the API call.
RAW_FLAG = "_raw"


def _slack_error(code: str, detail: str = "") -> ToolError:
    """Build a structural tool error (output-contract v1.2 §10).

    FastMCP converts a raised ToolError into an MCP result with is_error=true,
    so callers detect Slack failures structurally instead of parsing text.
    The text (with the ERROR_HINTS recovery hint, when catalogued) is preserved
    as the error message.
    """
    return ToolError(format_error(code, detail))


def _error_code_from(e: SlackApiError) -> str:
    """Extract the Slack error code from a SlackApiError, '' if unavailable.

    The WebClient raises on ok=false, so without this the hint catalogue would
    be bypassed — the code would arrive buried inside str(e) as 'exception'.
    """
    resp = e.response
    if resp is None:
        return ""
    try:
        return resp.get("error") or ""
    except Exception:
        return ""


def _encode_nested(params: dict) -> dict:
    return shaping.encode_nested(params)


def _validate_caller_blocks(params: dict) -> None:
    problem = shaping.caller_blocks_problem(params)
    if problem:
        raise _slack_error("invalid_blocks", problem)


def _shaping_call(fn, *args):
    try:
        return fn(*args)
    except shaping.ShapingError as e:
        raise _slack_error(e.code, e.detail) from e


def _response_data(resp) -> dict:
    """SlackResponse → dict (tests mock the client with plain dicts)."""
    if isinstance(resp, dict):
        return resp
    return resp.data if hasattr(resp, "data") else {"ok": True}


@mcp.tool()
def slack_api(method: str, params: dict | None = None, as_bot: bool = False,
              provenance: dict | None = None) -> str:
    """Call any Slack Web API method.

    Args:
        method: API method name (e.g. 'reactions.add', 'conversations.create',
                'reminders.add', 'users.profile.set', 'pins.add')
        params: Method parameters as a dict. Refer to https://api.slack.com/methods/{method}
        as_bot: If True, use bot token — writes appear as the bot; no attribution, and
                plain `text` over 3000 chars on chat.postMessage/update/scheduleMessage
                is auto-split into expanded sections (caller `blocks` win). If False (default),
                use user token — recipient-facing text sends get the :blue_heart: @<bot> attribution:
                chat.postMessage / chat.update / chat.scheduleMessage / chat.postEphemeral
                receive a Block Kit context block; files.uploadV2 / files.completeUploadExternal
                initial_comment receives a text suffix. Other methods (reactions, pins, reads)
                are untouched.

    File upload — `files.uploadV2` (the gateway runs getUploadURLExternal → PUT →
    completeUploadExternal itself; never PUT bytes by hand):
        params: `file` (absolute local path) or `content` (text); `filename`, `title`,
        `channel` (a string — not `channels`), `initial_comment`, `thread_ts`, `alt_txt`.
        `attribution: "block"` (user token only) posts the comment as a separate
        attributed message with `<permalink|title>` instead of the text suffix — a child
        op under the same approval; the file's sha256 is bound into the approval.

    Provenance (v2 core: chat.postMessage, chat.scheduleMessage, chat.update,
    chat.postEphemeral, chat.meMessage, files.uploadV2) — optional `provenance` object: `cls`
    (A = your voice, B-h = bot voice, both human-approved), `comm_id`, `op_id`,
    `target`, `checks`, `sources`, `origin`, `ttl_s`, `resend`. Without it a core
    send still asks the human (migration rule) — with a warning. The
    approval prompt shows the provenance; the decision is bound to the exact
    operation (any change after approval → `decision_mismatch`). Every send is
    journalled before it leaves (write-ahead). `slack_api("_rights")` shows what
    this host may do right now (profile × policy × class). `slack_api("_comm_id")`
    mints a journal-unique ledger comm_id (no Slack call) — pass it as provenance.comm_id;
    without one the door mints it and returns it in the result line. An approval is valid
    30 min; `decision_expired` = nothing sent, call again with the same input.
    `slack_api("_reconcile")` resolves journal rows with an unknown outcome by reading
    Slack (never resends); a comm_id with an unknown op refuses `reconcile_required` until then.

    Raw response — put `"_raw": true` inside params to get the response as JSON
    (cleaned of ok/response_metadata, cursor as `_next_cursor`)
    (e.g. the `blocks` of a message you just posted: conversations.history with
    limit=1). Delivery checks and debugging only — the JSON is large.

    Examples:
        slack_api('reactions.add', {'channel': 'C123', 'name': 'thumbsup', 'timestamp': '1234.5678'}, as_bot=True)
        slack_api('chat.postMessage', {'channel': 'C1', 'text': 'Hi from Atlas'}, as_bot=True)
        slack_api('conversations.list', {'types': 'private_channel', 'limit': 20})  # read as user
        slack_api('files.uploadV2', {'file': '/abs/report.pdf', 'channel': 'C1', 'initial_comment': 'Report'})
    """
    if method == "_rights":
        from atlas_slack.gate import rights
        return rights()
    if method == "_comm_id":
        # A journal-unique comm_id for the ledger, noted as the comm's first sighting (invariant x:
        # ttl_s counts from here). No Slack call. The same id then goes into provenance.comm_id.
        return _mint_comm_id_locally()
    if method == "_reconcile":
        return _reconcile_locally(params)
    if method in STREAM_METHODS and not as_bot:
        raise _slack_error(
            "user_token_stream",
            "chat.*Stream is a bot/agent surface — call with as_bot=True "
            "(streams post as the bot; the :blue_heart: footer doesn't apply).",
        )
    profile = load_profile()
    call_params = dict(params or {})
    raw = bool(call_params.pop(RAW_FLAG, False))
    effect = classify(method, call_params)
    if effect.route == "core":
        res = _core_send(method, call_params, as_bot, provenance, profile)
        comm = (f" · comm {res.comm_id}" + (" (minted by the door — record it in the ledger)"
                                             if res.comm_minted else "")) if res.comm_id else ""
        tail = (f"\njournal: op {res.op_id} ok{comm}" if not res.journal_error else
                f"\n⚠ outcome_not_journaled: sent ({core.outcome_id(res.data) or '?'}) but the outcome could not be "
                f"journaled ({res.journal_error}) — the message IS in Slack; do not resend. op {res.op_id} stays "
                f"pending → unknown in the journal.{comm}")
        if res.child_op_id:
            # composite upload: the comment post is a second op; a failure there never means "re-upload",
            # and an ambiguous failure never means "post again" (invariant viii: reconcile, not retry)
            if res.child_outcome == "ok":
                tail += f"\ncomment: op {res.child_op_id} ok ts={res.child_data.get('ts', '?')}"
            elif res.child_outcome == "unknown":
                tail += (f"\n⚠ comment_outcome_unknown: the file IS in Slack (op {res.op_id} ok); the comment post "
                         f"(op {res.child_op_id}) may or may not exist — {res.child_error}. Do not upload again and do "
                         "not post the comment again: run slack_api(\"_reconcile\") first.")
            elif res.child_outcome == "journal":
                tail += (f"\n⚠ comment_not_journaled: the file IS in Slack (op {res.op_id} ok) and the comment was posted "
                         f"(op {res.child_op_id}) but its outcome could not be journaled: {res.child_error}. Do not upload "
                         "again, do not post the comment again.")
            elif res.child_outcome == "skipped":
                tail += (f"\n⚠ comment_not_posted: the file IS in Slack (op {res.op_id}); the comment was not attempted — "
                         f"op {res.child_op_id}: {res.child_error}. Do not upload again; "
                         + ("the upload's own outcome is not journaled yet (pending → unknown): run slack_api(\"_reconcile\") "
                            "first, then post the comment with chat.postMessage (its own approval, the same comm_id with resend: true) "
                            "if it is still wanted." if res.journal_error else
                            "post the comment with chat.postMessage (its own approval; the same comm_id with resend: true) if it "
                            "is still wanted."))
            else:
                tail += (f"\n⚠ comment_not_posted: the file IS in Slack (op {res.op_id} ok) but Slack refused the comment — "
                         f"op {res.child_op_id} {res.child_error}. Do not upload again; post the comment with "
                         "chat.postMessage (its own approval; the same comm_id with resend: true — the upload already "
                         "counts as this comm's write) if it is still wanted.")
        if res.prior is not None and not res.prior.get("error"):
            from atlas_slack.prior import one_line
            tail += f"\nwas: {one_line(res.prior.get('text') or '', 200)}"
        return format_response(method, res.data, req_params=params or {}, raw=raw) + tail
    # Reads and transitional writes: the verdict still applies (freeze, identity
    # overrides, refused methods, broken profile, invalid policy); execution is
    # today's path. The hook already asked where the verdict is needs-human.
    verdict = evaluate(method, call_params, as_bot, None, profile, load_policy(profile), face="mcp")
    if verdict.decision == REFUSE:
        core.record_call(profile, method, "refused", as_bot=as_bot, caller="mcp", error_code=verdict.reason)
        raise _slack_error(verdict.reason, verdict.message + (" (transitional path)" if verdict.transitional else ""))
    extra: list[str] = []
    t0 = time.monotonic()
    try:
        client = get_client(as_bot=as_bot)
        if effect.route == "transitional":
            # a transitional write runs under whatever token is there: same identity check as a core send
            problems = identity.verify(profile, as_bot, _sdk_transport(client))
            if problems:
                code = identity.code_for(problems)
                _observe(profile, method, as_bot, "refused", code, t0)
                raise _slack_error(code, "; ".join(problems) + core.IDENTITY_HINTS[code])
        if method in DEFAULT_LIMIT_METHODS and "limit" not in call_params:
            call_params = {**call_params, "limit": DEFAULT_HISTORY_LIMIT}
        if method in BLOCKS_METHODS:
            _validate_caller_blocks(call_params)
        call_params = _shaping_call(shaping.shape_send, method, call_params, as_bot, profile.bot_user_id)
        data = _response_data(client.api_call(method, params=_encode_nested(call_params)))
    except SlackApiError as e:
        code = _error_code_from(e)
        _observe(profile, method, as_bot, "error", code or "exception", t0)
        if code:
            raise _slack_error(code) from e
        raise _slack_error("exception", str(e)) from e
    except ToolError:
        _observe(profile, method, as_bot, "error", "tool_error", t0)
        raise
    except Exception as e:
        _observe(profile, method, as_bot, "error", "exception", t0)
        raise _slack_error("exception", str(e)) from e
    if not data.get("ok", True):
        _observe(profile, method, as_bot, "error", data.get("error") or "unknown_error", t0)
        raise _slack_error(data.get("error") or "unknown_error")
    _observe(profile, method, as_bot, "ok", None, t0)
    # Echo the caller's original args (pre-injection) — see _args_echo (v1.2 §8)
    out = format_response(method, data, req_params=params or {}, raw=raw)
    return "\n".join([out, *extra]) if extra else out


def _observe(profile, method: str, as_bot: bool, status: str, error_code: str | None, t0: float) -> None:
    """Call-journal row for reads and transitional writes — best effort, never content."""
    core.record_call(profile, method, status, as_bot=as_bot, caller="mcp", error_code=error_code,
                     latency_ms=int((time.monotonic() - t0) * 1000))


def _mint_comm_id_locally() -> str:
    from atlas_slack.journal import JournalUnavailable, open_journal
    from atlas_slack.profile import load_profile
    profile = load_profile()
    from atlas_slack.provenance import mint_comm_id
    v = evaluate("_comm_id", {}, False, None, profile, load_policy(profile), face="mcp")
    if v.decision == REFUSE:
        raise _slack_error(v.reason, v.message)
    try:
        j = open_journal(profile.journal_dir)
    except JournalUnavailable as e:
        raise _slack_error("journal_unavailable", f"cannot mint a comm_id without the journal: {e}") from e
    cid = mint_comm_id(lambda c: j.comm_first_seen(c) is not None or j.has_comm(c))
    j.note_comm_seen(cid)
    return f"comm_id: {cid}\n(first sighting noted now; put it in the ledger file and in provenance.comm_id)"


def _sdk_transport(client):
    """The core's transport over a WebClient: api_call for Web API methods, files_upload_v2 for uploads.

    `bot` is the voice the core already chose the client for; a composite's child post and a
    chat.update's prior read run under the same client (same voice as the parent write)."""
    def transport(m: str, final_params: dict, bot: bool) -> dict:
        if m == UPLOAD_METHOD:
            return _response_data(client.files_upload_v2(**final_params))
        return _response_data(client.api_call(m, params=_encode_nested(final_params)))
    return transport


def _reconcile_locally(params: dict | None) -> str:
    """slack_api("_reconcile") — resolve `unknown` outcomes against Slack; reads only, writes the journal."""
    from atlas_slack import reconcile as _rec
    from atlas_slack.journal import JournalUnavailable, open_journal
    profile = load_profile()
    v = evaluate("_reconcile", {}, False, None, profile, load_policy(profile), face="mcp")
    if v.decision == REFUSE:
        raise _slack_error(v.reason, v.message)
    try:
        j = open_journal(profile.journal_dir)
    except JournalUnavailable as e:
        raise _slack_error("journal_unavailable", f"cannot reconcile without the journal: {e}") from e

    def reader(m: str, p: dict, as_bot: bool) -> dict:
        return _response_data(get_client(as_bot=as_bot).api_call(m, params=_encode_nested(p)))

    ids = (params or {}).get("op_ids")
    if ids is not None and not (isinstance(ids, list) and all(isinstance(i, str) for i in ids)):
        raise _slack_error("invalid_arguments", "`op_ids` must be a list of op id strings ([] reconciles nothing)")
    resolve = (params or {}).get("resolve")
    if resolve is not None:
        # the person's word on rows a read cannot settle — bound to the hook's one-time decision like a send:
        # a call that never met the hook (hooks off, another client) is refused, nothing is recorded
        if resolve not in _rec.MANUAL_RESOLUTIONS or not ids:
            raise _slack_error("invalid_arguments", "_reconcile by hand needs op_ids (non-empty) and resolve: \"ok\" | \"absent\"")
        import re as _re
        from atlas_slack import __version__, decision as decision_mod
        rhash = _rec.manual_hash(ids, resolve, profile.name)
        dec = (params or {}).get("decision")
        if isinstance(dec, dict) and dec.get("p") and dec["p"] != profile.name:
            raise _slack_error("profile_mismatch", f"the decision was issued under profile {dec['p']!r} but this server runs "
                                                   f"{profile.name!r} — ATLAS_SLACK_PROFILE differs between the hook and the server")
        dc = decision_mod.check(dec if isinstance(dec, dict) else None, rhash, j, profile.journal_dir, profile.decision_fallback)
        if not dc.ok:
            raise _slack_error(dc.code, dc.detail)
        if dc.fallback:
            # decision_fallback profile: no wire token — the newest unconsumed row for this exact hash is the decision
            row = j.latest_unconsumed_decision(rhash)
            if row is None:
                raise _slack_error("no_decision", "no unconsumed decision recorded for this exact resolution")
            if time.time() - row["issued_at"] > decision_mod.DECISION_MAX_AGE_S:
                raise _slack_error("decision_expired", decision_mod.expired_detail(time.time() - row["issued_at"],
                                                                                 decision_mod.DECISION_MAX_AGE_S))
            m = _re.search(r"\bv=(\S+)", row["adapter"] or "")
            hook_v, consume = (m.group(1) if m else None), (lambda: j.consume_decision(by_hash=row["token_hash"]))
        else:
            hook_v, consume = (dec or {}).get("v"), (lambda: j.consume_decision(dc.token))
        if hook_v != __version__:
            raise _slack_error("version_skew", f"the hook runs atlas-slack {hook_v or '< 0.6.0'} but this server runs "
                                               f"{__version__} — restart the session; nothing was recorded")
        if not consume():
            raise _slack_error("decision_already_used", "this resolution was already recorded")
        return _rec.report(_rec.resolve_by_hand(j, ids, resolve), op_ids=ids)
    return _rec.report(_rec.reconcile(j, reader, profile, op_ids=ids), op_ids=ids)


def _core_send(method: str, params: dict, as_bot: bool, provenance: dict | None, profile) -> "core.SendResult":
    """A core write through the door: policy → provenance → decision → journal → transport."""
    try:
        client = get_client(as_bot=as_bot, retries=False)      # one HTTP attempt per write (no hidden SDK retry)
    except Exception as e:
        raise _slack_error("token_unavailable", str(e)) from e
    transport = _sdk_transport(client)

    try:
        return core.execute_send(method, params, as_bot, provenance, profile=profile, transport=transport,
                                 face="mcp", caller=(provenance or {}).get("prepared_by") or "mcp")
    except core.Refused as e:
        raise _slack_error(e.code, e.message) from e
    except core.SendFailed as e:
        if e.outcome == "unknown":
            raise _slack_error("outcome_unknown", f"{e.detail}") from e
        raise _slack_error(e.code, f"{ERROR_HINTS.get(e.code, '')}{' — ' if ERROR_HINTS.get(e.code) else ''}"
                                   f"journal: op {e.op_id} error") from e


# Register slack_download on the same mcp instance (the one operation the
# JSON gateway cannot express — authenticated binary GET of file bytes)
import atlas_slack.tools  # noqa: F401
