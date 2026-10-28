"""Generic Slack API gateway — one tool for all 200+ methods."""

import json
import time
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from slack_sdk.errors import SlackApiError

from atlas_slack import core, shaping
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
        "chat.postMessage runs through the v2 core: the approval prompt shows its provenance, "
        "the human's decision is bound to that exact operation (change anything after approval → "
        "decision_mismatch; repeat → decision_already_used) and the send is journalled before it "
        "leaves. Optional `provenance` = {cls: 'A' (person voice) | 'B-h' (bot voice), comm_id, "
        "target, checks, sources, ttl_s, resend}. username/icon_emoji/icon_url are refused. "
        "slack_api('_rights') shows what this host may do now (profile × policy × class). "
        "Attribution is auto-applied to user-token sends on chat.postMessage, chat.update, "
        "chat.scheduleMessage, chat.postEphemeral (Block Kit context block) and "
        "files.uploadV2 / files.completeUploadExternal initial_comment (text suffix; "
        "uploadV2 with attribution='block' posts the comment as a separate attributed "
        "message instead); long text is "
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
# express on its own (no tool for the binary step).
UPLOAD_METHOD = "files.uploadV2"

# Streaming trio (agent surface, Apr 2026 platform additions). Policy: bot-only.
# A user-token stream would ship recipient-facing text with no reliable place to
# attach the :blue_heart: @Atlas footer (chunks arrive incrementally; stopStream
# finalization params vary) — so as_bot=False stream calls are refused pre-flight
# instead of silently posting unattributed. Streams post as Atlas; attribution
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


def _checked_upload_path(value: str, label: str) -> str:
    """Resolve a local upload path: `~` expanded, then absolute and a regular file.

    The MCP server's cwd is not the session's, so a relative path would resolve
    somewhere the caller never looked.
    """
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise _slack_error("invalid_arguments", f"`{label}` must be an absolute path, got {value!r}")
    if not path.is_file():
        raise _slack_error("file_not_found", f"`{label}` does not exist or is not a regular file: {path}")
    return str(path)


def _upload_v2(client, params: dict, as_bot: bool, bot_user_id: str | None = None) -> tuple[dict, list[str]]:
    """files.uploadV2 → WebClient.files_upload_v2 (UPL-002).

    Returns (response data, extra render lines). Pre-flight:
    - `channels` must be a list; one destination is `channel` as a string —
      a plain string in `channels` is joined character by character by the SDK
      (UPL-001: `"D06MG7QF3ME"` → `channel=D`).
    - `file` (and every `file_uploads[i].file`) is an absolute local path that
      exists — `~` expanded (the MCP server's cwd is not the session's);
      `content` is passed through.
    Attribution (as_bot=False): text suffix on `initial_comment` by default —
    Slack's file-share message cannot carry blocks. `attribution: "block"`
    (v1.3: comment as a separate attributed chat.postMessage) refuses
    `composite_not_in_slice` since the v2 core slice — that second post would
    bypass the core; it returns as a composite op (child op_id). Bot uploads
    never get attribution.
    """
    params = dict(params)
    attribution = params.pop("attribution", None)
    channels = params.get("channels")
    if isinstance(channels, str):
        raise _slack_error(
            "invalid_arguments",
            "`channels` must be a list — for one destination pass `channel` as a "
            "string (a plain string in `channels` is split character by character).",
        )
    if not any(k in params for k in ("file", "content", "file_uploads")):
        raise _slack_error("missing_params", "files.uploadV2 needs `file` (absolute path), `content`, or `file_uploads`")
    if isinstance(params.get("file"), str):
        params["file"] = _checked_upload_path(params["file"], "file")
    if isinstance(params.get("file_uploads"), list):
        items = []
        for i, item in enumerate(params["file_uploads"]):
            if isinstance(item, dict) and isinstance(item.get("file"), str):
                item = {**item, "file": _checked_upload_path(item["file"], f"file_uploads[{i}].file")}
            items.append(item)
        params["file_uploads"] = items
    comment = params.get("initial_comment")
    block_mode = (not as_bot) and attribution == "block" and bool(comment)
    if block_mode:
        # The block-mode comment is a second chat.postMessage. Since the v2 core slice
        # every chat.postMessage needs its own one-time decision and journal intent; a
        # composite upload+comment op (child op_id, A4) is not built yet → refuse
        # instead of posting outside the core (verifier finding 2026-10-28).
        raise _slack_error(
            "composite_not_in_slice",
            "attribution='block' posts the comment as a separate chat.postMessage outside the v2 core — "
            "not supported yet. Upload without `attribution` (the comment gets the 💙 text suffix), or upload "
            "without `initial_comment` and post the comment with chat.postMessage (its own approval).",
        )
    elif not as_bot and comment:
        params = _shaping_call(shaping.inject_attribution_suffix, params, "initial_comment", bot_user_id)

    data = _response_data(client.files_upload_v2(**params))
    extra: list[str] = []
    return data, extra


@mcp.tool()
def slack_api(method: str, params: dict | None = None, as_bot: bool = False,
              provenance: dict | None = None) -> str:
    """Call any Slack Web API method.

    Args:
        method: API method name (e.g. 'reactions.add', 'conversations.create',
                'reminders.add', 'users.profile.set', 'pins.add')
        params: Method parameters as a dict. Refer to https://api.slack.com/methods/{method}
        as_bot: If True, use bot token — writes appear as "Atlas"; no attribution, and
                plain `text` over 3000 chars on chat.postMessage/update/scheduleMessage
                is auto-split into expanded sections (caller `blocks` win). If False (default),
                use user token — recipient-facing text sends get :blue_heart: @Atlas attribution:
                chat.postMessage / chat.update / chat.scheduleMessage / chat.postEphemeral
                receive a Block Kit context block; files.uploadV2 / files.completeUploadExternal
                initial_comment receives a text suffix. Other methods (reactions, pins, reads)
                are untouched.

    File upload — `files.uploadV2` (the gateway runs getUploadURLExternal → PUT →
    completeUploadExternal itself; never PUT bytes by hand):
        params: `file` (absolute local path) or `content` (text); `filename`, `title`,
        `channel` (a string — not `channels`), `initial_comment`, `thread_ts`, `alt_txt`.
        `attribution: "block"` (user token only) posts the comment as a separate
        attributed message with `<permalink|title>` instead of the text suffix.

    Provenance (v2 core, chat.postMessage) — optional `provenance` object: `cls`
    (A = your voice, B-h = bot voice, both human-approved), `comm_id`, `op_id`,
    `target`, `checks`, `sources`, `origin`, `ttl_s`, `resend`. Without it a
    chat.postMessage still asks the human (migration rule) — with a warning. The
    approval prompt shows the provenance; the decision is bound to the exact
    operation (any change after approval → `decision_mismatch`). Every send is
    journalled before it leaves (write-ahead). `slack_api("_rights")` shows what
    this host may do right now (profile × policy × class).

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
        tail = (f"\njournal: op {res.op_id} ok" if not res.journal_error else
                f"\n⚠ outcome_not_journaled: sent (ts={res.data.get('ts', '?')}) but the outcome could not be "
                f"journaled ({res.journal_error}) — the message IS in Slack; do not resend. op {res.op_id} stays "
                "pending → unknown in the journal.")
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
        if method == UPLOAD_METHOD:
            data, extra = _upload_v2(client, call_params, as_bot, profile.bot_user_id)
        else:
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


def _core_send(method: str, params: dict, as_bot: bool, provenance: dict | None, profile) -> "core.SendResult":
    """chat.postMessage through the v2 core: policy → provenance → decision → journal → transport."""
    try:
        client = get_client(as_bot=as_bot)
    except Exception as e:
        raise _slack_error("token_unavailable", str(e)) from e

    def transport(m: str, final_params: dict, bot: bool) -> dict:
        return _response_data(client.api_call(m, params=_encode_nested(final_params)))

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
