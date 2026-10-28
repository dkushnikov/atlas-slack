"""Send shaping shared by every face — attribution, auto-split, block pre-checks, form encoding.

Moved out of `gateway.py` (v2 core slice) so the MCP server and the library
`Door` produce byte-identical payloads. Behaviour is unchanged from 0.4.0 /
contract v1.3 §9, except that the attributed bot id comes from the profile
(`bot_user_id`) — no workspace literal in code (Strategy п. 17).
"""

from __future__ import annotations

import json

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

# Slack caps a section block's text at 3000 chars; longer → invalid_blocks.
SECTION_TEXT_LIMIT = 3000

# Bot-token posts have no attribution wrap, so long plain `text` reached Slack
# unsplit and Slack cut it at ~4000 chars mid-sentence ("Ten years", 2026-10-19).
BOT_SPLIT_METHODS = frozenset({
    "chat.postMessage",
    "chat.update",
    "chat.scheduleMessage",
})


class ShapingError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _require_bot_id(bot_user_id: str | None) -> str:
    if not bot_user_id:
        raise ShapingError(
            "attribution_unavailable",
            "a send in the person's voice needs the :blue_heart: attribution to the bot, and the bot user id "
            "is unknown — set `bot_user_id` in the profile (~/.config/atlas-slack/<ctx>.yaml) or "
            "ATLAS_BOT_USER_ID in the environment",
        )
    return bot_user_id


def attribution_block(bot_user_id: str | None) -> dict:
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f":blue_heart: <@{_require_bot_id(bot_user_id)}>"}],
    }


def attribution_suffix(bot_user_id: str | None) -> str:
    return f"\n\n:blue_heart: <@{_require_bot_id(bot_user_id)}>"


def split_text_into_sections(text: str) -> list[dict]:
    """Split text into ≤3000-char mrkdwn section blocks.

    Prefers newline, then space boundaries; the boundary char stays in the left
    chunk so the concatenation of all chunks reproduces the input exactly.
    Each section carries `expand: true` — without it the Slack client folds a
    long section under "Show more", one fold per chunk (2026-10-20).
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


def inject_attribution_block(params: dict, bot_user_id: str | None) -> dict:
    """Append the :blue_heart: context block; plain `text` is wrapped (auto-split) first."""
    block = attribution_block(bot_user_id)
    params = dict(params)
    existing_blocks = params.get("blocks", [])
    if not existing_blocks and "text" in params:
        existing_blocks = split_text_into_sections(params["text"])
    params["blocks"] = list(existing_blocks) + [block]
    return params


def inject_attribution_suffix(params: dict, key: str, bot_user_id: str | None) -> dict:
    params = dict(params)
    params[key] = f"{params[key]}{attribution_suffix(bot_user_id)}"
    return params


def caller_blocks_problem(params: dict) -> str | None:
    """Pre-flight check of caller-supplied blocks against Slack's section cap (incident 2026-07-30)."""
    blocks = params.get("blocks")
    if not isinstance(blocks, list):
        return None
    for i, block in enumerate(blocks):
        if not isinstance(block, dict) or block.get("type") != "section":
            continue
        text_obj = block.get("text")
        text = text_obj.get("text", "") if isinstance(text_obj, dict) else ""
        if isinstance(text, str) and len(text) > SECTION_TEXT_LIMIT:
            return (f"blocks[{i}] section text is {len(text)} chars — Slack caps section "
                    f"text at {SECTION_TEXT_LIMIT}. Split it into multiple section blocks "
                    "(auto-split covers plain `text`, not caller-supplied blocks), or "
                    "pre-check with blocks.validate.")
    return None


def maybe_split_bot_text(params: dict) -> dict:
    """Bot-token auto-split: long plain `text`, no caller blocks → section blocks (text kept as fallback)."""
    text = params.get("text")
    if params.get("blocks") or not isinstance(text, str) or len(text) <= SECTION_TEXT_LIMIT:
        return params
    return {**params, "blocks": split_text_into_sections(text)}


def maybe_inject_attribution(method: str, params: dict, bot_user_id: str | None) -> dict:
    """Method-aware attribution for person-voice recipient-facing sends (whitelist)."""
    if method in BLOCKS_METHODS:
        return inject_attribution_block(params, bot_user_id)
    if method in SUFFIX_METHODS:
        key = SUFFIX_METHODS[method]
        if params.get(key):
            return inject_attribution_suffix(params, key, bot_user_id)
    return params


def shape_send(method: str, params: dict, as_bot: bool, bot_user_id: str | None) -> dict:
    """Final params for a send: attribution (person voice) or bot auto-split."""
    if not as_bot:
        return maybe_inject_attribution(method, params, bot_user_id)
    if method in BOT_SPLIT_METHODS:
        return maybe_split_bot_text(params)
    return params


def encode_nested(params: dict) -> dict:
    """JSON-encode list/dict values for the form transport (`api_call(params=…)` urlencodes)."""
    return {
        k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
        for k, v in params.items()
    }
