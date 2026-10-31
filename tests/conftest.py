"""Shared test fixtures."""

import os
from unittest.mock import MagicMock, patch

import pytest

# Test-only bot user id. The code has no workspace literal since the v2 core
# slice (Strategy п. 17); tests set the id explicitly through the env the
# built-in profile reads.
TEST_BOT_USER_ID = "U0BOTTEST01"


@pytest.fixture(autouse=True)
def no_live_slack(monkeypatch):
    """Level 1 never talks to Slack: any real WebClient.api_call in-process fails the test."""
    if os.environ.get("SLACK_E2E") == "1":
        yield
        return
    from slack_sdk import WebClient

    def _blocked(self, api_method, *a, **k):
        raise RuntimeError(f"live Slack call in a level-1 test: {api_method}")

    monkeypatch.setattr(WebClient, "api_call", _blocked)
    yield


@pytest.fixture(autouse=True)
def isolated_door_dirs(tmp_path, monkeypatch):
    """Every test gets its own profile dir and journal root — never ~/.config or ~/.local."""
    monkeypatch.setenv("ATLAS_SLACK_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("ATLAS_SLACK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("ATLAS_SLACK_PROFILE", raising=False)
    # Approval-prompt name resolution makes live reads — never in level-1 tests.
    monkeypatch.setenv("ATLAS_SLACK_RESOLVE_NAMES", "0")
    # The identity check at the first core write asks Slack `auth.test` — never in level 1; the
    # identity tests unset this and answer auth.test themselves.
    monkeypatch.setenv("ATLAS_SLACK_IDENTITY_CHECK", "0")
    from atlas_slack import identity, journal
    journal.reset_cache()
    identity.reset_cache()
    yield tmp_path
    journal.reset_cache()
    identity.reset_cache()


@pytest.fixture(autouse=True)
def mock_env():
    """Ensure both Slack tokens are set for unit/smoke tests.

    When SLACK_E2E=1 is set, the fixture passes through — real tokens from the
    environment are used, allowing live Slack API calls in test_e2e.py.
    """
    if os.environ.get("SLACK_E2E") == "1":
        from atlas_slack import client as mod
        mod._clients.clear()
        yield
        mod._clients.clear()
        return
    with patch.dict(os.environ, {"SLACK_USER_TOKEN": "xoxp-test-user", "SLACK_BOT_TOKEN": "xoxb-test-bot",
                                 "ATLAS_BOT_USER_ID": TEST_BOT_USER_ID}):
        from atlas_slack import client as mod
        mod._clients.clear()
        yield
        mod._clients.clear()


@pytest.fixture
def mock_webclient():
    """Return a mocked WebClient and patch get_client everywhere it's imported.

    The patched get_client accepts as_bot but returns the same mock — tests
    that need to distinguish modes assert on get_client call args directly.
    """
    mock = MagicMock()
    def fake_get_client(as_bot: bool = False, **_kw):
        return mock
    with patch("atlas_slack.gateway.get_client", side_effect=fake_get_client) as g_gw, \
         patch("atlas_slack.tools.get_client", side_effect=fake_get_client) as g_tools:
        mock._gateway_get_client = g_gw
        mock._tools_get_client = g_tools
        yield mock
