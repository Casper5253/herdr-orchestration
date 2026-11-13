from __future__ import annotations

import importlib.util
import inspect
import json
import os
from pathlib import Path
import subprocess
import types

import pytest

from hx import host, say


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("receipt_fakes", ROOT / "tests/test_say_receipts.py")
F = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(F)


@pytest.fixture
def module():
    if not os.environ.get("REVIEW_BASE"):
        return say
    source = subprocess.check_output(
        ["git", "show", os.environ["REVIEW_BASE"] + ":hx/say.py"], cwd=ROOT,
        encoding="utf-8",
    )
    baseline = types.ModuleType("hx.review_baseline")
    exec(compile(source, "baseline:hx/say.py", "exec"), baseline.__dict__)
    return baseline


def pane(kind, transcript, staged=""):
    if kind == "devin":
        return transcript + "\n❭ " + staged + "\n--------------------\nSWE-2 High · bypass"
    if kind == "codex":
        return transcript + "\n› " + staged + "\ngpt-6.1-sol · 91% context left"
    return transcript + "\n┃ " + staged + "\nBuild · opencode"


def evidence(module, fake, text, kind, status):
    result = host.SendResult(started=True, status=status, turn=4, ended=False, reason=None)
    args = [fake, "dev-target", text, result]
    if "kind" in inspect.signature(module._submission_evidence).parameters:
        args.append(kind)
    return module._submission_evidence(*args)


@pytest.fixture
def setup(module, monkeypatch, tmp_path):
    fake = F.FakeHc()
    fake.advance = False
    fake.press = lambda member, *keys: fake.keys.append(keys) or False
    fake.members["dev-target"] = F._member("dev-target", kind="devin", status="working", address="t99")
    monkeypatch.setattr(module, "SUBMIT_POLL_SECONDS", 0)
    monkeypatch.setattr(module, "ENTER_SETTLE_SECONDS", 0)
    monkeypatch.setattr(host, "detect", lambda: fake)
    monkeypatch.setattr(module.usage, "team_agents", lambda prefix: fake.team(prefix))
    monkeypatch.setattr(module, "_under_pressure", lambda name: False)
    monkeypatch.setattr(module, "_denied_line_for", lambda destination: "")
    monkeypatch.setenv("HX_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    return fake


@pytest.mark.parametrize("kind", ["devin", "codex", "opencode"])
@pytest.mark.parametrize("status", ["idle", "working"])
def test_old_brief_suffix_does_not_deliver_new_brief(module, setup, kind, status, tmp_path, capsys):
    fake = setup
    fake.members["dev-target"].update(kind=kind, status=status)
    old = "[team from dev-codex] Read .hx/briefs/old-task.md and do exactly that."
    new = "[team from dev-codex] Read .hx/briefs/new-task.md and do exactly that."
    assert old[-20:] == new[-20:]
    fake.views["dev-target"] = pane(kind, '<hc from="t9">' + old + '</hc>')
    assert new not in fake.views["dev-target"]
    code = module.say("target", new)
    output = capsys.readouterr().out
    record = json.loads(next((tmp_path / ".hx/outbox/dev__codex").glob("*.json")).read_text())
    print(json.dumps({"kind": kind, "status": status, "exit": code, "receipt": record.get("receipt"), "state": record["state"], "keys": fake.keys, "output": output.strip()}))
    assert (code, record["state"], record.get("receipt")) == (1, "uncertain", "uncertain")


@pytest.mark.parametrize("status", ["idle", "working"])
def test_multiline_opencode_composer_is_not_transcript(module, setup, status, tmp_path, capsys):
    fake = setup
    fake.members["dev-target"].update(kind="opencode", status=status)
    text = "[team from dev-codex] Unique unsubmitted task body"
    def on_send(name):
        fake.views["dev-target"] = (
            "Unrelated old output\n\u2503 <hc from=\"t9\">" + fake.sent[-1][1]
            + "\n\u2503 </hc>\nBuild \u00b7 opencode"
        )

    fake.on_send = on_send
    fake.press = lambda member, *keys: fake.keys.append(keys) or True
    code = module.say("target", text)
    output = capsys.readouterr().out
    record = json.loads(next((tmp_path / ".hx/outbox/dev__codex").glob("*.json")).read_text())
    print(json.dumps({"status": status, "exit": code, "receipt": record.get("receipt"), "state": record["state"], "keys": fake.keys, "output": output.strip()}))
    assert (code, record["state"], record.get("receipt")) == (1, "uncertain", "uncertain")


@pytest.mark.parametrize("kind", ["devin", "codex", "opencode"])
def test_current_staged_paste_stays_uncertain(module, setup, kind):
    fake = setup
    text = "[team from dev-codex] Unique unsubmitted task body [m:abc123]"
    fake.members["dev-target"].update(kind=kind)
    fake.views["dev-target"] = pane(kind, "Unrelated output", '<hc from="t9">' + text + '</hc>')
    assert evidence(module, fake, text, kind, "working") == "uncertain"


@pytest.mark.parametrize("kind", ["devin", "codex", "opencode"])
def test_current_transcript_paste_is_positive_control(module, setup, kind):
    fake = setup
    text = "[team from dev-codex] Unique delivered task body [m:abc123]"
    fake.members["dev-target"].update(kind=kind)
    fake.views["dev-target"] = pane(kind, '<hc from="t9">' + text + '</hc>')
    expected = "uncertain" if os.environ.get("REVIEW_BASE") else "queued"
    assert evidence(module, fake, text, kind, "working") == expected


def test_unmarked_text_earns_no_composer_proof(module, setup):
    fake = setup
    fake.members["dev-target"].update(kind="opencode", status="working")
    text = "[team from dev-codex] Unique delivered task body"
    fake.views["dev-target"] = pane("opencode", '<hc from="t9">' + text + '</hc>')
    assert evidence(module, fake, text, "opencode", "working") == "uncertain"


def test_a_second_identical_paste_is_not_proven_by_the_first(module, setup):
    fake = setup
    fake.members["dev-target"].update(kind="opencode", status="working")
    first = "[team from dev-codex] Same body [m:aaaaaa]"
    second = "[team from dev-codex] Same body [m:bbbbbb]"
    fake.views["dev-target"] = pane("opencode", '<hc from="t9">' + first + '</hc>', second)
    assert evidence(module, fake, second, "opencode", "working") == "uncertain"

@pytest.mark.parametrize("kind", ["devin", "codex", "opencode"])
def test_old_marker_collision_cannot_prove_new_message(module, setup, kind, tmp_path, monkeypatch):
    fake = setup
    fake.members["dev-target"].update(kind=kind, status="working")
    original = module._write_outbox
    def fixed_id(*args, **kwargs):
        path = original(*args, **kwargs)
        chosen = path.with_name("abc12322222222222222222222222222.json")
        path.rename(chosen)
        return chosen
    monkeypatch.setattr(module, "_write_outbox", fixed_id)
    fake.views["dev-target"] = pane(kind, '<hc from="t9">An older different message [m:abc123]</hc>')
    code = module.say("target", "New work that never appeared in transcript")
    record = json.loads(next((tmp_path / ".hx/outbox/dev__codex").glob("*.json")).read_text())
    assert (code, record["state"]) == (1, "uncertain")


@pytest.mark.parametrize("kind", ["devin", "codex", "opencode"])
def test_wrapped_marker_does_not_prevent_owed_enter(module, setup, kind):
    fake = setup
    fake.members["dev-target"].update(kind=kind, status="idle")
    def sent(name):
        text = fake.sent[-1][1]
        if "[m:" in text:
            text = text.replace("[m:", "[m:\n")
        staged = '<hc from="t9">' + text + '</hc>'
        if kind == "opencode":
            fake.views[name] = "\n".join("\u2503 " + line for line in staged.splitlines()) + "\nBuild \u00b7 opencode"
        else:
            fake.views[name] = pane(kind, "", staged)
    def press(member, *keys):
        fake.keys.append(keys)
        member["turn"] += 1
        return True
    fake.on_send = sent
    fake.press = press
    assert module.say("target", "A staged message ending in an intact suffix.") == 0
    assert fake.keys == [("enter",)]


@pytest.mark.parametrize("kind", ["devin", "codex", "opencode"])
def test_echoed_marker_is_not_enough_with_unrelated_composer(module, setup, kind):
    fake = setup
    text = "[team from dev-codex] New task [m:abc123]"
    fake.members["dev-target"].update(kind=kind)
    fake.views["dev-target"] = pane(kind, "Assistant output quotes [m:abc123]", "unrelated staged draft")
    assert evidence(module, fake, text, kind, "working") == "uncertain"
