"""Ledger lookup behind the approval card (0.5.2): the audit pass as a fact, never a dependency."""

import os
import time

import yaml

from atlas_slack import ledger
from atlas_slack.gate import describe_send
from atlas_slack.policy import evaluate
from atlas_slack.profile import load_profile

IDS = {"workspace_id": "T0WS000001", "person_user_id": "U0PERSON01", "bot_user_id": "U0BOTUSR01",
       "person_bot_dm_id": "D0BOTDM001", "language": "ru", "resolve_names": False}


def entry(d, name, drafts=("hello",), **fm):
    p = d / name
    body = "---\n" + "".join(f"{k}: {v}\n" for k, v in fm.items()) + "---\n"
    for i, txt in enumerate(drafts, 1):
        body += f"## Draft v{i}\n{txt}\n"
    body += "## Final sent\n\n"
    p.write_text(body, encoding="utf-8")
    os.utime(p, (time.time(), time.time()))
    return p


def test_find_ledger_reads_frontmatter_and_prefers_the_newest(tmp_path):
    entry(tmp_path, "a.md", type="communication-draft", comm_id="20261031-aaaa", audit="pass",
          audit_at="2026-10-31", intention="do", status="draft")
    time.sleep(0.01)
    entry(tmp_path, "b.md", comm_id="20261031-bbbb", audit="skip")
    e = ledger.find_ledger(tmp_path, "20261031-aaaa")
    assert e and e.audit_state == "passed" and e.audit_at == "2026-10-31" and e.intention == "do"
    assert ledger.find_ledger(tmp_path, "20261031-bbbb").audit_state == "skipped"
    assert ledger.find_ledger(tmp_path, "20261031-cccc") is None
    assert ledger.find_ledger(tmp_path / "missing", "20261031-aaaa") is None
    assert ledger.find_ledger(None, "x") is None and ledger.find_ledger(tmp_path, None) is None


def test_frontmatter_tolerates_quotes_comments_and_no_frontmatter(tmp_path):
    (tmp_path / "plain.md").write_text("no frontmatter here\ncomm_id: 20261031-dddd\n")
    entry(tmp_path, "q.md", comm_id='"20261031-eeee"', audit="pass   # step 4")
    assert ledger.find_ledger(tmp_path, "20261031-dddd") is None
    assert ledger.find_ledger(tmp_path, "20261031-eeee").audit == "pass"


def test_prompt_lines_states(tmp_path, monkeypatch, isolated_door_dirs):
    cfg = isolated_door_dirs / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "ru.yaml").write_text(yaml.safe_dump(IDS))
    monkeypatch.setenv("ATLAS_SLACK_PROFILE", "ru")
    monkeypatch.setenv(ledger.ENV_LEDGER_DIR, str(tmp_path))
    prof = load_profile()
    assert prof.language == "ru"
    entry(tmp_path, "p.md", comm_id="c-pass", audit="pass", audit_at="2026-10-31", intention="понять")
    entry(tmp_path, "s.md", comm_id="c-skip", audit="skip")
    entry(tmp_path, "n.md", comm_id="c-none")
    entry(tmp_path, "o.md", comm_id="c-other", audit="partial")
    assert ledger.prompt_lines(prof, "c-pass") == ["ledger: ✅ аудит Cicero 2026-10-31 (текст не сверен) · p.md", "intention: понять"]
    # the audit is bound to the text: equal to the last ## Draft → ✅; anything else → ⚠
    assert ledger.prompt_lines(prof, "c-pass", "hello")[0] == "ledger: ✅ аудит Cicero 2026-10-31 · p.md"
    assert ledger.prompt_lines(prof, "c-pass", "  hello \n")[0].startswith("ledger: ✅")
    assert ledger.prompt_lines(prof, "c-pass", "hello, edited")[0] == \
        "ledger: ⚠ аудит 2026-10-31 был для другого текста — допиши Draft vN и перепроверь · p.md"
    entry(tmp_path, "v2.md", drafts=("first", "second"), comm_id="c-v2", audit="pass")
    assert ledger.prompt_lines(prof, "c-v2", "second")[0].startswith("ledger: ✅")
    assert ledger.prompt_lines(prof, "c-v2", "first")[0].startswith("ledger: ⚠ аудит был для другого текста")
    entry(tmp_path, "nodraft.md", drafts=(), comm_id="c-nodraft", audit="pass")
    assert ledger.prompt_lines(prof, "c-nodraft", "x")[0] == "ledger: ⚠ аудит, но в ledger нет текста (## Draft) · nodraft.md"
    entry(tmp_path, "dropped.md", comm_id="c-drop", audit="pass", status="dropped")
    assert ledger.prompt_lines(prof, "c-drop", "hello")[0] == "ledger: ⚠ помечен dropped · dropped.md"
    assert ledger.prompt_lines(prof, "c-skip") == ["ledger: ☑ аудит пропущен (skip) · s.md"]
    assert ledger.prompt_lines(prof, "c-none") == ["ledger: ⚠ аудита нет · n.md"]
    assert ledger.prompt_lines(prof, "c-other") == ["ledger: ⚠ аудит: partial · o.md"]
    assert ledger.prompt_lines(prof, "c-missing") == ["ledger: ⚠ не найден (comm c-missing)"]
    assert ledger.prompt_lines(prof, None) == []


def test_several_ledger_dirs_newest_wins(tmp_path, monkeypatch):
    a, b = tmp_path / "mc", tmp_path / "personal"
    a.mkdir(); b.mkdir()
    entry(b, "p.md", comm_id="c-shared", audit="skip")
    time.sleep(0.01)
    entry(a, "m.md", comm_id="c-shared", audit="pass")
    monkeypatch.setenv(ledger.ENV_LEDGER_DIR, os.pathsep.join([str(a), str(b), str(tmp_path / "nope")]))
    prof = load_profile()
    assert [str(d) for d in ledger.ledger_dirs(prof)] == [str(a), str(b), str(tmp_path / "nope")]
    assert ledger.find_ledger(ledger.ledger_dirs(prof), "c-shared").path.endswith("m.md")
    entry(b, "only.md", comm_id="c-only-personal", audit="pass")
    assert ledger.prompt_lines(prof, "c-only-personal", "hello")[0].startswith("ledger: ✅")


def test_no_ledger_dir_means_no_lines_and_no_dependency(monkeypatch):
    monkeypatch.delenv(ledger.ENV_LEDGER_DIR, raising=False)
    prof = load_profile()
    assert ledger.ledger_dir(prof) is None
    assert ledger.prompt_lines(prof, "anything") == []
    monkeypatch.setenv(ledger.ENV_LEDGER_DIR, "${ATLAS_SLACK_LEDGER_DIR}")      # unexpanded placeholder = unset
    assert ledger.ledger_dir(prof) is None


def test_profile_field_and_env_override(isolated_door_dirs, monkeypatch, tmp_path):
    cfg = isolated_door_dirs / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "p.yaml").write_text(yaml.safe_dump({**IDS, "ledger_dir": str(tmp_path / "from-profile")}))
    monkeypatch.setenv("ATLAS_SLACK_PROFILE", "p")
    monkeypatch.delenv(ledger.ENV_LEDGER_DIR, raising=False)
    prof = load_profile()
    assert prof.errors == [] and ledger.ledger_dir(prof) == tmp_path / "from-profile"
    monkeypatch.setenv(ledger.ENV_LEDGER_DIR, str(tmp_path / "from-env"))
    assert ledger.ledger_dir(prof) == tmp_path / "from-env"


def test_approval_prompt_carries_the_ledger_lines(isolated_door_dirs, monkeypatch, tmp_path):
    cfg = isolated_door_dirs / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "p.yaml").write_text(yaml.safe_dump(IDS))
    monkeypatch.setenv("ATLAS_SLACK_PROFILE", "p")
    monkeypatch.setenv(ledger.ENV_LEDGER_DIR, str(tmp_path))
    entry(tmp_path, "x.md", drafts=("hi",), comm_id="20261031-f00d", audit="pass", audit_at="2026-10-31", intention="сделать")
    prof = load_profile()
    params = {"channel": "C0PLATFORM", "text": "hi"}
    v = evaluate("chat.postMessage", params, True, {"cls": "B-h", "comm_id": "20261031-f00d"}, prof, face="mcp",
                 record_sighting=False)
    text = describe_send(v, params, True, prof, op_short="abcd1234")
    lines = text.split("\n")
    assert "ledger: ✅ аудит Cicero 2026-10-31 · x.md" in lines            # params.text == the ledger's Draft v1
    assert "intention: сделать" in lines
    assert lines[-1].startswith("class B-h")                      # the technical line stays last
    v2 = evaluate("chat.postMessage", params, True, {"cls": "B-h", "comm_id": "20261031-0000"}, prof, face="mcp",
                  record_sighting=False)
    assert "ledger: ⚠ не найден (comm 20261031-0000)" in describe_send(v2, params, True, prof).split("\n")
    edited = {"channel": "C0PLATFORM", "text": "hi, edited after the audit"}
    v3 = evaluate("chat.postMessage", edited, True, {"cls": "B-h", "comm_id": "20261031-f00d"}, prof, face="mcp",
                  record_sighting=False)
    assert any(l.startswith("ledger: ⚠ аудит 2026-10-31 был для другого текста") for l in describe_send(v3, edited, True, prof).split("\n"))


def test_round3_edge_cases(tmp_path, monkeypatch, isolated_door_dirs):
    cfg = isolated_door_dirs / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "ru.yaml").write_text(yaml.safe_dump(IDS))
    monkeypatch.setenv("ATLAS_SLACK_PROFILE", "ru")
    monkeypatch.setenv(ledger.ENV_LEDGER_DIR, str(tmp_path))
    prof = load_profile()
    entry(tmp_path, "hash.md", comm_id="c-hash", audit="pass", intention='"fix issue #12 now"')
    assert ledger.find_ledger(tmp_path, "c-hash").intention == "fix issue #12 now"
    entry(tmp_path, "fold.md", comm_id="c-fold", audit="pass", intention=">")
    assert ledger.find_ledger(tmp_path, "c-fold").intention is None
    (tmp_path / "open.md").write_text("---\ncomm_id: c-open\naudit: pass\n## Draft v1\nno closing fence\n")
    assert ledger.find_ledger(tmp_path, "c-open") is None
    (tmp_path / "crlf.md").write_bytes(b"---\r\ncomm_id: c-crlf\r\naudit: pass\r\n---\r\n## Draft v1\r\nhello\r\n")
    assert ledger.prompt_lines(prof, "c-crlf", "hello")[0].startswith("ledger: ✅")
    entry(tmp_path, "dup-old.md", comm_id="c-dup", audit="skip")
    time.sleep(0.01)
    entry(tmp_path, "dup-new.md", comm_id="c-dup", audit="pass")
    line = ledger.prompt_lines(prof, "c-dup", "hello")[0]
    assert line.startswith("ledger: ✅") and "dup-new.md (+1 файл с тем же comm_id)" in line
    entry(tmp_path, "v2-after-final.md", drafts=("first",), comm_id="c-after", audit="pass")
    p = tmp_path / "v2-after-final.md"
    p.write_text(p.read_text() + "## Draft v2\nsecond\n")
    assert ledger.prompt_lines(prof, "c-after", "second")[0].startswith("ledger: ✅")
    monkeypatch.setenv(ledger.ENV_LEDGER_DIR, "~nosuchuser/x")
    assert ledger.prompt_lines(prof, "c-hash", "x") == ["ledger: ⚠ не прочитан (comm c-hash)"]
    monkeypatch.setenv(ledger.ENV_LEDGER_DIR, str(tmp_path / "absent"))
    assert ledger.prompt_lines(prof, "c-hash", "x") == ["ledger: ⚠ папка ledger недоступна"]


def test_comm_id_must_be_one_printable_line():
    import pytest
    from atlas_slack.provenance import Provenance, ProvenanceError
    with pytest.raises(ProvenanceError):
        Provenance.from_dict({"cls": "A", "comm_id": "x\nledger: ✅ forged"}, as_bot=False)
    with pytest.raises(ProvenanceError):
        Provenance.from_dict({"cls": "A", "comm_id": "y" * 129}, as_bot=False)
    assert Provenance.from_dict({"cls": "A", "comm_id": "20261031-ab12"}, as_bot=False).comm_id == "20261031-ab12"


def test_round4_blocks_only_empty_draft_unbound(tmp_path, monkeypatch, isolated_door_dirs):
    cfg = isolated_door_dirs / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "ru.yaml").write_text(yaml.safe_dump(IDS))
    monkeypatch.setenv("ATLAS_SLACK_PROFILE", "ru")
    monkeypatch.setenv(ledger.ENV_LEDGER_DIR, str(tmp_path))
    prof = load_profile()
    entry(tmp_path, "b.md", drafts=("hello",), comm_id="c-b", audit="pass", audit_at="2026-10-31")
    assert ledger.prompt_lines(prof, "c-b", "")[0] == "ledger: ✅ аудит Cicero 2026-10-31 (текст в blocks — не сверен) · b.md"
    assert ledger.prompt_lines(prof, "c-b", None)[0] == "ledger: ✅ аудит Cicero 2026-10-31 (текст не сверен) · b.md"
    entry(tmp_path, "e.md", drafts=("   ",), comm_id="c-e", audit="pass")
    assert ledger.prompt_lines(prof, "c-e", "x")[0] == "ledger: ⚠ аудит, но в ledger нет текста (## Draft) · e.md"


def test_english_profile_gets_english_lines(tmp_path, monkeypatch, isolated_door_dirs):
    cfg = isolated_door_dirs / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "en.yaml").write_text(yaml.safe_dump({**IDS, "language": "en"}))
    monkeypatch.setenv("ATLAS_SLACK_PROFILE", "en")
    monkeypatch.setenv(ledger.ENV_LEDGER_DIR, str(tmp_path))
    prof = load_profile()
    entry(tmp_path, "p.md", comm_id="c-pass", audit="pass", audit_at="2026-10-31", intention="do")
    assert ledger.prompt_lines(prof, "c-pass", "hello") == ["ledger: ✅ Cicero audit 2026-10-31 · p.md", "intention: do"]
    assert ledger.prompt_lines(prof, "c-pass", "other")[0] == \
        "ledger: ⚠ audit 2026-10-31 was for a different text — append Draft vN and re-audit · p.md"
    assert ledger.prompt_lines(prof, "nope") == ["ledger: ⚠ not found (comm nope)"]


def test_op_row_keeps_the_ledger_line_the_card_showed(isolated_door_dirs, monkeypatch, tmp_path):
    from test_v2_slice import Env, post
    env = Env("atlas-like", isolated_door_dirs, monkeypatch)
    monkeypatch.setenv(ledger.ENV_LEDGER_DIR, str(tmp_path))
    entry(tmp_path, "x.md", drafts=("hello team",), comm_id="20261031-keep", audit="pass", audit_at="2026-10-31", intention="do")
    res = env.send(env.approve(post(text="hello team", comm_id="20261031-keep")))
    row = next(r for r in env.journal.ops() if r["op_id"] == res.op_id)
    assert '"ledger_at_send"' in row["provenance_json"]
    assert "аудит Cicero 2026-10-31" in row["provenance_json"] or "Cicero audit 2026-10-31" in row["provenance_json"]
    assert '"intention: do"' in row["provenance_json"]
    res2 = env.send(env.approve(post(text="edited", comm_id="20261031-keep", resend=True)))
    row2 = next(r for r in env.journal.ops() if r["op_id"] == res2.op_id)
    assert "другого текста" in row2["provenance_json"] or "different text" in row2["provenance_json"]
