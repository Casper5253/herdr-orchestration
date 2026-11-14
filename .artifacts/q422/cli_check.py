"""Exercise CLI dispatch using scratch leases and fake terminal I/O."""

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from hx import context, host, ledger, say, usage
from hx.__main__ import main
from test_q346_recheck2 import FakeHc, POLICY, _seed_reset_hold

scratch = Path(__file__).parent / "cli-state"
scratch.mkdir(exist_ok=True)
env = {
    "HX_PROJECT_ROOT": str(scratch),
    "HX_LEDGER_PATH": str(scratch / "ledger.json"),
    "HX_LOCK_PATH": str(scratch / "ledger.lock"),
    "HX_LEASES_PATH": str(scratch / "leases.json"),
    "HX_LEASES_LOCK_PATH": str(scratch / "leases.lock"),
    "HX_CONTEXT_RESETS_PATH": str(scratch / "resets.json"),
    "HX_HERDR_PREFIX": "dev-",
}

with patch.dict(os.environ, env):
    led = ledger.default_ledger()
    hc = FakeHc()
    c = context.Controller(led, POLICY)
    worker = hc.members["dev-opencode"]
    worker.update(session_id="", status="working")
    sample = {"session_id": "lead-sid", "utilisation": .7,
              "sampled_at": context._now().isoformat()}
    calls = []

    def slash(member, command):
        calls.append((member["name"], command))
        sample.update(utilisation=.2, sampled_at=context._now().isoformat())
        return True

    def cli(*args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(list(args))
        return code, output.getvalue()

    with contextlib.ExitStack() as patches:
        for obj, name, replacement in [
            (host, "detect", lambda: hc),
            (usage, "load_policy", lambda: POLICY),
            (say, "_under_pressure", lambda name: False),
            (say, "_denied_line_for", lambda destination: ""),
            (say, "_submission_evidence", lambda *a, **k: "submitted"),
            (context, "Controller", lambda: c),
            (context, "agent_info", lambda name: copy.deepcopy(hc.members[name])),
            (context, "telemetry", lambda *a: dict(sample)),
            (context, "session_telemetry", lambda *a: ("lead-sid", dict(sample))),
            (context, "_send_slash", slash),
        ]:
            patches.enter_context(patch.object(obj, name, replacement))

        _seed_reset_hold(led, sid="old")
        code, output = cli("context", "recover", "dev-opencode")
        assert code == 0 and json.loads(output)["action"] == "keep"
        assert "reset:dev-opencode" in led._load_leases()["sessions"]
        worker["session_id"] = "new"
        code, output = cli("context", "recover", "dev-opencode")
        assert code == 0 and json.loads(output)["action"] == "recovered"

        original = hc.send_checked

        def send(name, text, **kwargs):
            assert ledger.delivery_hold(led._load_leases(), name)
            code, output = cli("compact", "dev-lead", "--quick")
            assert code == 0 and json.loads(output)["action"] == "keep"
            assert calls == []
            return original(name, text, **kwargs)

        hc.send_checked = send
        code, output = cli("say", "lead", "scratch acceptance report")
        assert code == 0 and "submitted" in output
        assert "dev-lead" not in led._load_leases().get("deliveries", {})
        code, output = cli("compact", "dev-lead", "--quick")
        assert code == 0 and json.loads(output)["action"] == "compacted"
        assert calls == [("dev-lead", "/compact")]

print("CLI scratch acceptance passed: unknown reset kept, replacement released, control send excludes compaction, later compaction succeeds.")
