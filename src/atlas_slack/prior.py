"""The message behind a `chat.update` — read before the edit, journalled as «was → becomes».

An edit changes what people already read, so the door records the prior text verbatim on
the op row (A4: payload stored verbatim) and shows it in the approval prompt. Edits inherit
the voice of the original (A3 note: «гейт проверяет автора оригинала, не правки»): the
original's author must be the identity behind the token — the bot user for `as_bot=True`,
the person for `as_bot=False`. Slack enforces the same (`cant_update_message`); the door
refuses earlier, with the reason, and never journals an intent it knows will fail.

Reading is best effort: no read (no token, timeout, `resolve_names: false`) → the prompt
says «⚠ не прочитано» and the row carries `prior_error`; the author check is then Slack's.

`reader(method, params, as_bot) -> dict` is a transport: the core passes its send transport,
the hook a short-timeout client (names.py style).
"""

from __future__ import annotations

from typing import Callable

Reader = Callable[[str, dict, bool], dict]

PRIOR_TEXT_MAX = 20_000     # journalled verbatim up to this; Slack's own cap is 40 000 chars


def fetch_prior(reader: Reader, channel: str, ts: str, as_bot: bool) -> dict:
    """`{ts, user, bot_id, text, thread_ts}` of the message at `ts`, or `{error: …}`.

    `conversations.replies` with the message's own `ts` returns that message first whether it
    is a thread parent or a reply (history would miss a reply)."""
    try:
        data = reader("conversations.replies", {"channel": channel, "ts": ts, "limit": 1, "inclusive": True,
                                                "include_all_metadata": True}, as_bot)
    except Exception as e:  # noqa: BLE001 — a read failure is reported, never raised into the send
        return {"error": f"{type(e).__name__}: {e}"[:200]}
    if not isinstance(data, dict) or not data.get("ok", True):
        return {"error": (data or {}).get("error") or "read failed"}
    msgs = data.get("messages") or []
    msg = next((m for m in msgs if isinstance(m, dict) and m.get("ts") == ts), None)
    if msg is None:
        return {"error": "message_not_found"}
    text = msg.get("text") if isinstance(msg.get("text"), str) else ""
    out = {"ts": ts, "user": msg.get("user"), "bot_id": msg.get("bot_id"), "text": text[:PRIOR_TEXT_MAX]}
    if isinstance(msg.get("metadata"), dict) and msg["metadata"]:
        out["metadata"] = msg["metadata"]          # an update must not overwrite another app's metadata
    if len(text) > PRIOR_TEXT_MAX:
        out["text_truncated"] = len(text)
    if msg.get("thread_ts") and msg.get("thread_ts") != ts:
        out["thread_ts"] = msg["thread_ts"]
    return out


def author_problem(prior: dict | None, as_bot: bool, profile) -> str | None:
    """Why the voice may not edit this message, or None (unknown author → None: Slack decides)."""
    if not prior or prior.get("error"):
        return None
    author = prior.get("user")
    if as_bot:
        expected = profile.bot_user_id
        if expected and author and author != expected:
            return f"the message was posted by {author}, not by the bot ({expected}) — an edit keeps the original's voice"
        if not prior.get("bot_id") and author and author != expected:
            return f"the message was posted by {author}, not by the bot — an edit keeps the original's voice"
        return None
    expected = profile.person_user_id
    if expected and author and author != expected:
        return f"the message was posted by {author}, not by you ({expected}) — an edit keeps the original's voice"
    if author and profile.bot_user_id and author == profile.bot_user_id:
        return (f"the message was posted by the bot ({author}), not by you — an edit keeps the original's voice; "
                "edit it with as_bot=True")
    if prior.get("bot_id") and not author:
        # a bot post with no `user` is the app's own voice; a user-token post keeps `user` = the person
        # (whether Slack also stamps bot_id/app_id on it is a live-check item, 2026-10-31)
        return (f"the message was posted by a bot ({prior['bot_id']}), not by you — an edit keeps the original's voice; "
                "edit it with as_bot=True if it is the Atlas bot's")
    return None


def one_line(text: str, cap: int = 400) -> str:
    """Whitespace-collapsed preview for a prompt line."""
    s = " ".join((text or "").split())
    return s if len(s) <= cap else s[:cap] + f"… ({len(s)} chars)"
