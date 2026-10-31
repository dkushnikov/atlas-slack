"""The three version strings move together; the wire decision carries the hook's."""
import json
import re
from pathlib import Path

from atlas_slack import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_versions_agree():
    py = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    pj = json.loads((ROOT / "plugins/atlas-slack/.claude-plugin/plugin.json").read_text())["version"]
    assert __version__ == py == pj


def test_version_skew_refuses_before_any_call(isolated_door_dirs, monkeypatch):
    from test_v2_slice import Env, post, refused
    env = Env("atlas-like", isolated_door_dirs, monkeypatch)
    ti = env.approve(post(comm_id="skew-1"))
    assert ti["provenance"]["decision"]["v"] == __version__
    ti["provenance"]["decision"]["v"] = "0.5.3"
    refused(env, ti, "version_skew")
    ti2 = env.approve(post(comm_id="skew-2"))
    del ti2["provenance"]["decision"]["v"]                     # a pre-0.5.4 hook: no version on the wire …
    env.journal._conn.execute("UPDATE decisions SET adapter=replace(adapter, ' v=' || ?, '')", (__version__,))   # … nor in the row
    refused(env, ti2, "version_skew")
    assert env.transport.calls == []
    env.send(env.approve(post(comm_id="skew-3")))
    assert len(env.transport.calls) == 1
