"""Policy engine — effect registry × sender class × context policy → verdict (A1, A2, A3).

    effective_policy = context policy if present and valid, else built-in strict
    rights           = immutable invariants ∩ capabilities ∩ effective_policy

A *missing* policy file and an *invalid* one are different states: missing →
built-in strict; invalid (bad yaml, schema, sha256 ≠ the profile's pin) →
every write refuses `policy_invalid`, reads continue. No fallback to a
friendlier default.

Verdict = `allow | needs-human | refuse` + reason + row + class + policy
version/digest + `transitional`. The verdict is computed locally and is the
same for every face (MCP hook, MCP server, library `Door`).

Scope: `CORE_METHODS` (rows 1/2 — postMessage, scheduleMessage, update, postEphemeral,
meMessage, files.uploadV2) are decided by the core; reads are row 0 from an explicit
registry; `conversations.open` is split by effect (A12.10). Every other write is
**transitional** — today's gate semantics (ask), flagged in the verdict and in the hook text.

The verdict is local: no network. The two exceptions are file reads for an upload (the
bytes are hashed into the decision binding) and the comm_id first-sighting note in the journal.
The prior text and author of a `chat.update` are read by the faces (core / hook), not here.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from atlas_slack.journal import DEFAULT_STALE_AFTER_S
from atlas_slack.provenance import (
    AUTONOMOUS, CLASSES, CLASS_VOICE, Provenance, ProvenanceError, op_hash, synthesize,
)

# --------------------------------------------------------------------------- registry

# Row 0 — reads by effect, listed explicitly (GATE-003). PII-class admin reads
# (`users.lookupByEmail`) are row 0 for sessions; a job's read scope must name them.
READ_METHODS = frozenset({
    "auth.test", "auth.teams.list",
    "bots.info",
    "blocks.validate",
    "bookmarks.list",
    "canvases.getContent",
    "agents.conversations.listViews", "agents.conversations.getCanvas",
    "chat.getPermalink", "chat.scheduledMessages.list",
    "conversations.history", "conversations.replies", "conversations.info",
    "conversations.list", "conversations.members",
    "dnd.info", "dnd.teamInfo",
    "emoji.list",
    "files.info", "files.list",
    "pins.list",
    "reactions.get", "reactions.list",
    "reminders.info", "reminders.list",
    "search.all", "search.files", "search.messages",
    "stars.list",
    "team.info", "team.profile.get",
    "usergroups.list", "usergroups.users.list",
    "users.conversations", "users.getPresence", "users.identity", "users.info", "users.list",
    "users.lookupByEmail", "users.profile.get",
})

# Row 1 writes decided by the core — policy → provenance → one-time decision → write-ahead journal.
# chat.scheduleMessage joined 2026-10-31 (slice 2, part 1): `post_at` is bound into the decision as
# `scheduled_for`; the message leaves at post_at without a fresh look (migration rule A12.15, until
# Reception) — the prompt says so. Slice 2, part 2 (same day): chat.update (`ts` bound; the message's
# prior text is read and journalled, the original's author must match the voice), files.uploadV2
# (file bytes bound by sha256; `attribution: block` = composite with a child op), chat.postEphemeral
# (`user` is a bound recipient), chat.meMessage.
CORE_METHODS = frozenset({"chat.postMessage", "chat.scheduleMessage", "chat.update", "chat.postEphemeral",
                          "chat.meMessage", "files.uploadV2"})
SCHEDULE_MIN_LEAD_S = 60          # Slack refuses post_at in the past; a minute keeps clock skew out
SCHEDULE_MAX_LEAD_S = 120 * 86400  # Slack: up to 120 days ahead
LOCAL_METHODS = frozenset({"_rights", "_comm_id", "_reconcile"})
_TS_RE = __import__("re").compile(r"\d{1,13}\.\d{1,6}")          # fullmatch: `$` would let a trailing newline through
_USER_ID_RE = __import__("re").compile(r"[UW][A-Z0-9]{2,}")

# Writes the 0.4.0 gate auto-allows by conscious classification (issue #1):
# stream continuation after an asked-for chat.startStream; agent-session chrome.
TRANSITIONAL_ALLOWED_WRITES = frozenset({
    "chat.appendStream", "chat.stopStream", "agents.sessions.setStatus", "agents.sessions.rename",
})

# Never through the door (A3 row 8: app/token management is the Demiurge path).
# auth.revoke matched the old `auth.` read prefix and was AUTO-ALLOWED in 0.4.0.
REFUSED_METHODS = {
    "auth.revoke": "row 8 — revoking the token is app management, not done through the door",
    # slack_sdk 3.41 → 3.46 (2026-10-31, Strategy п. 11): app / workspace management stays outside the door.
    "admin.users.getExpiration": "row 8 — admin API (org-level token), not a door surface",
    "apps.user.connection.requestAuth": "row 8 — app-connection management is the Demiurge path",
    "apps.user.connection.bulkUpdate": "row 8 — app-connection management is the Demiurge path",
}

# Known writes → A3 row, for verdict text and `_rights`; executed on the
# transitional path in this slice.
KNOWN_WRITE_ROWS = {
    **{m: 1 for m in (
        "chat.delete", "chat.deleteScheduledMessage",
        "chat.unfurl", "chat.startStream",
        "files.completeUploadExternal", "files.delete", "files.comments.delete",
        "reminders.add", "conversations.setTopic", "conversations.setPurpose", "conversations.rename",
        "canvases.create", "canvases.edit", "canvases.delete", "views.publish",
        "agents.conversations.setCanvasContent",   # content into the agent conversation (3.45)
    )},
    **{m: 3 for m in ("reactions.add", "reactions.remove", "pins.add", "pins.remove",
                      "bookmarks.add", "bookmarks.edit", "bookmarks.remove")},
    **{m: 4 for m in ("agents.conversations.create", "agents.conversations.archive",       # agent-view structure (3.45)
                      "agents.conversations.setView", "agents.conversations.removeView",
                      "agents.conversations.setCommands", "agents.conversations.setProperties",
                      "conversations.open", "conversations.join", "conversations.leave",
                      "conversations.invite", "conversations.kick", "conversations.create",
                      "conversations.archive", "conversations.unarchive", "conversations.inviteShared",
                      "conversations.acceptSharedInvite", "usergroups.create", "usergroups.update",
                      "usergroups.users.update", "usergroups.disable", "usergroups.enable")},
    **{m: 5 for m in ("users.profile.set", "users.setPresence", "users.setPhoto", "dnd.setSnooze",
                      "dnd.endSnooze", "dnd.endDnd")},
    **{m: 6 for m in ("conversations.mark", "stars.add", "stars.remove", "reminders.complete",
                      "reminders.delete")},
    **{m: 7 for m in ("files.sharedPublicURL", "files.revokePublicURL")},
}

# Invariant (vii): no caller sets the displayed sender identity (GATE-002).
IDENTITY_OVERRIDE_PARAMS = ("username", "icon_emoji", "icon_url")

# Legacy 0.4.0 heuristic — kept ONLY for the transitional path (unregistered names).
_LEGACY_READ_SUFFIXES = (".list", ".info", ".history", ".get", ".read", ".members", ".replies", ".test")
_LEGACY_READ_PREFIXES = ("search.", "auth.", "emoji.", "team.")


# Slack method names: dot-separated camelCase segments, no whitespace.
_METHOD_RE = __import__("re").compile(r"^[a-z][A-Za-z0-9]*(\.[a-z][A-Za-z0-9]*)+$")


def _registry_names() -> frozenset:
    return frozenset(READ_METHODS | CORE_METHODS | LOCAL_METHODS | TRANSITIONAL_ALLOWED_WRITES
                     | set(REFUSED_METHODS) | set(KNOWN_WRITE_ROWS))


_CANON_BY_LOWER = {m.lower(): m for m in _registry_names()}


def noncanonical(method) -> str | None:
    """Why a method name is refused before classification, or None.

    Classification is by exact name; a near-miss spelling ("chat.postMessage ",
    "chat.PostMessage") must never fall through to the transitional path
    (verifier finding 2026-10-28). Registered names in another case or with
    whitespace → refused, naming the canonical spelling; anything that is not
    a syntactically valid method name → refused.
    """
    if not isinstance(method, str) or not method:
        return "method must be a non-empty string"
    if method in LOCAL_METHODS:
        return None
    canon = _CANON_BY_LOWER.get(method.strip().lower())
    if canon is not None and canon != method:
        return f"{method!r} is not the canonical spelling — use {canon!r}"
    if not _METHOD_RE.match(method):
        return f"{method!r} is not a valid Slack method name"
    return None


def legacy_is_read(method: str) -> bool:
    if any(method.startswith(p) for p in _LEGACY_READ_PREFIXES):
        return True
    return any(method.endswith(s) for s in _LEGACY_READ_SUFFIXES)


def open_reason(params: dict) -> str:
    """Ask-text for conversations.open — it CREATES (or reopens) a DM/MPIM (GATE-001, kept verbatim)."""
    if not isinstance(params, dict):
        params = {}
    users = [u.strip() for u in str(params.get("users") or "").split(",") if u.strip()]
    tail = "To find existing DMs use users.conversations (read, auto-allowed)."
    if len(users) > 1:
        return (f"Slack write: conversations.open — creates a group DM (MPIM) "
                f"with {len(users)} users: {', '.join(users)}. {tail}")
    if len(users) == 1:
        return f"Slack write: conversations.open — opens a 1:1 DM with {users[0]}. {tail}"
    channel = params.get("channel")
    if channel:
        return f"Slack write: conversations.open — reopens conversation {channel}."
    return f"Slack write: conversations.open — creates a DM/MPIM. {tail}"


def _truthy(v) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "1")) or v == 1


@dataclass
class Effect:
    row: int | None
    route: str          # read | core | transitional | local | refused
    write: bool
    transitional: bool = False
    note: str = ""


def classify(method: str, params: dict | None) -> Effect:
    params = params if isinstance(params, dict) else {}
    if noncanonical(method):
        return Effect(None, "refused", True, note=noncanonical(method))
    if method in LOCAL_METHODS:
        return Effect(None, "local", False)
    if method in READ_METHODS:
        return Effect(0, "read", False)
    if method == "conversations.open":
        users = [u for u in str(params.get("users") or "").split(",") if u.strip()]
        if _truthy(params.get("prevent_creation")) and len(users) == 1 and not params.get("channel"):
            return Effect(0, "read", False, note="lookup of an existing DM (prevent_creation=true)")
        return Effect(4, "transitional", True, transitional=True, note="creates or reopens a DM/MPIM")
    if method in CORE_METHODS:
        return Effect(1, "core", True)
    if method in REFUSED_METHODS:
        return Effect(8, "refused", True, note=REFUSED_METHODS[method])
    if method in TRANSITIONAL_ALLOWED_WRITES:
        return Effect(KNOWN_WRITE_ROWS.get(method, 1), "transitional", True, transitional=True,
                      note="continuation/chrome write auto-allowed by the 0.4.0 gate")
    if method in KNOWN_WRITE_ROWS:
        return Effect(KNOWN_WRITE_ROWS[method], "transitional", True, transitional=True)
    if legacy_is_read(method):
        return Effect(0, "transitional", False, transitional=True,
                      note="unregistered name matched the legacy read heuristic")
    return Effect(None, "transitional", True, transitional=True, note="unregistered method")


# --------------------------------------------------------------------------- policy

STRICT_MODES = {"A": "human", "B-h": "human", "B-a": "deny", "C1": "deny", "C2": "deny"}
ALLOWED_MODES = {
    "A": {"human", "deny"}, "B-h": {"human", "deny"},
    "B-a": {"stamp", "deny"}, "C1": {"stamp", "deny"}, "C2": {"auto", "deny"},
}
APPROVALS = ("self-send-preferred", "explicit-greenlight", "one-word")
CHANNEL_CLASSES = ("governance", "leadership", "working", "announce", "dm", "external")
STRICT_POLICY = {
    "version": "builtin-strict-1",
    "ratified_by": "atlas-slack (built-in)",
    "ratified_at": None,
    "channels": {},
    "recipients_deny_autonomous": [],
    "recipients_deny": {},
    "grants": [],
    "report_channels": {},
    "defaults": {"unknown_channel": "strictest"},
}
_POLICY_KEYS = set(STRICT_POLICY)
_CHANNEL_KEYS = {"class", "voices", "approval", "modes", "name"}


def _digest_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _strict_digest() -> str:
    import json
    return _digest_bytes(json.dumps(STRICT_POLICY, sort_keys=True).encode())


@dataclass
class PolicyState:
    state: str                       # builtin | file | invalid
    version: str
    digest: str
    data: dict
    error: str = ""
    path: str | None = None
    note: str = ""

    @property
    def valid(self) -> bool:
        return self.state != "invalid"


def builtin_policy(note: str = "") -> PolicyState:
    return PolicyState("builtin", STRICT_POLICY["version"], _strict_digest(), dict(STRICT_POLICY), note=note)


def validate_policy(raw) -> dict:
    """Schema check → normalised policy dict. Raises ValueError with the first problem."""
    if not isinstance(raw, dict):
        raise ValueError("policy must be a mapping")
    unknown = set(raw) - _POLICY_KEYS
    if unknown:
        raise ValueError(f"unknown policy keys: {sorted(unknown)}")
    if raw.get("version") in (None, ""):
        raise ValueError("version is required")
    pol = {**STRICT_POLICY, **raw}
    pol["version"] = str(raw["version"])
    chans = raw.get("channels") or {}
    if not isinstance(chans, dict):
        raise ValueError("channels must map channel id → entry")
    norm = {}
    for cid, e in chans.items():
        if not isinstance(e, dict):
            raise ValueError(f"channels.{cid} must be a mapping")
        bad = set(e) - _CHANNEL_KEYS
        if bad:
            raise ValueError(f"channels.{cid}: unknown keys {sorted(bad)}")
        if e.get("class") is not None and e["class"] not in CHANNEL_CLASSES:
            raise ValueError(f"channels.{cid}.class must be one of {list(CHANNEL_CLASSES)}")
        voices = e.get("voices", ["person", "bot"])
        if not isinstance(voices, list) or not set(voices) <= {"person", "bot"}:
            raise ValueError(f"channels.{cid}.voices must be a list of person/bot")
        if e.get("approval") is not None and e["approval"] not in APPROVALS:
            raise ValueError(f"channels.{cid}.approval must be one of {list(APPROVALS)}")
        modes = e.get("modes") or {}
        if not isinstance(modes, dict):
            raise ValueError(f"channels.{cid}.modes must map class → mode")
        for c, m in modes.items():
            if c not in CLASSES:
                raise ValueError(f"channels.{cid}.modes: unknown class {c!r}")
            if m not in ALLOWED_MODES[c]:
                raise ValueError(f"channels.{cid}.modes.{c}={m!r}: allowed {sorted(ALLOWED_MODES[c])}")
        norm[str(cid)] = {"class": e.get("class"), "voices": list(voices), "approval": e.get("approval"),
                          "modes": {**STRICT_MODES, **modes}, "name": e.get("name")}
    pol["channels"] = norm
    rda = raw.get("recipients_deny_autonomous") or []
    if not isinstance(rda, list) or not all(isinstance(x, str) for x in rda):
        raise ValueError("recipients_deny_autonomous must be a list of ids")
    pol["recipients_deny_autonomous"] = list(rda)
    rd = raw.get("recipients_deny") or {}
    if not isinstance(rd, dict) or not all(k in CLASSES and isinstance(v, list) for k, v in rd.items()):
        raise ValueError("recipients_deny must map class → list of ids")
    pol["recipients_deny"] = {k: list(v) for k, v in rd.items()}
    if not isinstance(raw.get("grants", []) or [], list):
        raise ValueError("grants must be a list (parsed, no semantics yet)")
    if not isinstance(raw.get("report_channels", {}) or {}, dict):
        raise ValueError("report_channels must be a mapping (parsed, no semantics yet)")
    defaults = raw.get("defaults") or {"unknown_channel": "strictest"}
    if not isinstance(defaults, dict) or defaults.get("unknown_channel", "strictest") != "strictest":
        raise ValueError("defaults.unknown_channel supports only 'strictest' in this version")
    pol["defaults"] = {"unknown_channel": "strictest"}
    return pol


def load_policy(profile) -> PolicyState:
    path = profile.policy_path
    if not path:
        return builtin_policy("no context policy configured — built-in strict policy applies")
    p = Path(path)
    if not p.exists():
        if profile.policy_sha256:
            return PolicyState("invalid", "?", "", {}, error=f"policy pinned by sha256 but missing: {p}",
                               path=str(p))
        return builtin_policy(f"policy file missing: {p} — built-in strict policy applies")
    try:
        data = p.read_bytes()
    except OSError as e:
        return PolicyState("invalid", "?", "", {}, error=f"cannot read {p}: {e}", path=str(p))
    digest = _digest_bytes(data)
    if profile.policy_sha256 and digest != profile.policy_sha256:
        return PolicyState("invalid", "?", digest, {}, path=str(p),
                           error=f"sha256 mismatch: file {digest[:12]}… ≠ pinned {profile.policy_sha256[:12]}…")
    try:
        import yaml
        raw = yaml.safe_load(data.decode("utf-8"))
        pol = validate_policy(raw)
    except Exception as e:
        return PolicyState("invalid", "?", digest, {}, error=f"{type(e).__name__}: {e}", path=str(p))
    return PolicyState("file", pol["version"], digest, pol, path=str(p))


# --------------------------------------------------------------------------- capabilities

@dataclass
class Capabilities:
    user_token: bool
    bot_token: bool
    journal: bool | None
    decision_adapters: list
    notary: bool = False
    identity_source: bool = False


def capabilities(profile, journal_ok: bool | None = None) -> Capabilities:
    return Capabilities(
        user_token=profile.has_token(False), bot_token=profile.has_token(True), journal=journal_ok,
        decision_adapters=list(profile.decision_adapters),
    )


# --------------------------------------------------------------------------- verdict

ALLOW, NEEDS_HUMAN, REFUSE = "allow", "needs-human", "refuse"
PREPARED_AT_FUTURE_TOLERANCE_S = 60
BLOCKING_OUTCOMES = ("ok", "pending", "unknown")


@dataclass
class Verdict:
    decision: str
    reason: str
    message: str
    method: str
    row: int | None = None
    cls: str | None = None
    route: str = ""
    transitional: bool = False
    policy_version: str = ""
    policy_digest: str = ""
    policy_state: str = ""
    profile: str = ""
    profile_source: str = ""
    reasons: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    op_hash: str | None = None
    provenance: dict | None = None
    channel: str | None = None
    channel_class: str | None = None
    approval: str | None = None
    mode: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _Ctx:
    method: str
    params: dict
    as_bot: bool
    profile: object
    policy: PolicyState
    effect: Effect
    reasons: list = field(default_factory=list)   # (code, message)
    warnings: list = field(default_factory=list)

    def refuse(self, code: str, msg: str) -> None:
        self.reasons.append((code, msg))


def _base(ctx: _Ctx, decision: str, reason: str, message: str, **kw) -> Verdict:
    return Verdict(
        decision=decision, reason=reason, message=message, method=ctx.method, row=ctx.effect.row,
        route=ctx.effect.route, transitional=ctx.effect.transitional,
        policy_version=ctx.policy.version, policy_digest=ctx.policy.digest, policy_state=ctx.policy.state,
        profile=ctx.profile.name, profile_source=ctx.profile.source,
        reasons=[c for c, _ in ctx.reasons], warnings=list(ctx.warnings), **kw,
    )


def _profile_error_code(profile) -> str:
    first = profile.errors[0] if profile.errors else ""
    return "profile_missing" if first.startswith("profile_missing") else "profile_invalid"


def _check_manifest_read(ctx: _Ctx, manifest: dict) -> None:
    scope = manifest.get("read_scope")
    if scope is None:
        return
    if isinstance(scope, list):
        scope = {"channels": scope}
    chans = set(scope.get("channels") or [])
    extra = set(scope.get("methods") or [])
    if ctx.method.startswith("search."):
        ctx.refuse("read_scope_violation",
                   f"{ctx.method} reads the whole workspace; a read scope cannot post-filter it in this version")
        return
    ch = ctx.params.get("channel")
    if ch is not None:
        if ch not in chans:
            ctx.refuse("read_scope_violation", f"channel {ch} is outside the manifest read_scope")
    elif ctx.method not in extra:
        ctx.refuse("read_scope_violation",
                   f"{ctx.method} has no channel in scope; list it in read_scope.methods to allow it")


def evaluate(method: str, params: dict | None, as_bot: bool, provenance: dict | Provenance | None,
             profile, policy: PolicyState | None = None, *, face: str = "mcp", manifest: dict | None = None,
             journal=None, journal_error: str | None = None, now: float | None = None,
             record_sighting: bool = True) -> Verdict:
    """Compute the verdict. Pure except for the comm_id first-sighting note in the journal."""
    params = dict(params) if isinstance(params, dict) else {}
    policy = policy or load_policy(profile)
    now = time.time() if now is None else now
    eff = classify(method, params)
    ctx = _Ctx(method, params, bool(as_bot), profile, policy, eff)
    if not policy.valid:
        ctx.warnings.append(f"policy invalid: {policy.error}")
    elif policy.note:
        ctx.warnings.append(policy.note)
    if profile.broken:
        ctx.warnings.append("; ".join(profile.errors))

    if eff.route == "local":
        if method in ("_comm_id", "_reconcile"):
            # Minting writes a state row (the comm's first sighting): a frozen or broken profile
            # refuses it like any other write (verifier, 2026-10-31). `_rights` stays read-only.
            # `_reconcile` reads Slack and resolves journal outcomes — a broken profile cannot;
            # a frozen one may (resolving an `unknown` sends nothing).
            if profile.broken:
                return _base(ctx, REFUSE, _profile_error_code(profile),
                             f"profile {profile.name!r} is unusable: {'; '.join(profile.errors)}")
            if profile.freeze and method == "_comm_id":
                return _base(ctx, REFUSE, "frozen", f"profile {profile.name!r} has freeze: true — no comm_id is minted")
        return _base(ctx, ALLOW, "local", f"{method}: local pseudo-method, "
                                          f"{'reads only' if method == '_reconcile' else 'no Slack call'}")
    bad_name = noncanonical(method)
    if bad_name:
        return _base(ctx, REFUSE, "method_noncanonical", bad_name)

    if manifest is not None:
        allowed = manifest.get("allowed_methods")
        if allowed is not None and method not in allowed:
            ctx.refuse("method_not_in_manifest", f"{method} is not in the job manifest's allowed_methods")

    # ---------------------------------------------------------------- reads
    if not eff.write:
        if manifest is not None:
            _check_manifest_read(ctx, manifest)
        if ctx.reasons:
            code, msg = ctx.reasons[0]
            return _base(ctx, REFUSE, code, msg)
        if eff.transitional and face == "library":
            return _base(ctx, REFUSE, "not_in_slice",
                         f"{method}: not in the read registry — the library face runs registered reads only")
        if eff.transitional:
            return _base(ctx, ALLOW, "transitional_read",
                         f"{method}: unregistered name, allowed by the legacy read heuristic (transitional)")
        return _base(ctx, ALLOW, "read", f"{method}: read (row 0)" + (f" — {eff.note}" if eff.note else ""))

    # --------------------------------------------------------------- writes
    present = [k for k in IDENTITY_OVERRIDE_PARAMS if k in params]
    if present:
        ctx.refuse("identity_override",
                   f"{', '.join(present)} would set the displayed sender identity — refused unconditionally "
                   "(invariant vii); drop the parameter, the voice is chosen by as_bot")
    if eff.route == "refused":
        ctx.refuse("method_refused", f"{method}: {eff.note}")
    if profile.broken:
        ctx.refuse(_profile_error_code(profile), f"profile {profile.name!r} is unusable: {'; '.join(profile.errors)}")
    if profile.freeze:
        ctx.refuse("frozen", f"profile {profile.name!r} has freeze: true — every write refuses, reads continue")
    if not policy.valid:
        ctx.refuse("policy_invalid", f"policy is invalid ({policy.error}) — writes refuse until it is fixed")
    if manifest is not None:
        targets = manifest.get("allowed_targets")
        ch = params.get("channel")
        if targets is not None and ch not in targets:
            ctx.refuse("target_not_in_manifest", f"channel {ch} is not in the job manifest's allowed_targets")

    if eff.route == "transitional" or eff.route == "refused":
        if ctx.reasons:
            code, msg = ctx.reasons[0]
            return _base(ctx, REFUSE, code, msg)
        if face == "library":
            return _base(ctx, REFUSE, "not_in_slice",
                         f"{method}: transitional method — the library face executes only the core methods "
                         f"({', '.join(sorted(CORE_METHODS))}) and reads")
        if method in TRANSITIONAL_ALLOWED_WRITES:
            return _base(ctx, ALLOW, "transitional_allowed", f"{method}: {eff.note} (transitional)")
        text = open_reason(params) if method == "conversations.open" else f"Slack write: {method}"
        return _base(ctx, NEEDS_HUMAN, "transitional",
                     f"{text} (transitional — not yet under the v2 core; today's gate semantics)")

    # ------------------------------------------------------------- core (CORE_METHODS)
    return _evaluate_send(ctx, provenance, face=face, manifest=manifest, journal=journal,
                          journal_error=journal_error, now=now, record_sighting=record_sighting)


def _evaluate_send(ctx: _Ctx, provenance, *, face, manifest, journal, journal_error, now,
                   record_sighting) -> Verdict:
    params, profile, policy = ctx.params, ctx.profile, ctx.policy
    voice_of_token = "bot" if ctx.as_bot else "person"
    decision_only = isinstance(provenance, dict) and provenance and set(provenance) <= {"decision"}
    if provenance is None or decision_only:
        # no caller provenance: the hook's compact wire form carries only `decision`
        prov = synthesize(params, ctx.as_bot, profile.name, None)
        if decision_only:
            prov.decision = dict(provenance["decision"]) if isinstance(provenance["decision"], dict) else None
        ctx.warnings.append("provenance missing — migration rule A12.2 (classes A/B-h only): "
                            "the send needs a human decision")
    elif isinstance(provenance, Provenance):
        prov = provenance
    else:
        try:
            prov = Provenance.from_dict(provenance, as_bot=ctx.as_bot)
        except ProvenanceError as e:
            ctx.refuse("provenance_invalid", f"provenance invalid: {e}")
            code, msg = ctx.reasons[0]
            return _base(ctx, REFUSE, code, msg)
    if manifest is not None and manifest.get("cls") and prov.cls != manifest["cls"]:
        ctx.refuse("class_not_in_manifest", f"provenance class {prov.cls} ≠ manifest class {manifest['cls']}")

    channel = params.get("channel")
    if (not isinstance(channel, str) or not channel) and not (ctx.method == "files.uploadV2" and "channels" in params):
        ctx.refuse("missing_params", f"{ctx.method} needs `channel` (id string)")   # `channels` → the upload branch says how
    from atlas_slack import shaping as _shaping
    blocks_problem = _shaping.caller_blocks_problem(params)
    if blocks_problem:
        ctx.refuse("invalid_blocks", blocks_problem)      # a shape Slack would reject — no prompt for it
    if ctx.method == "chat.scheduleMessage":
        post_at = params.get("post_at")
        import math
        try:
            post_at_f = float(post_at) if post_at is not None and not isinstance(post_at, bool) else None
        except (TypeError, ValueError):
            post_at_f = None
        if post_at_f is not None and not math.isfinite(post_at_f):
            post_at_f = None                      # NaN passed both bounds and reached the transport (verifier)
        if post_at_f is None:
            ctx.refuse("missing_params", "chat.scheduleMessage needs `post_at` (finite epoch seconds)")
        elif post_at_f < now + SCHEDULE_MIN_LEAD_S:
            ctx.refuse("schedule_in_past", f"post_at {int(post_at_f)} is not at least {SCHEDULE_MIN_LEAD_S} s ahead — "
                                           "send now with chat.postMessage instead")
        elif post_at_f > now + SCHEDULE_MAX_LEAD_S:
            ctx.refuse("schedule_too_far", f"post_at {int(post_at_f)} is more than 120 days ahead (Slack limit)")
        if post_at_f is not None:
            if prov.scheduled_for is None:
                prov.scheduled_for = post_at_f
            elif abs(prov.scheduled_for - post_at_f) > 1:
                ctx.refuse("schedule_mismatch", f"provenance scheduled_for {int(prov.scheduled_for)} ≠ params.post_at "
                                                f"{int(post_at_f)}")
        ctx.warnings.append("scheduled send — leaves at post_at without a fresh look (migration rule A12.15, "
                            "until Reception)")
    elif ctx.method == "chat.update":
        ts = params.get("ts")
        if not isinstance(ts, str) or not _TS_RE.fullmatch(ts):
            ctx.refuse("missing_params", "chat.update needs `ts` (the message timestamp string, e.g. \"1700000000.123456\")")
        if not (isinstance(params.get("text"), str) and params["text"]) and not params.get("blocks") \
                and not params.get("attachments"):
            ctx.refuse("missing_params", "chat.update needs the new `text` (or `blocks`)")
    elif ctx.method == "chat.postEphemeral":
        user = params.get("user")
        if not isinstance(user, str) or not _USER_ID_RE.fullmatch(user):
            ctx.refuse("missing_params", "chat.postEphemeral needs `user` (the viewer's user id)")
        elif user not in prov.target.get("users", []):
            # the one viewer is the recipient: bound into the decision, checked against deny lists
            prov.target["users"] = [*prov.target.get("users", []), user]
        ctx.warnings.append("ephemeral — visible to one person only, never in history; an unknown outcome "
                            "cannot be reconciled against Slack")
    elif ctx.method == "chat.meMessage":
        if not (isinstance(params.get("text"), str) and params["text"]):
            ctx.refuse("missing_params", "chat.meMessage needs `text`")
    elif ctx.method == "files.uploadV2":
        from atlas_slack import shaping as _shaping
        try:
            prepared = _shaping.prepare_upload(params)
            hashes = _shaping.upload_file_hashes(prepared)
        except _shaping.ShapingError as e:
            ctx.refuse(e.code, e.detail)
            hashes = None
        except OSError as e:
            ctx.refuse("file_not_found", f"cannot read the upload: {e}")
            hashes = None
        if hashes:
            if prov.file_sha256 is None:
                prov.file_sha256 = hashes
            elif prov.file_sha256 != hashes:
                ctx.refuse("file_mismatch", "provenance file_sha256 ≠ the sha256 of the file(s) on disk — "
                                            "the file changed since it was declared")
        if _shaping.upload_block_mode(params, ctx.as_bot):
            ctx.warnings.append("composite op: the upload, then the comment as a separate attributed post "
                                "(child op) — one decision covers both")
    meta = params.get("metadata")
    if isinstance(meta, str):
        try:
            meta = __import__("json").loads(meta)
        except ValueError:
            meta = None
    if "metadata" in params and ctx.method == "chat.scheduleMessage":
        # Slack: a scheduled message with `metadata` is accepted (scheduled_message_id) and then never posts
        ctx.refuse("invalid_arguments", "chat.scheduleMessage with `metadata` is accepted by Slack and never posted "
                                        "(Slack docs bug alert) — drop `metadata`")
    elif isinstance(meta, dict) and meta.get("event_type") == _shaping.METADATA_EVENT_TYPE:
        # the door's own event type is the reconciliation handle: a caller may not forge one (it could make
        # _reconcile resolve another op against this message) nor rewrite an original's via an edit (verifier)
        ctx.refuse("metadata_reserved", f"metadata.event_type {_shaping.METADATA_EVENT_TYPE!r} is the door's own handle — "
                                        "drop it; the door sets it on bot sends itself")
    elif "metadata" in params and ctx.method == "chat.update":
        ctx.warnings.append("caller metadata on an edit replaces the message's existing metadata (the original post's "
                            "handle included); with the new text in place the original post can no longer be reconciled "
                            "from Slack — if its outcome is unknown, resolve it first")
    elif ctx.as_bot and "metadata" in params and ctx.method in _shaping.METADATA_METHODS:
        ctx.warnings.append("caller metadata kept — the op id is not in the message; an unknown outcome reconciles by text")
    if ctx.method == "chat.update" and not prov.comm_id and journal is not None and isinstance(params.get("ts"), str):
        # An edit is a new version of the communication the post belongs to: inherit its comm_id from the
        # journal row behind `ts` (MC live check 2026-10-31: the agent omitted it and the door minted a second
        # comm, losing the ledger ↔ journal link). Before the hash, so the hook and the server bind the same id.
        try:
            origin = journal.find_by_slack_ts(params["ts"], channel if isinstance(channel, str) else None)
        except Exception:  # noqa: BLE001 — a journal hiccup here is not a refusal; the door mints as before
            origin = None
        if origin and origin.get("comm_id"):
            prov.comm_id = origin["comm_id"]
            ctx.warnings.append(f"edit of op {origin['op_id']} — comm_id {origin['comm_id']} inherited from the post")
    t_ch = prov.target.get("channel")
    if t_ch and channel and t_ch != channel:
        ctx.refuse("target_mismatch", f"provenance target {t_ch} ≠ params.channel {channel}")

    if prov.voice != voice_of_token:
        ctx.refuse("voice_mismatch", f"provenance voice {prov.voice!r} ≠ token voice {voice_of_token!r} "
                                     f"(as_bot={ctx.as_bot})")
    elif CLASS_VOICE[prov.cls] != prov.voice:
        ctx.refuse("voice_mismatch", f"class {prov.cls} speaks in the {CLASS_VOICE[prov.cls]} voice, "
                                     f"not {prov.voice}")
    if prov.origin == "prepared-as-human":
        if prov.voice != "person":
            ctx.refuse("prepared_as_human_bot_voice",
                       "prepared-as-human leaves only in the person's voice, on a fresh human decision (A5.1)")
        if prov.ttl_s is None:
            ctx.refuse("ttl_required", "prepared-as-human needs ttl_s (A7 #5)")
        if not prov.comm_id:
            ctx.refuse("comm_id_required", "prepared-as-human needs comm_id — ttl_s counts from the journal's "
                                           "first sighting of the comm (A5.2); get one from slack_api('_comm_id')")

    if not profile.has_token(ctx.as_bot):
        ctx.refuse("token_unavailable", f"{profile.token_var(ctx.as_bot)} is not set — no "
                                        f"{'bot' if ctx.as_bot else 'person'} voice on this host (degraded mode)")

    is_report = ctx.as_bot and bool(profile.person_bot_dm_id) and channel == profile.person_bot_dm_id
    row = 2 if is_report else 1
    ctx.effect = Effect(row, "core", True)
    if prov.cls == "C2" and not is_report:
        ctx.refuse("c2_not_to_person", "class C2 talks only to the person (their bot DM) — invariant (ii)")

    if prov.cls in ("B-a", "C1"):
        if prov.synthesized:
            ctx.refuse("provenance_required", "autonomous speech needs provenance and a stamp (A12.2)")
        if profile.has_token(False):
            ctx.refuse("user_token_in_process",
                       f"class {prov.cls} never runs in a process holding the person's token — invariant (i)")
        ctx.refuse("no_notary", f"class {prov.cls} needs a notary stamp; no notary is configured")

    entry = policy.data.get("channels", {}).get(channel) if channel else None
    modes = entry["modes"] if entry else STRICT_MODES
    mode = modes.get(prov.cls, "deny")
    if entry and prov.voice not in entry["voices"]:
        ctx.refuse("voice_not_allowed", f"policy {policy.version}: voice {prov.voice} is not allowed in {channel}")
    if mode == "deny":
        ctx.refuse("not_enabled_by_policy",
                   f"class {prov.cls} is not enabled by policy {policy.version} for "
                   f"{'channel ' + channel if entry else 'unclassified channels (strictest)'}")
    recipients = {x for x in [channel, *prov.target.get("users", [])] if x}
    denied = set()
    if prov.cls in ("B-a", "C1"):
        denied |= recipients & set(policy.data.get("recipients_deny_autonomous", []))
    denied |= recipients & set(policy.data.get("recipients_deny", {}).get(prov.cls, []))
    if denied:
        ctx.refuse("recipient_denied",
                   f"policy {policy.version}: {', '.join(sorted(denied))} is on the deny list for class {prov.cls} "
                   "— a human decision does not override it (invariant iii)")

    # ------------------------------------------------ journal-backed send rules (A5)
    if journal is None:
        ctx.refuse("journal_unavailable",
                   f"journal unavailable ({journal_error or 'not opened'}) — no intent can be recorded, so "
                   "nothing is sent, including reports to the person (invariant viii)")
    else:
        if prov.prepared_at is not None and prov.prepared_at > now + PREPARED_AT_FUTURE_TOLERANCE_S:
            ctx.refuse("prepared_at_future", "provenance prepared_at is more than 60 s in the future")
        if prov.comm_id:
            first = (journal.note_comm_seen(prov.comm_id, at=now) if record_sighting
                     else (journal.comm_first_seen(prov.comm_id) or now))
            if first > now + PREPARED_AT_FUTURE_TOLERANCE_S:
                ctx.refuse("prepared_at_future", "the journal's first sighting of this comm_id is in the future "
                                                 "(clock skew)")
            if prov.ttl_s is not None and now - first > prov.ttl_s:
                ctx.refuse("ttl_expired", f"comm_id {prov.comm_id} first seen {int(now - first)} s ago, "
                                          f"ttl_s={int(prov.ttl_s)} (A5.2)")
            prior = [r for r in journal.find_by_comm_id(prov.comm_id) if r["outcome"] in BLOCKING_OUTCOMES]
            unresolved = [r for r in prior if r["outcome"] == "unknown"]
            if unresolved:
                # an `unknown` is resolved by reconciliation, never by a resend (invariant viii)
                ctx.refuse("reconcile_required",
                           f"comm_id {prov.comm_id} has {len(unresolved)} write(s) with an unknown outcome "
                           f"(op {unresolved[0]['op_id']}) — run slack_api(\"_reconcile\") first; a resend "
                           "before that could post it twice")
            elif prior and not prov.resend and ctx.method != "chat.update":
                # an edit is a new version of the same communication, not a second send (A4: a comm
                # «может иметь правки») — it carries the original's comm_id without `resend`
                ctx.refuse("duplicate_comm_id",
                           f"comm_id {prov.comm_id} already has {len(prior)} write(s) "
                           f"({', '.join(r['outcome'] for r in prior)}); pass resend=true with a fresh decision "
                           "(invariant ix)")
        else:
            if prov.ttl_s is not None:
                ctx.warnings.append("ttl_s without comm_id — the door cannot track prepared_at, ttl not enforced")
            # No comm_id → a fresh one is minted per send, so the comm_id rule cannot stop a blind repeat (a job
            # retrying on error after an ambiguous failure — verifier probe 2026-10-31). The same text to the same
            # channel while an `unknown` row of it exists is refused until that row is reconciled.
            text = params.get("text") if isinstance(params.get("text"), str) else None
            if text:
                import json as _json
                for r in journal.unknown_like(ctx.method, channel):
                    try:
                        prior_params = _json.loads(r["payload_json"]).get("params") or {}
                    except (ValueError, AttributeError):
                        prior_params = {}
                    prior_text = prior_params.get("text")
                    def _at(v):
                        try:
                            return int(float(v)) if v is not None and not isinstance(v, bool) else None
                        except (TypeError, ValueError):
                            return str(v)
                    same_place = prior_params.get("thread_ts") == params.get("thread_ts") and \
                        _at(prior_params.get("post_at")) == _at(params.get("post_at"))
                    if same_place and isinstance(prior_text, str) and " ".join(prior_text.split()) == " ".join(text.split()):
                        if r["outcome"] == "pending":
                            ctx.refuse("reconcile_required",
                                       f"the same text to {channel} has a write still in flight or whose process died less than "
                                       f"{DEFAULT_STALE_AFTER_S // 60} min ago (op {r['op_id']}, comm {r['comm_id']}, pending) — wait, "
                                       "then run slack_api(\"_reconcile\"); a repeat could post it twice")
                        else:
                            ctx.refuse("reconcile_required",
                                       f"the same text to {channel} has a write with an unknown outcome (op {r['op_id']}, "
                                       f"comm {r['comm_id']}) — run slack_api(\"_reconcile\") first; a repeat could post it twice")
                        break

    h = op_hash(ctx.method, params, ctx.as_bot, prov, profile.name)
    extra = dict(cls=prov.cls, op_hash=h, provenance=prov.to_dict(), channel=channel,
                 channel_class=entry.get("class") if entry else None,
                 approval=entry.get("approval") if entry else None, mode=mode)
    if ctx.reasons:
        code, msg = ctx.reasons[0]
        return _base(ctx, REFUSE, code, msg, **extra)
    if mode == "auto":
        return _base(ctx, ALLOW, "policy_auto", f"policy {policy.version}: class {prov.cls} mode auto", **extra)
    if not profile.decision_adapters:
        return _base(ctx, REFUSE, "no_decision_adapter",
                     "this send needs a human decision and the profile declares no decision adapter", **extra)
    if face == "library":
        return _base(ctx, REFUSE, "no_decision_adapter",
                     "needs a human decision; the library face has no decision adapter (needs-human = refuse)",
                     **extra)
    return _base(ctx, NEEDS_HUMAN, "human_decision", f"class {prov.cls}: a human decides this send", **extra)


# --------------------------------------------------------------------------- rights

_ROW_LABEL = {0: "0 read", 1: "1 message to others", 2: "2 report to the person", 4: "4 structure (open DM)"}


def rights_table(profile, policy: PolicyState | None = None, journal_ok: bool | None = None,
                 journal_path: str = "") -> str:
    policy = policy or load_policy(profile)
    caps = capabilities(profile, journal_ok)

    def yn(b):
        return "yes" if b else ("unknown" if b is None else "no")

    lines = [
        f"atlas-slack rights — profile {profile.name} ({profile.source}), policy {policy.version} "
        f"({policy.state}{': ' + policy.error if policy.error else ''}), digest {policy.digest[:12] or '-'}",
        f"freeze: {'YES — every write refuses' if profile.freeze else 'no'}"
        + (f" · profile errors: {'; '.join(profile.errors)}" if profile.errors else ""),
        f"identities: person {profile.person_user_id or '? (slack_api(\"auth.test\") under the user token tells)'} · bot {profile.bot_user_id or '?'} · bot DM "
        + (profile.person_bot_dm_id or "none yet — a bot message to the person goes to their user id, Slack opens the DM"),
        f"capabilities: user token {yn(caps.user_token)} · bot token {yn(caps.bot_token)} · journal "
        f"{yn(caps.journal)}{' (' + journal_path + ')' if journal_path else ''} · decision adapters "
        f"{', '.join(caps.decision_adapters) or 'none'} · notary none · identity source none",
        "",
        "row \\ class (unclassified channel)  " + "  ".join(f"{c:<26}" for c in CLASSES),
    ]
    writes_off = profile.freeze or profile.broken or not policy.valid or caps.journal is False
    off_reason = ("frozen" if profile.freeze else "profile broken" if profile.broken
                  else "policy invalid" if not policy.valid else "journal unavailable")

    def cell(row: int, c: str) -> str:
        if row == 0:
            return "allow" if c not in ("B-a", "C1") else "allow (own visibility)"
        if writes_off:
            return f"refuse ({off_reason})"
        if row == 4:
            return "human + warning (transitional)" if c in ("A", "B-h", "B-a") else "refuse"
        if row == 2 and c == "A":
            return "n/a"
        if row == 1 and c == "C2":
            return "refuse (C2 talks only to the person)"
        mode = STRICT_MODES[c]
        if c in ("B-a", "C1"):
            why = ["no notary"]
            if caps.user_token:
                why.append("person token in process")
            if mode == "deny":
                why.append("not enabled by policy")
            return "disabled: " + ", ".join(why)
        if mode == "deny":
            return "disabled: not enabled by policy"
        if mode == "auto":
            return "allow (policy)"
        if not caps.decision_adapters:
            return "refuse (no decision adapter)"
        return "human decision"

    for row in (0, 1, 2, 4):
        lines.append(f"{_ROW_LABEL[row]:<36}" + "  ".join(f"{cell(row, c):<26}" for c in CLASSES))
    chans = policy.data.get("channels") or {}
    if chans:
        lines.append("")
        lines.append("classified channels (row 1 modes):")
        for cid, e in sorted(chans.items()):
            lines.append(f"  {cid} {e.get('name') or ''} class={e.get('class')} voices={','.join(e['voices'])} "
                         f"approval={e.get('approval') or '-'} " +
                         " ".join(f"{c}={e['modes'][c]}" for c in CLASSES))
    lines += ["", "Every write not listed above is transitional: today's gate (ask), flagged \"transitional\". "
              "username/icon_emoji/icon_url are refused on every send."]
    return "\n".join(lines)
