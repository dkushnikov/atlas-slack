"""`init --policy-path PATH [--pin | --no-pin]` (0.7.3): the door attaches a context policy to an existing profile.

Level 1: no Slack (the CLI's `_auth` is replaced by a tripwire — the policy path needs no token and no network),
profiles under the test's own ATLAS_SLACK_CONFIG_DIR.
"""

import hashlib

import pytest
import yaml

from atlas_slack import __main__ as cli
from atlas_slack.policy import load_policy
from atlas_slack.profile import load_profile

WS, PERSON, BOT = "TACME00001", "UACMEPRSN", "UACMEBOT1"
HEADER = "# atlas-slack profile `acme` — written by init; holds no secrets\n# workspace acme · person pat · bot atlas\n"
IDS = {"workspace_id": WS, "person_user_id": PERSON, "bot_user_id": BOT, "person_bot_dm_id": "DACMEBOTDM",
       "token_sources": {"user": "ACME_SLACK_USER", "bot": "ACME_SLACK_BOT"}, "language": "en", "freeze": False}
POLICY = 'version: "2026-11-05.1"\nratified_by: test\nrequired_checks:\n  A: [cicero]\n'


@pytest.fixture
def cfg(isolated_door_dirs, monkeypatch):
    d = isolated_door_dirs / "config"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("ATLAS_SLACK_PROFILE", raising=False)

    def no_network(*a, **k):
        raise AssertionError("init --policy-path must not call Slack")
    monkeypatch.setattr(cli, "_auth", no_network)
    monkeypatch.setattr(cli, "_bot_dm", no_network)
    return d


def profile_file(cfg, name="acme", body=None):
    p = cfg / f"{name}.yaml"
    p.write_text(HEADER + yaml.safe_dump(IDS, sort_keys=False) if body is None else body, encoding="utf-8")
    return p


def policy_file(tmp_path, text=POLICY, name="acme.policy.yaml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def run(capsys, *argv):
    with pytest.raises(SystemExit) as e:
        cli.main(["init", *argv])
    out = capsys.readouterr()
    return e.value.code, out.out, out.err


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ------------------------------------------------------------------ writes, pins, unpins

def test_writes_the_path_and_keeps_every_other_key_and_line(cfg, tmp_path, capsys):
    prof = profile_file(cfg)
    pol = policy_file(tmp_path)
    before = prof.read_text()
    rc, out, err = run(capsys, "--profile", "acme", "--policy-path", str(pol))
    assert rc == 0, err
    assert out == f"policy: 2026-11-05.1 sha256 {sha(pol)[:12]} unpinned {pol}\n"
    after = prof.read_text()
    assert after.startswith(before)                                 # header comment and every line kept, byte for byte
    data = yaml.safe_load(after)
    assert data == {**IDS, "policy_path": str(pol)}                  # identity fields untouched
    p = load_profile("acme")
    assert not p.errors and p.policy_path == str(pol) and p.policy_sha256 is None
    st = load_policy(p)
    assert st.state == "file" and st.data["required_checks"] == {"A": ["cicero"]}


def test_pin_and_unpin(cfg, tmp_path, capsys):
    prof = profile_file(cfg)
    pol = policy_file(tmp_path)
    rc, out, _ = run(capsys, "--profile", "acme", "--policy-path", str(pol), "--pin")
    assert rc == 0 and out == f"policy: 2026-11-05.1 sha256 {sha(pol)[:12]} pinned {pol}\n"
    assert load_profile("acme").policy_sha256 == sha(pol)
    assert load_policy(load_profile("acme")).state == "file"       # the digest init pins is the one the loader compares
    rc, out, _ = run(capsys, "--profile", "acme", "--policy-path", str(pol), "--no-pin")
    assert rc == 0 and out.rstrip().endswith(f"unpinned {pol}")
    assert "policy_sha256" not in yaml.safe_load(prof.read_text())
    assert load_profile("acme").policy_sha256 is None


def test_neither_flag_keeps_a_matching_pin(cfg, tmp_path, capsys):
    pol = policy_file(tmp_path)
    profile_file(cfg)
    run(capsys, "--profile", "acme", "--policy-path", str(pol), "--pin")
    rc, out, _ = run(capsys, "--profile", "acme", "--policy-path", str(pol))
    assert rc == 0 and " pinned " in out and load_profile("acme").policy_sha256 == sha(pol)


def test_a_stale_pin_refuses_without_pin(cfg, tmp_path, capsys):
    prof = profile_file(cfg)
    pol = policy_file(tmp_path)
    run(capsys, "--profile", "acme", "--policy-path", str(pol), "--pin")
    pol.write_text(POLICY + "ratified_at: \"2026-11-06\"\n")         # the person edited the file after pinning
    before = prof.read_bytes()
    rc, out, err = run(capsys, "--profile", "acme", "--policy-path", str(pol))
    assert rc == 1 and out == ""
    assert "pins sha256" in err and "--pin" in err and "policy_invalid" in err
    assert prof.read_bytes() == before
    rc, out, _ = run(capsys, "--profile", "acme", "--policy-path", str(pol), "--pin")
    assert rc == 0 and load_profile("acme").policy_sha256 == sha(pol)


def test_tilde_path_is_stored_as_given(cfg, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "work").mkdir()
    pol = policy_file(tmp_path / "work")
    prof = profile_file(cfg)
    rc, out, err = run(capsys, "--profile", "acme", "--policy-path", "~/work/acme.policy.yaml", "--pin")
    assert rc == 0, err
    assert out.rstrip().endswith("pinned ~/work/acme.policy.yaml")
    assert yaml.safe_load(prof.read_text())["policy_path"] == "~/work/acme.policy.yaml"
    p = load_profile("acme")
    assert p.policy_path == str(pol) and load_policy(p).state == "file"   # the loader expands it


def test_idempotent_rerun(cfg, tmp_path, capsys):
    prof = profile_file(cfg)
    pol = policy_file(tmp_path)
    first = run(capsys, "--profile", "acme", "--policy-path", str(pol), "--pin")
    text = prof.read_bytes()
    second = run(capsys, "--profile", "acme", "--policy-path", str(pol), "--pin")
    assert first == second and prof.read_bytes() == text


def test_an_existing_policy_path_line_is_replaced_not_duplicated(cfg, tmp_path, capsys):
    old = policy_file(tmp_path, name="old.yaml")
    new = policy_file(tmp_path, POLICY.replace(".1", ".2"), name="new.yaml")
    prof = profile_file(cfg, body=HEADER + yaml.safe_dump({**IDS, "policy_path": str(old)}, sort_keys=False)
                        + "names: {UACMEPRSN: Pat}   # display labels\n")
    rc, out, _ = run(capsys, "--profile", "acme", "--policy-path", str(new))
    assert rc == 0 and "2026-11-05.2" in out
    text = prof.read_text()
    assert text.count("policy_path:") == 1 and "# display labels" in text
    assert yaml.safe_load(text)["names"] == {"UACMEPRSN": "Pat"}


def test_default_profile_and_a_file_without_trailing_newline(cfg, tmp_path, capsys):
    prof = profile_file(cfg, name="default", body=f"workspace_id: {WS}\nbot_user_id: {BOT}")
    pol = policy_file(tmp_path)
    rc, out, err = run(capsys, "--policy-path", str(pol))
    assert rc == 0, err
    assert yaml.safe_load(prof.read_text()) == {"workspace_id": WS, "bot_user_id": BOT, "policy_path": str(pol)}


def test_a_flow_mapping_profile_is_rewritten_as_a_whole(cfg, tmp_path, capsys):
    prof = profile_file(cfg, body=f"# header\n{{workspace_id: {WS}, bot_user_id: {BOT}}}\n")
    pol = policy_file(tmp_path)
    rc, out, err = run(capsys, "--profile", "acme", "--policy-path", str(pol))
    assert rc == 0 and "rewritten as a whole" in err
    text = prof.read_text()
    assert text.startswith("# header\n")
    assert yaml.safe_load(text) == {"workspace_id": WS, "bot_user_id": BOT, "policy_path": str(pol)}


# ------------------------------------------------------------------ refusals: nothing written

@pytest.mark.parametrize("text,msg", [
    ("version: [unclosed\n", "policy invalid: "),
    ('version: "1"\nrequired_checks: {X: [cicero]}\n', "unknown class"),
    ('version: "1"\nsurprise: 1\n', "unknown policy keys"),
    ("ratified_by: nobody\n", "version is required"),
])
def test_an_invalid_policy_is_refused_and_nothing_is_written(cfg, tmp_path, capsys, text, msg):
    prof = profile_file(cfg)
    pol = policy_file(tmp_path, text)
    before = prof.read_bytes()
    rc, out, err = run(capsys, "--profile", "acme", "--policy-path", str(pol), "--pin")
    assert rc == 1 and out == "" and msg in err and "nothing written" in err
    assert prof.read_bytes() == before


def test_a_missing_policy_file_is_refused(cfg, tmp_path, capsys):
    prof = profile_file(cfg)
    before = prof.read_bytes()
    rc, _, err = run(capsys, "--profile", "acme", "--policy-path", str(tmp_path / "nope.yaml"))
    assert rc == 1 and "policy file missing" in err and prof.read_bytes() == before


def test_a_missing_profile_is_refused(cfg, tmp_path, capsys):
    pol = policy_file(tmp_path)
    rc, out, err = run(capsys, "--profile", "acme", "--policy-path", str(pol))
    assert rc == 2 and out == ""
    assert "profile not found — run init (identity) first" in err
    assert not (cfg / "acme.yaml").exists()


@pytest.mark.parametrize("argv,msg", [
    (["--force", "--policy-path", "/x/p.yaml"], "does not combine"),
    (["--user-var", "ACME_SLACK_USER", "--bot-var", "ACME_SLACK_BOT", "--policy-path", "/x/p.yaml"], "does not combine"),
    (["--pin"], "go with --policy-path"),
    (["--no-pin"], "go with --policy-path"),
    (["--policy-path", "relative/p.yaml"], "must be absolute or start with ~"),
    (["--policy-path", " /x/p.yaml"], "no surrounding whitespace"),
    (["--policy-path", "/x/p.yaml\n"], "no surrounding whitespace"),
])
def test_combinations_and_bad_paths_are_refused(cfg, tmp_path, capsys, argv, msg):
    prof = profile_file(cfg)
    before = prof.read_bytes()
    rc, _, err = run(capsys, "--profile", "acme", *argv)
    assert rc == 2 and msg in err, err
    assert prof.read_bytes() == before


def test_pin_and_no_pin_are_exclusive(cfg, capsys):
    profile_file(cfg)
    with pytest.raises(SystemExit) as e:
        cli.main(["init", "--profile", "acme", "--policy-path", "/x/p.yaml", "--pin", "--no-pin"])
    assert e.value.code == 2 and "not allowed with" in capsys.readouterr().err


def test_identity_init_still_refuses_an_existing_profile(cfg, capsys):
    profile_file(cfg)
    rc, _, err = run(capsys, "--profile", "acme")            # no policy flags: the identity path, unchanged
    assert rc == 2 and "exists" in err


# ------------------------------------------------------------------ doctor shows the policy state

def _doctor(capsys, monkeypatch, *argv):
    answers = {False: {"ok": True, "team_id": WS, "user_id": PERSON, "_scopes": ["channels:history", "chat:write"]},
               True: {"ok": True, "team_id": WS, "user_id": BOT, "bot_id": "B0ACME", "_scopes": ["chat:write", "im:write"]}}
    monkeypatch.setattr(cli, "_auth", lambda as_bot, profile: answers[as_bot])
    with pytest.raises(SystemExit) as e:
        cli.main(["doctor", *argv])
    out = capsys.readouterr().out
    return e.value.code, out, [ln for ln in out.splitlines() if ln.startswith("next:")][-1]


def test_doctor_prints_the_policy_line(cfg, tmp_path, capsys, monkeypatch):
    profile_file(cfg)
    rc, out, _ = _doctor(capsys, monkeypatch, "--profile", "acme")
    assert "policy: built-in strict — the profile names no policy_path (init --profile acme --policy-path PATH" in out
    pol = policy_file(tmp_path)
    monkeypatch.setattr(cli, "_auth", lambda *a: (_ for _ in ()).throw(AssertionError("no network")))
    run(capsys, "--profile", "acme", "--policy-path", str(pol), "--pin")
    rc, out, nxt = _doctor(capsys, monkeypatch, "--profile", "acme")
    assert f"policy: 2026-11-05.1 sha256 {sha(pol)[:12]} pinned {pol} · valid" in out
    assert "required checks (core sends to others; the person's bot DM is exempt): A: cicero" in out
    assert nxt.startswith("next: send")
    pol.write_text(POLICY + "# edited\n")
    rc, out, nxt = _doctor(capsys, monkeypatch, "--profile", "acme")
    assert "policy: ✗ invalid — sha256 mismatch" in out and f"pinned {pol}" in out
    assert nxt.startswith("next: fix-policy — policy invalid") and f"--policy-path {pol} --pin" in nxt
