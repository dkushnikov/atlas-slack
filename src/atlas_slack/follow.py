"""Does the person already follow a thread? — the bot-voice cc on a thread reply (0.8.1).

A top-level bot-voice send to others carries `cc <@person>` (shaping.maybe_inject_cc). A reply inside a
thread carries it only while the person does not follow that thread yet — otherwise every reply would
mention them again (live check 2026-11-05: the parent's cc had subscribed the person, the reply's cc was
noise). Slack subscribes a person to a thread when they wrote the parent, replied in it, or were mentioned
in it; an earlier cc of this door in the same thread counts too. The journal answers the last one without a
Slack call; one read of the parent (`conversations.replies`, `limit: 1`) answers the other three. When the
parent cannot be read, the cc goes: a missed subscription costs more than one extra mention (the person's
rule, 2026-11-05). `chat.update` does not come here — an edit keeps what the original had (core).
"""

from __future__ import annotations

import json
import re
from typing import Callable

Reader = Callable[[str, dict, bool], dict]

# `<@U…>` and `<@U…|name>` — the id ends at `|` or `>`, so a longer id never matches a shorter person id
MENTION_RE = re.compile(r"<@([A-Z0-9]+)(?=[|>])")


def mentions(msg: dict) -> set[str]:
    """The user ids a Slack message mentions — in its text, its blocks and its attachments."""
    parts = [msg.get("text") if isinstance(msg.get("text"), str) else ""]
    for key in ("blocks", "attachments"):
        if isinstance(msg.get(key), list) and msg[key]:
            parts.append(json.dumps(msg[key], ensure_ascii=False))
    return set(MENTION_RE.findall(" ".join(parts)))


def payload_mentions(payload: dict) -> set[str]:
    """The user ids a journalled send mentioned on the wire: `final_params` text, initial_comment and blocks."""
    final = payload.get("final_params") or payload.get("params") or {}
    text = " ".join(v for k in ("text", "initial_comment") if isinstance((v := final.get(k)), str))
    return mentions({"text": text, "blocks": final.get("blocks")})


def follows_thread(reader: Reader, journal, channel: str | None, thread_ts: str, as_bot: bool,
                   person_id: str) -> bool | None:
    """True — the person follows the thread; False — not that the parent shows; None — the parent could not be read.

    Check 4 first (the journal: an earlier ok bot send of this door into the thread that mentioned the person — no
    Slack call), then checks 1–3 from one read of the parent: its author, its `reply_users`, its mentions."""
    if journal is not None:
        try:
            if any(person_id in payload_mentions(p) for p in journal.thread_sends(channel, thread_ts)):
                return True
        except Exception:  # noqa: BLE001 — the journal is the cheap check; the read decides
            pass
    try:
        data = reader("conversations.replies", {"channel": channel, "ts": thread_ts, "limit": 1, "inclusive": True}, as_bot)
    except Exception:  # noqa: BLE001 — a read failure is a None, never raised into the send
        return None
    if not isinstance(data, dict) or not data.get("ok", True):
        return None
    parent = next((m for m in (data.get("messages") or []) if isinstance(m, dict) and m.get("ts") == thread_ts), None)
    if parent is None:
        return None
    if parent.get("user") == person_id:
        return True
    if person_id in (parent.get("reply_users") or []):
        return True
    if person_id in mentions(parent):
        return True
    return False
