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
        "kind": "opencode", "session_id": "live-session"})


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
    assert report["workers"]["codex"]["model"]["status"] == "config"
    assert report["workers"]["opencode"]["model"]["status"] == "ok"
    assert report["workers"]["devin"]["model"]["status"] == "config"
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
        monkeypatch.setattr(doctor.context, "agent_info", lambda name: {"kind": "opencode"})
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


def _member(name, kind="codex", status="idle", sid="s1", address="t1", reason="bound session"):
    team, role = name.rsplit("-", 1)
    return doctor.host.Member(
        name=name, team=team, role=role, kind=kind, status=status,
        cwd="C:\repo", address=address, session_id=sid,
        session_reason=reason, focused=None, turn=None)


class FakeHost:
    def __init__(self, members=None, own_error=None):
        self.members = members or {}
        self.own_error = own_error

    def own(self):
        if self.own_error:
            raise self.own_error
        return next(iter(self.members.values()), None)

    def team(self, prefix):
        prefix = prefix.rstrip("-")
        return {n: m for n, m in self.members.items() if m["team"] == prefix}


def test_member_model_claude_reads_the_bound_status_file(tmp_path, monkeypatch):
    now = doctor.dt.datetime.now(doctor.dt.timezone.utc)
    state = tmp_path / "state"
    state.mkdir()
    (state / "claude-status-s1.json").write_text(json.dumps({
        "session_id": "s1", "model": {"id": "claude-opus-4.6"},
        "timestamp": now.isoformat()}), encoding="utf-8")
    monkeypatch.setenv("HX_STATE_DIR", str(state))
    member = _member("dev-lead", kind="claude", sid="s1")
    model = doctor._member_model(member, {}, _policy(), now)
    assert model["status"] == "ok" and model["observed"] == "opus"
    assert model["age_seconds"] == 0 and model["source"].endswith("claude-status-s1.json")


def test_member_model_opencode_uses_the_bound_session(tmp_path):
    now = doctor.dt.datetime.now(doctor.dt.timezone.utc)
    roots = {"opencode_db": tmp_path / "oc.db", "home": tmp_path}
    _message(roots["opencode_db"], "s1", "deepseek-v4.1-flash", 2)
    _message(roots["opencode_db"], "other", "qwen3.7-plus", 999)
    model = doctor._member_model(_member("dev-opencode", kind="opencode", sid="s1"), roots, _policy(), now)
    assert model["observed"] == "deepseek-v4.1-flash" and "session=s1" in model["source"]


def test_member_model_unknown_reasons(tmp_path, monkeypatch):
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "no-state"))
    now = doctor.dt.datetime.now(doctor.dt.timezone.utc)
    member = _member("dev-codex", sid=None, reason="no binding file")
    model = doctor._member_model(member, {}, _policy(), now)
    assert model["status"] == "unknown" and "no session binding" in model["reason"]
    devin = _member("dev-devin", kind="devin", sid="devin-pane:t9")
    model = doctor._member_model(devin, {}, _policy(), now)
    assert model["status"] == "unknown" and "transcript" in model["reason"]
    unknown = _member("dev-mystery", kind="unknown", sid="s1")
    assert "no model source" in doctor._member_model(unknown, {}, _policy(), now)["reason"]
    codex = _member("dev-codex", kind="codex", sid="absent-sid")
    assert "no model field" in doctor._member_model(codex, {}, _policy(), now)["reason"]


def _devin_pane(tmp_path: Path, transcript: dict | None = None,
                transcript_age_ms: int | None = None) -> dict:
    pane = tmp_path / "dev-devin"
    pane.mkdir()
    (pane / "config.json").write_text("{}\n", encoding="utf-8")
    if transcript is not None:
        (pane / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")
        if transcript_age_ms is not None:
            import os
            stamp = transcript_age_ms / 1000
            os.utime(pane / "transcript.json", (stamp, stamp))
    return {"devin_pane": pane / "config.json", "home": tmp_path}


def _devin_member(created_ms: int | None = None, sid: str = "devin-pane:t9") -> dict:
    member = _member("dev-devin", kind="devin", sid=sid)
    member["created_ms"] = created_ms
    return member


def test_member_model_devin_reads_the_export_transcript(tmp_path):
    """The pane's --export transcript records agent.model_name ("SWE-2 High")."""
    roots = _devin_pane(tmp_path, transcript={
        "session_id": "fierce-sprout",
        "agent": {"model_name": "SWE-2 High"},
        "steps": [],
    })
    now = doctor.dt.datetime.now(doctor.dt.timezone.utc)
    model = doctor._member_model(_devin_member(created_ms=1), roots, _policy(), now)
    assert model["status"] == "ok"
    assert model["observed"] == "swe-2-high"
    assert "transcript" in model["source"]


def test_member_model_devin_stale_generation_falls_back_to_the_footer(tmp_path, monkeypatch):
    """An export older than this terminal's created_ms is a previous generation:
    its model must never stand in for the live pane's (Codex's stale-SWE-2-High case)."""
    now = doctor.dt.datetime.now(doctor.dt.timezone.utc)
    created_ms = int(now.timestamp() * 1000)
    roots = _devin_pane(tmp_path, transcript={
        "session_id": "obsolete-session",
        "agent": {"model_name": "SWE-2 High"},
        "steps": [],
    }, transcript_age_ms=created_ms - 60000)
    monkeypatch.setattr(doctor.context, "_herdr_read", lambda name, lines=200:
        "SWE-2 Max · bypass                  Context: 118k / 262k tokens (45%)\n")
    model = doctor._member_model(_devin_member(created_ms=created_ms), roots, _policy(), now)
    assert model["observed"] == "swe-2-max"
    assert "footer" in model["source"]


def test_member_model_devin_transcript_session_mismatch_falls_back(tmp_path, monkeypatch):
    """A transcript naming a different bound session is not this pane's model."""
    roots = _devin_pane(tmp_path, transcript={
        "session_id": "ses-old",
        "agent": {"model_name": "SWE-2 Max"},
        "steps": [],
    })
    monkeypatch.setattr(doctor.context, "_herdr_read", lambda name, lines=200:
        "SWE-2 High · bypass                  Context: 118k / 262k tokens (45%)\n")
    now = doctor.dt.datetime.now(doctor.dt.timezone.utc)
    model = doctor._member_model(
        _devin_member(created_ms=1, sid="ses-live"), roots, _policy(), now)
    assert model["observed"] == "swe-2-high"
    assert "footer" in model["source"]


@pytest.mark.parametrize("transcript", [
    {"agent": ["malformed"]},
    {"agent": "malformed"},
    {"session_id": 7, "agent": {"model_name": "SWE-2 High"}},
    ["not", "a", "dict"],
    "just a string",
])
def test_member_model_devin_malformed_transcript_is_unknown_never_a_crash(
    tmp_path, monkeypatch, transcript
):
    roots = _devin_pane(tmp_path, transcript=transcript)
    monkeypatch.setattr(doctor.context, "_herdr_read", lambda name, lines=200:
        "Pro | 100% remaining (resets in 23h 20m)\n")
    now = doctor.dt.datetime.now(doctor.dt.timezone.utc)
    model = doctor._member_model(_devin_member(created_ms=1), roots, _policy(), now)
    assert model["status"] == "unknown"
    assert model["observed"] is None


def test_member_model_devin_falls_back_to_the_footer_label(tmp_path, monkeypatch):
    """No transcript: the footer's `SWE-2 Max · …` label normalizes to the id."""
    roots = _devin_pane(tmp_path)
    monkeypatch.setattr(doctor.context, "_herdr_read", lambda name, lines=200:
        "SWE-2 Max · bypass                  Context: 118k / 262k tokens (45%)\n")
    now = doctor.dt.datetime.now(doctor.dt.timezone.utc)
    model = doctor._member_model(_devin_member(), roots, _policy(), now)
    assert model["status"] == "ok"
    assert model["observed"] == "swe-2-max"
    assert "footer" in model["source"]


def test_member_model_devin_footer_label_on_the_narrow_two_line_footer(tmp_path, monkeypatch):
    """The narrow footer carries the label on the model line above a bare usage line."""
    roots = _devin_pane(tmp_path)
    monkeypatch.setattr(doctor.context, "_herdr_read", lambda name, lines=200:
        "SWE-2 High · bypass\n44k / 262k (16%)\n")
    now = doctor.dt.datetime.now(doctor.dt.timezone.utc)
    model = doctor._member_model(_devin_member(), roots, _policy(), now)
    assert model["status"] == "ok"
    assert model["observed"] == "swe-2-high"
    assert "footer" in model["source"]


def test_member_model_devin_unknown_when_neither_source_reads(tmp_path, monkeypatch):
    roots = _devin_pane(tmp_path)
    monkeypatch.setattr(doctor.context, "_herdr_read", lambda name, lines=200:
        "Pro · 100% remaining (resets in 23h 20m)\n")
    now = doctor.dt.datetime.now(doctor.dt.timezone.utc)
    model = doctor._member_model(_devin_member(), roots, _policy(), now)
    assert model["status"] == "unknown"
    assert model["observed"] is None


def test_check_members_lead_seat_is_information_never_drift(tmp_path, monkeypatch):
    """The lead's model is evidence for the reader, never a drift verdict."""
    state = tmp_path / "state"
    state.mkdir()
    now = doctor.dt.datetime.now(doctor.dt.timezone.utc)
    (state / "claude-status-s1.json").write_text(json.dumps({
        "session_id": "s1", "model": {"id": "claude-opus-4.6"},
        "timestamp": now.isoformat()}), encoding="utf-8")
    monkeypatch.setenv("HX_STATE_DIR", str(state))
    members = {
        "dev-lead": _member("dev-lead", kind="claude", sid="s1"),
        "dev-opencode": _member("dev-opencode", kind="opencode", sid="s1"),
    }
    roots = {"opencode_db": tmp_path / "oc.db", "home": tmp_path}
    _message(roots["opencode_db"], "s1", "deepseek-v4.1-flash", 2)
    report = doctor._check_members(_policy(), roots, now, detected=FakeHost(members))
    lead = report["members"]["dev-lead"]["model"]
    assert lead["observed"] == "opus"
    assert lead["status"] != "drift"
    assert report["members"]["dev-opencode"]["model"]["status"] == "ok"


def test_doctor_never_reports_drift_from_the_lead_seat(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    now = doctor.dt.datetime.now(doctor.dt.timezone.utc)
    (state / "claude-status-s1.json").write_text(json.dumps({
        "session_id": "s1", "model": {"id": "claude-opus-4.6"},
        "timestamp": now.isoformat()}), encoding="utf-8")
    monkeypatch.setenv("HX_STATE_DIR", str(state))
    members = {"dev-lead": _member("dev-lead", kind="claude", sid="s1")}
    monkeypatch.setattr(doctor.host, "detect", lambda: FakeHost(members))
    monkeypatch.setattr(doctor, "_check_workers", lambda policy, roots: {})
    monkeypatch.setattr(doctor, "_scan_prose", lambda policy, roots: [])
    monkeypatch.setattr(doctor, "_installed_files", lambda roots: [])
    report = doctor.doctor(_policy())
    assert report["drift"] is False
    assert report["members"]["members"]["dev-lead"]["model"]["status"] != "drift"


def test_ready_reproduces_the_lead_terminal_output(tmp_path, monkeypatch):
    """The exact t23 verdict: only the three true problems stay named."""
    monkeypatch.setattr(doctor.host, "tool_path", lambda exe: f"/bin/{exe}")
    roster = {
        "dev-lead": _member("dev-lead", kind="claude", status="idle", sid="s1"),
        "dev-codex": _member("dev-codex", status="idle", sid=None,
                             reason="no readable binding file"),
        "dev-devin": _member("dev-devin", kind="devin", status="idle", sid=None,
                             address="t2m", reason="no readable binding file"),
        "dev-opencode": _member("dev-opencode", status="unknown", sid=None,
                                reason="no readable binding file"),
    }
    verdict = doctor.ready(_policy(), {}, detected=FakeHost(roster))
    assert verdict["ready"] is False
    # Defect 1: a Claude lead does not make a Claude worker required.
    assert not any("role missing" in m and "claude" in m for m in verdict["missing"])
    # Defect 2: the lead's model is information, never worker drift.
    assert not any("dev-lead" in m for m in verdict["missing"])
    # Defect 3: hx's own devin-pane binding counts as a binding.
    assert not any("worker dev-devin: no session binding" in m for m in verdict["missing"])
    # The true problems still named verbatim.
    assert any("dev-codex" in m and "no session binding" in m for m in verdict["missing"])
    assert any("dev-opencode" in m and "status unknown" in m for m in verdict["missing"])
    assert any("dev-opencode" in m and "no session binding" in m for m in verdict["missing"])


def test_check_members_reports_per_member_and_unknown_never_drifts(tmp_path):
    members = {
        "dev-opencode": _member("dev-opencode", kind="opencode", sid="s1"),
        "dev-codex": _member("dev-codex", kind="codex", sid=None, reason=None),
    }
    roots = {"opencode_db": tmp_path / "oc.db", "home": tmp_path}
    _message(roots["opencode_db"], "s1", "wrong-model", 2)
    now = doctor.dt.datetime.now(doctor.dt.timezone.utc)
    report = doctor._check_members(_policy(), roots, now, detected=FakeHost(members))
    assert report["members"]["dev-opencode"]["model"]["status"] == "drift"
    unknown = report["members"]["dev-codex"]["model"]
    assert unknown["status"] == "unknown" and "no session binding" in unknown["reason"]


def test_ready_verdict_names_the_missing_pieces(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor.host, "tool_path", lambda exe: f"/bin/{exe}")
    monkeypatch.setattr(doctor, "_member_model",
                        lambda m, r, p, n: {"status": "ok", "observed":
                            doctor._selected_models(p).get(m["kind"]),
                            "source": "s", "age_seconds": 0, "reason": None})
    full = FakeHost({f"dev-{r}": _member(f"dev-{r}", kind="claude" if r == "lead" else r,
                     status="idle", sid="s1")
                     for r in ("lead", "claude", "codex", "opencode", "devin")})
    assert doctor.ready(_policy(), {}, detected=full)["ready"] is True
    assert doctor.ready(_policy(), {}, detected=FakeHost({}))["ready"] is False
    sparse = FakeHost({"dev-codex": _member("dev-codex", status="idle", sid="s1")})
    verdict = doctor.ready(_policy(), {}, detected=sparse)
    assert verdict["ready"] is False
    assert any("role missing" in m and "lead" in m for m in verdict["missing"])

    class NoOwn(FakeHost):
        def own(self):
            return None
    verdict = doctor.ready(_policy(), {}, detected=NoOwn(
        {"dev-codex": _member("dev-codex", status="idle", sid="s1")}))
    assert any("identity" in m for m in verdict["missing"])

    bad = FakeHost({
        "dev-codex": _member("dev-codex", status="unknown", sid="s1"),
        "dev-opencode": _member("dev-opencode", status="idle", sid=None, reason=None),
    }, own_error=doctor.host.IdentityError("terminal t1 is finished"))
    verdict = doctor.ready(_policy(), {}, detected=bad)
    assert verdict["ready"] is False
    assert any("lead identity unavailable" in m for m in verdict["missing"])
    assert any("dev-codex" in m and "status unknown" in m for m in verdict["missing"])
    assert any("dev-opencode" in m and "no session binding" in m for m in verdict["missing"])

    monkeypatch.setattr(doctor.host, "tool_path", lambda exe: (_ for _ in ()).throw(doctor.host.HostToolMissing("hc not found")))
    verdict = doctor.ready(_policy(), {}, detected=full)
    assert any("hc executable" in m for m in verdict["missing"])


def test_main_ready_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(doctor.host, "tool_path", lambda exe: f"/bin/{exe}")
    monkeypatch.setattr(doctor, "_member_model",
                        lambda m, r, p, n: {"status": "ok", "observed":
                            doctor._selected_models(p).get(m["kind"]),
                            "source": "s", "age_seconds": 0, "reason": None})
    monkeypatch.setattr(doctor.host, "detect", lambda: FakeHost(
        {f"dev-{r}": _member(f"dev-{r}", kind="claude" if r == "lead" else r,
         status="idle", sid="s1")
         for r in ("lead", "claude", "codex", "opencode", "devin")}))
    assert doctor.main(["--ready"]) == 0
    assert "ready" in capsys.readouterr().out
    monkeypatch.setattr(doctor.host, "detect", lambda: FakeHost(
        {}, own_error=doctor.host.IdentityError("gone")))
    assert doctor.main(["--ready"]) == 1
    assert "lead identity unavailable" in capsys.readouterr().out
