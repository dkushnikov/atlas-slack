"""Format Slack API responses into human-readable text per output-contract.md.

See ../../docs/output-contract.md for the contract this module implements.
The public API is `format_response(method, resp)` and `format_error(code, detail)`.
"""

import json
from datetime import datetime, timezone
from typing import Callable

# === Error hints (§10) ===
ERROR_HINTS: dict[str, str] = {
    "channel_not_found": "DM channel IDs are per-viewer; try conversations.open from the correct token side.",
    "not_in_channel": "Atlas isn't a member; invite via conversations.invite or post as user.",
    "missing_scope": "Token lacks the scope; check auth.test for granted scopes.",
    "cannot_dm_bot": "Target is a bot account — DMs not supported.",
    "is_archived": "Channel is archived; unarchive first or pick another.",
    "invalid_blocks": "Per-block limits exceeded — section text caps at 3000 chars; split into multiple sections or pre-check with blocks.validate.",
}

# === List size caps per shape (§7) ===
LIST_CAPS: dict[str, int] = {
    "messages": 200,
    "channels": 100,
    "users": 50,
    "members": 50,
    "files": 20,
    "items": 50,
    "pins": 50,
    "reminders": 1000,
    "usergroups": 50,
}

LABEL_CAP = 200


# === Public API ===

def format_error(error_code: str, detail: str = "") -> str:
    """Format an API error. If `detail` is empty, look up hint catalogue."""
    msg = f"Error: {error_code}"
    if detail:
        msg += f" — {detail}"
    elif error_code in ERROR_HINTS:
        msg += f" — {ERROR_HINTS[error_code]}"
    return msg


def format_response(method: str, resp: dict) -> str:
    """Dispatch a Slack API response to its shape handler per output-contract.md.

    Order of operations:
      1. If ok=False → format_error with hint lookup.
      2. Extract warnings + cursor from response_metadata before stripping.
      3. If method is empty-success or cleaned dict is empty → 'Done ✓'.
      4. Otherwise dispatch by method, falling back to shape inference, then JSON.
      5. Prepend warning line if any.
    """
    if not resp.get("ok", True):
        return format_error(resp.get("error", "unknown_error"))

    cursor = (resp.get("response_metadata") or {}).get("next_cursor") or ""
    warnings = _extract_warnings(resp)
    cleaned = {
        k: v for k, v in resp.items()
        if k not in ("ok", "response_metadata", "warning")
    }

    if not cleaned or method in _EMPTY_SUCCESS_METHODS:
        body = "Done ✓"
    else:
        handler = _DISPATCH.get(method) or _infer_handler(cleaned)
        body = handler(method, cleaned, cursor)

    if warnings:
        return f"⚠️ {warnings}\n{body}"
    return body


def _extract_warnings(resp: dict) -> str:
    """Slack `warning` field — single string or list joined by '; '."""
    w = resp.get("warning")
    if not w:
        return ""
    if isinstance(w, list):
        return "; ".join(str(x) for x in w)
    return str(w)


# === Field formatters ===

def _label(s, cap: int = LABEL_CAP) -> str:
    """Label class — cap with '...' suffix per §6."""
    if not isinstance(s, str):
        s = str(s)
    if len(s) > cap:
        return s[:cap] + "..."
    return s


def _human_size(n) -> str:
    """Human-readable byte count."""
    try:
        n = int(n)
    except (ValueError, TypeError):
        return str(n)
    if n < 1024:
        return f"{n}B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f}KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f}MB"
    return f"{n / 1024 ** 3:.1f}GB"


def _ts_to_date(ts) -> str:
    """Convert Slack ts (epoch float/string) or unix int to YYYY-MM-DD UTC."""
    try:
        if isinstance(ts, str):
            ts = float(ts)
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError):
        return str(ts)


def _aggregate_reactions(reactions) -> str:
    """Render reactions list as ':emoji_name:N :emoji_name:N' aggregate per §4.2.

    Empty string when no reactions. Slack returns name as 'thumbsup', not 👍 — we
    keep ':name:' form because mapping name→glyph would require a full emoji table.
    Visual rendering is the consumer's concern; we preserve unambiguous names.
    """
    if not reactions:
        return ""
    parts = []
    for r in reactions:
        name = r.get("name", "")
        count = r.get("count", 0)
        if not name or not count:
            continue
        parts.append(f":{name}:{count}")
    out = " ".join(parts)
    if len(out) > LABEL_CAP:
        out = out[:LABEL_CAP] + "..."
    return out


# === Message extras — attachments & file stubs (§4.2b) ===

# Per-message render caps. Bound pathological messages (bot spam with dozens of
# attachments), not real usage — shares and unfurls rarely exceed 2-3.
ATTACHMENT_RENDER_CAP = 5
FILE_RENDER_CAP = 10


def _render_attachment(att: dict) -> list[str]:
    """One attachment as an indented block: kind, author/title/link, text, footer.

    Message shares (`is_msg_unfurl`) carry the quoted message in `text` — that is
    payload, never truncated. Link unfurls and legacy bot attachments render the
    same way; Slack caps unfurl text server-side.
    """
    kind = "shared message" if att.get("is_msg_unfurl") else "attachment"
    head = f"  ↳ [{kind}]"
    author = att.get("author_name") or att.get("author_subname") or att.get("service_name") or ""
    if author:
        head += f" <{_label(author)}>"
    title = att.get("title") or ""
    if title and title != author:
        head += f" {_label(title)}"
    link = att.get("title_link") or att.get("from_url") or ""
    if link:
        head += f" {link}"
    lines = [head]

    text = att.get("text") or att.get("fallback") or ""
    if text:
        lines.extend(f"    {t}" for t in str(text).split("\n"))

    footer = att.get("footer") or ""
    f_ts = att.get("ts")
    if footer or f_ts:
        bits = [b for b in (str(footer) if footer else "", _ts_to_date(f_ts) if f_ts else "") if b]
        lines.append(f"    — {', '.join(bits)}")
    return lines


def _render_file_stub(f: dict) -> str:
    """One file as a stub line — id first (feeds slack_download for the bytes)."""
    fid = f.get("id", "?")
    name = _label(f.get("name") or f.get("title") or "?")
    bits = []
    ft = f.get("filetype") or f.get("mimetype") or ""
    if ft:
        bits.append(str(ft))
    size = f.get("size")
    if size:
        bits.append(_human_size(size))
    detail = f" ({', '.join(bits)})" if bits else ""
    return f"  📎 file {fid}: {name}{detail}"


def _message_extras(item: dict) -> list[str]:
    """Attachments + file stubs carried by a message dict (§4.2b)."""
    lines: list[str] = []
    atts = [a for a in (item.get("attachments") or []) if isinstance(a, dict)]
    for att in atts[:ATTACHMENT_RENDER_CAP]:
        lines.extend(_render_attachment(att))
    if len(atts) > ATTACHMENT_RENDER_CAP:
        lines.append(f"  ... and {len(atts) - ATTACHMENT_RENDER_CAP} more attachments")
    files = [f for f in (item.get("files") or []) if isinstance(f, dict)]
    for f in files[:FILE_RENDER_CAP]:
        lines.append(_render_file_stub(f))
    if len(files) > FILE_RENDER_CAP:
        lines.append(f"  ... and {len(files) - FILE_RENDER_CAP} more files")
    return lines


# === Shape handlers ===

def _h_single_message(method: str, c: dict, cursor: str) -> str:
    """§4.1 — chat.postMessage / update / scheduleMessage / postEphemeral."""
    msg = c.get("message")
    if not isinstance(msg, dict):
        return _h_unknown(method, c, cursor)

    ts = msg.get("ts", "")
    text = msg.get("text", "") or ""
    thread_ts = msg.get("thread_ts")
    channel = c.get("channel", "")

    header = f"Message (ts={ts})"
    if thread_ts and thread_ts != ts:
        header += f" thread_ts={thread_ts}"
    header += ":"

    lines = [header, text]
    if channel:
        lines.append(f"channel: {channel}")
    return "\n".join(lines)


def _h_message_list(method: str, c: dict, cursor: str) -> str:
    """§4.2 — conversations.history / replies / search.messages."""
    items = c.get("messages") or []
    # search.messages nests the list: {"messages": {"total": N, "matches": [...]}}
    if isinstance(items, dict):
        items = items.get("matches") or []
    if not items:
        return "No messages found."

    cap = LIST_CAPS["messages"]
    header = f"{len(items)} messages"
    if cursor:
        header += f" next_cursor={cursor}"
    header += ":"
    lines = [header]

    for item in items[:cap]:
        if not isinstance(item, dict):
            lines.append(f"  {item}")
            continue
        ts = item.get("ts", "")
        user = item.get("user") or item.get("bot_id") or item.get("username") or ""
        text = item.get("text", "") or ""
        thread_ts = item.get("thread_ts")
        reply_count = item.get("reply_count")
        reactions = item.get("reactions") or []

        meta = []
        if thread_ts and thread_ts != ts:
            meta.append(f"thread_ts={thread_ts}")
        elif reply_count:
            meta.append(f"replies={reply_count}")
        agg = _aggregate_reactions(reactions)
        if agg:
            meta.append(agg)
        meta_str = (" " + " ".join(meta)) if meta else ""

        lines.append(f"  [{ts}] <{user}>{meta_str}")
        lines.append(f"  {text}")
        lines.extend(_message_extras(item))

    if len(items) > cap:
        lines.append(f"  ... and {len(items) - cap} more")
    return "\n".join(lines)


def _h_channel_info(method: str, c: dict, cursor: str) -> str:
    """§4.3 — conversations.info / create / open."""
    ch = c.get("channel")
    if isinstance(ch, str):
        return f"Channel: {ch}"
    if not isinstance(ch, dict):
        return _h_unknown(method, c, cursor)

    name = _label(ch.get("name", "?"))
    cid = ch.get("id", "?")
    flags = []
    if ch.get("is_private"):
        flags.append("private")
    if ch.get("is_archived"):
        flags.append("archived")
    if ch.get("is_general"):
        flags.append("general")
    flag_str = (" " + " ".join(flags)) if flags else ""

    lines = [f"Channel: #{name} (ID: {cid}){flag_str}"]
    if ch.get("num_members") is not None:
        lines.append(f"  members: {ch['num_members']}")
    topic = (ch.get("topic") or {}).get("value")
    if topic:
        lines.append(f"  topic: {topic}")
    purpose = (ch.get("purpose") or {}).get("value")
    if purpose:
        lines.append(f"  purpose: {purpose}")
    if ch.get("created"):
        lines.append(f"  created: {_ts_to_date(ch['created'])}")
    return "\n".join(lines)


def _h_channel_list(method: str, c: dict, cursor: str) -> str:
    """§4.4 — conversations.list / members."""
    items = c.get("channels") or c.get("members") or []
    is_members = "channels" not in c
    if not items:
        return "No channels found." if not is_members else "No members found."

    # conversations.members returns list of user-ID strings, not dicts
    if items and isinstance(items[0], str):
        cap = LIST_CAPS["members"]
        header = f"{len(items)} members"
        if cursor:
            header += f" next_cursor={cursor}"
        header += ":"
        lines = [header]
        for uid in items[:cap]:
            lines.append(f"  {uid}")
        if len(items) > cap:
            lines.append(f"  ... and {len(items) - cap} more")
        return "\n".join(lines)

    cap = LIST_CAPS["channels"]
    header = f"{len(items)} channels"
    if cursor:
        header += f" next_cursor={cursor}"
    header += ":"
    lines = [header]
    for item in items[:cap]:
        if not isinstance(item, dict):
            lines.append(f"  {item}")
            continue
        cid = item.get("id", "?")
        name = _label(item.get("name", "?"))
        flags = []
        if item.get("is_private"):
            flags.append("private")
        if item.get("is_archived"):
            flags.append("archived")
        flag_str = (" " + " ".join(flags)) if flags else ""
        members = item.get("num_members")
        member_str = f" ({members})" if members is not None else ""
        lines.append(f"  {cid:14s} #{name}{flag_str}{member_str}")
    if len(items) > cap:
        lines.append(f"  ... and {len(items) - cap} more")
    return "\n".join(lines)


def _h_user_info(method: str, c: dict, cursor: str) -> str:
    """§4.5 — users.info / users.profile.get."""
    if "user" in c and isinstance(c["user"], dict):
        u = c["user"]
        p = u.get("profile") or {}
        return _render_user(u, p)
    if "profile" in c and isinstance(c["profile"], dict):
        p = c["profile"]
        return _render_user({}, p)
    return _h_unknown(method, c, cursor)


def _render_user(u: dict, p: dict) -> str:
    """Shared rendering for user dict + profile dict."""
    uid = u.get("id", "")
    real_name = _label(u.get("real_name") or p.get("real_name") or u.get("name") or "?")
    flags = []
    if u.get("is_bot"):
        flags.append("bot")
    if u.get("deleted"):
        flags.append("deleted")
    flag_str = (" " + " ".join(flags)) if flags else ""

    head = f"User: {real_name}"
    if uid:
        head += f" (ID: {uid})"
    head += flag_str
    lines = [head]

    for key in ("display_name", "email", "title"):
        val = p.get(key)
        if val:
            lines.append(f"  {key}: {val}")
    if u.get("tz"):
        lines.append(f"  tz: {u['tz']}")
    status_emoji = p.get("status_emoji", "")
    status_text = p.get("status_text", "")
    if status_emoji or status_text:
        lines.append(f"  status: {status_emoji} {status_text}".rstrip())
    image = p.get("image_72") or p.get("image_512")
    if image:
        lines.append(f"  image: {image}")
    return "\n".join(lines)


def _h_profile_set(method: str, c: dict, cursor: str) -> str:
    """§4.6 — users.profile.set diff."""
    p = c.get("profile") or {}
    if not isinstance(p, dict):
        return _h_unknown(method, c, cursor)
    return f"Profile updated: {p.get('status_emoji', '')} {p.get('status_text', '')}".rstrip()


def _h_user_list(method: str, c: dict, cursor: str) -> str:
    """§4.7 — users.list."""
    items = c.get("members") or c.get("users") or []
    if not items:
        return "No users found."

    cap = LIST_CAPS["users"]
    header = f"{len(items)} users"
    if cursor:
        header += f" next_cursor={cursor}"
    header += ":"
    lines = [header]
    for item in items[:cap]:
        if not isinstance(item, dict):
            lines.append(f"  {item}")
            continue
        uid = item.get("id", "?")
        p = item.get("profile") or {}
        real_name = _label(item.get("real_name") or p.get("real_name") or item.get("name") or "?")
        email = p.get("email", "")
        email_str = f" <{email}>" if email else ""
        lines.append(f"  {uid:14s} {real_name}{email_str}")
    if len(items) > cap:
        lines.append(f"  ... and {len(items) - cap} more")
    return "\n".join(lines)


def _h_file_single(method: str, c: dict, cursor: str) -> str:
    """§4.8 — files.info / files.uploadV2 (single)."""
    f = c.get("file") if isinstance(c.get("file"), dict) else None
    files = c.get("files") or []
    if f is None and files and isinstance(files[0], dict) and len(files) == 1:
        f = files[0]
    if not isinstance(f, dict):
        return _h_file_list(method, c, cursor)

    fid = f.get("id", "?")
    name = _label(f.get("name", "?"))
    permalink = f.get("permalink", "")
    filetype = f.get("filetype", "")
    size = f.get("size")
    title = f.get("title", "")
    created = f.get("created")

    lines = [f"File: {name} (ID: {fid})"]
    if permalink:
        lines.append(f"  permalink: {permalink}")
    type_size = []
    if filetype:
        type_size.append(f"type: {filetype}")
    if size:
        type_size.append(f"size: {_human_size(size)}")
    if type_size:
        lines.append(f"  {', '.join(type_size)}")
    if title and title != name:
        lines.append(f"  title: {title}")
    if created:
        lines.append(f"  created: {_ts_to_date(created)}")
    return "\n".join(lines)


def _h_file_list(method: str, c: dict, cursor: str) -> str:
    """§4.9 — files.list / files.uploadV2 (multi)."""
    items = c.get("files") or []
    if not items:
        return "No files found."

    cap = LIST_CAPS["files"]
    header = f"{len(items)} files"
    if cursor:
        header += f" next_cursor={cursor}"
    header += ":"
    lines = [header]
    for item in items[:cap]:
        if not isinstance(item, dict):
            lines.append(f"  {item}")
            continue
        fid = item.get("id", "?")
        name = _label(item.get("name", "?"))
        permalink = item.get("permalink", "")
        line = f"  {fid:14s} {name}"
        if permalink:
            line += f"  {permalink}"
        lines.append(line)
    if len(items) > cap:
        lines.append(f"  ... and {len(items) - cap} more")
    return "\n".join(lines)


def _h_reminder_single(method: str, c: dict, cursor: str) -> str:
    """§4.10 — reminders.add / reminders.info."""
    r = c.get("reminder")
    if not isinstance(r, dict):
        return _h_unknown(method, c, cursor)
    rid = r.get("id", "?")
    text = r.get("text", "") or ""
    when = r.get("time")
    recurring = bool(r.get("recurring", False))
    complete = bool(r.get("complete_ts"))

    lines = [f"Reminder (ID: {rid}): {text}"]
    if when:
        lines.append(f"  time: {_ts_to_date(when)}")
    lines.append(f"  recurring: {recurring}")
    lines.append(f"  complete: {complete}")
    return "\n".join(lines)


def _h_reminder_list(method: str, c: dict, cursor: str) -> str:
    """§4.10 — reminders.list."""
    items = c.get("reminders") or []
    if not items:
        return "No reminders found."
    cap = LIST_CAPS["reminders"]
    lines = [f"{len(items)} reminders:"]
    for item in items[:cap]:
        if not isinstance(item, dict):
            lines.append(f"  {item}")
            continue
        rid = item.get("id", "?")
        text = _label(item.get("text", "") or "", cap=LABEL_CAP)
        lines.append(f"  {rid:14s} {text}")
    if len(items) > cap:
        lines.append(f"  ... and {len(items) - cap} more")
    return "\n".join(lines)


def _h_usergroup_single(method: str, c: dict, cursor: str) -> str:
    """§4.11 — usergroups.create / usergroups.users.update."""
    g = c.get("usergroup")
    if not isinstance(g, dict):
        return _h_unknown(method, c, cursor)
    gid = g.get("id", "?")
    handle = g.get("handle", "?")
    name = _label(g.get("name", ""))
    user_count = len(g.get("users") or []) or g.get("user_count", 0)
    created = g.get("date_create")

    head = f"Usergroup @{handle} (ID: {gid})"
    if name:
        head += f": {name}"
    lines = [head, f"  users: {user_count} members"]
    if created:
        lines.append(f"  created: {_ts_to_date(created)}")
    return "\n".join(lines)


def _h_usergroup_list(method: str, c: dict, cursor: str) -> str:
    """§4.11 — usergroups.list."""
    items = c.get("usergroups") or []
    if not items:
        return "No usergroups found."
    cap = LIST_CAPS["usergroups"]
    lines = [f"{len(items)} usergroups:"]
    for item in items[:cap]:
        if not isinstance(item, dict):
            lines.append(f"  {item}")
            continue
        gid = item.get("id", "?")
        handle = item.get("handle", "?")
        name = _label(item.get("name", ""))
        users = item.get("users") or []
        lines.append(f"  {gid:14s} @{handle} ({name}, {len(users)} members)")
    if len(items) > cap:
        lines.append(f"  ... and {len(items) - cap} more")
    return "\n".join(lines)


def _h_pin_list(method: str, c: dict, cursor: str) -> str:
    """§4.12 — pins.list / stars.list (both use `items` key)."""
    items = c.get("items") or c.get("pins") or []
    if not items:
        return "No pins found."

    cap = LIST_CAPS["pins"]
    header = f"{len(items)} pins"
    if cursor:
        header += f" next_cursor={cursor}"
    header += ":"
    lines = [header]
    for item in items[:cap]:
        if not isinstance(item, dict):
            lines.append(f"  {item}")
            continue
        msg = item.get("message") if isinstance(item.get("message"), dict) else item
        ts = msg.get("ts", "")
        user = msg.get("user", "")
        text = msg.get("text", "") or ""
        channel = item.get("channel", "")
        head = f"  [{ts}] <{user}>"
        if channel:
            head += f" in {channel}"
        lines.append(head)
        lines.append(f"  {text}")
        lines.extend(_message_extras(msg))
    if len(items) > cap:
        lines.append(f"  ... and {len(items) - cap} more")
    return "\n".join(lines)


def _h_permalink(method: str, c: dict, cursor: str) -> str:
    """§4.13 — chat.getPermalink."""
    return f"Permalink: {c.get('permalink', '')}\n  channel: {c.get('channel', '')}"


def _h_auth(method: str, c: dict, cursor: str) -> str:
    """§4.14 — auth.test (team is string) / team.info (team is dict)."""
    team = c.get("team")
    if isinstance(team, dict):
        lines = [f"Team: {team.get('name', '?')} (ID: {team.get('id', '?')})"]
        if team.get("domain"):
            lines.append(f"  domain: {team['domain']}")
        if team.get("email_domain"):
            lines.append(f"  email_domain: {team['email_domain']}")
        return "\n".join(lines)
    user = c.get("user", "?")
    user_id = c.get("user_id", "?")
    team_id = c.get("team_id", "?")
    url = c.get("url", "")
    head = f"Auth: {user}@{team or '?'} (user_id={user_id}, team_id={team_id})"
    if url:
        return f"{head}\n  url: {url}"
    return head


def _h_unknown(method: str, c: dict, cursor: str) -> str:
    """§4.16 — JSON fallback for unknown shapes.

    Cursor (if present) is re-injected as `_next_cursor` so callers can paginate
    even on unrecognized shapes. If JSON output is large, the right fix is to add
    the shape to the registry — JSON is intentionally permissive.
    """
    out = dict(c)
    if cursor:
        out["_next_cursor"] = cursor
    return json.dumps(out, indent=2, ensure_ascii=False, default=str)


# === Method → handler dispatch (§5) ===

_DISPATCH: dict[str, Callable[[str, dict, str], str]] = {
    # 4.1 single message
    "chat.postMessage": _h_single_message,
    "chat.update": _h_single_message,
    "chat.scheduleMessage": _h_single_message,
    "chat.postEphemeral": _h_single_message,
    # 4.2 message list
    "conversations.history": _h_message_list,
    "conversations.replies": _h_message_list,
    "search.messages": _h_message_list,
    # 4.3 channel info
    "conversations.info": _h_channel_info,
    "conversations.create": _h_channel_info,
    "conversations.open": _h_channel_info,
    "conversations.setPurpose": _h_channel_info,
    "conversations.setTopic": _h_channel_info,
    "conversations.rename": _h_channel_info,
    # 4.4 channel list
    "conversations.list": _h_channel_list,
    "conversations.members": _h_channel_list,
    # 4.5 user info
    "users.info": _h_user_info,
    "users.profile.get": _h_user_info,
    # 4.6 profile set
    "users.profile.set": _h_profile_set,
    # 4.7 user list
    "users.list": _h_user_list,
    # 4.8 file single
    "files.info": _h_file_single,
    "files.uploadV2": _h_file_single,
    # 4.9 file list
    "files.list": _h_file_list,
    # 4.10 reminder
    "reminders.add": _h_reminder_single,
    "reminders.info": _h_reminder_single,
    "reminders.list": _h_reminder_list,
    # 4.11 usergroup
    "usergroups.create": _h_usergroup_single,
    "usergroups.users.update": _h_usergroup_single,
    "usergroups.list": _h_usergroup_list,
    # 4.12 pin / star list
    "pins.list": _h_pin_list,
    "stars.list": _h_pin_list,
    # 4.13 permalink
    "chat.getPermalink": _h_permalink,
    # 4.14 auth / team
    "auth.test": _h_auth,
    "team.info": _h_auth,
}

# §4.15 — methods that return empty success (Done ✓) regardless of payload
_EMPTY_SUCCESS_METHODS: frozenset[str] = frozenset({
    "reactions.add", "reactions.remove",
    "pins.add", "pins.remove",
    "stars.add", "stars.remove",
    "dnd.setSnooze", "dnd.endSnooze",
    "bookmarks.add", "bookmarks.remove",
    "conversations.archive", "conversations.unarchive",
    "conversations.invite", "conversations.kick", "conversations.leave",
    "chat.delete",
    "chat.meMessage",
    "reminders.delete", "reminders.complete",
})


def _infer_handler(c: dict) -> Callable[[str, dict, str], str]:
    """Shape inference fallback when method not in dispatch (§4.16 trigger)."""
    if "message" in c and isinstance(c["message"], dict):
        return _h_single_message
    if "messages" in c and isinstance(c["messages"], list):
        return _h_message_list
    if "permalink" in c and "channel" in c and isinstance(c.get("channel"), str):
        return _h_permalink
    if "channel" in c:
        return _h_channel_info
    if "channels" in c:
        return _h_channel_list
    if "user" in c or "profile" in c:
        return _h_user_info
    if "members" in c and isinstance(c["members"], list):
        return _h_user_list
    if "file" in c and isinstance(c["file"], dict):
        return _h_file_single
    if "files" in c and isinstance(c["files"], list):
        return _h_file_list
    if "reminder" in c:
        return _h_reminder_single
    if "reminders" in c:
        return _h_reminder_list
    if "usergroup" in c:
        return _h_usergroup_single
    if "usergroups" in c:
        return _h_usergroup_list
    if "items" in c or "pins" in c:
        return _h_pin_list
    return _h_unknown
