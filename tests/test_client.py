"""Tests for Slack client initialization."""

import os
from unittest.mock import patch

import pytest


def _reset_clients():
    from atlas_slack import client as mod
    mod._clients.clear()


def test_get_client_default_returns_user_token():
    with patch.dict(os.environ, {"SLACK_USER_TOKEN": "xoxp-test-user"}):
        _reset_clients()
        from atlas_slack.client import get_client
        client = get_client()
        assert client.token == "xoxp-test-user"


def test_get_client_as_bot_returns_bot_token():
    with patch.dict(os.environ, {"SLACK_USER_TOKEN": "xoxp-test-user", "SLACK_BOT_TOKEN": "xoxb-test-bot"}):
        _reset_clients()
        from atlas_slack.client import get_client
        client = get_client(as_bot=True)
        assert client.token == "xoxb-test-bot"


def test_get_client_explicit_user_mode():
    with patch.dict(os.environ, {"SLACK_USER_TOKEN": "xoxp-u", "SLACK_BOT_TOKEN": "xoxb-b"}):
        _reset_clients()
        from atlas_slack.client import get_client
        client = get_client(as_bot=False)
        assert client.token == "xoxp-u"


def test_get_client_missing_user_token_raises():
    with patch.dict(os.environ, {}, clear=True):
        _reset_clients()
        from atlas_slack.client import get_client
        with pytest.raises(ValueError, match="SLACK_USER_TOKEN"):
            get_client()


def test_get_client_missing_bot_token_raises():
    with patch.dict(os.environ, {"SLACK_USER_TOKEN": "xoxp-u"}, clear=True):
        _reset_clients()
        from atlas_slack.client import get_client
        with pytest.raises(ValueError, match="SLACK_BOT_TOKEN"):
            get_client(as_bot=True)


def test_get_client_is_singleton_per_mode():
    with patch.dict(os.environ, {"SLACK_USER_TOKEN": "xoxp-u", "SLACK_BOT_TOKEN": "xoxb-b"}):
        _reset_clients()
        from atlas_slack.client import get_client
        u1 = get_client(as_bot=False)
        u2 = get_client(as_bot=False)
        b1 = get_client(as_bot=True)
        b2 = get_client(as_bot=True)
        assert u1 is u2
        assert b1 is b2
        assert u1 is not b1
