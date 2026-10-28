"""Display names for the approval prompt — ids → "#channel", "DM владелец↔Atlas", "Atlas".

Resolution order per id: profile `names` override → cache (`<journal_dir>/names.json`,
TTL 24 h) → one live read (`conversations.info` / `users.info`, reads — row 0)
under the token of the send's voice, then the other token → fallback to the id
with "(unresolved)". Failures are never cached, so the next prompt tries again.

Live reads are skipped when the profile says `resolve_names: false` or the env
has `ATLAS_SLACK_RESOLVE_NAMES=0` (tests set it — no network in level 1).
Each live read gets a 3 s timeout: a slow Slack must not stall the prompt.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

CACHE_TTL_S = 24 * 3600
TIMEOUT_S = 3

# client_factory(as_bot) -> object with .api_call(method, params=...) returning a dict-like
ClientFactory = Callable[[bool], object]


def _default_factory(profile) -> ClientFactory:
    def make(as_bot: bool):
        from slack_sdk import WebClient
        token = os.environ.get(profile.token_var(as_bot))
        if not token:
            raise ValueError(f"{profile.token_var(as_bot)} not set")
        return WebClient(token=token, timeout=TIMEOUT_S)
    return make


def _data(resp) -> dict:
    if isinstance(resp, dict):
        return resp
    return getattr(resp, "data", None) or {}


class Names:
    def __init__(self, profile, client_factory: ClientFactory | None = None, now: float | None = None):
        self.profile = profile
        self.path = Path(profile.journal_dir).expanduser() / "names.json"
        self.now = time.time() if now is None else now
        self.live = profile.resolve_names and os.environ.get("ATLAS_SLACK_RESOLVE_NAMES", "1") != "0"
        self.factory = client_factory or _default_factory(profile)
        self._clients: dict = {}
        self._cache = self._load()
        self._dirty = False

    # ------------------------------------------------------------- cache
    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self) -> None:
        if not self._dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._cache, ensure_ascii=False, sort_keys=True))
            os.replace(tmp, self.path)
            self._dirty = False
        except OSError:
            pass

    def _cached(self, key: str) -> dict | None:
        e = self._cache.get(key)
        if isinstance(e, dict) and self.now - float(e.get("at", 0)) < CACHE_TTL_S:
            return e
        return None

    def _put(self, key: str, info: dict) -> dict:
        info = {**info, "at": self.now}
        self._cache[key] = info
        self._dirty = True
        return info

    # -------------------------------------------------------------- live
    def _read(self, method: str, params: dict, prefer_bot: bool) -> dict | None:
        if not self.live:
            return None
        from atlas_slack.core import record_call
        for as_bot in (prefer_bot, not prefer_bot):
            t0 = time.monotonic()
            try:
                if as_bot not in self._clients:
                    self._clients[as_bot] = self.factory(as_bot)
                data = _data(self._clients[as_bot].api_call(method, params=params))
            except ValueError:          # this voice has no token on the host — try the other
                continue
            except Exception:           # timeout / network: stop resolving for this prompt
                record_call(self.profile, method, "error", as_bot=as_bot, caller="names",
                            error_code="exception", latency_ms=int((time.monotonic() - t0) * 1000))
                self.live = False
                return None
            record_call(self.profile, method, "ok" if data.get("ok") else "error", as_bot=as_bot, caller="names",
                        error_code=None if data.get("ok") else data.get("error"),
                        latency_ms=int((time.monotonic() - t0) * 1000))
            if data.get("ok"):
                return data
        return None

    def user(self, uid: str | None, prefer_bot: bool = False) -> str | None:
        """Display name of a user id, or None when unknown."""
        if not uid:
            return None
        if uid in self.profile.names:
            return self.profile.names[uid]
        e = self._cached(f"user:{uid}")
        if e is None:
            data = self._read("users.info", {"user": uid}, prefer_bot)
            if data is None:
                return None
            u = data.get("user") or {}
            prof = u.get("profile") or {}
            name = prof.get("display_name") or prof.get("real_name") or u.get("real_name") or u.get("name")
            if not name:
                return None
            e = self._put(f"user:{uid}", {"name": name})
        return e.get("name")

    def channel(self, cid: str | None, as_bot: bool, declared_users: list | None = None) -> tuple[str, bool]:
        """(label, resolved) for a channel id."""
        if not cid:
            return ("?", False)
        if cid in self.profile.names:
            return (self.profile.names[cid], True)
        if cid == self.profile.person_bot_dm_id:
            person = self.user(self.profile.person_user_id, prefer_bot=as_bot)
            bot = self.user(self.profile.bot_user_id, prefer_bot=as_bot)
            if person and bot:
                return (f"DM {person}↔{bot}", True)
        e = self._cached(f"chan:{cid}")
        if e is None:
            data = self._read("conversations.info", {"channel": cid}, as_bot)
            if data is None:
                return (f"{cid} (unresolved)", False)
            c = data.get("channel") or {}
            kind = "im" if c.get("is_im") else "mpim" if c.get("is_mpim") else "channel"
            e = self._put(f"chan:{cid}", {"kind": kind, "name": c.get("name") or "", "user": c.get("user") or ""})
        kind = e.get("kind")
        if kind == "im":
            other = self.user(e.get("user"), prefer_bot=as_bot)
            if e.get("user") == self.profile.person_user_id and as_bot:
                bot = self.user(self.profile.bot_user_id, prefer_bot=True)
                if other and bot:
                    return (f"DM {other}↔{bot}", True)
            return (f"DM {other}" if other else f"DM {e.get('user') or cid}", bool(other))
        if kind == "mpim":
            names = [self.user(u, prefer_bot=as_bot) or u for u in (declared_users or [])]
            return ((f"group DM {', '.join(names)}" if names else f"group DM {e.get('name') or cid}"), True)
        return (f"#{e['name']}" if e.get("name") else f"{cid} (unresolved)", bool(e.get("name")))
