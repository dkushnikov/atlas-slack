"""Host profile — which workspace, which identities, where policy and journal live (A7).

Machine-local, per host, no secrets: `~/.config/atlas-slack/<ctx>.yaml`, chosen
by env `ATLAS_SLACK_PROFILE=<ctx>` (default `default`).

No file and no `ATLAS_SLACK_PROFILE` → the **built-in profile**, derived from
the environment exactly as the 0.4.0 plugin used it (`SLACK_USER_TOKEN`,
`SLACK_BOT_TOKEN`, `ATLAS_BOT_USER_ID`). Every verdict carries
`profile_source: builtin|file`.

`ATLAS_SLACK_PROFILE` names a profile whose file is missing or invalid →
the profile is *broken*: reads continue, writes refuse (`profile_missing` /
`profile_invalid`). Silently falling back to the built-in env would attribute a
send to whatever tokens happen to be in the shell — the cross-workspace
mis-attribution Strategy п. 17 forbids.

Test/relocation overrides: `ATLAS_SLACK_CONFIG_DIR` (profile dir),
`ATLAS_SLACK_DATA_DIR` (journal root).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PROFILE = "default"
DEFAULT_TOKEN_SOURCES = {"user": "SLACK_USER_TOKEN", "bot": "SLACK_BOT_TOKEN"}
KNOWN_ADAPTERS = {"cc-hook"}
# Claude Code permission modes in which a hook's `ask` reaches a human: documented for
# default/acceptEdits/plan, and for auto ("a hook's ask also forces a permission prompt
# in auto mode … it can't approve the call silently", hooks reference 2026-10-27).
# bypassPermissions / dontAsk / anything unknown → the hook denies instead of asking.
PERMISSION_MODES = ("default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions")
DEFAULT_DECISION_MODES = ["default", "acceptEdits", "plan", "auto"]


def _env(name: str) -> str | None:
    """Env value, treating an unexpanded `${VAR}` (left by .mcp.json for an unset var) as unset."""
    v = os.environ.get(name)
    if not v or v.startswith("${"):
        return None
    return v

_FIELDS = {
    "workspace_id", "person_user_id", "bot_user_id", "person_bot_dm_id", "token_sources",
    "policy_path", "policy_sha256", "journal_dir", "decision_adapters", "freeze",
    "decision_fallback", "decision_permission_modes", "language", "names", "resolve_names",
    "ledger_dir",
}
LANGUAGES = ("en", "ru")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SECRET_RE = re.compile(r"^xox[abpre]-|^xapp-")


def config_dir() -> Path:
    return Path(_env("ATLAS_SLACK_CONFIG_DIR") or "~/.config/atlas-slack").expanduser()


def data_dir() -> Path:
    return Path(_env("ATLAS_SLACK_DATA_DIR") or "~/.local/share/atlas-slack").expanduser()


@dataclass
class Profile:
    name: str
    source: str                      # builtin | file
    workspace_id: str | None = None
    person_user_id: str | None = None
    bot_user_id: str | None = None
    person_bot_dm_id: str | None = None
    token_sources: dict = field(default_factory=lambda: dict(DEFAULT_TOKEN_SOURCES))
    policy_path: str | None = None
    policy_sha256: str | None = None
    journal_dir: str = ""
    ledger_dir: str | None = None    # the /comms ledger folder; env ATLAS_SLACK_LEDGER_DIR overrides (0.5.2)
    decision_adapters: list = field(default_factory=lambda: ["cc-hook"])
    freeze: bool = False
    decision_fallback: bool = False  # pre-registered decision row instead of an updatedInput token
    decision_permission_modes: list = field(default_factory=lambda: list(DEFAULT_DECISION_MODES))
    language: str = "en"             # approval prompt language
    names: dict = field(default_factory=dict)   # id → display label override (e.g. "Ann", "DM Ann↔Atlas")
    resolve_names: bool = True       # resolve ids via conversations.info / users.info (reads) for the prompt
    errors: list = field(default_factory=list)   # non-empty → broken: writes refuse
    path: str | None = None

    @property
    def broken(self) -> bool:
        return bool(self.errors)

    def token_var(self, as_bot: bool) -> str:
        return self.token_sources.get("bot" if as_bot else "user") or \
            DEFAULT_TOKEN_SOURCES["bot" if as_bot else "user"]

    def has_token(self, as_bot: bool) -> bool:
        """Presence only — the value is never read into anything but the client."""
        return bool(os.environ.get(self.token_var(as_bot)))

    def summary(self) -> dict:
        return {
            "name": self.name, "source": self.source, "workspace_id": self.workspace_id,
            "person_user_id": self.person_user_id, "bot_user_id": self.bot_user_id,
            "person_bot_dm_id": self.person_bot_dm_id, "token_sources": dict(self.token_sources),
            "policy_path": self.policy_path, "policy_sha256": self.policy_sha256,
            "journal_dir": self.journal_dir, "ledger_dir": self.ledger_dir,
            "decision_adapters": list(self.decision_adapters),
            "freeze": self.freeze, "decision_fallback": self.decision_fallback,
            "decision_permission_modes": list(self.decision_permission_modes),
            "language": self.language, "names": dict(self.names), "resolve_names": self.resolve_names,
            "errors": list(self.errors),
        }


def _builtin(name: str) -> Profile:
    return Profile(
        name=name, source="builtin",
        workspace_id=_env("ATLAS_SLACK_WORKSPACE_ID"),
        person_user_id=_env("ATLAS_SLACK_PERSON_USER_ID"),
        bot_user_id=_env("ATLAS_BOT_USER_ID"),
        person_bot_dm_id=_env("ATLAS_SLACK_PERSON_BOT_DM_ID"),
        journal_dir=str(data_dir() / name),
    )


def _validate(raw: dict, prof: Profile) -> None:
    errs = prof.errors
    unknown = set(raw) - _FIELDS
    if unknown:
        errs.append(f"unknown profile keys: {sorted(unknown)}")
    for k in ("workspace_id", "person_user_id", "bot_user_id", "person_bot_dm_id",
              "policy_path", "policy_sha256", "journal_dir", "ledger_dir"):
        v = raw.get(k)
        if v is None:
            continue
        if not isinstance(v, str) or not v.strip():
            errs.append(f"{k} must be a non-empty string")
            continue
        if _SECRET_RE.search(v):
            errs.append(f"{k} looks like a Slack token — profiles hold no secrets")
            continue
        setattr(prof, k, v.strip())
    if prof.policy_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", prof.policy_sha256):
        errs.append("policy_sha256 must be 64 lowercase hex chars")
    ts = raw.get("token_sources")
    if ts is not None:
        if not isinstance(ts, dict) or not set(ts) <= {"user", "bot"} or \
                not all(isinstance(v, str) and re.fullmatch(r"[A-Z_][A-Z0-9_]*", v) for v in ts.values()):
            errs.append("token_sources must map user/bot to env var NAMES (e.g. SLACK_BOT_TOKEN)")
        else:
            prof.token_sources = {**DEFAULT_TOKEN_SOURCES, **ts}
    da = raw.get("decision_adapters")
    if da is not None:
        if not isinstance(da, list) or not all(isinstance(a, str) for a in da):
            errs.append("decision_adapters must be a list of adapter names")
        else:
            bad = [a for a in da if a not in KNOWN_ADAPTERS]
            if bad:
                errs.append(f"unknown decision adapters: {bad} (known: {sorted(KNOWN_ADAPTERS)})")
            prof.decision_adapters = [a for a in da if a in KNOWN_ADAPTERS]
    pm = raw.get("decision_permission_modes")
    if pm is not None:
        if not isinstance(pm, list) or not all(m in PERMISSION_MODES for m in pm):
            errs.append(f"decision_permission_modes must be a list of {list(PERMISSION_MODES)}")
        else:
            prof.decision_permission_modes = list(pm)
    lang = raw.get("language")
    if lang is not None:
        if lang not in LANGUAGES:
            errs.append(f"language must be one of {list(LANGUAGES)}")
        else:
            prof.language = lang
    nm = raw.get("names")
    if nm is not None:
        if not isinstance(nm, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in nm.items()):
            errs.append("names must map ids to display labels (strings)")
        else:
            prof.names = dict(nm)
    for k in ("freeze", "decision_fallback", "resolve_names"):
        if k in raw:
            if not isinstance(raw[k], bool):
                errs.append(f"{k} must be true/false")
            else:
                setattr(prof, k, raw[k])


def load_profile(name: str | None = None) -> Profile:
    """Resolve the active profile. Never raises — problems land in `.errors`."""
    explicit = name or _env("ATLAS_SLACK_PROFILE")
    pname = explicit or DEFAULT_PROFILE
    if not _NAME_RE.match(pname):
        p = _builtin(DEFAULT_PROFILE)
        p.name = pname
        p.errors.append(f"invalid profile name {pname!r}")
        return p
    path = config_dir() / f"{pname}.yaml"
    if not path.exists():
        prof = _builtin(pname)
        if explicit and pname != DEFAULT_PROFILE:
            prof.errors.append(f"profile_missing: {path} does not exist (profile {pname!r} selected by ATLAS_SLACK_PROFILE or --profile)")
        return prof
    prof = Profile(name=pname, source="file", journal_dir=str(data_dir() / pname), path=str(path))
    try:
        import yaml
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:  # yaml error, permissions
        prof.errors.append(f"profile_invalid: cannot parse {path}: {type(e).__name__}: {e}")
        return prof
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        prof.errors.append(f"profile_invalid: {path} must be a mapping")
        return prof
    _validate(raw, prof)
    if prof.policy_path:
        prof.policy_path = str(Path(prof.policy_path).expanduser())
    prof.journal_dir = str(Path(prof.journal_dir).expanduser())
    return prof
