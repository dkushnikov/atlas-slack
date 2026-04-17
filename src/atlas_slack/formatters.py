"""Format Slack API responses into human-readable text."""

import json


def format_error(error_code: str, detail: str = "") -> str:
    """Format an API error into a readable string."""
    msg = f"Error: {error_code}"
    if detail:
        msg += f" — {detail}"
    return msg


def format_response(method: str, resp: dict) -> str:
    """Format a Slack API response dict into readable text.

    Handles common response shapes. Falls back to pretty JSON for unknown shapes.
    """
    if not resp.get("ok", True):
        return format_error(resp.get("error", "unknown_error"))

    # Remove metadata keys for cleaner output
    cleaned = {k: v for k, v in resp.items() if k not in ("ok", "response_metadata")}

    if not cleaned:
        return "Done ✓"

    # Single message
    if "message" in cleaned and isinstance(cleaned["message"], dict):
        msg = cleaned["message"]
        text = msg.get("text", "")
        ts = msg.get("ts", "")
        return f"Message (ts={ts}): {text}"

    # Channel created/info
    if "channel" in cleaned:
        ch = cleaned["channel"]
        if isinstance(ch, dict):
            return f"Channel: #{ch.get('name', '?')} (ID: {ch.get('id', '?')})"
        if isinstance(ch, str):
            return f"Channel: {ch}"

    # Lists (channels, users, members, messages, files, items, reminders, pins)
    for key in ("channels", "members", "users", "messages", "files", "items", "reminders", "pins"):
        if key in cleaned and isinstance(cleaned[key], list):
            items = cleaned[key]
            if not items:
                return f"No {key} found."
            lines = [f"{len(items)} {key}:"]
            for item in items[:50]:
                if isinstance(item, dict):
                    label = item.get("name") or item.get("text") or item.get("id") or str(item)
                    if isinstance(label, str) and len(label) > 80:
                        label = label[:80] + "..."
                    item_id = item.get("id", "")
                    ts = item.get("ts", "")
                    if key == "messages" and ts:
                        user = item.get("user", "")
                        lines.append(f"  [{ts}] <{user}> {label}")
                    else:
                        lines.append(f"  {item_id:14s} {label}")
                else:
                    lines.append(f"  {item}")
            if len(items) > 50:
                lines.append(f"  ... and {len(items) - 50} more")
            return "\n".join(lines)

    # User profile
    if "profile" in cleaned and isinstance(cleaned["profile"], dict):
        p = cleaned["profile"]
        return f"Profile updated: {p.get('status_emoji', '')} {p.get('status_text', '')}"

    # Fallback: pretty JSON
    return json.dumps(cleaned, indent=2, ensure_ascii=False, default=str)
