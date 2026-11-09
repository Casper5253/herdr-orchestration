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
  stdout: stream, stderr: null, stateDir: __STATE__, pid: 4242,
  now: () => new Date("2026-11-09T00:00:00.000Z"),
  terminalInfo: () => ({terminal_created_ms: 111}),
  writtenMs: () => 222,
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
        "v": 2,
        "terminal": "t8",
        "terminal_created_ms": 111,
        "provider": "opencode",
        "session_id": "ses_one",
        "provider_pid": 4242,
        "written_ms": 222,
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
  stdout: {isTTY: true, write() {}}, stderr: null, stateDir: __STATE__,
  pid: 1000, now: () => new Date(now), terminalInfo: () => ({terminal_created_ms: 111}),
  writtenMs: () => Date.parse(now),
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
    assert out["before"]["t9"]["session_id"] == "ses_two"
    assert out["after"]["t8"]["session_id"] == "ses_three"
    assert out["after"]["t8"]["written_ms"] == out["before"]["t8"]["written_ms"]
    assert out["before"]["t8"]["written_ms"] > out["before"]["t9"]["written_ms"]
    assert out["after"]["t9"] == out["before"]["t9"]
    assert out["after"]["files"] == ["t8.json", "t8.json.owner", "t9.json", "t9.json.owner"]


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
  stateDir: __STATE__, pid: 1001,
  now: () => new Date("2026-11-09T00:00:00.000Z"),
  terminalInfo: () => ({terminal_created_ms: 111}),
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


def _replay(tmp_path, events):
    return _run(tmp_path, _body('''
import * as p from __PLUGIN__;
import fs from "node:fs";
const writes = [];
const hooks = await p.createPlugin({
 env: {HC_TERMINAL_ID: "t1", HC_SESSION: "core-one", OPENCODE_CONFIG: "opencode-trusted-dev.jsonc"},
 stdout: {isTTY: true, write(s) { writes.push(s); }}, stderr: null,
 stateDir: __STATE__, pid: 100,
 terminalInfo: () => ({terminal_created_ms: 111}), writtenMs: () => 222,
})({});
const event = async (type, properties) => hooks.event({event: {type, properties}});
await hooks["chat.message"]({sessionID: "root"});
__EVENTS__
console.log(JSON.stringify({writes, binding: JSON.parse(fs.readFileSync(__STATE__ + "/hc-bindings/t1.json"))}));
''', PLUGIN=PLUGIN.as_uri(), STATE=str(tmp_path / "state")).replace("__EVENTS__", events))


def test_message_and_unrecognized_ids_cannot_become_sessions(tmp_path):
    out = _replay(tmp_path, '''
await event("message.updated", {info: {id: "msg_1", sessionID: "root", role: "assistant"}});
await event("unknown", {info: {id: "alien"}});
''')
    assert out["binding"]["session_id"] == "root"


def test_child_completion_and_error_do_not_complete_busy_root(tmp_path):
    out = _replay(tmp_path, '''
await event("session.created", {info: {id: "child", parentID: "root"}});
await event("session.idle", {sessionID: "child"});
await event("session.error", {sessionID: "child"});
''')
    assert out["writes"] == [f"{ESC}]7501;state=working:app=opencode{ESC}\\"]


def test_concurrent_child_prompts_stay_blocked_until_all_resolve(tmp_path):
    out = _replay(tmp_path, '''
await event("session.updated", {info: {id: "a", parentID: "root"}});
await event("session.updated", {info: {id: "b", parentID: "root"}});
await event("permission.asked", {sessionID: "a", id: "p1"});
await event("question.asked", {sessionID: "b", id: "q1"});
await event("permission.replied", {sessionID: "a", requestID: "p1"});
await event("session.status", {sessionID: "root", status: {type: "busy"}});
''')
    assert "state=blocked" in out["writes"][-1]


def test_previous_root_lifecycle_cannot_undo_local_selection(tmp_path):
    out = _replay(tmp_path, '''
await hooks["chat.message"]({sessionID: "new-root"});
await event("session.idle", {sessionID: "root"});
await event("session.created", {info: {id: "unrelated", parentID: null}});
''')
    assert out["binding"]["session_id"] == "new-root"
    assert all("state=working" in w for w in out["writes"])


def test_new_process_invalidates_binding_and_fences_old_writer(tmp_path):
    out = _run(tmp_path, _body('''
import * as p from __PLUGIN__;
import fs from "node:fs";
const file = __STATE__ + "/hc-bindings/t1.json";
const make = async (pid) => p.createPlugin({
 env: {HC_TERMINAL_ID: "t1", HC_SESSION: "core-one", OPENCODE_CONFIG: "opencode-trusted-dev.jsonc"},
 stdout: {isTTY: true, write() {}}, stderr: null, stateDir: __STATE__, pid,
 now: () => new Date(pid === 100 ? "2026-01-01" : "2026-01-02"),
 terminalInfo: () => ({terminal_created_ms: 111}), writtenMs: () => pid === 100 ? 1000 : 2000,
})({});
const old = await make(100);
await old["chat.message"]({sessionID: "old"});
const fresh = await make(200);
const startup = JSON.parse(fs.readFileSync(file));
await old["chat.message"]({sessionID: "stale"});
await fresh["chat.message"]({sessionID: "old"});
await old.event({event: {type: "session.created", properties: {info: {id: "alien"}}}});
console.log(JSON.stringify({startup, binding: JSON.parse(fs.readFileSync(file))}));
''', PLUGIN=PLUGIN.as_uri(), STATE=str(tmp_path / "state")))
    assert out["startup"]["session_id"] is None
    assert out["startup"]["provider_pid"] == 200
    assert out["binding"]["session_id"] == "old"
    assert out["binding"]["provider_pid"] == 200
    assert out["binding"]["written_ms"] == 2000


def test_reader_rejects_stale_v1_dead_and_wrong_identity(tmp_path):
    out = _run(tmp_path, _body('''
import * as p from __PLUGIN__;
import fs from "node:fs";
const file = __STATE__ + "/hc-bindings/t1.json";
const expected = {terminal: "t1", createdMs: 111, isAlive: (pid) => pid === 200};
const make = (pid) => p.createPlugin({
 env: {HC_TERMINAL_ID: "t1", HC_SESSION: "core-one", OPENCODE_CONFIG: "opencode-trusted-dev.jsonc"},
 stdout: {isTTY: true, write() {}}, stderr: null, stateDir: __STATE__, pid,
 terminalInfo: () => ({terminal_created_ms: 111}), writtenMs: () => pid === 100 ? 1000 : 2000,
})({});
const old = await make(100);
await old["chat.message"]({sessionID: "old"});
const stale = fs.readFileSync(file);
const fresh = await make(200);
await fresh["chat.message"]({sessionID: "new"});
fs.writeFileSync(file, stale);
const raced = p.readBinding(file, expected);
await fresh["chat.message"]({sessionID: "new"});
fs.writeFileSync(file, JSON.stringify({terminal: "t1", session_id: "v1", pid: 200}));
const v1 = p.readBinding(file, expected);
await fresh["chat.message"]({sessionID: "new"});
console.log(JSON.stringify({raced, current: p.readBinding(file, expected), v1,
 dead: p.readBinding(file, {...expected, isAlive: () => false}),
 wrongProvider: p.readBinding(file, {...expected, provider: "codex"}),
 wrongTerminal: p.readBinding(file, {...expected, terminal: "t9"}),
 wrongCreated: p.readBinding(file, {...expected, createdMs: 999}),
}));
''', PLUGIN=PLUGIN.as_uri(), STATE=str(tmp_path / "state")))
    assert out["raced"] is None
    assert out["current"]["session_id"] == "new"
    assert all(out[k] is None for k in ["v1", "dead", "wrongProvider", "wrongTerminal", "wrongCreated"])


@pytest.mark.parametrize("failure", ["write", "rename"])
def test_failed_binding_io_is_cleaned_and_retried(tmp_path, failure):
    out = _run(tmp_path, _body('''
import * as p from __PLUGIN__;
import fs from "node:fs";
const file = __STATE__ + "/hc-bindings/t1.json";
let fail = false;
const io = {...fs,
 writeFileSync(...args) {
   fs.writeFileSync(...args);
   if (fail && __FAILURE__ === "write") throw new Error("write failed");
 },
 renameSync(...args) {
   if (fail && __FAILURE__ === "rename") throw new Error("rename failed");
   fs.renameSync(...args);
 },
};
const writes = [];
const hooks = await p.createPlugin({
 env: {HC_TERMINAL_ID: "t1", OPENCODE_CONFIG: "opencode-trusted-dev.jsonc"},
 stdout: {isTTY: false, write(s) { writes.push(s); }},
 stderr: {isTTY: false, write(s) { writes.push(s); }}, stateDir: __STATE__, fs: io,
 terminalInfo: () => ({terminal_created_ms: 111}),
})({});
fail = true;
await hooks["chat.message"]({sessionID: "root"});
const failed = JSON.parse(fs.readFileSync(file));
const temps = fs.readdirSync(__STATE__ + "/hc-bindings").filter(s => s.endsWith(".tmp"));
fail = false;
await hooks.event({event: {type: "session.status", properties: {sessionID: "root", status: "busy"}}});
console.log(JSON.stringify({failed, recovered: JSON.parse(fs.readFileSync(file)), writes, temps}));
''', PLUGIN=PLUGIN.as_uri(), STATE=str(tmp_path / "state"), FAILURE=failure))
    assert out["failed"]["session_id"] is None
    assert out["recovered"]["session_id"] == "root"
    assert out["writes"] == out["temps"] == []


def test_multiple_prompts_for_one_child_resolve_independently(tmp_path):
    out = _replay(tmp_path, '''
await event("session.updated", {info: {id: "child", parentID: "root"}});
await event("permission.asked", {sessionID: "child", id: "p1"});
await event("permission.asked", {sessionID: "child", id: "p2"});
await event("permission.replied", {sessionID: "child", requestID: "p1"});
const stillBlocked = writes.at(-1);
await event("permission.replied", {sessionID: "child", requestID: "p2"});
if (!stillBlocked.includes("state=blocked")) throw new Error("second prompt was lost");
''')
    assert "state=working" in out["writes"][-1]


def test_binding_failure_does_not_discard_a_permission_prompt(tmp_path):
    out = _run(tmp_path, _body('''
import * as p from __PLUGIN__;
import fs from "node:fs";
let fail = false;
const io = {...fs, renameSync(...args) {
 if (fail) throw new Error("rename failed");
 fs.renameSync(...args);
}};
const writes = [];
const hooks = await p.createPlugin({
 env: {HC_TERMINAL_ID: "t1", OPENCODE_CONFIG: "opencode-trusted-dev.jsonc"},
 stdout: {isTTY: true, write(s) { writes.push(s); }}, stderr: null,
 stateDir: __STATE__, fs: io, terminalInfo: () => ({terminal_created_ms: 111}),
})({});
await hooks["chat.message"]({sessionID: "root"});
fail = true;
await hooks.event({event: {type: "permission.asked", properties: {sessionID: "root", id: "p1"}}});
fail = false;
await hooks.event({event: {type: "session.status", properties: {sessionID: "root", status: "busy"}}});
console.log(JSON.stringify({writes}));
''', PLUGIN=PLUGIN.as_uri(), STATE=str(tmp_path / "state")))
    assert all("state=blocked" in w for w in out["writes"][1:])
    assert len(out["writes"]) == 3


def test_terminal_info_requires_live_ancestor_and_generation(tmp_path: Path) -> None:
    body = _body(
        """
import * as p from __PLUGIN__;
const live = (extra = {}) => ({address: "t1", state: "live", pid: 50, created_ms: 111, ...extra});
const info = (data, ancestors = [50]) => p.terminalInfo("t1", {
  hcGet: () => data, ancestors: () => ancestors, ownPid: 100,
});
console.log(JSON.stringify({
  ok: info(live()),
  own: info(live({pid: 100})),
  finished: info(live({state: "finished"})),
  foreign: info(live({address: "t9"})),
  noGeneration: info(live({created_ms: null})),
  notAncestor: info(live(), []),
  wrongAncestor: info(live(), [99]),
  missing: info(null),
  unsafe: p.terminalInfo("../t1", {hcGet: () => live(), ancestors: () => [50], ownPid: 100}),
}));
""",
        PLUGIN=PLUGIN.as_uri(),
    )
    out = _run(tmp_path, body)
    assert out["ok"] == {"terminal_created_ms": 111}
    assert out["own"] == {"terminal_created_ms": 111}
    assert all(out[key] is None for key in [
        "finished", "foreign", "noGeneration", "notAncestor", "wrongAncestor", "missing", "unsafe",
    ])


def test_plugin_refuses_unset_terminal_with_one_diagnostic(tmp_path: Path) -> None:
    state = tmp_path / "state"
    out = _run(tmp_path, _body('''
import * as p from __PLUGIN__;
const diagnostics = [];
const hooks = await p.createPlugin({
  env: {OPENCODE_CONFIG: "opencode-trusted-dev.jsonc"},
  stateDir: __STATE__, stderr: {write(s) { diagnostics.push(s); }},
})({});
console.log(JSON.stringify({hooks: Object.keys(hooks), diagnostics}));
''', PLUGIN=PLUGIN.as_uri(), STATE=str(state)))
    assert out["hooks"] == []
    assert out["diagnostics"] == ["hc-binding: refusing: HC_TERMINAL_ID is not set\n"]
    assert not state.exists()


def test_plugin_refuses_a_stale_terminal_with_one_diagnostic(tmp_path: Path) -> None:
    state = tmp_path / "state"
    body = _body(
        """
import * as p from __PLUGIN__;
const diagnostics = [];
const hooks = await p.createPlugin({
  env: {HC_TERMINAL_ID: "t1", OPENCODE_CONFIG: "C:/repo/opencode-trusted-dev.jsonc"},
  stdout: {isTTY: true, write() {}}, stderr: {write(s) { diagnostics.push(s); }},
  stateDir: __STATE__, pid: 100,
  terminalInfo: () => null,
})({});
console.log(JSON.stringify({hooks: Object.keys(hooks), diagnostics}));
""",
        PLUGIN=PLUGIN.as_uri(),
        STATE=str(state),
    )
    out = _run(tmp_path, body)
    assert out["hooks"] == []
    assert len(out["diagnostics"]) == 1
    assert "refusing t1" in out["diagnostics"][0]
    assert not (state / "hc-bindings" / "t1.json").exists()
    assert not (state / "hc-bindings" / "t1.json.owner").exists()
