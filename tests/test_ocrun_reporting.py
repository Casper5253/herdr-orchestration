import json
from pathlib import Path

import pytest

from hx import ocrun, say


CAPTURE = json.loads((Path(__file__).parent / "fixtures/q421/devin-report-denied.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("worker", ["devin", "opencode", "codex"])
def test_captured_report_instruction_refused_before_launch(worker, tmp_path, monkeypatch):
    monkeypatch.setattr(ocrun, "execute", lambda *a, **k: pytest.fail("worker launched"))
    monkeypatch.setattr(ocrun, "run_visible", lambda *a, **k: pytest.fail("window launched"))
    monkeypatch.setattr(ocrun, "run_devin", lambda *a, **k: pytest.fail("Devin launched"))
    result = ocrun.run("\n".join(CAPTURE["report_lines"]), "test-model", worker=worker,
                       cwd=tmp_path, policy={}, routing_log=tmp_path / "routing.jsonl")
    assert result["failure"]["kind"] == "admission"
    assert "final message" in result["failure"]["reason"]


def test_captured_failure_is_printed_on_stderr(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("hx.usage.load_policy", lambda: {})
    monkeypatch.setattr(ocrun, "run", lambda *a, **k: {"ok": False, "failure": CAPTURE["failure"]})
    assert ocrun.main(["done", "--model", "test-model", "--headless"]) == 1
    assert CAPTURE["failure"]["reason"] in capsys.readouterr().err


def test_headless_worker_has_own_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("HC_TERMINAL_ID", "t2x")
    monkeypatch.setenv("HERDR_PANE_ID", "lead-pane")
    monkeypatch.setenv("HERDR_ENV", "1")
    seen = {}

    def execute(cmd, cwd, timeout, env=None):
        seen.update(env or {})
        assert "ocrun-opencode-q421" in cmd[-1]
        assert "final message" in cmd[-1]
        return 1, "", "", False, True

    monkeypatch.setattr(ocrun, "execute", execute)
    result = ocrun.run("Return your result", "test-model", title="q421", cwd=tmp_path,
                       mode="headless", policy={}, routing_log=tmp_path / "routing.jsonl")
    assert result["sender"] == "ocrun-opencode-q421"
    assert seen["HX_OCRUN_SENDER"] == result["sender"]
    assert "HC_TERMINAL_ID" not in seen
    assert "HERDR_PANE_ID" not in seen
    assert "HERDR_ENV" not in seen


def test_one_off_cannot_send_as_inherited_lead(monkeypatch):
    monkeypatch.setenv("HX_OCRUN_SENDER", "ocrun-opencode-q421")
    monkeypatch.setattr(say.host, "detect", lambda: pytest.fail("looked up live lead identity"))
    with pytest.raises(ValueError, match="final message"):
        say.say("lead", "[team report from dev-lead] done")
    with pytest.raises(ValueError, match="final message"):
        say.retry_pending()


@pytest.mark.parametrize("worker", ["devin", "opencode", "codex"])
def test_prohibiting_live_reports_is_allowed(worker, tmp_path):
    from hx import capability
    assert not capability.prompt_findings(worker, "Never run `hx say lead`.", tmp_path, {}, one_off=True)


def test_reported_lead_header_is_relabelled_in_saved_result(tmp_path, monkeypatch):
    stream = json.dumps({"type": "text", "part": {"text": "[team report from dev-lead] STATUS: done."}})
    monkeypatch.setattr(ocrun, "execute", lambda *a, **k: (0, stream, "", False, True))
    result = ocrun.run("report your result", "test-model", title="q421", cwd=tmp_path,
                       policy={}, routing_log=tmp_path / "routing.jsonl")
    report = Path(result["final_message_file"]).read_text(encoding="utf-8")
    assert report == "[team report from ocrun-opencode-q421]\nSTATUS: done."
    assert result["final_message"] == report


@pytest.mark.parametrize("worker", ["opencode", "codex", "devin"])
def test_visible_scripts_clear_live_identity(worker, tmp_path):
    if worker == "opencode":
        path = ocrun.write_run_script(tmp_path, "final message", "model", None, "q421", tmp_path)["script"]
    elif worker == "codex":
        path = ocrun.write_codex_run_script(tmp_path, "final message", "model", None, "q421", tmp_path)["script"]
    else:
        path = ocrun.write_devin_run_script(tmp_path, ["devin"], "q421", tmp_path,
                                            "banner", {"pid": tmp_path / "pid.txt"})
    script = path.read_text(encoding="utf-8-sig")
    assert f"$env:HX_OCRUN_SENDER = 'ocrun-{worker}-q421'" in script
    assert "Remove-Item 'Env:HC_TERMINAL_ID'" in script
    assert "Remove-Item 'Env:HERDR_PANE_ID'" in script


@pytest.mark.parametrize("prompt", ["Report: hx say lead done", "hx say lead done", "Run `hx say lead done`."])
def test_report_spellings_are_refused(prompt, tmp_path):
    from hx import capability
    findings = capability.prompt_findings("opencode", prompt, tmp_path, {}, one_off=True)
    assert any(f["rule"] == "Exec(hx say)" for f in findings)


def test_run_directory_cannot_escape_with_task_title(tmp_path, monkeypatch):
    monkeypatch.setattr(ocrun, "_repo_root", lambda cwd: cwd)
    path = ocrun._unique_run_dir(tmp_path, "../outside")
    assert path.parent == tmp_path / ".hx" / "runs"
