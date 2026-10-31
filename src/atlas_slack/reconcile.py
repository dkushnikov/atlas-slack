"""Reconciliation of `unknown` outcomes — by the fact in Slack, never by a retry (invariant viii).

An op is `unknown` when the transport failed ambiguously (timeout, reset) or the process died
between the call and the outcome. The door resolves it by reading what Slack has:

- `chat.postMessage` / `chat.meMessage` — the channel (or thread) around the intent time; a bot
  send is matched by `{op_id}` in message metadata, a person send by text and author.
- `chat.update` — the message at `ts`: its text equals the new text → `ok`, the prior → `error`.
- `chat.scheduleMessage` — `chat.scheduledMessages.list` by channel and time; once `post_at`
  has passed, the channel at `post_at`.
- `files.uploadV2` — `files.list` by channel, time and uploader: `ok` only when every expected
  name is matched by a distinct file created at/after the intent (an older namesake never
  counts); a partial match, a file without `created`, or no name to match → stays `unknown`.

Every paginated read follows `next_cursor` / `has_more` / files.list `paging` up to MAX_PAGES;
an incomplete scan is no proof of absence and leaves the row `unknown`.
- person-voice posts are matched by text and author when the profile names `person_user_id`;
  without it, by text alone (the profile should name it).
- `chat.postEphemeral` — unobservable (never in history): resolved as `error`
  (`reconciled_unobservable`); a repeat shows the one viewer twice at worst.

Not found inside the window and the window has passed → `error` with `reconciled_absent`.
Not found but too early (Slack may still be delivering) → stays `unknown`, says why.
Resolution writes the journal (`record_outcome`); it never sends.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from atlas_slack.shaping import METADATA_EVENT_TYPE

Reader = Callable[[str, dict, bool], dict]

SETTLE_S = 120            # Slack delivers at once; two minutes covers a slow network and clock skew
WINDOW_BEFORE_S = 60
WINDOW_AFTER_S = 1800
HISTORY_LIMIT = 200


@dataclass
class Resolution:
    op_id: str
    method: str
    outcome: str            # ok | error | unknown
    detail: str
    slack_ts: str | None = None
    error_code: str | None = None

    def line(self) -> str:
        if self.outcome == "ok":
            return f"op {self.op_id} {self.method} → ok ts={self.slack_ts} ({self.detail})"
        if self.outcome == "error":
            return f"op {self.op_id} {self.method} → error {self.error_code} ({self.detail})"
        return f"op {self.op_id} {self.method} → still unknown ({self.detail})"


def _text_of(payload: dict) -> str:
    final = payload.get("final_params") or {}
    params = payload.get("params") or {}
    t = final.get("text") if isinstance(final.get("text"), str) else params.get("text")
    return " ".join((t or "").split())


def _norm(s) -> str:
    return " ".join((s or "").split()) if isinstance(s, str) else ""


MAX_PAGES = 5


def _read(reader: Reader, method: str, params: dict, as_bot: bool) -> dict | None:
    try:
        data = reader(method, params, as_bot)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
    return data if isinstance(data, dict) else None


def _read_pages(reader: Reader, method: str, params: dict, as_bot: bool, key: str) -> tuple[list, bool, str | None]:
    """Every item of a paginated read, up to MAX_PAGES: (items, complete, error).

    Cursor methods (history, replies, scheduledMessages.list) continue on `response_metadata.next_cursor`
    (or `has_more`); files.list on `paging.page < paging.pages`. An absence proven on a partial scan is no
    proof (Codex addendum A2): the caller keeps the row `unknown` when `complete` is False."""
    items: list = []
    page_params = dict(params)
    for _ in range(MAX_PAGES):
        data = _read(reader, method, page_params, as_bot)
        if not data or not data.get("ok", True):
            return items, False, (data or {}).get("error") or "read failed"
        items.extend(x for x in (data.get(key) or []) if isinstance(x, dict))
        cursor = ((data.get("response_metadata") or {}).get("next_cursor") or "") if isinstance(data.get("response_metadata"), dict) else ""
        paging = data.get("paging") if isinstance(data.get("paging"), dict) else None
        if cursor:
            page_params = {**params, "cursor": cursor}
            continue
        if method == "files.list":
            # files.list pages by `paging.page/pages`; a response without that object cannot prove completeness
            if not (paging and isinstance(paging.get("page"), int) and isinstance(paging.get("pages"), int)):
                return items, False, "files.list without paging — completeness unknown"
            if paging["page"] < paging["pages"]:
                page_params = {**params, "page": paging["page"] + 1}
                continue
        if data.get("has_more") and not cursor:
            return items, False, "has_more without a cursor"
        return items, True, None
    return items, False, f"more than {MAX_PAGES} pages — scan incomplete"


CLOCK_SKEW_S = 5


def _matches(msg: dict, op_id: str, text: str, as_bot: bool, profile, intent_at: float) -> bool:
    meta = msg.get("metadata") if isinstance(msg.get("metadata"), dict) else {}
    if meta.get("event_type") == METADATA_EVENT_TYPE:
        return (meta.get("event_payload") or {}).get("op_id") == op_id      # ours: the op id decides, never the text
    if not text or _norm(msg.get("text")) != text:
        return False
    # a text match counts only for a message posted at or after the intent (an older identical message
    # from the same or another sender is not ours — verifier probe 2026-10-31) and by the voice's identity
    try:
        if float(msg.get("ts")) < intent_at - CLOCK_SKEW_S:
            return False
    except (TypeError, ValueError):
        return False
    expected = profile.bot_user_id if as_bot else profile.person_user_id
    if expected:
        return msg.get("user") == expected
    return bool(msg.get("bot_id")) if as_bot else True


def _resolve_post(row: dict, payload: dict, reader: Reader, profile, now: float) -> Resolution:
    params = payload.get("params") or {}
    as_bot = bool(payload.get("as_bot"))
    channel = params.get("channel")
    text = _text_of(payload)
    intent_at = float(row["intent_at"])
    window = {"oldest": str(intent_at - WINDOW_BEFORE_S), "latest": str(intent_at + WINDOW_AFTER_S), "inclusive": True,
              "limit": HISTORY_LIMIT, "include_all_metadata": True}     # metadata only with include_all_metadata
    if params.get("thread_ts"):
        msgs, complete, err = _read_pages(reader, "conversations.replies", {"channel": channel, "ts": params["thread_ts"], **window},
                                          as_bot, "messages")
    else:
        msgs, complete, err = _read_pages(reader, "conversations.history", {"channel": channel, **window}, as_bot, "messages")
    for msg in msgs:
        if _matches(msg, row["op_id"], text, as_bot, profile, intent_at):
            return Resolution(row["op_id"], row["method"], "ok", "found in the channel", slack_ts=msg.get("ts"))
    if not complete:
        return Resolution(row["op_id"], row["method"], "unknown", f"not found on an incomplete scan ({err}) — no proof of absence")
    if now - intent_at < SETTLE_S:
        return Resolution(row["op_id"], row["method"], "unknown", f"not found yet — intent {int(now - intent_at)} s ago, "
                                                                  f"wait until {SETTLE_S} s")
    return Resolution(row["op_id"], row["method"], "error", "not in the channel within the window",
                      error_code="reconciled_absent")


def _resolve_update(row: dict, payload: dict, reader: Reader, profile, now: float) -> Resolution:
    params = payload.get("params") or {}
    as_bot = bool(payload.get("as_bot"))
    data = _read(reader, "conversations.replies", {"channel": params.get("channel"), "ts": params.get("ts"),
                                                   "limit": 1, "inclusive": True}, as_bot)
    if not data or not data.get("ok", True):
        return Resolution(row["op_id"], row["method"], "unknown", f"read failed: {(data or {}).get('error')}")
    msg = next((m for m in data.get("messages") or [] if isinstance(m, dict) and m.get("ts") == params.get("ts")), None)
    if msg is None:
        return Resolution(row["op_id"], row["method"], "error", "the message no longer exists", error_code="reconciled_absent")
    if not _text_of(payload):
        return Resolution(row["op_id"], row["method"], "unknown", "a blocks-only edit cannot be verified by text — check the "
                                                                  "message by hand (the row stays unknown)")
    if _norm(msg.get("text")) == _text_of(payload):
        return Resolution(row["op_id"], row["method"], "ok", "the message carries the new text", slack_ts=params.get("ts"))
    if now - float(row["intent_at"]) < SETTLE_S:
        return Resolution(row["op_id"], row["method"], "unknown", "the message still shows the old text — too early to call")
    return Resolution(row["op_id"], row["method"], "error", "the message does not carry the new text",
                      error_code="reconciled_absent")


def _resolve_schedule(row: dict, payload: dict, reader: Reader, profile, now: float) -> Resolution:
    params = payload.get("params") or {}
    as_bot = bool(payload.get("as_bot"))
    text = _text_of(payload)
    try:
        post_at = float(params.get("post_at"))
    except (TypeError, ValueError):
        return Resolution(row["op_id"], row["method"], "unknown", "no post_at in the payload")
    if now < post_at:
        items, complete, err = _read_pages(reader, "chat.scheduledMessages.list",
                                           {"channel": params.get("channel"), "oldest": int(post_at) - 1,
                                            "latest": int(post_at) + 1, "limit": HISTORY_LIMIT}, as_bot, "scheduled_messages")
        for m in items:
            if _norm(m.get("text")) == text:
                return Resolution(row["op_id"], row["method"], "ok", "in the scheduled list", slack_ts=m.get("id"))
        if not complete:
            return Resolution(row["op_id"], row["method"], "unknown", f"not in the scheduled list on an incomplete scan ({err})")
        if now - float(row["intent_at"]) < SETTLE_S:
            return Resolution(row["op_id"], row["method"], "unknown", "not in the scheduled list yet — too early to call")
        return Resolution(row["op_id"], row["method"], "error", "not in the scheduled list", error_code="reconciled_absent")
    fake = {**row, "intent_at": post_at}
    return _resolve_post(fake, payload, reader, profile, now)


def _upload_eligible(row: dict, payload: dict, reader: Reader, profile, owned: set) -> dict:
    """Files that could belong to this upload: the right name, by the voice's identity, created at/after the
    intent (an older namesake is not this upload — Codex A3), not owned by another op. Returns a dict with
    `expected` (names, one slot each), `eligible` (files, oldest first), `complete`, `err`, `undatable`, `early`."""
    params = payload.get("params") or {}
    as_bot = bool(payload.get("as_bot"))
    intent_at = float(row["intent_at"])
    expected_names: list[str] = []
    if isinstance(params.get("file"), str):
        expected_names.append(params.get("filename") or Path(params["file"]).name)
    elif isinstance(params.get("content"), str):
        expected_names.append(params.get("filename") or params.get("title") or "")
    for item in params.get("file_uploads") or []:
        if isinstance(item, dict):
            expected_names.append(item.get("filename") or (Path(item["file"]).name if isinstance(item.get("file"), str) else ""))
    out = {"expected": expected_names, "eligible": [], "complete": True, "err": None, "undatable": False, "intent_at": intent_at}
    if not expected_names or "" in expected_names:
        out["nameless"] = True
        return out
    expected = profile.bot_user_id if as_bot else profile.person_user_id
    query = {"channel": params.get("channel"), "ts_from": int(intent_at - WINDOW_BEFORE_S),
             "ts_to": int(intent_at + WINDOW_AFTER_S), "count": 100}
    if expected:
        query["user"] = expected                   # Slack filters by uploader; checked again below
    files, complete, err = _read_pages(reader, "files.list", query, as_bot, "files")
    out["complete"], out["err"] = complete, err
    names = set(expected_names)
    for f in sorted(files, key=lambda x: str(x.get("created") or x.get("timestamp") or "")):
        name = f.get("name") if f.get("name") in names else f.get("title") if f.get("title") in names else None
        if name is None or str(f.get("id")) in owned:
            continue
        if expected and f.get("user") and f.get("user") != expected:
            continue
        created = f.get("created") or f.get("timestamp")
        try:
            created_f = float(created)
        except (TypeError, ValueError):
            created_f = float("nan")
        if not math.isfinite(created_f) or created_f <= 0:
            out["undatable"] = True
            continue
        if created_f < intent_at - CLOCK_SKEW_S:
            continue
        out["eligible"].append((name, f))
    return out


def _resolve_upload(row: dict, payload: dict, reader: Reader, profile, now: float, owned: set | None = None,
                    elig: dict | None = None) -> Resolution:
    """`owned`: file ids other ops already account for (ok rows in the journal, or claimed earlier in this run)
    — a file proves at most one operation (Codex A3: two uploads of one name, one file → one ok at most)."""
    owned = owned or set()
    e = elig or _upload_eligible(row, payload, reader, profile, owned)
    if e.get("nameless"):
        return Resolution(row["op_id"], row["method"], "unknown", "a file without a name to match (a `content` upload "
                                                                  "without filename/title) — check the channel by hand")
    expected_names = e["expected"]
    # one slot per expected name (two items may share a name): a file fills one slot, never two
    slots = {n: expected_names.count(n) for n in expected_names}
    matched: list[tuple[str, dict]] = []
    for name, f in e["eligible"]:
        if str(f.get("id")) in owned or not slots.get(name):
            continue
        slots[name] -= 1
        matched.append((name, f))
    if len(matched) == len(expected_names):
        ids = ",".join(str(f.get("id")) for _, f in matched)
        return Resolution(row["op_id"], row["method"], "ok", f"file(s) {', '.join(n for n, _ in matched)} in the channel", slack_ts=ids)
    if matched:
        return Resolution(row["op_id"], row["method"], "unknown", f"{len(matched)} of {len(expected_names)} files found "
                                                                  f"({', '.join(n for n, _ in matched)}) — a partial upload; resolve by hand")
    if e["undatable"]:
        return Resolution(row["op_id"], row["method"], "unknown", "a namesake file without a creation time — cannot tell it "
                                                                  "from an older one; resolve by hand")
    if not e["complete"]:
        return Resolution(row["op_id"], row["method"], "unknown", f"no such file on an incomplete scan ({e['err']}) — no proof of absence")
    if now - e["intent_at"] < SETTLE_S:
        return Resolution(row["op_id"], row["method"], "unknown", "no such file in the channel yet — too early to call")
    return Resolution(row["op_id"], row["method"], "error", "no such file in the channel within the window",
                      error_code="reconciled_absent")


def _resolve_ephemeral(row: dict, payload: dict, reader: Reader, profile, now: float) -> Resolution:
    return Resolution(row["op_id"], row["method"], "error", "an ephemeral message is never in history — treated as not "
                                                            "delivered; a repeat shows the viewer a second copy at worst",
                      error_code="reconciled_unobservable")


_RESOLVERS = {
    "chat.postMessage": _resolve_post,
    "chat.meMessage": _resolve_post,
    "chat.update": _resolve_update,
    "chat.scheduleMessage": _resolve_schedule,
    "files.uploadV2": _resolve_upload,
    "chat.postEphemeral": _resolve_ephemeral,
}


MANUAL_RESOLUTIONS = ("ok", "absent")


def manual_hash(op_ids: list[str], resolve: str, profile_name: str) -> str:
    """What the person's one-time decision covers: these ops, this resolution, this profile."""
    import hashlib
    from atlas_slack.provenance import canonical_json
    return hashlib.sha256(canonical_json({"method": "_reconcile", "op_ids": sorted(op_ids), "resolve": resolve,
                                          "context": profile_name}).encode("utf-8")).hexdigest()


def describe_rows(journal, op_ids: list[str]) -> list[str]:
    """One line per op for the prompt: method · channel · when · text preview (what to look for in Slack)."""
    import json
    import time as _t
    out = []
    for op_id in op_ids:
        row = journal.get_op(op_id)
        if row is None:
            out.append(f"op {op_id}: not in the journal")
            continue
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except ValueError:
            payload = {}
        params = payload.get("params") or {}
        text = params.get("text") or params.get("initial_comment") or ""
        text = " ".join(str(text).split())
        if len(text) > 120:
            text = text[:120] + "…"
        when = _t.strftime("%d.%m %H:%M", _t.localtime(float(row["intent_at"])))
        where = params.get("channel") or (json.loads(row["target_json"] or "{}").get("channel") if row.get("target_json") else "?")
        extra = f" · ts {params['ts']}" if params.get("ts") else ""
        out.append(f"op {op_id}: {row['method']} → {where}{extra} · {when} · {row['outcome']}" + (f" · “{text}”" if text else ""))
    return out


def resolve_by_hand(journal, op_ids: list[str], resolve: str) -> list[Resolution]:
    """The person's word on rows reconcile cannot settle (a blocks-only edit, a nameless snippet, a read the
    token cannot make): they looked in Slack and say the write is there (`ok`) or is not (`absent`). A human
    decision — the hook asks for it; the library has no one to ask and refuses. Writes the journal, sends nothing."""
    if resolve not in MANUAL_RESOLUTIONS:
        raise ValueError(f"resolve must be one of {list(MANUAL_RESOLUTIONS)}")
    out = []
    for row in journal.ops("unknown"):
        if row["op_id"] not in op_ids:
            continue
        if resolve == "ok":
            res = Resolution(row["op_id"], row["method"], "ok", "resolved by hand: the person saw it in Slack", slack_ts=None)
            journal.record_outcome(row["op_id"], "ok", slack_ts=None, error_code="resolved_by_hand")
        else:
            res = Resolution(row["op_id"], row["method"], "error", "resolved by hand: the person did not find it in Slack",
                             error_code="resolved_by_hand_absent")
            journal.record_outcome(row["op_id"], "error", error_code="resolved_by_hand_absent")
        out.append(res)
    return out


def reconcile(journal, reader: Reader, profile, now: float | None = None, op_ids: list[str] | None = None) -> list[Resolution]:
    """Resolve every `unknown` op (or the named ones); writes outcomes to the journal. Reads only."""
    import json
    now = time.time() if now is None else now
    try:
        journal.mark_stale_unknown()          # a long-lived server: pending rows past the stale window become unknown now
    except Exception:  # noqa: BLE001
        pass
    all_unknown = journal.ops("unknown")
    rows = [r for r in all_unknown if op_ids is None or r["op_id"] in op_ids]
    # Files other ops already own: every id on an ok uploadV2 row (slack_ts holds `F1[,F2…]`). A file proves at most
    # one operation; one eligible for several unknown ops in this run is ambiguous and proves none (Codex A3).
    owned: set = set()
    try:
        for r in journal.ops("ok"):
            if r["method"] == "files.uploadV2" and r.get("slack_ts"):
                owned.update(x for x in str(r["slack_ts"]).split(",") if x)
    except Exception:  # noqa: BLE001
        pass
    payloads = {}
    for row in all_unknown:
        try:
            payloads[row["op_id"]] = json.loads(row["payload_json"]) if row.get("payload_json") else {}
        except ValueError:
            payloads[row["op_id"]] = {}
    # ambiguity is judged over EVERY unknown upload in the journal, not only the ones asked for: a run
    # restricted by op_ids must not hand a shared file to one op while its rival's row still exists
    uploads = [r for r in all_unknown if r["method"] == "files.uploadV2"]
    contested: dict[str, set] = {}
    planned: dict[str, Resolution] = {}       # a group's assignment, decided once for every member (requested or not)
    elig: dict[str, dict] = {}
    if len(uploads) > 1:
        # ops whose eligible files overlap form a group; when the group's distinct files cannot fill every
        # op's slots, no op in it is proven — a file may belong to any of them (ambiguous → unknown)
        for r in uploads:
            elig[r["op_id"]] = _upload_eligible(r, payloads[r["op_id"]], reader, profile, owned)
        by_file: dict[str, set] = {}
        for op, e in elig.items():
            for _, f in e["eligible"]:
                by_file.setdefault(str(f.get("id")), set()).add(op)
        parent = {op: op for op in elig}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for ops in by_file.values():
            ops = sorted(ops)
            for o in ops[1:]:
                parent[find(o)] = find(ops[0])
        groups: dict[str, list] = {}
        for op in elig:
            groups.setdefault(find(op), []).append(op)
        for members in groups.values():
            if len(members) < 2:
                continue
            files_in_group = {str(f.get("id")) for op in members for _, f in elig[op]["eligible"]}
            # capacity per NAME, not in total (Codex: one report.pdf + two summary.md ≠ three slots of mixed
            # names); then a dry run of the assignment — if any member ends short, nobody in the group is proven
            demand: dict[str, int] = {}
            supply: dict[str, set] = {}
            for op in members:
                for n in elig[op]["expected"]:
                    demand[n] = demand.get(n, 0) + 1
                for n, f in elig[op]["eligible"]:
                    supply.setdefault(n, set()).add(str(f.get("id")))
            short = any(len(supply.get(n, set())) < d for n, d in demand.items())
            trial: dict[str, Resolution] = {}
            if not short:
                # the dry run IS the assignment: every member, requested or not, claims in intent order, so a run
                # restricted by op_ids executes the same distribution it proved (verifier: a late requested op
                # took an early unrequested op's file, which then read «absent» in its own run)
                trial_owned = set(owned)
                for op in sorted(members, key=lambda o: float(next(r["intent_at"] for r in uploads if r["op_id"] == o))):
                    res = _resolve_upload(next(r for r in uploads if r["op_id"] == op), payloads[op], reader, profile, now,
                                          trial_owned, elig[op])
                    if res.outcome != "ok":
                        short = True
                        break
                    trial_owned.update(x for x in str(res.slack_ts).split(",") if x)
                    trial[op] = res
            if short:
                for op in members:
                    contested[op] = files_in_group
            else:
                planned.update(trial)
                for res in trial.values():
                    owned.update(x for x in str(res.slack_ts).split(",") if x)   # reserved for their ops, recorded or not
    out = []
    for row in rows:
        payload = payloads[row["op_id"]]
        if row["op_id"] in contested:
            fids = ", ".join(sorted(contested[row["op_id"]]))
            res = Resolution(row["op_id"], row["method"], "unknown",
                             f"ambiguous: file {fids} could belong to another unknown upload of the same name — resolve by hand")
        elif row["op_id"] in planned:
            res = planned[row["op_id"]]                                          # the group's assignment, as proven
        elif row["method"] == "files.uploadV2":
            res = _resolve_upload(row, payload, reader, profile, now, owned, elig.get(row["op_id"]))
            if res.outcome == "ok" and res.slack_ts:
                owned.update(x for x in str(res.slack_ts).split(",") if x)      # claimed: the next op skips these
        else:
            resolver = _RESOLVERS.get(row["method"])
            if resolver is None:
                res = Resolution(row["op_id"], row["method"], "unknown", "no resolver for this method — check by hand")
            else:
                res = resolver(row, payload, reader, profile, now)
        if res.outcome in ("ok", "error"):
            try:
                journal.record_outcome(row["op_id"], res.outcome, slack_ts=res.slack_ts, error_code=res.error_code)
            except Exception as e:  # noqa: BLE001 — JournalUnavailable: the row stays unknown, the report says why
                res = Resolution(row["op_id"], row["method"], "unknown", f"resolved as {res.outcome} but the journal could "
                                                                         f"not record it ({e}) — run again")
        out.append(res)
    return out


def report(resolutions: list[Resolution], op_ids: list[str] | None = None) -> str:
    if not resolutions:
        if op_ids is not None:
            return f"reconcile: no unknown op among {op_ids or '[]'} — nothing to resolve (other unknown rows, if any, were not asked for)"
        return "reconcile: no unknown outcomes in the journal"
    n_ok = sum(r.outcome == "ok" for r in resolutions)
    n_err = sum(r.outcome == "error" for r in resolutions)
    n_unk = len(resolutions) - n_ok - n_err
    head = f"reconcile: {len(resolutions)} unknown → {n_ok} ok · {n_err} error · {n_unk} still unknown"
    lines = [head, *(r.line() for r in resolutions)]
    if n_unk:
        lines.append("still unknown: those comm_ids stay blocked (reconcile_required). Run _reconcile again later; a row "
                     "that cannot be verified by reading (blocks-only edit, nameless snippet, a read the token cannot make) "
                     "is settled by the person: they look in Slack, then slack_api(\"_reconcile\", {\"op_ids\": [...], "
                     "\"resolve\": \"ok\" | \"absent\"}) (asks for approval). Never resend under a new comm_id.")
    return "\n".join(lines)
