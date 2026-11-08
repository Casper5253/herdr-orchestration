"""Tests for hx.doctor: ok, drift and unknown across the checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hx import doctor


def _policy() -> dict:
    return {
        "claude": {"strong_model": "sonnet"},
        "codex": {"strong_model": "gpt-6.1-sol"},
        "persistent_opencode_model": "deepseek-v4.1-flash",
        "devin": {"models": {"medium": "swe-2-high"}},
        "context_limits": {"deepseek-v4.1-flash": 200000},
    }


def _skills_tree(root: Path, skills=("herdr-team", "hx-router", "poteto-mode")) -> Path:
    for name in skills:
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    return root


def _roots(tmp_path: Path) -> dict:
    claude = _skills_tree(tmp_path / "claude-skills")
    codex = _skills_tree(tmp_path / "codex-skills")
    devin = _skills_tree(tmp_path / "devin-skills")
    overlay = tmp_path / "overlay.jsonc"
    overlay.write_text('{"model": "opencode-go/deepseek-v4.1-flash"}\n', encoding="utf-8")
    profile = tmp_path / "trusted-dev.config.toml"
    profile.write_text('model = "gpt-6.1-sol"\n', encoding="utf-8")
    pane = tmp_path / "dev-devin" / "config.json"
    pane.parent.mkdir(parents=True)
    pane.write_text(
        json.dumps(
            {
                "agent": {"model": "swe-2-high"},
                "permissions": {
                    "allow": [],
                    "deny": [
                        "Exec(hx ocrun)", "Exec(hx compact)", "Exec(hx context)",
                        "Exec(hx task)", "Exec(hx route)", "Exec(python -m hx)",
                        "Exec(git push)", "Exec(git clone)", "Exec(gh)", "Exec(herdr)",
                    ],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    router = tmp_path / "hx-router"
    (claude / "hx-router" / "SKILL.md").write_text("neutral text\n", encoding="utf-8")
    return {
        "claude_skills": claude,
        "codex_skills": codex,
        "devin_skills": devin,
        "overlay": overlay,
        "codex_profile": profile,
        "devin_pane": pane,
        "hx_ocrun": tmp_path / "missing-ocrun.py",
    }


def test_ok(tmp_path: Path) -> None:
    report = doctor.doctor(_policy(), _roots(tmp_path))
    assert report["workers"]["codex"]["model"]["status"] == "ok"
    assert report["workers"]["opencode"]["model"]["status"] == "ok"
    assert report["workers"]["devin"]["model"]["status"] == "ok"
    assert report["workers"]["opencode"]["context_telemetry"] == "known"
    assert report["workers"]["devin"]["context_telemetry"] == "unknown"
    assert report["workers"]["claude"]["model"]["status"] == "unknown"
    assert report["workers"]["codex"]["skills"]["status"] == "ok"
    assert report["drift"] is False


def test_drift_model_and_missing_skills(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    roots["overlay"].write_text('{"model": "opencode-go/qwen3.7-plus"}\n', encoding="utf-8")
    (roots["devin_skills"] / "hx-router" / "SKILL.md").unlink()
    report = doctor.doctor(_policy(), roots)
    model = report["workers"]["opencode"]["model"]
    assert model["status"] == "drift"
    assert model["observed"] == "qwen3.7-plus"
    assert model["selected"] == "deepseek-v4.1-flash"
    assert report["workers"]["devin"]["skills"]["status"] == "drift"
    assert "hx-router" in report["workers"]["devin"]["skills"]["missing"]
    assert report["drift"] is True


def test_deny_profile_drift_and_unknown(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    # Live pane config denies git push but the profile agrees, so ok; add a
    # rule the profile does not narrow (hx say denied) -> drift.
    pane = json.loads(roots["devin_pane"].read_text(encoding="utf-8"))
    pane["permissions"]["deny"] = ["Exec(git push)", "Exec(hx say)"]
    roots["devin_pane"].write_text(json.dumps(pane), encoding="utf-8")
    report = doctor.doctor(_policy(), roots)
    deny = report["workers"]["devin"]["deny_profile"]
    assert deny["status"] == "drift"
    assert any(m["command"] == "hx say claude hi" for m in deny["mismatches"])

    roots["devin_pane"].write_text("not json", encoding="utf-8")
    report = doctor.doctor(_policy(), roots)
    assert report["workers"]["devin"]["deny_profile"]["status"] == "unknown"


def test_prose_scan_finds_stale_lines(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    (roots["claude_skills"] / "hx-router" / "SKILL.md").write_text(
        "spend first, each OpenCode model has its own allowance\n", encoding="utf-8"
    )
    ocrun = tmp_path / "hx-ocrun.py"
    ocrun.write_text('x = "start its notes with a plain How to review block"\n', encoding="utf-8")
    roots["hx_ocrun"] = ocrun
    roots["overlay"].write_text('{"model": "opencode-go/qwen3.7-plus"}\n', encoding="utf-8")
    report = doctor.doctor(_policy(), roots)
    rules = {f["rule"]: f for f in report["prose"]}
    assert rules["hx-router-per-model-allowance"]["status"] == "drift"
    assert rules["devin-preamble-review-in-notes"]["status"] == "drift"
    overlay = rules["opencode-overlay-model-pin"]
    assert overlay["status"] == "drift"
    assert overlay["policy_value"] == "deepseek-v4.1-flash"
    assert overlay["line"] == 1


def test_unreadable_sources_are_unknown(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    roots["codex_profile"] = tmp_path / "absent.toml"
    roots["codex_skills"] = tmp_path / "no-such-dir"
    report = doctor.doctor(_policy(), roots)
    assert report["workers"]["codex"]["model"]["status"] == "unknown"
    assert report["workers"]["codex"]["skills"]["status"] == "unknown"


def test_main_exit_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(doctor, "doctor", lambda: {"workers": {}, "prose": [], "drift": True})
    assert doctor.main(["--json"]) == 1
    monkeypatch.setattr(doctor, "doctor", lambda: {"workers": {}, "prose": [], "drift": False})
    assert doctor.main([]) == 0
