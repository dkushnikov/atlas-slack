"""Shared test fixtures."""

import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_env():
    """Ensure both Slack tokens are set for all tests."""
    with patch.dict(os.environ, {"SLACK_USER_TOKEN": "xoxp-test-user", "SLACK_BOT_TOKEN": "xoxb-test-bot"}):
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
    def fake_get_client(as_bot: bool = False):
        return mock
    with patch("atlas_slack.gateway.get_client", side_effect=fake_get_client) as g_gw, \
         patch("atlas_slack.tools.get_client", side_effect=fake_get_client) as g_tools:
        mock._gateway_get_client = g_gw
        mock._tools_get_client = g_tools
        yield mock
