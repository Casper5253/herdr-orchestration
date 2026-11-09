"""The OpenCode hc status plugin, exercised through node.

The plugin is a plain ES module with node built-ins only, so these tests load
it in node and assert the exact OSC bytes, the no-op gates, the binding file
shape, and the temp-then-rename write.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "team" / "opencode-hc-status" / "index.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")

ESC = "\x1b"


def _run(tmp_path: Path, body: str) -> dict:
    driver = tmp_path / "driver.mjs"
    driver.write_text(body, encoding="utf-8")
    proc = subprocess.run(["node", str(driver)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _body(text: str, **values: object) -> str:
    for name, value in values.items():
        text = text.replace(f"__{name}__", json.dumps(value))
    return text


def test_install_manifest_installs_the_plugin() -> None:
    manifest = json.loads((ROOT / "team" / "install-files.json").read_text(encoding="utf-8"))
    assert {
        "source": "team/opencode-hc-status/index.js",
        "target": ".config/opencode/plugins/opencode-hc-status.js",
    } in manifest


def test_osc_records_are_exact(tmp_path: Path) -> None:
    body = _body(
        """
import * as p from __PLUGIN__;
console.log(JSON.stringify({
  working: p.record("working"),
  idle: p.record("idle"),
  blocked: p.record("blocked", "permission"),
  error: p.record("error"),
  invalid: p.record("bogus"),
}));
""",
        PLUGIN=PLUGIN.as_uri(),
    )
    out = _run(tmp_path, body)
    assert out == {
        "working": f"{ESC}]7501;state=working:app=opencode{ESC}\\",
        "idle": f"{ESC}]7501;state=idle:app=opencode{ESC}\\",
        "blocked": f"{ESC}]7501;state=blocked:app=opencode:kind=permission{ESC}\\",
        "error": f"{ESC}]7501;state=error:app=opencode{ESC}\\",
        "invalid": "",
    }


def test_no_hooks_outside_hc_or_the_team(tmp_path: Path) -> None:
    body = _body(
        """
import * as p from __PLUGIN__;
const off = await p.createPlugin({env: {}})({directory: "repo"});
const hcOnly = await p.createPlugin({env: {HC_TERMINAL_ID: "t8"}})({});
const other = await p.createPlugin({env: {HC_TERMINAL_ID: "t8", OPENCODE_CONFIG: "C:/tmp/other.jsonc"}})({});
console.log(JSON.stringify({
  enabled: {
    none: p.enabled({}),
    hcOnly: p.enabled({HC_TERMINAL_ID: "t8"}),
    team: p.enabled({HC_TERMINAL_ID: "t8", OPENCODE_CONFIG: "C:/repo/opencode-trusted-dev.jsonc"}),
    other: p.enabled({HC_TERMINAL_ID: "t8", OPENCODE_CONFIG: "C:/repo/other.jsonc"}),
  },
  offHooks: Object.keys(off),
  hcOnlyHooks: Object.keys(hcOnly),
  otherHooks: Object.keys(other),
}));
""",
        PLUGIN=PLUGIN.as_uri(),
    )
    out = _run(tmp_path, body)
    assert out["enabled"] == {"none": False, "hcOnly": False, "team": True, "other": False}
    assert out["offHooks"] == []
    assert out["hcOnlyHooks"] == []
    assert out["otherHooks"] == []


def test_status_across_a_turn_and_binding_shape(tmp_path: Path) -> None:
    state = tmp_path / "state"
    body = _body(
        """
import * as p from __PLUGIN__;
import fs from "node:fs";
const writes = [];
const stream = { isTTY: true, write(s) { writes.push(s); } };
const factory = p.createPlugin({
  env: {HC_TERMINAL_ID: "t8", OPENCODE_CONFIG: "C:/repo/opencode-trusted-dev.jsonc"},
  stdout: stream, stderr: null, stateDir: __STATE__, cwd: "C:/repo", pid: 4242,
  now: () => new Date("2026-11-09T00:00:00.000Z"),
});
const hooks = await factory({directory: "C:/repo"});
await hooks["chat.message"]({ sessionID: "ses_one" }, {});
await hooks.event({event: {type: "permission.asked", properties: {sessionID: "ses_one"}}});
await hooks.event({event: {type: "permission.replied", properties: {sessionID: "ses_one"}}});
await hooks.event({event: {type: "session.idle", properties: {sessionID: "ses_one"}}});
await hooks.event({event: {type: "session.error", properties: {sessionID: "ses_one"}}});
const binding = JSON.parse(fs.readFileSync(__STATE__ + "/hc-bindings/t8.json", "utf8"));
const tmp = fs.readdirSync(__STATE__ + "/hc-bindings").filter((name) => name.endsWith(".tmp"));
console.log(JSON.stringify({writes, binding, tmp}));
""",
        PLUGIN=PLUGIN.as_uri(),
        STATE=str(state),
    )
    out = _run(tmp_path, body)
    assert out["writes"] == [
        f"{ESC}]7501;state=working:app=opencode{ESC}\\",
        f"{ESC}]7501;state=blocked:app=opencode:kind=permission{ESC}\\",
        f"{ESC}]7501;state=working:app=opencode{ESC}\\",
        f"{ESC}]7501;state=idle:app=opencode{ESC}\\",
        f"{ESC}]7501;state=error:app=opencode{ESC}\\",
    ]
    assert out["binding"] == {
        "terminal": "t8",
        "session_id": "ses_one",
        "cwd": "C:/repo",
        "pid": 4242,
        "started_at": "2026-11-09T00:00:00.000Z",
    }
    assert out["tmp"] == []


def test_binding_overwrites_only_its_own_terminal(tmp_path: Path) -> None:
    state = tmp_path / "state"
    body = _body(
        """
import * as p from __PLUGIN__;
import fs from "node:fs";
const options = (terminal, now) => p.createPlugin({
  env: {HC_TERMINAL_ID: terminal, OPENCODE_CONFIG: "C:/repo/opencode-trusted-dev.jsonc"},
  stdout: {isTTY: true, write() {}}, stderr: null, stateDir: __STATE__, cwd: "C:/repo",
  pid: 1000, now: () => new Date(now),
});
const t8 = await options("t8", "2026-11-09T00:00:00.000Z")({});
await t8["chat.message"]({sessionID: "ses_one"}, {});
const t9 = await options("t9", "2026-11-09T00:01:00.000Z")({});
await t9["chat.message"]({sessionID: "ses_two"}, {});
const same = await options("t8", "2026-11-09T00:02:00.000Z")({});
await same["chat.message"]({sessionID: "ses_one"}, {});
const before = {
  t8: JSON.parse(fs.readFileSync(__STATE__ + "/hc-bindings/t8.json", "utf8")),
  t9: JSON.parse(fs.readFileSync(__STATE__ + "/hc-bindings/t9.json", "utf8")),
};
await same["chat.message"]({sessionID: "ses_three"}, {});
const after = {
  t8: JSON.parse(fs.readFileSync(__STATE__ + "/hc-bindings/t8.json", "utf8")),
  t9: JSON.parse(fs.readFileSync(__STATE__ + "/hc-bindings/t9.json", "utf8")),
  files: fs.readdirSync(__STATE__ + "/hc-bindings").sort(),
};
console.log(JSON.stringify({before, after}));
""",
        PLUGIN=PLUGIN.as_uri(),
        STATE=str(state),
    )
    out = _run(tmp_path, body)
    assert out["before"]["t8"]["session_id"] == "ses_one"
    assert out["before"]["t8"]["started_at"] == "2026-11-09T00:00:00.000Z"
    assert out["before"]["t9"]["session_id"] == "ses_two"
    assert out["after"]["t8"]["session_id"] == "ses_three"
    assert out["after"]["t8"]["started_at"] == "2026-11-09T00:02:00.000Z"
    assert out["after"]["t9"] == out["before"]["t9"]
    assert out["after"]["files"] == ["t8.json", "t9.json"]


def test_child_session_does_not_replace_the_root_binding(tmp_path: Path) -> None:
    state = tmp_path / "state"
    body = _body(
        """
import * as p from __PLUGIN__;
import fs from "node:fs";
const writes = [];
const hooks = await p.createPlugin({
  env: {HC_TERMINAL_ID: "t10", OPENCODE_CONFIG: "C:/repo/opencode-trusted-dev.jsonc"},
  stdout: {isTTY: true, write(s) { writes.push(s); }}, stderr: null,
  stateDir: __STATE__, cwd: "C:/repo", pid: 1001,
  now: () => new Date("2026-11-09T00:00:00.000Z"),
})({});
await hooks.event({event: {type: "session.created", properties: {info: {id: "root", parentID: null}}}});
await hooks.event({event: {type: "session.created", properties: {info: {id: "child", parentID: "root"}}}});
await hooks.event({event: {type: "permission.asked", properties: {sessionID: "child"}}});
const binding = JSON.parse(fs.readFileSync(__STATE__ + "/hc-bindings/t10.json", "utf8"));
console.log(JSON.stringify({binding, writes}));
""",
        PLUGIN=PLUGIN.as_uri(),
        STATE=str(state),
    )
    out = _run(tmp_path, body)
    assert out["binding"]["session_id"] == "root"
    assert out["writes"] == [f"{ESC}]7501;state=blocked:app=opencode:kind=permission{ESC}\\"]


def test_atomic_write_uses_a_temp_file_then_renames(tmp_path: Path) -> None:
    target = tmp_path / "state" / "hc-bindings" / "t1.json"
    body = _body(
        """
import * as p from __PLUGIN__;
const calls = [];
const io = {
  mkdirSync(dir) { calls.push(["mkdir", dir]); },
  readFileSync() { throw new Error("absent"); },
  writeFileSync(file, data) { calls.push(["write", file, data]); },
  renameSync(from, to) { calls.push(["rename", from, to]); },
};
p.atomicWriteJson(__TARGET__, {terminal: "t1"}, io);
console.log(JSON.stringify(calls));
""",
        PLUGIN=PLUGIN.as_uri(),
        TARGET=str(target),
    )
    calls = _run(tmp_path, body)
    assert calls[0] == ["mkdir", str(target.parent)]
    assert calls[1][0] == "write"
    assert calls[1][1].endswith(".tmp")
    assert json.loads(calls[1][2]) == {"terminal": "t1"}
    assert calls[2] == ["rename", calls[1][1], str(target)]


def test_event_and_status_mapping(tmp_path: Path) -> None:
    body = _body(
        """
import * as p from __PLUGIN__;
console.log(JSON.stringify({
  idle: p.statusForSessionStatus("idle"),
  active: p.statusForSessionStatus({type: "active"}),
  busy: p.statusForSessionStatus({type: "busy"}),
  unknown: p.statusForSessionStatus("paused"),
  permission: p.stateForEvent("permission.asked"),
  question: p.stateForEvent("question.asked"),
  replied: p.stateForEvent("permission.replied"),
  error: p.stateForEvent("session.error"),
  status: p.stateForEvent("session.status", {status: {type: "running"}}),
}));
""",
        PLUGIN=PLUGIN.as_uri(),
    )
    out = _run(tmp_path, body)
    assert out == {
        "idle": "idle",
        "active": "working",
        "busy": "working",
        "unknown": None,
        "permission": {"state": "blocked", "kind": "permission"},
        "question": {"state": "blocked", "kind": "question"},
        "replied": {"state": "working"},
        "error": {"state": "error"},
        "status": {"state": "working"},
    }
