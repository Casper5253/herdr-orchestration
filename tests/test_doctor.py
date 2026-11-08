"""Tests for hx.doctor: ok, drift and unknown across the checks."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from hx import doctor


@pytest.fixture(autouse=True)
def isolated_live_agent(monkeypatch):
    monkeypatch.setattr(doctor.usage, "team_prefix", lambda policy: "dev-")
    monkeypatch.setattr(doctor.context, "agent_info", lambda name: {
        "agent": "opencode", "agent_session": {"value": "live-session"}})


def _message(db, session, model, created=1, provider="opencode-go"):
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS message (session_id TEXT, time_created INTEGER, data TEXT)")
        conn.execute("INSERT INTO message VALUES (?, ?, ?)", (session, created, json.dumps({
            "role": "assistant", "modelID": model, "providerID": provider})))


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
    db = tmp_path / "opencode.db"
    _message(db, "live-session", "deepseek-v4.1-flash")
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
        "opencode_db": db,
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
    _message(roots["opencode_db"], "live-session", "qwen3.7-plus", 2)
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


def test_installed_qwen_repo_deepseek_regression(monkeypatch, tmp_path):
    """The stale 22 Oct installed overlay must not be hidden by the repo copy."""
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    (repo / "team").mkdir(parents=True)
    (home / ".herdr-team").mkdir(parents=True)
    (repo / "team/opencode-trusted-dev.jsonc").write_text(
        '{"model":"opencode-go/deepseek-v4.1-flash"}', encoding="utf-8")
    (home / ".herdr-team/opencode-trusted-dev.jsonc").write_text(
        '{"model":"opencode-go/qwen3.7-plus"}', encoding="utf-8")
    # Reproduce the real install layout without reading the real home or DB.
    manifest = Path(__file__).resolve().parents[1] / "team/install-files.json"
    (repo / "team/install-files.json").write_bytes(manifest.read_bytes())
    monkeypatch.setattr(doctor.paths, "package_root", lambda: repo)
    monkeypatch.setattr(doctor.os.path, "expanduser", lambda value: str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData/Roaming"))
    monkeypatch.setenv("HX_OPENCODE_DB", str(tmp_path / "absent.db"))
    monkeypatch.setattr(doctor.devin_mod, "state_dir", lambda: home / ".herdr-team/state")
    monkeypatch.setattr(doctor.devin_mod, "pane_config_template", lambda: repo / "team/devin-pane.config.json")
    monkeypatch.setattr(doctor, "_deny_check", lambda *args: {"status": "unknown"})
    report = doctor.doctor({"persistent_opencode_model": "deepseek-v4.1-flash"})
    assert report["drift"] is True
    assert report["workers"]["opencode"]["model"]["status"] != "ok"
    overlay = next(c for c in report["installed_files"] if c["target"].endswith("opencode-trusted-dev.jsonc"))
    assert overlay["status"] == "drift"


@pytest.mark.parametrize("model,provider,status", [
    ("qwen3.7-plus", "opencode-go", "drift"),
    ("deepseek-v4.1-flash", "opencode-go", "ok"),
    ("deepseek-v4.1-flash", "other-provider", "drift"),
])
def test_live_model_latest_message_bound_to_session(tmp_path, model, provider, status):
    roots = _roots(tmp_path)
    _message(roots["opencode_db"], "live-session", model, 2, provider)
    _message(roots["opencode_db"], "unrelated-session", "qwen3.7-plus", 999)
    check = doctor.doctor(_policy(), roots)["workers"]["opencode"]["model"]
    assert check["status"] == status
    assert "session=live-session" in check["source"]


@pytest.mark.parametrize("failure", ["no-session", "missing-db", "bad-db", "bad-message", "no-message"])
def test_live_unknown_never_falls_back_to_good_overlay(tmp_path, monkeypatch, failure):
    roots = _roots(tmp_path)
    if failure == "no-session":
        monkeypatch.setattr(doctor.context, "agent_info", lambda name: {"agent": "opencode"})
    elif failure == "missing-db":
        roots["opencode_db"] = tmp_path / "absent.db"
    elif failure == "bad-db":
        roots["opencode_db"].write_text("not sqlite", encoding="utf-8")
    else:
        with sqlite3.connect(roots["opencode_db"]) as conn:
            conn.execute("DELETE FROM message")
            if failure == "bad-message":
                conn.execute("INSERT INTO message VALUES ('live-session', 2, 'bad json')")
    assert doctor.doctor(_policy(), roots)["workers"]["opencode"]["model"]["status"] == "unknown"


def test_every_manifest_copy_drift_and_missing_file(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    roots = {"repo": repo, "home": tmp_path}
    entries = json.loads((repo / "team/install-files.json").read_text())
    for entry in entries:
        target = tmp_path / entry["target"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((repo / entry["source"]).read_bytes())
    assert all(c["status"] == "ok" for c in doctor._installed_files(roots))
    for entry in entries:
        target = tmp_path / entry["target"]
        good = target.read_bytes()
        target.write_bytes(good + b"\nchanged")
        checks = doctor._installed_files(roots)
        assert next(c for c in checks if c["target"] == str(target))["status"] == "drift"
        target.unlink()
        checks = doctor._installed_files(roots)
        assert next(c for c in checks if c["target"] == str(target))["status"] == "drift"
        target.write_bytes(good)
