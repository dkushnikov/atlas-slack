#!/usr/bin/env python3
"""Paginate Slack conversations.list, print id<TAB>name<TAB>visibility for all channels.

Respects Slack rate-limits (Retry-After header). Output is stable-sorted by channel
name. Useful for batch-populating a channel registry (e.g., the business-logic
.local.md that sits next to the atlas-slack skill).

Usage:
  python3 scripts/list-channels.py                        # all channels
  python3 scripts/list-channels.py | grep -E "^C.*\tfoo_" # filter by name prefix
  python3 scripts/list-channels.py > all-channels.tsv     # capture for batch match

Requires SLACK_USER_TOKEN in env (loaded from ~/.zshenv).
"""
import os
import sys
import time
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

client = WebClient(token=os.environ["SLACK_USER_TOKEN"])
all_channels = []
cursor = None
while True:
    kwargs = {
        "types": "public_channel,private_channel",
        "limit": 200,
        "exclude_archived": True,
    }
    if cursor:
        kwargs["cursor"] = cursor
    try:
        resp = client.conversations_list(**kwargs)
    except SlackApiError as e:
        if e.response.get("error") == "ratelimited":
            wait = int(e.response.headers.get("Retry-After", "60"))
            print(f"[ratelimited; sleep {wait}s then retry]", file=sys.stderr)
            time.sleep(wait + 1)
            continue
        raise
    all_channels.extend(resp["channels"])
    cursor = resp.get("response_metadata", {}).get("next_cursor")
    if not cursor:
        break

for ch in sorted(all_channels, key=lambda c: c["name"]):
    private = "private" if ch.get("is_private") else "public"
    print(f"{ch['id']}\t{ch['name']}\t{private}")

print(f"--- total: {len(all_channels)} channels ---", file=sys.stderr)
