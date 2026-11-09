"""Every one-off OpenCode run renders a permission config and records its provenance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hx import ocrun
from hx import profile as profile_mod

SESSION = "ses_fixture00000000000000000000"
STREAM = "\n".join(
    [
        json.dumps({"type": "step_start", "sessionID": SESSION, "part": {"id": "p1", "sessionID": SESSION, "type": "step_start"}}),
        json.dumps(
            {
                "type": "step_finish",
                "sessionID": SESSION,
                "part": {
                    "reason": "stop",
                    "sessionID": SESSION,
                    "type": "step-finish",
                    "tokens": {"total": 10, "input": 1, "output": 9, "reasoning": 0, "cache": {"write": 0, "read": 0}},
                    "cost": 0.001,
                },
            }
        ),
    ]
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


def _capture(monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict = {}

    def fake_execute(cmd, cwd, timeout, env=None):  # noqa: ARG001
        captured["env"] = env
        return 0, STREAM, "", False, True

    monkeypatch.setattr(ocrun, "execute", fake_execute)
    return captured


def test_unprofiled_run_renders_the_baseline_config(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)
    routing_log = repo / ".hx" / "routing.jsonl"

    result = ocrun.run("read TEAM.md", "glm-5.3-flash", cwd=repo, routing_log=routing_log)

    config_path = Path(captured["env"]["OPENCODE_CONFIG"])
    assert config_path == repo / ".hx" / "profiles" / "opencode-baseline.jsonc"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    for root in profile_mod.OPENCODE_SKILL_ROOTS:
        assert config["permission"]["external_directory"][root] == "allow", root
    # Finding 4: an unprofiled run keeps the team bash boundary.
    assert config["permission"]["bash"]["herdr *"] == "deny"
    assert config["permission"]["bash"]["hc *"] == "ask"
    provenance = result["permission_config"]
    assert set(provenance) == {"path", "sha256", "profile"}
    assert provenance["path"] == str(config_path)
    assert provenance["profile"] == "baseline"
    assert provenance["sha256"] == hashlib.sha256(config_path.read_bytes()).hexdigest()


def test_profiled_run_records_its_profile(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)
    routing_log = repo / ".hx" / "routing.jsonl"

    result = ocrun.run(
        "read the repo and report", "glm-5.3-flash", cwd=repo, routing_log=routing_log, profile="research"
    )

    config_path = Path(captured["env"]["OPENCODE_CONFIG"])
    assert config_path == repo / ".hx" / "profiles" / "opencode-research.jsonc"
    assert result["permission_config"]["profile"] == "research"
    assert result["permission_config"]["sha256"] == hashlib.sha256(config_path.read_bytes()).hexdigest()


def test_outcome_line_carries_the_permission_provenance(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _capture(monkeypatch)
    routing_log = repo / ".hx" / "routing.jsonl"

    result = ocrun.run("x", "glm-5.3-flash", cwd=repo, routing_log=routing_log)

    entry = json.loads(routing_log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert entry["permission_config"] == result["permission_config"]
    assert "token" not in json.dumps(entry["permission_config"]).lower()


def test_unknown_profile_still_raises_before_rendering(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _capture(monkeypatch)
    with pytest.raises(ValueError):
        ocrun._permission_config("opencode", "nope", repo, None)


def test_codex_run_has_no_opencode_config(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[Path] = []
    monkeypatch.setattr(ocrun, "launch_visible", lambda script: launched.append(script) or True)
    monkeypatch.setattr(ocrun, "wait_for_done", lambda done, timeout: False)

    result = ocrun.run(
        "x",
        "gpt-6-luna",
        cwd=repo,
        timeout=1.0,
        routing_log=repo / "r.jsonl",
        mode="visible",
        worker="codex",
    )
    assert result["permission_config"] is None
    assert not (repo / ".hx" / "profiles").exists()


def test_visible_unprofiled_run_carries_the_baseline_config(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launched: list[Path] = []
    monkeypatch.setattr(ocrun, "launch_visible", lambda script: launched.append(script) or True)
    monkeypatch.setattr(ocrun, "wait_for_done", lambda done, timeout: False)
    monkeypatch.setattr(ocrun, "find_session_by_title", lambda title, cwd=None: None)

    result = ocrun.run(
        "read TEAM.md",
        "glm-5.3-flash",
        cwd=repo,
        timeout=1.0,
        title="perm visible",
        routing_log=repo / "r.jsonl",
        mode="visible",
    )

    script = launched[0].read_text(encoding="utf-8")
    assert "$env:OPENCODE_CONFIG" in script
    config_path = repo / ".hx" / "profiles" / "opencode-baseline.jsonc"
    assert str(config_path) in script
    assert result["permission_config"]["profile"] == "baseline"
    assert result["permission_config"]["sha256"] == hashlib.sha256(config_path.read_bytes()).hexdigest()
