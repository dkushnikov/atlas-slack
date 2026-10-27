"""Tests for slack_download and the v1.2 tool surface.

The 13 convenience wrappers (slack_react, slack_upload, …) were removed in
v1.2 — 0 calls in 86 days; every one was a thin alias for slack_api calls.
Their tests went with them. slack_download stays: file bytes require an
authenticated HTTP GET the JSON gateway cannot express.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError

from atlas_slack.tools import slack_download


# --- v1.2 tool surface: exactly slack_api + slack_download ---


def test_server_exposes_exactly_two_tools():
    from atlas_slack.gateway import mcp
    listed = mcp.list_tools()
    if asyncio.iscoroutine(listed):
        listed = asyncio.run(listed)
    assert sorted(t.name for t in listed) == ["slack_api", "slack_download"]


# --- slack_download: files.info + authenticated HTTP GET, no live calls ---


def _http_response(status_code=200, content=b"", content_type="application/octet-stream"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.headers = {"content-type": content_type}
    return resp


def _file_info(fid="F123", name="report.pdf", mimetype="application/pdf", **extra):
    file = {
        "id": fid,
        "name": name,
        "mimetype": mimetype,
        "url_private_download": f"https://files.slack.com/files-pri/T1-{fid}/download/{name}",
    }
    file.update(extra)
    return {"ok": True, "file": file}


def test_download_happy_path(mock_webclient, tmp_path):
    mock_webclient.token = "xoxp-test-user"
    mock_webclient.files_info.return_value = _file_info()
    pdf_bytes = b"%PDF-1.4 fake"
    with patch("atlas_slack.tools.httpx.get", return_value=_http_response(content=pdf_bytes, content_type="application/pdf")) as get:
        result = slack_download("F123", out_dir=str(tmp_path))

    mock_webclient._tools_get_client.assert_called_with(as_bot=False)
    mock_webclient.files_info.assert_called_once_with(file="F123")
    kwargs = get.call_args.kwargs
    assert get.call_args.args[0] == "https://files.slack.com/files-pri/T1-F123/download/report.pdf"
    assert kwargs["headers"]["Authorization"] == "Bearer xoxp-test-user"
    assert kwargs["follow_redirects"] is True

    saved = tmp_path / "F123--report.pdf"
    assert saved.read_bytes() == pdf_bytes
    assert str(saved) in result
    assert "application/pdf" in result
    assert "13B" in result


def test_download_html_login_page_detected(mock_webclient, tmp_path):
    """Slack serves an HTML login page when the token can't access the file — must not save it."""
    mock_webclient.files_info.return_value = _file_info()
    login_page = _http_response(content=b"<html>Sign in to Slack</html>", content_type="text/html; charset=utf-8")
    with patch("atlas_slack.tools.httpx.get", return_value=login_page):
        with pytest.raises(ToolError) as exc_info:
            slack_download("F123", out_dir=str(tmp_path))

    assert "access_denied" in str(exc_info.value)
    assert "token" in str(exc_info.value)
    assert list(tmp_path.iterdir()) == []


def test_download_html_file_itself_is_allowed(mock_webclient, tmp_path):
    """An actual .html file legitimately comes back as text/html — not a login page."""
    mock_webclient.files_info.return_value = _file_info(name="page.html", mimetype="text/html", filetype="html")
    with patch("atlas_slack.tools.httpx.get", return_value=_http_response(content=b"<html>doc</html>", content_type="text/html")):
        result = slack_download("F123", out_dir=str(tmp_path))

    assert "access_denied" not in result
    assert (tmp_path / "F123--page.html").read_bytes() == b"<html>doc</html>"


def test_download_file_not_found(mock_webclient, tmp_path):
    mock_webclient.files_info.return_value = {"ok": False, "error": "file_not_found"}
    with patch("atlas_slack.tools.httpx.get") as get:
        with pytest.raises(ToolError, match="file_not_found"):
            slack_download("F404", out_dir=str(tmp_path))

    get.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_download_http_error(mock_webclient, tmp_path):
    mock_webclient.files_info.return_value = _file_info()
    with patch("atlas_slack.tools.httpx.get", return_value=_http_response(status_code=403)):
        with pytest.raises(ToolError) as exc_info:
            slack_download("F123", out_dir=str(tmp_path))

    assert "http_error" in str(exc_info.value)
    assert "403" in str(exc_info.value)
    assert list(tmp_path.iterdir()) == []


def test_download_url_private_fallback_and_bot_token(mock_webclient, tmp_path):
    """No url_private_download → fall back to url_private; as_bot=True routes to bot client."""
    mock_webclient.token = "xoxb-test-bot"
    mock_webclient.files_info.return_value = {
        "ok": True,
        "file": {"id": "F9", "name": "notes.txt", "mimetype": "text/plain",
                 "url_private": "https://files.slack.com/files-pri/T1-F9/notes.txt"},
    }
    with patch("atlas_slack.tools.httpx.get", return_value=_http_response(content=b"hi", content_type="text/plain")) as get:
        result = slack_download("F9", out_dir=str(tmp_path), as_bot=True)

    mock_webclient._tools_get_client.assert_called_with(as_bot=True)
    assert get.call_args.args[0] == "https://files.slack.com/files-pri/T1-F9/notes.txt"
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer xoxb-test-bot"
    assert (tmp_path / "F9--notes.txt").read_bytes() == b"hi"
    assert "notes.txt" in result


def test_download_sanitizes_filename(mock_webclient, tmp_path):
    """Path separators and shell-unsafe chars in the original name must not escape out_dir."""
    mock_webclient.files_info.return_value = _file_info(name="../../etc/my report (v2).pdf")
    with patch("atlas_slack.tools.httpx.get", return_value=_http_response(content=b"x", content_type="application/pdf")):
        slack_download("F123", out_dir=str(tmp_path))

    (saved,) = list(tmp_path.iterdir())
    assert saved.name == "F123--my_report_v2_.pdf"


# === v1.3 — slack_download(vtt=True) ===

def test_download_vtt_transcript(mock_webclient, tmp_path):
    mock_webclient.files_info.return_value = {"ok": True, "file": {
        "id": "F1", "name": "video.mp4", "mimetype": "video/mp4",
        "url_private_download": "https://files.slack.com/files-pri/T1-F1/download/video.mp4",
        "transcription": {"status": "complete", "locale": "en-US"},
        "vtt": "https://files.slack.com/files-tmb/T1-F1-x/file.vtt?_xcb=1",
    }}
    with patch("atlas_slack.tools.httpx.get", return_value=_http_response(content=b"WEBVTT\n\n00:00.000 --> 00:01.000\nhi\n", content_type="text/vtt")) as get:
        out = slack_download("F1", out_dir=str(tmp_path), vtt=True)
    assert get.call_args.args[0].endswith("file.vtt?_xcb=1")
    assert "Downloaded transcript: video.vtt (ID: F1)" in out
    assert (tmp_path / "F1--video.vtt").read_bytes().startswith(b"WEBVTT")
    assert "text/vtt" in out


def test_download_vtt_absent_is_structural(mock_webclient, tmp_path):
    mock_webclient.files_info.return_value = {"ok": True, "file": {
        "id": "F1", "name": "clip.mp4", "url_private": "https://files.slack.com/x",
        "transcription": {"status": "none"},
    }}
    with patch("atlas_slack.tools.httpx.get") as get:
        with pytest.raises(ToolError, match="no_transcript.*status: none"):
            slack_download("F1", out_dir=str(tmp_path), vtt=True)
    get.assert_not_called()
