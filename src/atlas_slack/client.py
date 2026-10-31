"""Slack WebClient factory with dual-token support (person + bot).

Token env var NAMES come from the active profile (`token_sources`; default
`SLACK_USER_TOKEN` / `SLACK_BOT_TOKEN`). The bot user id used for attribution
lives in the profile (`bot_user_id`, or `ATLAS_BOT_USER_ID` for the built-in
profile) — no workspace literal in code since the v2 core slice.
"""

import os

from slack_sdk import WebClient

from atlas_slack.profile import load_profile

_clients: dict[str, WebClient] = {}


def get_client(as_bot: bool = False, retries: bool = True) -> WebClient:
    """Return a cached WebClient.

    Args:
        as_bot: If True, use the bot token (bot voice). If False, use the
                person's user token (reads + person-voice sends with attribution).
        retries: the SDK's default ConnectionErrorRetryHandler repeats a request once
                after a connection error — for a write that is a possible duplicate
                (Slack may have accepted the first attempt; Codex review 2026-10-31).
                The core passes `retries=False`: one HTTP attempt, an ambiguous
                failure is `unknown` for reconciliation. Reads keep the retry.
    """
    token_var = load_profile().token_var(as_bot)
    key = token_var if retries else f"{token_var}:no-retry"
    if key not in _clients:
        token = os.environ.get(token_var)
        if not token:
            raise ValueError(
                f"{token_var} environment variable is required. "
                "Set it in the shell environment (e.g. ~/.zshenv)."
            )
        _clients[key] = WebClient(token=token) if retries else WebClient(token=token, retry_handlers=[])
    return _clients[key]
