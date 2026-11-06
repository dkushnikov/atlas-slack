"""Send shaping shared by every face — attribution, auto-split, block pre-checks, form encoding.

Moved out of `gateway.py` (v2 core slice) so the MCP server and the library
`Door` produce byte-identical payloads. Behaviour is unchanged from 0.4.0 /
contract v1.3 §9, except that the attributed bot id comes from the profile
(`bot_user_id`) — no workspace literal in code (Strategy п. 17).

0.8.0 — the marks come from the profile as a whole (`Marks.from_profile`): the person-voice footer
carries a label (`attribution_label`, default «Sent via»: the bot is the channel, not the author), and
bot-voice sends to others carry `cc <@person>` when the profile sets `cc_person` (the mention subscribes
the person to the thread the recipients reply in; the bot's inbound is read by nobody).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from atlas_slack.profile import DEFAULT_ATTRIBUTION_LABEL, DEFAULT_CC_LABEL

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

# Bot-voice cc (0.8.0): the three message sends that reach a thread, plus the suffix methods below. Not
# `chat.postEphemeral` (one viewer, nobody replies to it) and not the streams (transitional, bot-only; v1 skips them).
CC_BLOCKS_METHODS = frozenset({
    "chat.postMessage",
    "chat.update",
    "chat.scheduleMessage",
})
CHANNEL_KEYS = ("channel", "channel_id", "channels")      # completeUploadExternal: `channel_id`, or `channels` (comma-separated)
# The sends that take `thread_ts` — the thread rule (follow.py) applies to these; a /me has no thread, an ephemeral is one viewer.
THREAD_METHODS = frozenset({"chat.postMessage", "chat.scheduleMessage", "files.uploadV2", "files.completeUploadExternal"})

MARK_EMOJI = ":blue_heart:"
# Slack caps a message at 50 blocks; the cc section is not worth an `invalid_blocks` on a full message.
MAX_BLOCKS = 50

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


@dataclass(frozen=True)
class Marks:
    """What the door adds to a send, read from the profile (0.8.0): the person-voice footer and the bot-voice cc.

    `person_channels` are the conversations that reach the person themself (their bot DM, their user id —
    `policy.person_targets`): a cc there would be a cc to oneself, so none is added."""
    bot_user_id: str | None = None
    attribution_label: str = DEFAULT_ATTRIBUTION_LABEL
    cc_person: bool = False
    cc_label: str = DEFAULT_CC_LABEL
    person_user_id: str | None = None
    person_channels: frozenset = frozenset()

    @classmethod
    def from_profile(cls, profile) -> "Marks":
        from atlas_slack.policy import person_targets
        return cls(bot_user_id=profile.bot_user_id, attribution_label=profile.attribution_label,
                   cc_person=bool(profile.cc_person), cc_label=profile.cc_label,
                   person_user_id=profile.person_user_id, person_channels=frozenset(person_targets(profile)))

    @classmethod
    def of(cls, marks) -> "Marks":
        """A `Marks`, or the pre-0.8.0 argument — the bot user id alone (defaults for the rest)."""
        return marks if isinstance(marks, Marks) else cls(bot_user_id=marks)


def _require_bot_id(bot_user_id: str | None) -> str:
    if not bot_user_id:
        raise ShapingError(
            "attribution_unavailable",
            "a send in the person's voice needs the :blue_heart: attribution to the bot, and the bot user id "
            "is unknown — set `bot_user_id` in the profile (~/.config/atlas-slack/<ctx>.yaml) or "
            "ATLAS_BOT_USER_ID in the environment",
        )
    return bot_user_id


def attribution_text(marks: Marks) -> str:
    """`:blue_heart: Sent via <@bot>` — the profile's label between the emoji and the mention; "" = the mention alone."""
    bot = _require_bot_id(marks.bot_user_id)
    return f"{MARK_EMOJI} {marks.attribution_label} <@{bot}>" if marks.attribution_label else f"{MARK_EMOJI} <@{bot}>"


def attribution_block(marks: Marks) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": attribution_text(marks)}]}


def attribution_suffix(marks: Marks) -> str:
    return f"\n\n{attribution_text(marks)}"


def cc_text(marks: Marks) -> str:
    """`cc <@person>` — the profile's label before the mention; "" = the mention alone."""
    return f"{marks.cc_label} <@{marks.person_user_id}>" if marks.cc_label else f"<@{marks.person_user_id}>"


def targets_of(params: dict) -> list[str]:
    """The conversations a send addresses, in order: `channel`, `channel_id`, `channels` (a comma-separated string or a list)."""
    out: list[str] = []
    for k in CHANNEL_KEYS:
        v = params.get(k)
        items = v.split(",") if isinstance(v, str) else (v if isinstance(v, list) else [])
        out.extend(x.strip() for x in items if isinstance(x, str) and x.strip() and x.strip() not in out)
    return out


def cc_applies(method: str, params: dict, marks: Marks) -> bool:
    """A bot-voice send to others under a profile with `cc_person: true` — never to the person's own conversations
    (any addressed conversation that is the person's: their bot DM or their user id, as the profile records them)."""
    if not (marks.cc_person and marks.person_user_id):
        return False
    if method not in CC_BLOCKS_METHODS and method not in SUFFIX_METHODS:
        return False
    return not (set(targets_of(params)) & marks.person_channels)


def mentions_person(params: dict, marks: Marks) -> bool:
    """The caller's text (or blocks) already mentions the person — a mention subscribes them; no cc on top.
    Also what makes a bot `chat.update` idempotent when the new text carries the old cc line."""
    needle = re.compile(re.escape(f"<@{marks.person_user_id}") + r"(?=[|>])")     # `<@U…>` and `<@U…|name>`, never a longer id
    for key in ("text", "initial_comment"):
        if isinstance(params.get(key), str) and needle.search(params[key]):
            return True
    blocks = params.get("blocks")
    return isinstance(blocks, list) and bool(needle.search(json.dumps(blocks, ensure_ascii=False)))


def maybe_inject_cc(method: str, params: dict, marks: Marks, *, cc: bool | None = None) -> dict:
    """Bot voice (0.8.0): `cc <@person>` as the last line of a send to others.

    Plain `text` gets a text suffix (the message stays a plain message — a bot edit with `text` alone keeps
    working); a send that carries `blocks` (the caller's, or the auto-split) gets a trailing section block;
    the suffix methods (an upload's `initial_comment`, a /me message) a text suffix. A send that already
    mentions the person is left alone. `cc=False` is the core's word that the person already follows the
    conversation (a thread reply, an edit of a message without the cc — core.cc_decision, 0.8.1): no cc then;
    `None` / `True` leave the base rule to decide."""
    if cc is False or not cc_applies(method, params, marks) or mentions_person(params, marks):
        return params
    line = cc_text(marks)
    params = dict(params)
    if method in CC_BLOCKS_METHODS:
        blocks = params.get("blocks")
        if blocks:
            if len(blocks) < MAX_BLOCKS:                   # a full message keeps its 50 blocks; no cc rather than invalid_blocks
                params["blocks"] = list(blocks) + [{"type": "section", "text": {"type": "mrkdwn", "text": line}}]
        elif isinstance(params.get("text"), str) and params["text"]:
            params["text"] = f"{params['text']}\n\n{line}"
        return params
    key = SUFFIX_METHODS[method]
    if isinstance(params.get(key), str) and params[key]:
        params[key] = f"{params[key]}\n\n{line}"
    return params


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


def inject_attribution_block(params: dict, marks: Marks) -> dict:
    """Append the :blue_heart: context block; plain `text` is wrapped (auto-split) first."""
    block = attribution_block(marks)
    params = dict(params)
    existing_blocks = params.get("blocks", [])
    if not existing_blocks and "text" in params:
        existing_blocks = split_text_into_sections(params["text"])
    params["blocks"] = list(existing_blocks) + [block]
    return params


def inject_attribution_suffix(params: dict, key: str, marks: Marks) -> dict:
    params = dict(params)
    params[key] = f"{params[key]}{attribution_suffix(marks)}"
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


def maybe_inject_attribution(method: str, params: dict, marks: Marks) -> dict:
    """Method-aware attribution for person-voice recipient-facing sends (whitelist)."""
    if method in BLOCKS_METHODS:
        return inject_attribution_block(params, marks)
    if method in SUFFIX_METHODS:
        key = SUFFIX_METHODS[method]
        if params.get(key):
            return inject_attribution_suffix(params, key, marks)
    return params


def shape_send(method: str, params: dict, as_bot: bool, marks, *, cc: bool | None = None) -> dict:
    """Final params for a send: attribution (person voice), or bot auto-split and the cc (bot voice).

    `marks` is `Marks.from_profile(profile)` (or, pre-0.8.0, the bot user id alone — `Marks.of`); `cc` is the
    core's decision for a thread reply or an edit (`core.cc_decision`), None for the base rule.
    `files.uploadV2`: the door-level `attribution` option is stripped; in block mode the
    comment leaves the upload (it becomes the composite's child post, see core)."""
    marks = Marks.of(marks)
    if method == UPLOAD_METHOD:
        block = upload_block_mode(params, as_bot)
        params = {k: v for k, v in params.items() if k != UPLOAD_ATTRIBUTION_PARAM}
        if block:
            params = {k: v for k, v in params.items() if k != "initial_comment"}
            return params
    if not as_bot:
        return maybe_inject_attribution(method, params, marks)
    if method in BOT_SPLIT_METHODS:
        params = maybe_split_bot_text(params)
    return maybe_inject_cc(method, params, marks, cc=cc)


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
