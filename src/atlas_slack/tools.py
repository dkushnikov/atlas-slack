"""slack_download — the one operation slack_api cannot express.

The Slack Web API never serves file bytes through JSON methods; downloading
requires an authenticated HTTP GET of url_private_download. Everything else
goes through the `slack_api` gateway (v1.2 removed the 13 convenience wrappers
— 0 calls in 86 days of usage; each was a thin alias for one or two slack_api
calls, at a per-session context tax for 15 tool schemas).

Token routing: user token by default (the person's file visibility is the superset);
as_bot=True for bot-visible-only files.
"""

import os
import re
from pathlib import Path

import httpx
from fastmcp.exceptions import ToolError

from atlas_slack.gateway import mcp
from atlas_slack.client import get_client
from atlas_slack.formatters import format_error, _human_size


def _call_raw(fn, **kwargs) -> dict:
    """Call a slack_sdk method and return the raw response dict. Raises on exception."""
    resp = fn(**kwargs)
    return resp if isinstance(resp, dict) else (resp.data if hasattr(resp, "data") else {"ok": True})


def _safe_filename(name: str) -> str:
    """Sanitize an original filename for local saving: basename only, unsafe chars → '_'."""
    base = os.path.basename(name.replace("\\", "/"))
    safe = re.sub(r"[^\w.\-]+", "_", base).strip("._")
    return safe or "file"


@mcp.tool()
def slack_download(file_id: str, out_dir: str = "", as_bot: bool = False, vtt: bool = False) -> str:
    """Download a Slack file to local disk.

    Fetches metadata via files.info, then GETs url_private_download (fallback:
    url_private) with the same token. Saves as "{file_id}--{original name}"
    (sanitized). The Slack Web API does not serve file bytes via JSON methods —
    this is the only supported download path; never fall back to raw curl.

    Args:
        file_id: Slack file ID (e.g. 'F0123ABCDEF') — from files.list, files.info,
                 or a message's files attachment.
        out_dir: Directory to save into. Default: ~/Downloads/slack (created if missing).
        as_bot: If True, download with the bot token (file must be visible to Atlas).
                If False (default), use the person's user token (broadest file visibility).
        vtt: If True, download Slack's own transcript of a video/clip (WebVTT) instead
             of the media — files.info shows `transcription: complete` and `vtt: available`
             when one exists. Saved as "{file_id}--{name}.vtt".
    """
    try:
        client = get_client(as_bot=as_bot)
        info = _call_raw(client.files_info, file=file_id)
        if not info.get("ok", True):
            raise ToolError(format_error(info.get("error") or "unknown_error"))
        f = info.get("file") or {}
        if vtt:
            url = f.get("vtt")
            if not url:
                status = (f.get("transcription") or {}).get("status") if isinstance(f.get("transcription"), dict) else None
                raise ToolError(format_error(
                    "no_transcript",
                    f"files.info for {file_id} has no vtt (transcription status: {status or 'none'}) — "
                    "Slack has not transcribed this media; use atlas-transcribe on the downloaded file",
                ))
        else:
            url = f.get("url_private_download") or f.get("url_private")
        if not url:
            raise ToolError(format_error(
                "no_download_url",
                f"files.info for {file_id} returned no url_private_download/url_private",
            ))

        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {client.token}"},
            follow_redirects=True,
            timeout=120.0,
        )
        if resp.status_code != 200:
            raise ToolError(format_error("http_error", f"HTTP {resp.status_code} downloading file {file_id}"))

        content_type = (resp.headers.get("content-type") or "").lower()
        file_is_html = (f.get("mimetype") or "").lower().startswith("text/html") or f.get("filetype") in ("html", "htm")
        if content_type.startswith("text/html") and not file_is_html:
            raise ToolError(format_error(
                "access_denied",
                f"got HTML (Slack login page) instead of file bytes for {file_id} — "
                f"the {'bot' if as_bot else 'user'} token can't access this file; try as_bot={not as_bot}",
            ))

        target_dir = Path(out_dir).expanduser() if out_dir else Path.home() / "Downloads" / "slack"
        target_dir.mkdir(parents=True, exist_ok=True)
        name = f.get("name") or file_id
        if vtt:
            name = f"{Path(name).stem}.vtt"
        path = target_dir / f"{file_id}--{_safe_filename(name)}"
        path.write_bytes(resp.content)

        mimetype = "text/vtt" if vtt else (f.get("mimetype") or content_type or "?")
        return "\n".join([
            f"Downloaded{' transcript' if vtt else ''}: {name} (ID: {file_id})",
            f"  path: {path}",
            f"  type: {mimetype}, size: {_human_size(len(resp.content))}",
        ])
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(format_error("exception", str(e))) from e
