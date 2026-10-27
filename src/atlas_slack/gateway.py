"""Generic Slack API gateway — one tool for all 200+ methods."""

import json
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from slack_sdk.errors import SlackApiError

from atlas_slack.client import get_client, ATLAS_BOT_USER_ID
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
        "Pass as_bot=True for posts/reactions/writes to appear as Atlas; "
        "as_bot=False (default) posts as the owner with :blue_heart: @Atlas attribution. "
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


BLOCKS_METHODS = frozenset({
    "chat.postMessage",
    "chat.update",
    "chat.scheduleMessage",
    "chat.postEphemeral",
})

SUFFIX_METHODS = {
    "files.completeUploadExternal": "initial_comment",
    "chat.meMessage": "text",
}
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

# Slack caps a section block's text at 3000 chars; longer → invalid_blocks.
SECTION_TEXT_LIMIT = 3000

# Slack's default for these is 100 messages but historically the tool passed no
# limit and Slack served up to 1000 — three oversized responses (78–98K chars)
# hit sessions on 08-04/13/15. Inject an explicit default; callers override freely.
DEFAULT_LIMIT_METHODS = frozenset({
    "conversations.history",
    "conversations.replies",
})
DEFAULT_HISTORY_LIMIT = 100

# Bot-token posts have no attribution wrap, so long plain `text` reached Slack
# unsplit and Slack cut it at ~4000 chars mid-sentence ("Ten years", 2026-10-19).
# Same auto-split as the user path, applied when there are no caller blocks;
# `text` stays as the notification fallback. postEphemeral is left alone.
BOT_SPLIT_METHODS = frozenset({
    "chat.postMessage",
    "chat.update",
    "chat.scheduleMessage",
})

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


def _attribution_block() -> dict:
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f":blue_heart: <@{ATLAS_BOT_USER_ID}>"}],
    }


def _attribution_suffix() -> str:
    return f"\n\n:blue_heart: <@{ATLAS_BOT_USER_ID}>"


def _split_text_into_sections(text: str) -> list[dict]:
    """Split text into ≤3000-char mrkdwn section blocks.

    Prefers newline, then space boundaries; the boundary char stays in the left
    chunk so the concatenation of all chunks reproduces the input exactly.
    Multiple sections render seamlessly as one message. Each section carries
    `expand: true` — without it the Slack client folds a long section under
    "Show more", one fold per chunk (the owner Reflects post, 2026-10-20).
    """
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= SECTION_TEXT_LIMIT:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, SECTION_TEXT_LIMIT)
        if cut <= 0:
            cut = remaining.rfind(" ", 0, SECTION_TEXT_LIMIT)
        if cut <= 0:
            cut = SECTION_TEXT_LIMIT - 1
        chunks.append(remaining[:cut + 1])
        remaining = remaining[cut + 1:]
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": c}, "expand": True}
        for c in chunks if c.strip()
    ]


def _inject_attribution_block(params: dict) -> dict:
    """Append :blue_heart: @Atlas context block for methods that accept blocks.

    When wrapping plain `text` into blocks, long text is auto-split into ≤3000-char
    section blocks (Slack's per-section cap) so attribution wrapping never turns a
    valid long post into invalid_blocks. Caller-supplied blocks pass through as-is.
    """
    params = dict(params)
    existing_blocks = params.get("blocks", [])
    if not existing_blocks and "text" in params:
        existing_blocks = _split_text_into_sections(params["text"])
    params["blocks"] = list(existing_blocks) + [_attribution_block()]
    return params


def _inject_attribution_suffix(params: dict, key: str) -> dict:
    """Append attribution as text suffix for methods without Block Kit support."""
    params = dict(params)
    params[key] = f"{params[key]}{_attribution_suffix()}"
    return params


def _validate_caller_blocks(params: dict) -> None:
    """Pre-flight check of caller-supplied blocks against Slack's section cap.

    The attribution auto-split (§9) covers plain `text` only — caller blocks pass
    through untouched, so an oversized section previously died as a bare API 400
    `invalid_blocks` (incident 2026-07-30). Fail fast with the offending block
    index and length instead.
    """
    blocks = params.get("blocks")
    if not isinstance(blocks, list):
        return
    for i, block in enumerate(blocks):
        if not isinstance(block, dict) or block.get("type") != "section":
            continue
        text_obj = block.get("text")
        text = text_obj.get("text", "") if isinstance(text_obj, dict) else ""
        if isinstance(text, str) and len(text) > SECTION_TEXT_LIMIT:
            raise _slack_error(
                "invalid_blocks",
                f"blocks[{i}] section text is {len(text)} chars — Slack caps section "
                f"text at {SECTION_TEXT_LIMIT}. Split it into multiple section blocks "
                "(auto-split covers plain `text`, not caller-supplied blocks), or "
                "pre-check with blocks.validate.",
            )


def _maybe_split_bot_text(params: dict) -> dict:
    """Bot-token auto-split: long plain `text`, no caller blocks → section blocks.

    Mirrors the user-path wrap minus the attribution block (Atlas is already the
    author on record). `text` is kept — Slack uses it for notifications and as
    the fallback where blocks don't render.
    """
    text = params.get("text")
    if params.get("blocks") or not isinstance(text, str) or len(text) <= SECTION_TEXT_LIMIT:
        return params
    return {**params, "blocks": _split_text_into_sections(text)}


def _maybe_inject_attribution(method: str, params: dict) -> dict:
    """Method-aware attribution injection for user-token recipient-facing sends.

    Whitelist approach: only inject for methods that post recipient-facing text.
    Reactions, pins, user-state changes (profile.set, dnd.*) don't need attribution.
    """
    if method in BLOCKS_METHODS:
        return _inject_attribution_block(params)
    if method in SUFFIX_METHODS:
        key = SUFFIX_METHODS[method]
        if params.get(key):
            return _inject_attribution_suffix(params, key)
    return params


def _encode_nested(params: dict) -> dict:
    """JSON-encode list/dict values for the form transport.

    `WebClient.api_call(params=…)` urlencodes the dict (slack_sdk
    base_client, `urlencode(args["params"])`), so a nested `blocks` list
    arrived as its Python repr: Slack tolerated the strings, but any boolean
    (`True`) broke the block → `invalid_blocks_format`. Slack accepts arrays
    and objects as JSON-encoded strings in form fields, for every method.
    """
    return {
        k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
        for k, v in params.items()
    }


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


def _upload_v2(client, params: dict, as_bot: bool) -> tuple[dict, list[str]]:
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
    uploads without the comment and posts it as a separate chat.postMessage
    (auto-split sections + context block + `<permalink|title>` of the first
    file): two messages, block attribution preserved. If that second call
    fails, the error names the uploaded file so the caller retries the post,
    not the upload. Bot uploads never get attribution.
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
        if not params.get("channel"):
            raise _slack_error("missing_params", "attribution='block' needs `channel` (string) — the comment is posted there")
        params.pop("initial_comment")
    elif not as_bot and comment:
        params = _inject_attribution_suffix(params, "initial_comment")

    data = _response_data(client.files_upload_v2(**params))
    extra: list[str] = []
    if block_mode:
        files = data.get("files") or ([data["file"]] if isinstance(data.get("file"), dict) else [])
        first = files[0] if files and isinstance(files[0], dict) else {}
        permalink = first.get("permalink", "")
        label = params.get("title") or first.get("title") or first.get("name") or "file"
        text = f"{comment}\n\n<{permalink}|{label}>" if permalink else comment
        post = {"channel": params["channel"], "text": text}
        if params.get("thread_ts"):
            post["thread_ts"] = params["thread_ts"]
        # The file is already in Slack at this point: a failure here must not read
        # as "upload failed" — a retry of files.uploadV2 would post the file twice.
        uploaded = f"file {first.get('id', '?')} is already uploaded ({permalink or 'no permalink'})"

        def _post_failed(code: str, detail: str = "") -> ToolError:
            hint = detail or ERROR_HINTS.get(code, "")
            return _slack_error(
                code,
                f"{hint + '. ' if hint else ''}{uploaded}; the comment post failed — "
                "retry with chat.postMessage, not with another files.uploadV2.",
            )

        try:
            post_data = _response_data(
                client.api_call("chat.postMessage", params=_encode_nested(_inject_attribution_block(post)))
            )
        except SlackApiError as e:
            raise _post_failed(_error_code_from(e) or "exception", "" if _error_code_from(e) else str(e)) from e
        except Exception as e:
            raise _post_failed("exception", str(e)) from e
        if not post_data.get("ok", True):
            raise _post_failed(post_data.get("error") or "unknown_error")
        extra.append(f"  comment: posted ts={post_data.get('ts', '?')} (block attribution)")
    return data, extra


@mcp.tool()
def slack_api(method: str, params: dict | None = None, as_bot: bool = False) -> str:
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
    if method in STREAM_METHODS and not as_bot:
        raise _slack_error(
            "user_token_stream",
            "chat.*Stream is a bot/agent surface — call with as_bot=True "
            "(streams post as Atlas; the :blue_heart: footer doesn't apply).",
        )
    extra: list[str] = []
    call_params = dict(params or {})
    raw = bool(call_params.pop(RAW_FLAG, False))
    try:
        client = get_client(as_bot=as_bot)
        if method == UPLOAD_METHOD:
            data, extra = _upload_v2(client, call_params, as_bot)
        else:
            if method in DEFAULT_LIMIT_METHODS and "limit" not in call_params:
                call_params = {**call_params, "limit": DEFAULT_HISTORY_LIMIT}
            if method in BLOCKS_METHODS:
                _validate_caller_blocks(call_params)
            if not as_bot:
                call_params = _maybe_inject_attribution(method, call_params)
            elif method in BOT_SPLIT_METHODS:
                call_params = _maybe_split_bot_text(call_params)
            data = _response_data(client.api_call(method, params=_encode_nested(call_params)))
    except SlackApiError as e:
        code = _error_code_from(e)
        if code:
            raise _slack_error(code) from e
        raise _slack_error("exception", str(e)) from e
    except ToolError:
        raise
    except Exception as e:
        raise _slack_error("exception", str(e)) from e
    if not data.get("ok", True):
        raise _slack_error(data.get("error") or "unknown_error")
    # Echo the caller's original args (pre-injection) — see _args_echo (v1.2 §8)
    out = format_response(method, data, req_params=params or {}, raw=raw)
    return "\n".join([out, *extra]) if extra else out


# Register slack_download on the same mcp instance (the one operation the
# JSON gateway cannot express — authenticated binary GET of file bytes)
import atlas_slack.tools  # noqa: F401
