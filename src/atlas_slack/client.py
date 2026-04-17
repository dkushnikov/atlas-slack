"""Slack WebClient factory with dual-token support (user + bot)."""

import os

from slack_sdk import WebClient

# Atlas bot user ID (Acme workspace). Override via env for V2 multi-workspace.
ATLAS_BOT_USER_ID = os.environ.get("ATLAS_BOT_USER_ID", "U0BOTUSR01")

_clients: dict[str, WebClient] = {}


def get_client(as_bot: bool = False) -> WebClient:
    """Return a cached WebClient.

    Args:
        as_bot: If True, use SLACK_BOT_TOKEN (Atlas persona, writes).
                If False, use SLACK_USER_TOKEN (the owner's read-only + attributed writes).
    """
    token_var = "SLACK_BOT_TOKEN" if as_bot else "SLACK_USER_TOKEN"
    if token_var not in _clients:
        token = os.environ.get(token_var)
        if not token:
            raise ValueError(
                f"{token_var} environment variable is required. "
                "Set it in ~/.zshenv."
            )
        _clients[token_var] = WebClient(token=token)
    return _clients[token_var]
