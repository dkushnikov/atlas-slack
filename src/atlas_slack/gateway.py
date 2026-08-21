"""Generic Slack API gateway — one tool for all 200+ methods."""

from fastmcp import FastMCP

from atlas_slack.client import get_client, ATLAS_BOT_USER_ID
from atlas_slack.formatters import format_response, format_error

mcp = FastMCP(
    "atlas-slack",
    instructions=(
        "Full Slack Web API access. Use slack_api to call any Slack method "
        "(reactions.add, conversations.create, reminders.add, users.profile.set, etc). "
        "Convenience tools available for common operations: slack_react, slack_set_status, "
        "slack_remind, slack_pin, slack_upload, slack_edit, slack_delete, slack_create_channel, "
        "slack_invite, slack_archive, slack_usergroup, slack_bookmark, slack_dnd, slack_download. "
        "Use slack_download to save a Slack file to local disk (the JSON API never serves "
        "file bytes — never curl url_private). "
        "Pass as_bot=True for posts/reactions/writes to appear as Atlas; "
        "as_bot=False (default) posts as the owner with :blue_heart: @Atlas attribution. "
        "Attribution is auto-applied to user-token sends on chat.postMessage, chat.update, "
        "chat.scheduleMessage, chat.postEphemeral (Block Kit context block) and "
        "files.completeUploadExternal.initial_comment (text suffix); long text is "
        "auto-split into ≤3000-char sections. Streaming methods (chat.startStream/"
        "appendStream/stopStream) are bot-only — always pass as_bot=True for them. "
        "Never fall back to raw curl or bash — slack_api covers all 200+ Slack Web API methods."
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
        return format_error(
            "user_token_stream",
            "chat.*Stream is a bot/agent surface — call with as_bot=True "
            "(streams post as Atlas; the :blue_heart: footer doesn't apply).",
        )
    try:
        client = get_client(as_bot=as_bot)
        call_params = params or {}
        if not as_bot:
            call_params = _maybe_inject_attribution(method, call_params)
        resp = client.api_call(method, params=call_params)
        if isinstance(resp, dict):
            return format_response(method, resp)
        return format_response(method, resp.data if hasattr(resp, "data") else {"ok": True})
    except Exception as e:
        return format_error("exception", str(e))


# Register convenience tools on the same mcp instance
import atlas_slack.tools  # noqa: F401
