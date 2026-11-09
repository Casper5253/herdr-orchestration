"""Tests for live Devin panes: usage.live_sessions, the shared concurrency cap,
persistent-devin routing and the generated denied-prefix prompt section.

All agent lists are synthetic; nothing touches real herdr or the network.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from hx import devin, host
from hx import usage as hx_usage
from hx.route import route

MODEL = "swe-2-high"
ENDS_AT = "2026-11-30T10:00:00Z"
NOW = datetime.datetime(2026, 11, 1, tzinfo=datetime.timezone.utc)
REAL_LIVE_MEMBERS = hx_usage.live_members


@pytest.fixture(autouse=True)
def synthetic_herdr_agents(monkeypatch):
    monkeypatch.setattr(hx_usage, "live_members", lambda: [])


def _policy(tmp_path: Path, **overrides) -> dict:
    bin_path = tmp_path / "devin.exe"
    bin_path.write_text("dummy", encoding="utf-8")
    config = tmp_path / "worker.config.json"
    config.write_text("{}", encoding="utf-8")
    conf = {
        "enabled": True,
        "bin": str(bin_path),
        "worker_config": str(config),
        "kinds": list(devin.DEFAULT_KINDS),
        "models": dict(devin.DEFAULT_MODELS),
        "allowed_models": ["swe-2-medium", "swe-2-high", "swe-2-max"],
        "permission_mode": "accept-edits",
        "max_concurrent": 2,
        "promo": {"ends_at": ENDS_AT},
    }
    conf.update(overrides)
    return {"devin": conf}


def _pane(name: str | None, status: str = "idle", agent: str = "devin") -> dict:
    return host.member({"agent": agent, "agent_status": status, "cwd": "C:\\work", "name": name or ""})


# ---------- devin.usage live_sessions ----------


def test_live_sessions_lists_devin_agents_any_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every agent whose kind is devin counts, named or not; other kinds don't."""
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    agents = [
        _pane("dev-devin", status="working"),
        _pane(None),  # the user's own pane has no team name
        _pane("dev-claude", agent="claude"),
        _pane("dev-codex", agent="codex"),
    ]
    status = devin.usage(_policy(tmp_path), now=NOW, agents=agents)
    assert status["live_sessions"] == [
        {"name": "dev-devin", "status": "working", "cwd": "C:\\work"},
        {"name": None, "status": "idle", "cwd": "C:\\work"},
    ]
    assert status["active_runs"] == 0


def test_live_sessions_empty_when_agents_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    status = devin.usage(_policy(tmp_path), now=NOW)
    assert status["live_sessions"] == []


def test_live_sessions_fill_the_cap_and_make_usage_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    status = devin.usage(
        _policy(tmp_path), now=NOW, agents=[_pane("dev-devin"), _pane(None)]
    )
    assert status["status"] == "busy"
    assert status["active_runs"] == 0
    assert "2/2" in status["reason"]


def test_live_sessions_and_run_slots_share_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    slot = devin.acquire_slot(2, 600.0, {"id": "run-a"}, now=NOW)
    assert slot is not None
    status = devin.usage(_policy(tmp_path), now=NOW, agents=[_pane("dev-devin")])
    assert status["status"] == "busy"
    assert status["active_runs"] == 1
    assert len(status["live_sessions"]) == 1


def test_acquire_slot_refuses_when_live_sessions_fill_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``hx ocrun --worker devin`` refuses busy through the same slot path:
    the recorded live sessions hold capacity inside acquire_slot."""
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(hx_usage, "live_members", lambda: [_pane("dev-devin", "working"), _pane(None)])
    devin.mark_live_sessions(
        [{"name": "dev-devin", "status": "working", "cwd": None},
         {"name": None, "status": "idle", "cwd": None}],
        now=NOW,
    )
    info = devin.new_slot_info(tmp_path, "run-a", MODEL)
    assert devin.acquire_slot(2, 60.0, info, now=NOW) is None
    # One free seat remains when the cap is higher than the live count.
    assert devin.acquire_slot(3, 60.0, info, now=NOW) is not None


def test_recorded_live_sessions_expire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    devin.mark_live_sessions(
        [{"name": "dev-devin", "status": "idle", "cwd": None}], now=NOW
    )
    later = NOW + datetime.timedelta(seconds=devin.LIVE_SESSIONS_TTL_S + 1)
    def unavailable():
        raise OSError("Herdr unavailable")
    monkeypatch.setattr(hx_usage, "live_members", unavailable)
    assert devin.acquire_slot(1, 60.0, {"id": "a"}, now=later) is None
    status = devin.usage(_policy(tmp_path), now=later)
    assert status["live_sessions"] is None
    assert status["status"] == "busy"


# ---------- one herdr call feeds both usage blocks ----------


def test_usage_snapshot_shares_one_agent_list_and_marks_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("HX_OPENCODE_DB", str(tmp_path / "no.db"))
    monkeypatch.setenv("HX_CODEX_SESSIONS_DIR", str(tmp_path / "no-sessions"))
    monkeypatch.setenv("HX_CLAUDE_STATUS", str(tmp_path / "no-status.json"))
    agents = [
        _pane("dev-devin", status="working"),
        _pane(None),
        _pane("dev-claude", agent="claude"),
    ]
    calls = []

    def fake_list() -> list:
        calls.append(1)
        return agents

    monkeypatch.setattr("hx.usage.live_members", fake_list)
    policy = _policy(tmp_path)
    policy["team_prefix"] = "dev-"
    snapshot = hx_usage.usage_snapshot(policy=policy, now=NOW)

    assert calls == [1]
    assert snapshot["devin"]["live_sessions"] == [
        {"name": "dev-devin", "status": "working", "cwd": "C:\\work"},
        {"name": None, "status": "idle", "cwd": "C:\\work"},
    ]
    assert [a["name"] for a in snapshot["agents"]["agents"]] == ["dev-devin", "dev-claude"]
    # Admission refreshes the same synthetic agent list.
    assert devin.usage(policy, now=NOW)["status"] == "busy"
    assert devin.acquire_slot(2, 60.0, {"id": "a"}, now=NOW) is None


# ---------- route: persistent-devin ----------


@pytest.fixture
def route_policy() -> dict:
    return {
        "team_prefix": "dev-",
        "ladders": {
            "economy": ["deepseek-v4.1-flash", "mimo-v2.6-flash"],
            "default": ["minimax-m3", "qwen3.7-plus"],
            "strong": ["kimi-k2.7-code"],
        },
        "thresholds": {"pool_soft_limit": 0.85, "stale_seconds": 3600},
        "max_attempts": 3,
        "codex": {"economy_model": "gpt-6-luna", "strong_model": "gpt-6.1-sol"},
        "effort_by_kind": {"feature": "medium", "routine": "low", "debug": "medium"},
        "default_effort": "medium",
        "routing_order": ["devin", "opencode"],
        "devin": {
            "enabled": True,
            "kinds": ["routine", "investigate", "feature", "debug", "refactor", "test"],
            "models": {
                "low": "swe-2-high",
                "medium": "swe-2-high",
                "high": "swe-2-max",
                "max": "swe-2-max",
            },
            "allowed_models": ["swe-2-medium", "swe-2-high", "swe-2-max"],
            "promo": {"ends_at": ENDS_AT},
        },
    }


def _route_usage(live_sessions: list | None = None, status: str = "available") -> dict:
    return {
        "opencode": {
            "tag": "estimated",
            "sample_age_seconds": 0,
            "pool": {"windows": {
                "5h": {"utilisation": 0.1},
                "7d": {"utilisation": 0.1},
                "30d": {"utilisation": 0.1},
            }},
            "models": {},
        },
        "codex": {"tag": "measured", "sample_age_seconds": 0, "rate_limits": {}},
        "claude": {"tag": "measured", "sample_age_seconds": 0, "rate_limits": {}},
        "devin": {
            "status": status,
            "promo": {"ends_at": ENDS_AT},
            "live_sessions": live_sessions or [],
        },
        "agents": {"prefix": "dev-"},
    }


def _task(**overrides) -> dict:
    task = {
        "kind": "feature",
        "risk": "low",
        "size": "small",
        "attempt": 1,
        "last_failure": "none",
        "parallel": False,
        "prev": None,
        "persistent": None,
        "iterative": None,
    }
    task.update(overrides)
    return task


IDLE_DEVIN = [{"name": "dev-devin", "status": "idle", "cwd": "C:\\work"}]


def test_r1_admission_refreshes_expired_pane_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(hx_usage, "live_members", lambda: [_pane("dev-devin", "working")])
    devin.mark_live_sessions(devin.live_sessions([_pane("dev-devin")]), now=NOW)
    later = NOW + datetime.timedelta(seconds=devin.LIVE_SESSIONS_TTL_S + 1)
    assert devin.acquire_slot(1, 60, {"id": "r1"}, now=later) is None


def test_r1_full_cap_reuses_existing_pane(tmp_path, monkeypatch, route_policy):
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    block = devin.usage(_policy(tmp_path, max_concurrent=1), now=NOW,
                        agents=[_pane("dev-devin")])
    snapshot = _route_usage()
    snapshot["devin"] = block
    decision = route(_task(iterative=True), snapshot, route_policy)
    assert decision["method"] == "persistent-devin"
    assert decision["agent"] == "dev-devin"


def test_r1_done_pane_is_reusable(route_policy):
    decision = route(_task(iterative=True), _route_usage([
        {"name": "dev-devin", "status": "done"}]), route_policy)
    assert decision["method"] == "persistent-devin"
    assert decision["agent"] == "dev-devin"


def test_r1_input_repair_keeps_persistent_pane(route_policy):
    previous = {"worker": "devin", "model": MODEL, "effort": "medium",
                "method": "persistent-devin", "agent": "dev-devin"}
    decision = route(_task(iterative=True, last_failure="missing-context", prev=previous),
                     _route_usage(IDLE_DEVIN), route_policy)
    assert decision["method"] == "persistent-devin"
    assert decision["agent"] == "dev-devin"


@pytest.mark.parametrize("document", [{}, {"result": {}}, {"result": {"agents": "unknown"}}])
def test_admission_refuses_malformed_agent_list(tmp_path, monkeypatch, document):
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(hx_usage, "live_members", REAL_LIVE_MEMBERS)
    payload = json.dumps(document).encode("utf-8")
    monkeypatch.setattr(host.subprocess, "check_output", lambda *args, **kwargs: payload)

    assert devin.acquire_slot(1, 60, {"id": "unknown"}, now=NOW) is None
    assert devin.usage(_policy(tmp_path), now=NOW)["status"] == "busy"


def test_unresolved_provider_kind_refuses_devin_admission(tmp_path, monkeypatch):
    """An unknown provider kind could be Devin, so the live count is unknown."""
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    unresolved = host.member({"name": "dev-devin", "pane_id": "p", "agent_status": "idle"})
    assert unresolved["kind"] == "unknown"
    monkeypatch.setattr(hx_usage, "live_members", lambda: [unresolved])

    assert devin._read_live_sessions() is None
    status = devin.usage(_policy(tmp_path), now=NOW, agents=[unresolved])
    assert status["status"] == "busy"
    assert status["live_sessions"] is None

    def forbidden():
        raise RuntimeError("passed admission guard")

    monkeypatch.setattr(devin, "slots_dir", forbidden)
    assert devin.acquire_slot(1, 60, {"id": "probe"}, now=NOW) is None


def test_failed_snapshot_reads_herdr_once_and_refuses_admission(tmp_path, monkeypatch):
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    calls = []

    def unavailable():
        calls.append(1)
        raise OSError("Herdr unavailable")

    monkeypatch.setattr(hx_usage, "live_members", unavailable)
    for provider in ("opencode_usage", "codex_usage", "claude_usage"):
        monkeypatch.setattr(hx_usage, provider, lambda *a, **kw: {})
    snapshot = hx_usage.usage_snapshot(_policy(tmp_path), now=NOW)
    assert calls == [1]
    assert snapshot["devin"]["status"] == "busy"
    assert snapshot["devin"]["reuse_available"] is False
    assert snapshot["agents"]["tag"] == "unknown"


@pytest.mark.parametrize("guard", ["disabled", "expired", "quota", "model"])
def test_pane_reuse_obeys_pool_guards(tmp_path, monkeypatch, route_policy, guard):
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    policy = _policy(tmp_path, max_concurrent=1)
    if guard == "disabled":
        policy["devin"]["enabled"] = False
    elif guard == "expired":
        policy["devin"]["promo"] = {"ends_at": "2020-01-01T00:00:00Z"}
    elif guard == "model":
        policy["devin"]["allowed_models"] = ["swe-2-medium"]
    else:
        devin.record_error({"kind": "quota", "reason": "429"}, MODEL, now=NOW)
    snapshot = _route_usage()
    snapshot["devin"] = devin.usage(policy, now=NOW, agents=[_pane("dev-devin")])
    assert snapshot["devin"]["reuse_available"] is False
    assert route(_task(iterative=True), snapshot, route_policy)["worker"] == "opencode"


def test_iterative_task_routes_persistent_devin(route_policy: dict) -> None:
    result = route(_task(iterative=True), _route_usage(IDLE_DEVIN), route_policy)
    assert result["worker"] == "devin"
    assert result["method"] == "persistent-devin"
    assert result["agent"] == "dev-devin"
    assert result["model"] == "swe-2-high"
    assert result["effort"] == "medium"
    assert "pool:devin" in result["stages"]


def test_non_iterative_task_keeps_ocrun_devin(route_policy: dict) -> None:
    result = route(_task(), _route_usage(IDLE_DEVIN), route_policy)
    assert result["worker"] == "devin"
    assert result["method"] == "ocrun-devin"
    assert "agent" not in result


def test_persistent_block_describing_the_pane_routes_persistent(route_policy: dict) -> None:
    persistent = {"name": "dev-devin", "status": "idle"}
    result = route(_task(persistent=persistent), _route_usage(IDLE_DEVIN), route_policy)
    assert result["method"] == "persistent-devin"
    assert result["agent"] == "dev-devin"


def test_parallel_never_routes_persistent_devin(route_policy: dict) -> None:
    result = route(
        _task(iterative=True, parallel=True), _route_usage(IDLE_DEVIN), route_policy
    )
    assert result["method"] == "ocrun-devin"


def test_busy_pane_keeps_ocrun_devin(route_policy: dict) -> None:
    sessions = [{"name": "dev-devin", "status": "working", "cwd": None}]
    result = route(_task(iterative=True), _route_usage(sessions), route_policy)
    assert result["method"] == "ocrun-devin"


def test_unnamed_user_pane_does_not_route_persistent(route_policy: dict) -> None:
    """The user's own Devin pane counts for the cap but is not the team pane."""
    sessions = [{"name": None, "status": "idle", "cwd": None},
                {"name": "devin", "status": "idle", "cwd": None}]
    result = route(_task(iterative=True), _route_usage(sessions), route_policy)
    assert result["method"] == "ocrun-devin"


def test_no_live_sessions_keeps_ocrun_devin(route_policy: dict) -> None:
    result = route(_task(iterative=True), _route_usage([]), route_policy)
    assert result["method"] == "ocrun-devin"


def test_live_sessions_filling_the_cap_routes_opencode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route_policy: dict
) -> None:
    """End to end: two live panes reach max_concurrent -> status busy -> route
    falls through to the OpenCode ladder exactly like a full slot set."""
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    devin_block = devin.usage(
        _policy(tmp_path), now=NOW, agents=[_pane("dev-devin"), _pane(None)]
    )
    assert devin_block["status"] == "busy"
    usage = _route_usage()
    usage["devin"] = devin_block
    result = route(_task(iterative=False), usage, route_policy)
    assert result["worker"] == "opencode"
    assert result["method"] == "ocrun"
    assert "devin:busy" in result["stages"]


# ---------- denied-prefix prompt section ----------


def _config(tmp_path: Path, deny: list[str]) -> Path:
    config = tmp_path / "deny-rules.config.json"
    config.write_text(
        "// comment lines and trailing comments are legal here\n"
        + json.dumps({"permissions": {"allow": ["exec"], "deny": deny}}, indent=2)
        + "\n// trailing comment\n",
        encoding="utf-8",
    )
    return config


def test_prepare_run_prepends_generated_denied_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """prompt.md starts with the deny list generated from the worker config."""
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    config = _config(tmp_path, ["Exec(git push)", "Exec(herdr)", "Exec(hx)", "Write(.env*)"])
    policy = _policy(tmp_path, worker_config=str(config))
    run_dir = tmp_path / "run"
    devin.prepare_run(run_dir, "PREAMBLE\ndo the brief", policy)
    prompt = (run_dir / "prompt.md").read_text(encoding="utf-8")
    assert prompt.startswith("Denied command prefixes")
    assert "`git push`" in prompt and "`herdr`" in prompt and "`hx`" in prompt
    assert "Write(.env*)" in prompt
    assert "PREAMBLE\ndo the brief" in prompt
    assert prompt.index("Denied command prefixes") < prompt.index("PREAMBLE")


def test_denied_section_comes_from_config_not_a_handcopy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The list tracks the config: a custom rule appears and a removed rule is gone."""
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    config = _config(tmp_path, ["Exec(custom-tool --x)"])
    policy = _policy(tmp_path, worker_config=str(config))
    run_dir = tmp_path / "run"
    devin.prepare_run(run_dir, "do it", policy)
    prompt = (run_dir / "prompt.md").read_text(encoding="utf-8")
    assert "`custom-tool --x`" in prompt
    assert "git push" not in prompt


def test_denied_section_real_worker_config_lists_agent_clis(tmp_path: Path) -> None:
    """Against the real team/devin-worker.config.json the section names every
    denied agent CLI and herdr/hx, the commands that killed the first attempt."""
    policy = {"devin": {"worker_config": "team/devin-worker.config.json"}}
    section = devin.denied_section(policy)
    assert section
    for prefix in ("`git push`", "`git commit`", "`devin`", "`devin.exe`",
                   "`herdr`", "`hx`", "`opencode`", "`codex`", "`claude`",
                   "`python -m hx`", "`curl`"):
        assert prefix in section
    assert "Read(~/.ssh/**)" in section


def test_prepare_run_without_deny_rules_leaves_prompt_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    config = _config(tmp_path, [])
    policy = _policy(tmp_path, worker_config=str(config))
    run_dir = tmp_path / "run"
    devin.prepare_run(run_dir, "just the brief", policy)
    assert (run_dir / "prompt.md").read_text(encoding="utf-8") == "just the brief"
