"""Keep host-specific and personal tokens out of this repository.

Scans every tracked text file (and, with --commits <range>, the commit messages
in that range) for: home-directory paths, references to the maintainer's private
trees and vault folders, session-tracking trailers, workspace-specific Slack ids,
and a short list of personal names kept as salted hashes so the list itself does
not name anyone.

Run: python3 tests/test_no_private_tokens.py [--commits origin/main..HEAD] [--all]
     python3 -m pytest -q tests/test_no_private_tokens.py
     python3 tests/test_no_private_tokens.py --hash <token>     # the hash to add to the list

Every tracked text file is scanned (`--all` is accepted and changes nothing since 0.7.0).
"""
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PATTERNS = [
    (re.compile(r"/Users/[A-Za-z0-9_.-]+"), "home path"),
    (re.compile(r"~/(Atlas|Obsidian|Sources|Data|Code|Projects|Stores)\b"), "private tree path"),
    (re.compile(r"\b_claude/"), "private working folder"),
    (re.compile(r"\bProjects/Atlas Slack\b"), "private vault folder"),
    (re.compile("Claude-" + "Session:"), "session trailer"),  # split so this file does not match itself
    (re.compile("Session-" + "Id:"), "session trailer"),
    (re.compile(r"\bsession[_ ]?[0-9a-f]{8,}\b", re.I), "session id"),
]
SALT = "atlas-slack-public-repo"
TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9'’-]{2,}")
# URL allowance: the maintainer's GitHub handle is public in repository URLs.
URL_RE = re.compile(r"https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?(?:@[A-Za-z0-9_./-]+)?")

HISTORY_PATHS: tuple[str, ...] = ()      # nothing is exempt: the working history lives in the maintainer's vault, not here
SKIP_NAMES = ("private-token-hashes.txt", "test_no_private_tokens.py")
BINARY_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".lock")


def _hash(token: str) -> str:
    return hashlib.sha1((SALT + token.lower()).encode("utf-8")).hexdigest()


def _load_private_hashes() -> set:
    extra = ROOT / "tests" / "private-token-hashes.txt"
    hashes = set()
    if extra.exists():
        for line in extra.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                hashes.add(line.split()[0])
    return hashes


def scan_text(text: str, where: str, hashes: set) -> list:
    hits = []
    stripped = URL_RE.sub("<url>", text)
    for pat, label in PATTERNS:
        for m in pat.finditer(stripped):
            hits.append(f"{where}: {label}: {m.group(0)}")
    for m in TOKEN_RE.finditer(stripped):
        if _hash(m.group(0)) in hashes:
            line = stripped.count("\n", 0, m.start()) + 1
            hits.append(f"{where}:{line}: private name")
    return hits


def tracked_files() -> list:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True).stdout
    return [p for p in out.split("\n") if p and not p.endswith(BINARY_SUFFIXES)]


def is_history(rel: str) -> bool:
    return any(rel == h or rel.startswith(h) for h in HISTORY_PATHS)


def scan_repo(hashes: set, include_history: bool = False) -> list:
    hits = []
    for rel in tracked_files():
        path = ROOT / rel
        if path.name in SKIP_NAMES or (is_history(rel) and not include_history):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits += scan_text(text, rel, hashes)
    return hits


def scan_commits(rng: str, hashes: set) -> list:
    out = subprocess.run(["git", "-C", str(ROOT), "log", "--format=%H%n%B%n==END==", rng],
                         capture_output=True, text=True, check=True).stdout
    hits = []
    for block in out.split("==END=="):
        block = block.strip()
        if not block:
            continue
        sha, _, body = block.partition("\n")
        hits += scan_text(body, f"commit {sha[:8]}", hashes)
    return hits


def test_tracked_files_have_no_private_tokens():
    hits = scan_repo(_load_private_hashes())
    assert not hits, "\n".join(hits)


if __name__ == "__main__":
    hashes = _load_private_hashes()
    if "--hash" in sys.argv:  # helper: print the hash of a token to add to the list
        print(_hash(sys.argv[sys.argv.index("--hash") + 1]))
        sys.exit(0)
    hits = scan_repo(hashes, include_history="--all" in sys.argv)
    if "--commits" in sys.argv:
        hits += scan_commits(sys.argv[sys.argv.index("--commits") + 1], hashes)
    for h in hits:
        print(h)
    print(f"private-token scan: {len(hits)} hit(s)")
    sys.exit(1 if hits else 0)
