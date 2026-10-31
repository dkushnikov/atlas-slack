"""Provenance of a write to others — schema, validation, binding hash (A4).

Every send to others carries provenance: who prepared it, from which context,
for which target, which checks the context declares, which decision covers it.
The door validates the *structure* and binds the decision to the operation; it
does not judge the content of the declared checks (A2).

Binding: `op_hash = sha256(canonical JSON of {method, as_bot, params, cls,
context, scheduled_for, file_sha256, target.users, comm_id, resend})`. The brief named {method, text, blocks, channel,
thread_ts, as_bot, scheduled_for}; this slice hashes the **whole** caller
params (text, blocks, channel and thread_ts are inside) plus class and profile,
so a change of *any* parameter after the decision — `reply_broadcast`,
`unfurl_links`, `mrkdwn` — also invalidates it. Strictly stronger, same API.
The attribution block added for class A is deterministic from the profile, so
hashing the caller's params binds the final payload too.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime

CLASSES = ("A", "B-h", "B-a", "C1", "C2")
AUTONOMOUS = frozenset({"B-a", "C1", "C2"})
HUMAN_DECIDED = frozenset({"A", "B-h"})
VOICES = ("person", "bot")
ORIGINS = ("live", "prepared-as-human")
CLASS_VOICE = {"A": "person", "B-h": "bot", "B-a": "bot", "C1": "bot", "C2": "bot"}

# Caller flags that change rendering only, never what Slack receives.
NON_BINDING_PARAMS = frozenset({"_raw"})

_KEYS = {
    "comm_id", "op_id", "cls", "voice", "prepared_by", "context", "target", "checks",
    "sources", "origin", "ttl_s", "scheduled_for", "prepared_at", "resend", "decision",
    "stamp", "migration", "file_sha256",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ProvenanceError(ValueError):
    pass


def _epoch(v, label: str) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        raise ProvenanceError(f"{label} must be epoch seconds or ISO-8601")
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    raise ProvenanceError(f"{label} must be epoch seconds or ISO-8601, got {v!r}")


def _users(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [u.strip() for u in v.split(",") if u.strip()]
    if isinstance(v, list) and all(isinstance(u, str) for u in v):
        return [u.strip() for u in v if u.strip()]
    raise ProvenanceError("target.users must be a list of user ids or a comma-separated string")


@dataclass
class Provenance:
    cls: str
    voice: str
    op_id: str = ""
    comm_id: str | None = None
    prepared_by: str | None = None
    context: str | None = None
    target: dict = field(default_factory=dict)
    checks: dict = field(default_factory=dict)
    sources: list = field(default_factory=list)
    origin: str = "live"
    ttl_s: float | None = None
    scheduled_for: float | None = None
    file_sha256: list | None = None       # files.uploadV2: sha256 of each uploaded file, bound into the decision
    prepared_at: float | None = None      # caller's claim — informational; door's first sighting wins
    resend: bool = False
    decision: dict | None = None
    stamp: dict | None = None
    migration: str | None = None          # set when the door synthesised this (A12.2)

    @property
    def synthesized(self) -> bool:
        return self.migration is not None

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v not in (None, {}, [], False) or k in ("cls", "voice")}

    @classmethod
    def from_dict(cls, d: dict, as_bot: bool | None = None) -> "Provenance":
        if not isinstance(d, dict):
            raise ProvenanceError("provenance must be an object")
        unknown = set(d) - _KEYS
        if unknown:
            raise ProvenanceError(f"unknown provenance fields: {sorted(unknown)}")
        c = d.get("cls")
        if c is None and as_bot is not None:
            # A send under the person's token is class A, under the bot token B-h (A12.2):
            # the voice already says it, so `{"comm_id": …}` alone is a valid provenance.
            c = "B-h" if as_bot else "A"
        if c not in CLASSES:
            raise ProvenanceError(f"cls must be one of {list(CLASSES)}, got {c!r}")
        voice = d.get("voice")
        if voice is None and as_bot is not None:
            voice = "bot" if as_bot else "person"
        if voice not in VOICES:
            raise ProvenanceError(f"voice must be one of {list(VOICES)}, got {voice!r}")
        origin = d.get("origin") or "live"
        if origin not in ORIGINS:
            raise ProvenanceError(f"origin must be one of {list(ORIGINS)}, got {origin!r}")
        op_id = d.get("op_id") or ""
        if op_id and (not isinstance(op_id, str) or len(op_id) > 128):
            raise ProvenanceError("op_id must be a string ≤128 chars")
        for k in ("comm_id", "prepared_by", "context"):
            if d.get(k) is not None and not isinstance(d[k], str):
                raise ProvenanceError(f"{k} must be a string")
        cid = d.get("comm_id")
        if cid is not None and (len(cid) > 128 or any(ch < " " for ch in cid)):
            raise ProvenanceError("comm_id must be one line of at most 128 printable characters")
        target = d.get("target") or {}
        if not isinstance(target, dict):
            raise ProvenanceError("target must be an object {channel, users, is_mpim}")
        target = dict(target)
        if "users" in target:
            target["users"] = _users(target["users"])
        if target.get("channel") is not None and not isinstance(target["channel"], str):
            raise ProvenanceError("target.channel must be a channel id string")
        checks = d.get("checks") or {}
        if not isinstance(checks, dict):
            raise ProvenanceError("checks must be an object (voice, recipient, policy_row, mark, disclosure)")
        sources = d.get("sources") or []
        if not isinstance(sources, list):
            raise ProvenanceError("sources must be a list")
        ttl = d.get("ttl_s")
        if ttl is not None and (isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl <= 0):
            raise ProvenanceError("ttl_s must be a positive number of seconds")
        resend = d.get("resend", False)
        if not isinstance(resend, bool):
            raise ProvenanceError("resend must be true/false")
        decision = d.get("decision")
        if decision is not None and (not isinstance(decision, dict) or
                                     not isinstance(decision.get("token", decision.get("t", "")), str)):
            raise ProvenanceError("decision must be an object {t, p} (set by the decision adapter)")
        stamp = d.get("stamp")
        if stamp is not None and not isinstance(stamp, dict):
            raise ProvenanceError("stamp must be an object {by, version, verdict, at}")
        mig = d.get("migration")
        if mig is not None and not isinstance(mig, str):
            raise ProvenanceError("migration must be a string")
        fh = d.get("file_sha256")
        if fh is not None and (not isinstance(fh, list) or not fh
                               or not all(isinstance(h, str) and _SHA256_RE.match(h) for h in fh)):
            raise ProvenanceError("file_sha256 must be a non-empty list of 64-hex sha256 strings")
        return cls(
            cls=c, voice=voice, op_id=op_id, comm_id=d.get("comm_id"), prepared_by=d.get("prepared_by"),
            context=d.get("context"), target=target, checks=dict(checks), sources=list(sources),
            origin=origin, ttl_s=float(ttl) if ttl is not None else None,
            scheduled_for=_epoch(d.get("scheduled_for"), "scheduled_for"),
            file_sha256=list(fh) if fh else None,
            prepared_at=_epoch(d.get("prepared_at"), "prepared_at"),
            resend=resend, decision=dict(decision) if decision else None,
            stamp=dict(stamp) if stamp else None, migration=mig,
        )


def synthesize(params: dict, as_bot: bool, context: str | None, prepared_by: str | None) -> Provenance:
    """Migration rule A12.2: a human-decided send without provenance.

    Only classes A / B-h, inferred from the voice; marked `migration` so the
    decision text and the journal show that provenance was missing.
    """
    return Provenance(
        cls="B-h" if as_bot else "A", voice="bot" if as_bot else "person",
        context=context, prepared_by=prepared_by,
        target={"channel": params.get("channel")} if isinstance(params.get("channel"), str) else {},
        origin="live", migration="provenance_missing",
    )


COMM_ID_RE = re.compile(r"^\d{8}-[0-9a-f]{4}$")


def mint_comm_id(taken=None, now: float | None = None) -> str:
    """`YYYYMMDD-<4 hex>` from the OS entropy source — the ledger convention, minted by the door.

    An LLM asked for "4 random hex" is not a random source: across 160 ledger ids on 2026-10-31
    only 131 suffixes were distinct (`7c3e` ×8). `taken(id) -> bool` lets the caller reject an
    id the journal has already seen.
    """
    import time as _t
    day = _t.strftime("%Y%m%d", _t.localtime(_t.time() if now is None else now))
    for _ in range(64):
        cid = f"{day}-{secrets.token_hex(2)}"
        if taken is None or not taken(cid):
            return cid
    raise RuntimeError("could not mint a free comm_id in 64 draws")


def ensure_op_id(p: Provenance) -> Provenance:
    if not p.op_id:
        p.op_id = str(uuid.uuid4())
    return p


def binding_payload(method: str, params: dict, as_bot: bool, prov: Provenance | None,
                    context: str | None) -> dict:
    return {
        "method": method,
        "as_bot": bool(as_bot),
        "params": {k: v for k, v in (params or {}).items() if k not in NON_BINDING_PARAMS},
        "cls": prov.cls if prov else None,
        "context": context,
        "scheduled_for": prov.scheduled_for if prov else None,
        # an upload binds to the bytes, not the path: a file rewritten after approval → decision_mismatch (A6)
        "file_sha256": list(prov.file_sha256) if prov and prov.file_sha256 else None,
        # the approval binds to the declared recipients (a DM/MPIM target is its users),
        # the communication item and the resend intent (verifier finding 2026-10-28)
        "users": sorted((prov.target or {}).get("users") or []) if prov else [],
        "comm_id": prov.comm_id if prov else None,
        "resend": bool(prov.resend) if prov else False,
    }


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def op_hash(method: str, params: dict, as_bot: bool, prov: Provenance | None, context: str | None) -> str:
    return hashlib.sha256(canonical_json(binding_payload(method, params, as_bot, prov, context))
                          .encode("utf-8")).hexdigest()
