"""Tests for the frozen skill-eval runner: runner errors are not votes."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("skill_eval_run", ROOT / "evals" / "skills" / "run.py")
assert spec and spec.loader
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

CASE = {"id": "T1", "prompt": "p", "checks": {"must": []}}


def _isolate(monkeypatch, tmp_path: Path) -> Path:
    skill = tmp_path / "SKILL.md"
    skill.write_text("skill", encoding="utf-8")
    monkeypatch.setattr(run, "check_frozen", lambda: None)
    monkeypatch.setattr(run, "load_cases", lambda _skill: [CASE])
    monkeypatch.setattr(run, "resolve_skill", lambda _base, _skill: skill)
    return skill


def _args(skill: Path) -> list[str]:
    return ["--skill", "poteto-mode", "--before", str(skill), "--after", str(skill), "--repeat", "3"]


def test_six_runner_errors_exit_error_not_promotion(monkeypatch, tmp_path: Path) -> None:
    skill = _isolate(monkeypatch, tmp_path)
    calls: list[int] = []

    def boom(*_args, **_kwargs):
        calls.append(1)
        raise run.EvalError("synthetic runner failure")

    monkeypatch.setattr(run, "run_model", boom)
    code = run.main(_args(skill))
    assert len(calls) == 6
    assert code == 1


def test_equal_scores_promote_when_both_sides_ran(monkeypatch, tmp_path: Path) -> None:
    skill = _isolate(monkeypatch, tmp_path)
    calls: list[int] = []

    def ok(*_args, **_kwargs):
        calls.append(1)
        return "answer"

    monkeypatch.setattr(run, "run_model", ok)
    monkeypatch.setattr(run, "grade", lambda _case, _text: (True, "ok"))
    code = run.main(_args(skill))
    assert len(calls) == 6
    assert code == 0


def test_one_runner_error_blocks_an_otherwise_promoting_run(monkeypatch, tmp_path: Path) -> None:
    skill = _isolate(monkeypatch, tmp_path)
    calls: list[int] = []

    def sometimes(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise run.EvalError("synthetic runner failure")
        return "answer"

    monkeypatch.setattr(run, "run_model", sometimes)
    monkeypatch.setattr(run, "grade", lambda _case, _text: (True, "ok"))
    assert run.main(_args(skill)) == 1
