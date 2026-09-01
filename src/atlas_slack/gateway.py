"""Generic Slack API gateway — one tool for all 200+ methods."""

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from slack_sdk.errors import SlackApiError

from atlas_slack.client import get_client, ATLAS_BOT_USER_ID
from atlas_slack.formatters import format_response, format_error

mcp = FastMCP(
    "atlas-slack",
    instructions=(
        "Full Slack Web API access. Use slack_api to call any Slack method "
        "(reactions.add, conversations.open, reminders.add, users.profile.set, "
        "pins.add, chat.update, chat.delete, dnd.setSnooze, etc — all 200+ methods). "
        "Use slack_download to save a Slack file to local disk (the JSON API never serves "
        "file bytes — never curl url_private). File uploads go through the 3-step "
        "files.getUploadURLExternal → binary PUT → files.completeUploadExternal flow. "
        "Pass as_bot=True for posts/reactions/writes to appear as Atlas; "
        "as_bot=False (default) posts as the owner with :blue_heart: @Atlas attribution. "
        "Attribution is auto-applied to user-token sends on chat.postMessage, chat.update, "
        "chat.scheduleMessage, chat.postEphemeral (Block Kit context block) and "
        "files.completeUploadExternal.initial_comment (text suffix); long text is "
        "auto-split into ≤3000-char sections. Streaming methods (chat.startStream/"
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
    Multiple sections render seamlessly as one message.
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
        {"type": "section", "text": {"type": "mrkdwn", "text": c}}
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


@mcp.tool()
def slack_api(method: str, params: dict | None = None, as_bot: bool = False) -> str:
    """Call any Slack Web API method.

    Args:
        method: API method name (e.g. 'reactions.add', 'conversations.create',
                'reminders.add', 'users.profile.set', 'pins.add')
        params: Method parameters as a dict. Refer to https://api.slack.com/methods/{method}
        as_bot: If True, use bot token — writes appear as "Atlas". If False (default),
                use user token — recipient-facing text sends get :blue_heart: @Atlas attribution:
                chat.postMessage / chat.update / chat.scheduleMessage / chat.postEphemeral
                receive a Block Kit context block; files.completeUploadExternal.initial_comment
                receives a text suffix. Other methods (reactions, pins, reads) are untouched.

    Examples:
        slack_api('reactions.add', {'channel': 'C123', 'name': 'thumbsup', 'timestamp': '1234.5678'}, as_bot=True)
        slack_api('chat.postMessage', {'channel': 'C1', 'text': 'Hi from Atlas'}, as_bot=True)
        slack_api('conversations.list', {'types': 'private_channel', 'limit': 20})  # read as user
    """
    if method in STREAM_METHODS and not as_bot:
        raise _slack_error(
            "user_token_stream",
            "chat.*Stream is a bot/agent surface — call with as_bot=True "
            "(streams post as Atlas; the :blue_heart: footer doesn't apply).",
        )
    try:
        client = get_client(as_bot=as_bot)
        call_params = params or {}
        if method in DEFAULT_LIMIT_METHODS and "limit" not in call_params:
            call_params = {**call_params, "limit": DEFAULT_HISTORY_LIMIT}
        if method in BLOCKS_METHODS:
            _validate_caller_blocks(call_params)
        if not as_bot:
            call_params = _maybe_inject_attribution(method, call_params)
        resp = client.api_call(method, params=call_params)
        data = resp if isinstance(resp, dict) else (resp.data if hasattr(resp, "data") else {"ok": True})
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
    return format_response(method, data, req_params=params or {})


# Register slack_download on the same mcp instance (the one operation the
# JSON gateway cannot express — authenticated binary GET of file bytes)
import atlas_slack.tools  # noqa: F401
