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


def get_client(as_bot: bool = False) -> WebClient:
    """Return a cached WebClient.

    Args:
        as_bot: If True, use the bot token (bot voice). If False, use the
                person's user token (reads + person-voice sends with attribution).
    """
    token_var = load_profile().token_var(as_bot)
    if token_var not in _clients:
        token = os.environ.get(token_var)
        if not token:
            raise ValueError(
                f"{token_var} environment variable is required. "
                "Set it in the shell environment (e.g. ~/.zshenv)."
            )
        _clients[token_var] = WebClient(token=token)
    return _clients[token_var]
