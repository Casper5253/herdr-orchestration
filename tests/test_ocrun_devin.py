"""Tests for the devin worker of hx.ocrun (faked headless execute)."""

from __future__ import annotations

import codecs
import json
import subprocess
from pathlib import Path

import pytest

from hx import devin as devin_mod
from hx import ocrun

MODEL = "swe-2-high"


@pytest.fixture(autouse=True)
def synthetic_herdr_agents(monkeypatch):
    monkeypatch.setattr("hx.usage.herdr_agents", lambda: [])


def _export_doc(model: str) -> dict:
    """A small synthetic ATIF export: one exec step plus a final answer."""
    return {
        "session_id": "ses_devin_1",
        "agent": {"model_name": "SWE-2 High", "extra": {"permission_mode": "accept-edits"}},
        "steps": [
            {
                "source": "agent",
                "model_name": model,
                "extra": {"generation_model": model},
                "message": "",
                "tool_calls": [
                    {
                        "tool_call_id": "c1",
                        "function_name": "exec_command",
                        "arguments": {"command": "python -m pytest tests -q"},
                    }
                ],
                "observation": {
                    "results": [
                        {
                            "source_call_id": "c1",
                            "content": "Output from command `python -m pytest tests -q`: 1 passed",
                        }
                    ]
                },
            },
            {
                "source": "agent",
                "model_name": model,
                "extra": {"generation_model": model},
                "message": "done: wrote the tests",
                "tool_calls": [],
                "observation": {"results": []},
            },
        ],
        "final_metrics": {
            "total_prompt_tokens": 120,
            "total_completion_tokens": 30,
            "total_cached_tokens": 40,
        },
    }


def _free_lister(bin_path):  # noqa: ARG001
    return (
        {m: {"model_uid": m, "cost_tier": "Free"} for m in ("swe-2-medium", "swe-2-high", "swe-2-max")},
        None,
    )


@pytest.fixture
def devin_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict, list]:
    """Policy, captured execute calls and an isolated state dir for faked runs."""
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    bin_path = tmp_path / "devin.exe"
    bin_path.write_text("dummy", encoding="utf-8")
    config = tmp_path / "worker.config.json"
    config.write_text("{}", encoding="utf-8")
    policy = {
        "devin": {
            "enabled": True,
            "bin": str(bin_path),
            "worker_config": str(config),
            "kinds": list(devin_mod.DEFAULT_KINDS),
            "models": dict(devin_mod.DEFAULT_MODELS),
            "allowed_models": ["swe-2-medium", "swe-2-high", "swe-2-max"],
            "permission_mode": "accept-edits",
            "max_concurrent": 1,
            "promo": {"ends_at": "2099-01-01T00:00:00Z"},
        }
    }
    calls: list[dict] = []

    def fake_execute(cmd, cwd, timeout, env=None):  # noqa: ARG001
        calls.append({"cmd": list(cmd), "cwd": cwd, "env": env})
        model = cmd[cmd.index("--model") + 1]
        export = Path(cmd[cmd.index("--export") + 1])
        export.write_text(json.dumps(_export_doc(model)), encoding="utf-8")
        return 0, "", "", False, True

    monkeypatch.setattr(ocrun, "execute", fake_execute)
    return policy, calls


def test_devin_headless_ok(devin_run: tuple[dict, list], tmp_path: Path) -> None:
    policy, calls = devin_run
    routing_log = tmp_path / "routing.jsonl"
    result = ocrun.run_devin(
        "write the tests",
        MODEL,
        cwd=tmp_path,
        timeout=5.0,
        title="dv-ok",
        routing_log=routing_log,
        task_id="task_1",
        decision_id="dec_1",
        mode="headless",
        policy=policy,
        models_lister=_free_lister,
    )

    assert result["ok"] is True, result["failure"]
    assert result["worker"] == "devin"
    assert result["provider"] == "devin"
    assert result["model"] == MODEL
    assert result["effort"] == "high"
    assert result["models_seen"] == [MODEL]
    assert result["session_id"] == "ses_devin_1"
    assert result["final_message"] == "done: wrote the tests"
    assert result["exit_code"] == 0
    assert result["tokens"]["input"] == 120
    assert result["tokens"]["cache_read"] == 40

    prompt = (Path(result["run_dir"]) / "prompt.md").read_text(encoding="utf-8")
    assert "You are a Devin worker started by hx" in prompt
    assert "write the tests" in prompt

    entry = json.loads(routing_log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert entry["kind"] == "outcome"
    assert entry["worker"] == "devin"
    assert entry["provider"] == "devin"
    assert entry["model"] == MODEL
    assert entry["ok"] is True
    assert entry["task_id"] == "task_1"
    assert entry["decision_id"] == "dec_1"
    assert entry["models_seen"] == [MODEL]


def test_devin_preflight_refusal_never_executes(devin_run: tuple[dict, list], tmp_path: Path) -> None:
    policy, calls = devin_run
    result = ocrun.run_devin(
        "say hi",
        "claude-opus-5-5",
        cwd=tmp_path,
        timeout=5.0,
        title="dv-refused",
        mode="headless",
        policy=policy,
        models_lister=_free_lister,
    )
    assert result["ok"] is False
    assert result["failure"]["kind"] == "policy"
    assert "allowed_models" in result["failure"]["reason"]
    assert calls == []


def test_devin_busy_slot_refusal(devin_run: tuple[dict, list], tmp_path: Path) -> None:
    policy, calls = devin_run
    slot = devin_mod.acquire_slot(1, 600.0, devin_mod.new_slot_info(tmp_path, "other-run", MODEL))
    assert slot is not None
    try:
        result = ocrun.run_devin(
            "say hi",
            MODEL,
            cwd=tmp_path,
            timeout=5.0,
            title="dv-busy",
            mode="headless",
            policy=policy,
            models_lister=_free_lister,
        )
    finally:
        devin_mod.release_slot(slot)
    assert result["ok"] is False
    assert result["failure"]["kind"] == "busy"
    assert calls == []


def test_devin_scrubs_inherited_env(
    devin_run: tuple[dict, list], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, calls = devin_run
    monkeypatch.setenv("DEVIN_MODEL", "swe-2-max")
    monkeypatch.setenv("DEVIN_PERMISSION_MODE", "yolo")
    result = ocrun.run_devin(
        "say hi",
        MODEL,
        cwd=tmp_path,
        timeout=5.0,
        title="dv-env",
        mode="headless",
        policy=policy,
        models_lister=_free_lister,
    )
    assert result["ok"] is True, result["failure"]
    env = calls[0]["env"]
    assert env is not None
    for key in devin_mod.SCRUBBED_ENV:
        assert key not in env
    assert result["model"] == MODEL


def test_devin_command_shape(devin_run: tuple[dict, list], tmp_path: Path) -> None:
    policy, calls = devin_run
    result = ocrun.run_devin(
        "say hi",
        MODEL,
        cwd=tmp_path,
        timeout=5.0,
        title="dv-cmd",
        mode="headless",
        policy=policy,
        models_lister=_free_lister,
    )
    assert result["ok"] is True, result["failure"]
    cmd = calls[0]["cmd"]
    assert cmd[0].endswith("devin.exe")
    assert "-p" in cmd
    assert cmd[cmd.index("--model") + 1] == MODEL
    assert cmd[cmd.index("--permission-mode") + 1] == "accept-edits"
    config = Path(cmd[cmd.index("--config") + 1])
    assert config.parent == Path(result["run_dir"])
    assert config.name == devin_mod.CONFIG_FILE
    export = Path(cmd[cmd.index("--export") + 1])
    assert export.parent == Path(result["run_dir"])
    assert "--cloud" not in cmd


def test_write_devin_run_script_scrubs_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in devin_mod.SCRUBBED_ENV:
        monkeypatch.setenv(key, "x")
    monkeypatch.setenv("GH_TOKEN", "secret")
    monkeypatch.setenv("QUESTLOG_TOKEN_OPENCODE", "kept")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    files = {"pid": run_dir / "pid.txt"}
    script = ocrun.write_devin_run_script(
        run_dir,
        ["devin", "-p", "--export", str(run_dir / "export.json")],
        "dv-visible",
        tmp_path,
        "banner",
        files,
    )
    text = script.read_text(encoding="utf-8-sig")
    for key in devin_mod.SCRUBBED_ENV:
        assert f"Remove-Item 'Env:{key}'" in text
    assert "Remove-Item 'Env:GH_TOKEN'" in text
    assert "QUESTLOG_TOKEN_OPENCODE" not in text
    assert "-ErrorAction Stop" in text
    assert script.read_bytes().startswith(codecs.BOM_UTF8)
    assert "done.json" in text
    assert "HX-DONE" in text


def test_wait_devin_visible_done_returns_finished(tmp_path: Path) -> None:
    done = tmp_path / "done.json"
    done.write_text('{"exit_code": 0}', encoding="utf-8")
    assert ocrun._wait_devin_visible(done, tmp_path / "pid.txt", 1.0) == (True, False, True)


def test_devin_unconfirmed_kill_keeps_slot_and_is_not_retry_safe(
    devin_run: tuple[dict, list], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A timed-out run whose kill was not confirmed may still be working:
    its slot stays held until expiry and the result is marked retry_safe False."""
    policy, calls = devin_run

    def fake_execute(cmd, cwd, timeout, env=None):  # noqa: ARG001
        calls.append({"cmd": list(cmd), "cwd": cwd, "env": env})
        return None, "", "", True, False

    monkeypatch.setattr(ocrun, "execute", fake_execute)
    result = ocrun.run_devin(
        "say hi",
        MODEL,
        cwd=tmp_path,
        timeout=5.0,
        title="dv-timeout",
        mode="headless",
        policy=policy,
        models_lister=_free_lister,
    )
    assert result["ok"] is False
    assert result["failure"] == {"kind": "tool", "reason": "termination-unconfirmed"}
    assert result["retry_safe"] is False
    held = list(devin_mod.slots_dir().glob("slot-*.json"))
    assert len(held) == 1
    assert len(devin_mod.active_slots()) == 1


def test_devin_finished_run_releases_slot(devin_run: tuple[dict, list], tmp_path: Path) -> None:
    policy, _ = devin_run
    result = ocrun.run_devin(
        "say hi",
        MODEL,
        cwd=tmp_path,
        timeout=5.0,
        title="dv-release",
        mode="headless",
        policy=policy,
        models_lister=_free_lister,
    )
    assert result["ok"] is True, result["failure"]
    assert devin_mod.active_slots() == []
    assert list(devin_mod.slots_dir().glob("slot-*.json")) == []


def test_devin_changed_files_excludes_preexisting_dirt(
    devin_run: tuple[dict, list], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """changed_files is the worker's footprint: files already dirty before
    the run are reported in dirty_before, not attributed to the run."""
    policy, calls = devin_run
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    (repo / "pre-existing.txt").write_text("dirty before the run", encoding="utf-8")

    def fake_execute(cmd, cwd, timeout, env=None):  # noqa: ARG001
        calls.append({"cmd": list(cmd), "cwd": cwd, "env": env})
        model = cmd[cmd.index("--model") + 1]
        export = Path(cmd[cmd.index("--export") + 1])
        export.write_text(json.dumps(_export_doc(model)), encoding="utf-8")
        (cwd / "created-by-worker.txt").write_text("new", encoding="utf-8")
        return 0, "", "", False, True

    monkeypatch.setattr(ocrun, "execute", fake_execute)
    result = ocrun.run_devin(
        "say hi",
        MODEL,
        cwd=repo,
        timeout=5.0,
        title="dv-files",
        mode="headless",
        policy=policy,
        models_lister=_free_lister,
    )
    assert result["ok"] is True, result["failure"]
    assert "pre-existing.txt" in result["dirty_before"]
    assert result["changed_files"] == ["created-by-worker.txt"]


def test_devin_changed_files_includes_touched_preexisting_dirt(
    devin_run: tuple[dict, list], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression, 2026-10-31 live run: a worker that edited files which were
    already untracked reported changed_files == []."""
    policy, calls = devin_run
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    (repo / "untouched.txt").write_text("dirty before", encoding="utf-8")
    (repo / "edited.txt").write_text("dirty before", encoding="utf-8")

    def fake_execute(cmd, cwd, timeout, env=None):  # noqa: ARG001
        calls.append({"cmd": list(cmd), "cwd": cwd, "env": env})
        export = Path(cmd[cmd.index("--export") + 1])
        export.write_text(json.dumps(_export_doc(cmd[cmd.index("--model") + 1])), encoding="utf-8")
        (cwd / "edited.txt").write_text("dirty before, then edited by the worker", encoding="utf-8")
        return 0, "", "", False, True

    monkeypatch.setattr(ocrun, "execute", fake_execute)
    result = ocrun.run_devin(
        "say hi", MODEL, cwd=repo, timeout=5.0, title="dv-touched", mode="headless", policy=policy,
        models_lister=_free_lister,
    )
    assert result["ok"] is True, result["failure"]
    assert result["changed_files"] == ["edited.txt"]


def test_devin_final_message_file_holds_full_text(
    devin_run: tuple[dict, list], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """result["final_message"] is a 4000-char preview; final.md is complete."""
    policy, calls = devin_run
    message = "full answer " * 500

    def fake_execute(cmd, cwd, timeout, env=None):  # noqa: ARG001
        calls.append({"cmd": list(cmd), "cwd": cwd, "env": env})
        model = cmd[cmd.index("--model") + 1]
        doc = _export_doc(model)
        doc["steps"][-1]["message"] = message
        Path(cmd[cmd.index("--export") + 1]).write_text(json.dumps(doc), encoding="utf-8")
        return 0, "", "", False, True

    monkeypatch.setattr(ocrun, "execute", fake_execute)
    result = ocrun.run_devin(
        "say hi",
        MODEL,
        cwd=tmp_path,
        timeout=5.0,
        title="dv-final",
        mode="headless",
        policy=policy,
        models_lister=_free_lister,
    )
    assert result["ok"] is True, result["failure"]
    assert len(result["final_message"]) == 4000
    final_file = Path(result["final_message_file"])
    assert final_file.exists()
    assert final_file.read_text(encoding="utf-8") == message.strip()


def test_devin_same_title_gets_distinct_run_dirs(devin_run: tuple[dict, list], tmp_path: Path) -> None:
    """Two runs with one title must never share a run directory."""
    policy, _ = devin_run
    run_kw = dict(
        cwd=tmp_path,
        timeout=5.0,
        title="dv-dup",
        mode="headless",
        policy=policy,
        models_lister=_free_lister,
    )
    first = ocrun.run_devin("first prompt", MODEL, **run_kw)
    second = ocrun.run_devin("second prompt", MODEL, **run_kw)
    assert first["ok"] is True and second["ok"] is True
    assert first["run_dir"] != second["run_dir"]
    assert Path(first["run_dir"]).name == "dv-dup"
    assert Path(second["run_dir"]).name == "dv-dup-2"
