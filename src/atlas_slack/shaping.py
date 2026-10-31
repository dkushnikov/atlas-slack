"""Send shaping shared by every face — attribution, auto-split, block pre-checks, form encoding.

Moved out of `gateway.py` (v2 core slice) so the MCP server and the library
`Door` produce byte-identical payloads. Behaviour is unchanged from 0.4.0 /
contract v1.3 §9, except that the attributed bot id comes from the profile
(`bot_user_id`) — no workspace literal in code (Strategy п. 17).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

BLOCKS_METHODS = frozenset({
    "chat.postMessage",
    "chat.update",
    "chat.scheduleMessage",
    "chat.postEphemeral",
})

SUFFIX_METHODS = {
    "files.uploadV2": "initial_comment",
    "files.completeUploadExternal": "initial_comment",
    "chat.meMessage": "text",
}

UPLOAD_METHOD = "files.uploadV2"
# Door-level upload option, never forwarded to the SDK: `"block"` posts the comment as a
# separate attributed chat.postMessage (composite op, child op_id) instead of the text suffix.
UPLOAD_ATTRIBUTION_PARAM = "attribution"
UPLOAD_ATTRIBUTION_VALUES = (None, "suffix", "block")

# Reconciliation handle for bot-voice sends (invariant viii): the op id travels in Slack's
# message metadata, so an `unknown` outcome is resolved by reading the channel, never by a retry.
# User-token sends cannot carry app metadata — those are matched by text and time window.
METADATA_EVENT_TYPE = "atlas_slack_op"
# Not chat.update: an edit replaces the message's metadata, which would rewrite the original post's own
# op id (its reconciliation handle) — an edit is reconciled by the text at `ts` instead (spec-verifier).
# Not chat.scheduleMessage: Slack's docs (bug alert, 2026-10-31): «Messages scheduled with chat.scheduleMessage
# will not post if the metadata parameter is used» — a scheduled send is reconciled by text in the scheduled
# list and, after post_at, in the channel (Codex addendum A1).
METADATA_METHODS = frozenset({"chat.postMessage"})

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
    """Final params for a send: attribution (person voice) or bot auto-split.

    `files.uploadV2`: the door-level `attribution` option is stripped; in block mode the
    comment leaves the upload (it becomes the composite's child post, see core)."""
    if method == UPLOAD_METHOD:
        block = upload_block_mode(params, as_bot)
        params = {k: v for k, v in params.items() if k != UPLOAD_ATTRIBUTION_PARAM}
        if block:
            params = {k: v for k, v in params.items() if k != "initial_comment"}
            return params
    if not as_bot:
        return maybe_inject_attribution(method, params, bot_user_id)
    if method in BOT_SPLIT_METHODS:
        return maybe_split_bot_text(params)
    return params


def with_op_metadata(method: str, params: dict, as_bot: bool, op_id: str, comm_id: str | None) -> dict:
    """Bot-voice sends carry `{op_id, comm_id}` in message metadata (unless the caller set its own)."""
    if not as_bot or method not in METADATA_METHODS or "metadata" in params:
        return params
    payload = {"op_id": op_id}
    if comm_id:
        payload["comm_id"] = comm_id
    return {**params, "metadata": {"event_type": METADATA_EVENT_TYPE, "event_payload": payload}}


# ------------------------------------------------------------------ uploads (files.uploadV2)

def _checked_upload_path(value: str, label: str) -> str:
    """`~` expanded, then absolute and a regular file — the MCP server's cwd is not the session's."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ShapingError("invalid_arguments", f"`{label}` must be an absolute path, got {value!r}")
    if not path.is_file():
        raise ShapingError("file_not_found", f"`{label}` does not exist or is not a regular file: {path}")
    return str(path)


def upload_block_mode(params: dict, as_bot: bool) -> bool:
    return (not as_bot) and params.get(UPLOAD_ATTRIBUTION_PARAM) == "block" and bool(params.get("initial_comment"))


def prepare_upload(params: dict) -> dict:
    """Pre-flight for files.uploadV2 — refuses before any call, returns params with resolved paths.

    - `channels` must not be a string: one destination is `channel` (a string in `channels` is
      joined character by character by the SDK — UPL-001); through the door an upload goes to one
      `channel`, so a `channels` list is refused too (the decision binds one recipient).
    - `file` (and every `file_uploads[i].file`) is an absolute local path that exists; `content`
      passes through; `attribution` ∈ {suffix, block}.
    """
    params = dict(params)
    if "channels" in params:
        raise ShapingError("invalid_arguments",
                           "pass one destination as `channel` (a string) — `channels` is not accepted through "
                           "the door (a string there is split character by character; a list binds no single recipient)")
    present = [k for k in ("file", "content", "file_uploads") if k in params]
    if not present:
        raise ShapingError("missing_params", "files.uploadV2 needs `file` (absolute path), `content`, or `file_uploads`")
    if len(present) > 1:
        raise ShapingError("invalid_arguments", f"files.uploadV2 takes one of `file`, `content`, `file_uploads` — got {present} "
                                                "(the SDK refuses the combination after the approval)")
    if "content" in params and not isinstance(params["content"], str):
        raise ShapingError("invalid_arguments", "`content` must be a string")
    if params.get(UPLOAD_ATTRIBUTION_PARAM) not in UPLOAD_ATTRIBUTION_VALUES:
        raise ShapingError("invalid_arguments", "`attribution` must be \"suffix\" (default) or \"block\"")
    if isinstance(params.get("file"), str):
        params["file"] = _checked_upload_path(params["file"], "file")
    elif "file" in params:
        raise ShapingError("invalid_arguments", "`file` must be an absolute path string")
    if isinstance(params.get("file_uploads"), list):
        if not params["file_uploads"]:
            raise ShapingError("invalid_arguments", "`file_uploads` is empty")
        items = []
        for i, item in enumerate(params["file_uploads"]):
            if not isinstance(item, dict) or not (isinstance(item.get("file"), str) or isinstance(item.get("content"), str)):
                raise ShapingError("invalid_arguments", f"`file_uploads[{i}]` must be an object with `file` (absolute path) or `content`")
            if "file" in item and "content" in item:
                raise ShapingError("invalid_arguments", f"`file_uploads[{i}]` takes `file` or `content`, not both")
            if isinstance(item.get("file"), str):
                item = {**item, "file": _checked_upload_path(item["file"], f"file_uploads[{i}].file")}
            items.append(item)
        params["file_uploads"] = items
    elif "file_uploads" in params:
        raise ShapingError("invalid_arguments", "`file_uploads` must be a list of {file | content, …}")
    return params


def upload_paths(params: dict) -> list[str]:
    """Every local file an upload reads, in SDK order (`file`, then `file_uploads[*].file`)."""
    out = []
    if isinstance(params.get("file"), str):
        out.append(params["file"])
    for item in params.get("file_uploads") or []:
        if isinstance(item, dict) and isinstance(item.get("file"), str):
            out.append(item["file"])
    return out


def upload_file_hashes(params: dict) -> list[str]:
    """sha256 of each file an upload would send (paths already resolved by prepare_upload)."""
    out = []
    for p in upload_paths(params):
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        out.append(h.hexdigest())
    return out


def upload_with_bytes(params: dict) -> tuple[dict, list[str]]:
    """Replace every `file` path with its bytes (read once) and return the sha256 of exactly those bytes.

    `filename` defaults to the path's basename (the SDK derives it from a path, not from bytes)."""
    out = dict(params)
    hashes = []

    def load(p: str) -> bytes:
        with open(p, "rb") as f:
            data = f.read()
        hashes.append(hashlib.sha256(data).hexdigest())
        return data

    if isinstance(out.get("file"), str):
        path = out["file"]
        out["file"] = load(path)
        out.setdefault("filename", Path(path).name)
    if isinstance(out.get("file_uploads"), list):
        items = []
        for item in out["file_uploads"]:
            if isinstance(item, dict) and isinstance(item.get("file"), str):
                path = item["file"]
                item = {**item, "file": load(path)}
                item.setdefault("filename", Path(path).name)
            items.append(item)
        out["file_uploads"] = items
    return out, hashes


def upload_label(params: dict) -> str:
    """What the upload is, for the prompt: `name (size)` / `snippet (n chars)` / `n files`."""
    items = params.get("file_uploads")
    if isinstance(items, list) and items:
        return f"{len(items)} files"
    if isinstance(params.get("file"), str):
        p = Path(params["file"]).expanduser()
        name = params.get("filename") or params.get("title") or p.name
        try:
            return f"{name} ({_human_size(p.stat().st_size)})"
        except OSError:
            return str(name)
    if isinstance(params.get("content"), str):
        name = params.get("filename") or params.get("title") or "snippet"
        return f"{name} ({len(params['content'])} chars)"
    return "file"


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def encode_nested(params: dict) -> dict:
    """JSON-encode list/dict values for the form transport (`api_call(params=…)` urlencodes)."""
    return {
        k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
        for k, v in params.items()
    }
