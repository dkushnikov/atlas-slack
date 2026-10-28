#!/usr/bin/env python3
"""Level-2 live check of the v2 core slice — run by the human, against their own bot DM only.

    ATLAS_SLACK_PROFILE=<ctx> PYTHONPATH=<worktree>/src python3 <worktree>/scripts/v2-level2-check.py doctor
    ATLAS_SLACK_PROFILE=<ctx> PYTHONPATH=<worktree>/src python3 <worktree>/scripts/v2-level2-check.py repeat
    ATLAS_SLACK_PROFILE=<ctx> PYTHONPATH=<worktree>/src python3 <worktree>/scripts/v2-level2-check.py journal

doctor   two live `auth.test` calls (user + bot token) compared with the profile — no write.
repeat   the decision contract outside Claude Code, with ONE real post to `person_bot_dm_id`:
         issue a decision the way the hook does → send it → send the same approved input
         again (must refuse decision_already_used, no second post) → send a modified copy
         (must refuse decision_mismatch, no post). Asks for "y" before the one real post.
journal  print the journal rows (ops + last calls) for the active profile.

Tokens are read from the env vars the profile names; their values are never printed.
"""

import json
import sqlite3
import sys

from atlas_slack.door import Door, _SdkTransport
from atlas_slack.gate import hook_output
from atlas_slack.journal import open_journal
from atlas_slack.profile import load_profile

TOOL = "mcp__plugin_atlas-slack_atlas-slack__slack_api"


def doctor() -> int:
    p = load_profile()
    door = Door(profile=p)
    t = _SdkTransport(p)
    bad = 0
    for as_bot in (False, True):
        resp = t("auth.test", {}, as_bot)
        shown = {k: resp.get(k) for k in ("ok", "team_id", "team", "user_id", "user", "bot_id", "error")}
        problems = door.doctor(resp, as_bot=as_bot)
        print(f"{'bot ' if as_bot else 'user'} auth.test: {json.dumps(shown)}")
        for x in problems:
            print(f"   MISMATCH: {x}")
        bad += len(problems)
    print("doctor: OK" if not bad else f"doctor: {bad} problem(s)")
    return 1 if bad else 0


def repeat() -> int:
    from atlas_slack import core
    p = load_profile()
    if not p.person_bot_dm_id:
        print("profile has no person_bot_dm_id — refusing to pick a target")
        return 2
    ti = {"method": "chat.postMessage", "as_bot": True,
          "params": {"channel": p.person_bot_dm_id, "text": "atlas-slack v2 level-2 check — repeat test"},
          "provenance": {"cls": "B-h", "comm_id": f"v2-level2-repeat-{int(__import__('time').time())}",
                         "prepared_by": "v2-level2-check"}}
    out = hook_output({"tool_name": TOOL, "tool_input": ti, "session_id": "level2"})["hookSpecificOutput"]
    print("hook:", out["permissionDecision"])
    print(out.get("permissionDecisionReason", ""))
    if out["permissionDecision"] != "ask":
        return 1
    if input(f"\nPost ONE message to {p.person_bot_dm_id} as the bot? [y/N] ").strip().lower() != "y":
        print("not sent")
        return 0
    up = out["updatedInput"]
    t = _SdkTransport(p)
    res = core.execute_send(up["method"], up["params"], True, up["provenance"], profile=p, transport=t)
    print(f"1st send: ok ts={res.data.get('ts')} op={res.op_id}")
    for label, inp in (("repeat (same approval)", up),
                       ("modified after approval", {**up, "params": {**up["params"], "text": "tampered"}})):
        try:
            core.execute_send(inp["method"], inp["params"], True, inp["provenance"], profile=p, transport=t)
            print(f"{label}: SENT — this is a FAILURE of the check")
            return 1
        except core.Refused as e:
            print(f"{label}: refused {e.code} (no Slack call)")
    return 0


def journal() -> int:
    p = load_profile()
    j = open_journal(p.journal_dir)
    c = sqlite3.connect(str(j.path))
    print(f"journal: {j.path}")
    for r in c.execute("SELECT op_id, comm_id, cls, voice, outcome, slack_ts, error_code, "
                       "datetime(intent_at,'unixepoch') FROM ops ORDER BY intent_at"):
        print("  op  ", r)
    for r in c.execute("SELECT datetime(at,'unixepoch'), method, token_kind, status, error_code, op_id "
                       "FROM calls ORDER BY id DESC LIMIT 15"):
        print("  call", r)
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    sys.exit({"doctor": doctor, "repeat": repeat, "journal": journal}.get(cmd, lambda: (print(__doc__), 2)[1])())
