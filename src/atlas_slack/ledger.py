"""Ledger lookup — the communication record behind a `comm_id` (the outbox side of A4).

The `/comms` ritual writes one Markdown file per communication with `comm_id:` in its
frontmatter, its audit pass (Cicero, step 4) records `audit: pass | skip` there, and
every draft version is a `## Draft vN` section. The door does not judge text (A2); it
can verify that the record exists, what the audit pass wrote, and that the text on
the approval card is the text that was audited (equal to the last `## Draft` section).
That turns the card's «Cicero» from a claim the sending agent typed into a fact read
from the artefact the ritual left — and an edit after the audit shows ⚠ until the
ritual re-audits and appends the new draft.

Informational only: no ledger, no directory, an unreadable file — a ⚠ line on the
card, never a refusal (the owner, 2026-10-31: «если не создаёт зависимость»).

Several ledger folders may be named, `os.pathsep`-separated (the MC door also reads
the Personal vault's ledger: a comm drafted there is sent through an MC session).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

ENV_LEDGER_DIR = "ATLAS_SLACK_LEDGER_DIR"
FRONTMATTER_MAX_LINES = 80
BODY_MAX_BYTES = 256_000
AUDIT_PASSED = ("pass", "passed", "ok", "done")
AUDIT_SKIPPED = ("skip", "skipped", "n/a")
_DRAFT_HEADING = re.compile(r"^## +draft\b.*$", re.I | re.M)
_ANY_HEADING = re.compile(r"^## +", re.M)
_WS = re.compile(r"\s+")


@dataclass
class LedgerEntry:
    path: str
    comm_id: str
    audit: str | None = None       # the `audit:` field as written, lower-cased
    audit_at: str | None = None
    intention: str | None = None
    status: str | None = None
    draft: str | None = None       # the last `## Draft …` section's text, None when the file has none
    duplicates: int = 0            # other files carrying the same comm_id (older; the newest is this one)

    @property
    def audit_state(self) -> str:
        """passed | skipped | missing | other"""
        if self.audit is None or self.audit == "":
            return "missing"
        if self.audit in AUDIT_PASSED:
            return "passed"
        if self.audit in AUDIT_SKIPPED:
            return "skipped"
        return "other"

    def text_matches(self, text: str | None) -> bool | None:
        """True/False when both the draft and the text are known; None otherwise."""
        if self.draft is None or text is None:
            return None
        return _norm(self.draft) == _norm(text)


def _norm(s: str) -> str:
    return _WS.sub(" ", s).strip()


def ledger_dirs(profile) -> list[Path]:
    """The env var first (the vault's settings carry it), then the profile field; [] when neither.

    A path list (`os.pathsep`): every existing directory, in order.
    """
    raw = os.environ.get(ENV_LEDGER_DIR) or getattr(profile, "ledger_dir", None)
    if not raw or str(raw).startswith("${"):
        return []
    out = []
    for part in str(raw).split(os.pathsep):
        part = part.strip()
        if part:
            out.append(Path(part).expanduser())
    return out


def ledger_dir(profile) -> Path | None:
    """The first configured ledger folder, or None (kept for callers that want one)."""
    dirs = ledger_dirs(profile)
    return dirs[0] if dirs else None


def _read(path: Path) -> tuple[dict[str, str], str] | None:
    """Frontmatter pairs and the body; None when the file has no frontmatter or cannot be read."""
    try:
        with open(path, encoding="utf-8", errors="replace", newline=None) as fh:
            first = fh.readline()
            if first.strip() != "---":
                return None
            fm: dict[str, str] = {}
            closed = False
            for _ in range(FRONTMATTER_MAX_LINES):
                line = fh.readline()
                if not line:
                    break
                if line.strip() == "---":
                    closed = True
                    break
                if ":" not in line or line.startswith((" ", "\t", "-")):
                    continue
                k, v = line.split(":", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]                 # a quoted scalar keeps its # as is
                else:
                    v = re.split(r"\s+#", v, maxsplit=1)[0].strip()
                if v in (">", "|", ">-", "|-"):
                    v = ""                      # a folded/literal block: not a scalar the card can show
                fm[k.strip()] = v
            if not closed:
                return None
            return fm, fh.read(BODY_MAX_BYTES)
    except OSError:
        return None


def _last_draft(body: str) -> str | None:
    heads = list(_DRAFT_HEADING.finditer(body))
    if not heads:
        return None
    start = heads[-1].end()
    nxt = _ANY_HEADING.search(body, start)
    section = body[start:nxt.start()] if nxt else body[start:]
    section = section.strip("\n")
    return section if section.strip() else None        # an empty draft section is no text


def find_ledger(directories, comm_id: str | None) -> LedgerEntry | None:
    """The newest ledger file (across the folders) whose frontmatter carries this `comm_id`."""
    if not directories or not comm_id:
        return None
    dirs = [directories] if isinstance(directories, (str, Path)) else list(directories)
    files: list[Path] = []
    for d in dirs:
        p = Path(d).expanduser()
        if not p.is_dir():
            continue
        try:
            files.extend(p.glob("*.md"))
        except OSError:
            continue
    try:
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    found: LedgerEntry | None = None
    for f in files:
        got = _read(f)
        if not got or got[0].get("comm_id") != comm_id:
            continue
        if found is not None:
            found.duplicates += 1          # an older file with the same id: the newest one is shown
            continue
        fm, body = got
        audit = fm.get("audit")
        found = LedgerEntry(
            path=str(f), comm_id=comm_id,
            audit=audit.lower() if audit is not None else None,
            audit_at=fm.get("audit_at") or None,
            intention=fm.get("intention") or None,
            status=fm.get("status") or None,
            draft=_last_draft(body),
        )
    return found


_T = {
    "ru": {
        "unavailable": "ledger: ⚠ папка ledger недоступна",
        "unread": "ledger: ⚠ не прочитан (comm {comm})",
        "not_found": "ledger: ⚠ не найден (comm {comm})",
        "dropped": "ledger: ⚠ помечен dropped · {name}",
        "pass_blocks": "ledger: ✅ аудит Cicero{when} (текст в blocks — не сверен) · {name}",
        "pass_unbound": "ledger: ✅ аудит Cicero{when} (текст не сверен) · {name}",
        "pass_no_draft": "ledger: ⚠ аудит{when}, но в ledger нет текста (## Draft) · {name}",
        "pass_other_text": "ledger: ⚠ аудит{when} был для другого текста — допиши Draft vN и перепроверь · {name}",
        "pass": "ledger: ✅ аудит Cicero{when} · {name}",
        "skipped": "ledger: ☑ аудит пропущен ({audit}) · {name}",
        "other": "ledger: ⚠ аудит: {audit} · {name}",
        "none": "ledger: ⚠ аудита нет · {name}",
        "dup": " (+{n} файл с тем же comm_id)",
    },
    "en": {
        "unavailable": "ledger: ⚠ ledger folder unavailable",
        "unread": "ledger: ⚠ unreadable (comm {comm})",
        "not_found": "ledger: ⚠ not found (comm {comm})",
        "dropped": "ledger: ⚠ marked dropped · {name}",
        "pass_blocks": "ledger: ✅ Cicero audit{when} (blocks-only text, not compared) · {name}",
        "pass_unbound": "ledger: ✅ Cicero audit{when} (text not compared) · {name}",
        "pass_no_draft": "ledger: ⚠ audit{when}, but the ledger holds no text (## Draft) · {name}",
        "pass_other_text": "ledger: ⚠ audit{when} was for a different text — append Draft vN and re-audit · {name}",
        "pass": "ledger: ✅ Cicero audit{when} · {name}",
        "skipped": "ledger: ☑ audit skipped ({audit}) · {name}",
        "other": "ledger: ⚠ audit: {audit} · {name}",
        "none": "ledger: ⚠ no audit · {name}",
        "dup": " (+{n} file with the same comm_id)",
    },
}


def prompt_lines(profile, comm_id: str | None, text: str | None = None) -> list[str]:
    """Lines for the approval prompt: `ledger: …` and `intention: …`; [] when no ledger dir is set.

    `text` is what the card shows; a passed audit counts only when it equals the ledger's
    last `## Draft` section (whitespace-insensitive) — an edited text reads ⚠ until re-audited.
    Wording follows the profile language; the shape `ledger: <✅|☑|⚠> <text> · <file>` is fixed
    (the approval-card mod parses it).
    """
    t = _T.get(getattr(profile, "language", "en"), _T["en"])
    try:
        dirs = ledger_dirs(profile)
        if not dirs or not comm_id:
            return []
        if not any(d.is_dir() for d in dirs):
            return [t["unavailable"]]
        entry = find_ledger(dirs, comm_id)
    except Exception:            # never let a ledger quirk reach the hook (a `~user` path, a bad fs)
        return [t["unread"].format(comm=comm_id)]
    if entry is None:
        return [t["not_found"].format(comm=comm_id)]
    name = Path(entry.path).name
    if entry.duplicates:
        name += t["dup"].format(n=entry.duplicates)
    state = entry.audit_state
    when = f" {entry.audit_at}" if entry.audit_at else ""
    v = dict(name=name, when=when, audit=entry.audit, comm=comm_id)
    if (entry.status or "").lower() == "dropped":
        lines = [t["dropped"].format(**v)]
    elif state == "passed":
        if text == "":
            lines = [t["pass_blocks"].format(**v)]       # a blocks-only send carries no plain text to compare
        elif text is None:
            lines = [t["pass_unbound"].format(**v)]
        elif entry.draft is None:
            lines = [t["pass_no_draft"].format(**v)]
        elif entry.text_matches(text) is False:
            lines = [t["pass_other_text"].format(**v)]
        else:
            lines = [t["pass"].format(**v)]
    elif state == "skipped":
        lines = [t["skipped"].format(**v)]
    elif state == "other":
        lines = [t["other"].format(**v)]
    else:
        lines = [t["none"].format(**v)]
    if entry.intention:
        lines.append(f"intention: {entry.intention}")
    return lines
