"""Tests for the C4 admission preflight (hx.capability, ocrun hook, route retry).

The documented fault: 9/21 routed outcomes failed, every one a tool-permission
denial, and the same cause was retried (q196-setup three times:
``permission denied: webfetch {"url": "https://api.github.com/..."}``, with the
brief still asking for ``git clone``/``gh api`` the worker could not run).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hx import capability
from hx import ocrun
from hx import route as route_pkg
from hx.route import route

# The q196-setup brief, reduced to the lines the preflight reads. Step 2's
# clone instruction, the follow-up `gh api` fallback, `herdr-app check`, the
# `hx usage`/`hx context` data collection and the `hx say` report line were all
# denied for the devin worker that got the task.
Q196_BRIEF = """\
# Brief: Herdr app setup, the Triage layout plus our own usage and context bar (Q-196)

## Do

1. Read the app's mod contract first. Also run `herdr-app --skill` and `hc --skill`.
2. Vendor Triage for review: `git clone --depth 1 https://github.com/sgomez/herdr-triage herdr-app/vendor/herdr-triage`.
3. Show the subscription pools from `hx usage --json`, each agent's context % from `hx context decide <agent>` (agents from `hc` or `herdr agent list`).
4. Run `herdr-app check herdr-app/mods/hx-usage` and fix every error.

## Report

Via `hx say claude`, under 10 lines: the files, the check result.

## Update from the lead (second run)

Fetch them with `gh api` or a shallow clone into herdr-app/vendor/herdr-app-defaults.
"""

CHECKOUT_BRIEF = """\
# Brief: refresh the worktree

Check out the feature branch the lead just made:

    git checkout feat/c4-admission

Then run the focused tests.
"""


def _feature_task(**overrides) -> dict:
    task = {
        "kind": "feature",
        "risk": "low",
        "size": "small",
        "attempt": 1,
        "last_failure": "tool",
        "parallel": False,
        "prev": None,
        "persistent": None,
        "iterative": None,
    }
    task.update(overrides)
    return task


def _usage() -> dict:
    """All pools available, devin ready, so the router can pick devin."""
    models = [
        "deepseek-v4.1-flash",
        "mimo-v2.6-flash",
        "minimax-m3",
        "qwen3.7-plus",
        "kimi-k2.7-code",
    ]
    return {
        "opencode": {
            "tag": "estimated",
            "available": True,
            "sample_age_seconds": 0,
            "pool": {"windows": {
                "5h": {"utilisation": 0.1},
                "7d": {"utilisation": 0.1},
                "30d": {"utilisation": 0.1},
            }},
            "models": {
                m: {
                    "windows": {"5h": {"spend": 0.1}, "7d": {"spend": 0.1}, "30d": {"spend": 0.1}},
                    "last_error": None,
                }
                for m in models
            },
        },
        "codex": {
            "tag": "measured",
            "available": True,
            "sample_age_seconds": 0,
            "rate_limits": {
                "primary": {"used_percent": 20.0},
                "secondary": {"used_percent": 10.0},
            },
        },
        "claude": {
            "tag": "measured",
            "available": True,
            "sample_age_seconds": 0,
            "rate_limits": {
                "five_hour": {"used_percentage": 20.0},
                "seven_day": {"used_percentage": 10.0},
            },
        },
        "devin": {"status": "available", "promo": {"ends_at": "2099-01-01T00:00:00Z"}},
    }


def _route_policy() -> dict:
    return {
        "ladders": {
            "economy": ["deepseek-v4.1-flash", "mimo-v2.6-flash"],
            "default": ["minimax-m3", "qwen3.7-plus"],
            "strong": ["kimi-k2.7-code"],
        },
        "thresholds": {"pool_soft_limit": 0.85, "stale_seconds": 3600},
        "max_attempts": 3,
        "codex": {"economy_model": "gpt-6-luna", "strong_model": "gpt-6.1-sol"},
        "effort_by_kind": {"feature": "medium", "review": "high"},
        "default_effort": "medium",
        "routing_order": ["devin", "opencode"],
        "devin": {
            "enabled": True,
            "kinds": ["routine", "investigate", "feature", "debug", "refactor", "test"],
            "models": {"medium": "swe-2-high", "high": "swe-2-max", "max": "swe-2-max"},
            "allowed_models": ["swe-2-medium", "swe-2-high", "swe-2-max"],
            "promo": {"ends_at": "2099-01-01T00:00:00Z"},
        },
    }


# ---------- profile table ----------


def test_devin_profile_denies_documented_commands() -> None:
    """The live/shipped devin config denies the commands q196 died on."""
    profile = capability.worker_profile("devin")
    assert profile is not None
    assert profile["sources"], "expected at least the shipped worker template"
    assert capability.denied_exec(profile, "git clone --depth 1 https://x") == "Exec(git clone)"
    assert capability.denied_exec(profile, "gh api repos/x/tags") == "Exec(gh)"
    assert capability.denied_exec(profile, "git checkout feat/x") == "Exec(git checkout)"
    assert capability.denied_exec(profile, "hx ocrun --worker devin") == "Exec(hx ocrun)"
    assert capability.denied_exec(profile, "git status --short") is None
    # The pane's narrower hx denies leave report/checkpoint commands allowed.
    assert capability.denied_exec(profile, "hx say claude done") is None
    assert capability.denied_exec(profile, "hx checkpoint write t") is None
    assert capability.denied_exec(profile, "hx usage --json") is None
    # Literal-word matching, per docs/audit/devin.md: devin.exe needed its own
    # rule, `herdr-app` is not the `herdr` word, and `claude` as an argument
    # of `hx say` is not a command.
    assert capability.denied_exec(profile, "devin.exe --version") == "Exec(devin.exe)"
    assert capability.denied_exec(profile, "herdr-app check mods/x") is None
    assert capability.denied_exec(profile, "claude --help") == "Exec(claude)"


def test_devin_deny_match_is_word_subsequence() -> None:
    """git global flags don't hide the subcommand; compound lines still match."""
    profile = capability.worker_profile("devin")
    assert capability.denied_exec(profile, "git -C . push origin main") == "Exec(git push)"
    assert capability.denied_exec(profile, "echo hi && git push") == "Exec(git push)"
    # `hx` mid-command is an argument, so python -m hx needs its own rule.
    assert capability.denied_exec(profile, "python -m hx usage") == "Exec(python -m hx)"


def test_opencode_profile_denies_herdr_mutation_only() -> None:
    profile = capability.worker_profile("opencode")
    assert profile is not None
    assert capability.denied_exec(profile, "herdr agent prompt dev-x hi") is not None
    assert capability.denied_exec(profile, "herdr agent list") is None
    assert capability.denied_exec(profile, "git clone https://x") is None
    assert capability.denied_exec(profile, "hx usage --json") is None


def test_codex_profile_bounds_writes() -> None:
    profile = capability.worker_profile("codex")
    assert profile is not None
    assert profile["writes_outside_cwd"] is False


# ---------- the preflight scan ----------


def test_q196_brief_findings_for_devin(tmp_path: Path) -> None:
    """Only the executable instructions flag: the clone and the `gh api` fallback."""
    findings = capability.prompt_findings("devin", Q196_BRIEF, tmp_path)
    rules = {f["rule"] for f in findings}
    assert rules == {"Exec(git clone)", "Exec(gh)"}


def test_q196_brief_is_clean_for_opencode(tmp_path: Path) -> None:
    """OpenCode may run everything the q196 brief names."""
    assert capability.prompt_findings("opencode", Q196_BRIEF, tmp_path) == []


def test_checkout_brief_finding_for_devin(tmp_path: Path) -> None:
    findings = capability.prompt_findings("devin", CHECKOUT_BRIEF, tmp_path)
    assert any(f["rule"] == "Exec(git checkout)" for f in findings)


def test_checkout_brief_is_clean_for_opencode(tmp_path: Path) -> None:
    assert capability.prompt_findings("opencode", CHECKOUT_BRIEF, tmp_path) == []


def test_clean_brief_has_no_findings(tmp_path: Path) -> None:
    brief = "Implement the feature in hx/x.py, then run `python -m pytest tests -q`."
    assert capability.prompt_findings("devin", brief, tmp_path) == []
    assert capability.prompt_findings("opencode", brief, tmp_path) == []


def test_codex_refuses_write_outside_workspace(tmp_path: Path) -> None:
    brief = "write the mod into C:\\Users\\caspe\\.config\\herdr-app\\mods\\x"
    findings = capability.prompt_findings("codex", brief, tmp_path)
    assert any(f["kind"] == "outside-cwd" for f in findings)
    inside = f"write the file to {tmp_path}\\out\\x.txt"
    assert not any(
        f["kind"] == "outside-cwd"
        for f in capability.prompt_findings("codex", inside, tmp_path)
    )


def test_devin_denied_path_rules(tmp_path: Path) -> None:
    findings = capability.prompt_findings(
        "devin", "edit the file ~/.ssh/config for the host alias", tmp_path
    )
    assert any("ssh" in f["rule"].lower() for f in findings)


def test_all_todays_briefs_give_zero_findings(tmp_path: Path) -> None:
    """The real briefs the lead replayed that carry no denied instruction."""
    fixtures = Path(__file__).parent / "fixtures" / "briefs"
    for name in (
        "greats-gather-devin.md",
        "greats-diff.md",
        "c4-admission.md",
        "q205-opencode-measured.md",
    ):
        text = (fixtures / name).read_text(encoding="utf-8")
        assert capability.prompt_findings("devin", text, tmp_path) == [], name


def test_herdr_app_setup_brief_flags_its_real_denied_steps(tmp_path: Path) -> None:
    """C4: an unrelated 'never' must not hide the brief's real `gh api` step."""
    text = (Path(__file__).parent / "fixtures" / "briefs" / "herdr-app-setup.md").read_text(
        encoding="utf-8"
    )
    rules = {f["rule"] for f in capability.prompt_findings("devin", text, tmp_path)}
    assert "Exec(gh)" in rules


@pytest.mark.parametrize(
    "line",
    [
        "Finish by running git checkout -b feat/y and starting the work.",
        "Then execute git checkout -b feat/y and move on.",
        "Then invoke git checkout -b feat/y to switch over.",
        "Next start git checkout -b feat/y from the worktree.",
        "Then try git checkout -b feat/y if the branch exists.",
    ],
)
def test_run_phrase_verbs_catch_denied_command(line: str, tmp_path: Path) -> None:
    """C12 it1: bare-prose run/execute/invoke/start/try phrasing still flags."""
    rules = {f["rule"] for f in capability.prompt_findings("devin", line, tmp_path)}
    assert "Exec(git checkout)" in rules


def test_documented_denial_cases_still_caught(tmp_path: Path) -> None:
    """The q196 clone line and a checkout instruction are real instructions."""
    q196 = "Vendor Triage for review: `git clone --depth 1 https://github.com/sgomez/herdr-triage herdr-app/vendor/herdr-triage`."
    rules = {f["rule"] for f in capability.prompt_findings("devin", q196, tmp_path)}
    assert "Exec(git clone)" in rules
    checkout = "Start with `git checkout -b feat/x main`, then continue."
    rules = {f["rule"] for f in capability.prompt_findings("devin", checkout, tmp_path)}
    assert "Exec(git checkout)" in rules


def test_disclosure_lines_are_not_findings(tmp_path: Path) -> None:
    """A forwarded pane deny footer must not refuse itself."""
    brief = (
        "Do the task. Denied for you (a denied call cancels your turn): `git push`, `gh`.\n"
        "Also denied: `Write(.env*)`.\n"
        "Denied command prefixes (a denied call ends this run): `git clone`.\n"
        "Other denied rules: `Write(**/.git/**)`."
    )
    assert capability.prompt_findings("devin", brief, tmp_path) == []


# ---------- C4: a prohibition suppresses only its own instruction ----------


def test_unrelated_no_phrase_does_not_suppress_same_line(tmp_path: Path) -> None:
    """'No tests' on a line must not hide a real `git push` instruction."""
    brief = "No tests are needed for this change. Run `git push` when done."
    rules = {f["rule"] for f in capability.prompt_findings("devin", brief, tmp_path)}
    assert "Exec(git push)" in rules


def test_prohibition_does_not_suppress_other_lines(tmp_path: Path) -> None:
    """'Do not run X' on line 1 must not hide a real X instruction on line 2."""
    brief = "Do not run `git push` yourself.\nOnce reviewed, run `git push` to ship it."
    findings = capability.prompt_findings("devin", brief, tmp_path)
    assert all(f["line"] == 2 for f in findings)
    assert {f["rule"] for f in findings} == {"Exec(git push)"}


def test_prohibition_still_suppresses_its_own_instruction(tmp_path: Path) -> None:
    brief = "Do not run `git push`; the lead ships."
    assert capability.prompt_findings("devin", brief, tmp_path) == []


def test_implement_write_scope_is_cwd_not_anywhere(tmp_path: Path) -> None:
    """C5/C4: write_roots 'cwd' means inside cwd, not anywhere."""
    import json as _json

    policy = _json.loads(Path("policy.json").read_text(encoding="utf-8"))
    outside = capability.prompt_findings(
        "devin", "write the output to C:\\Users\\caspe\\outside\\x.md", tmp_path, policy, "implement"
    )
    assert any(f["kind"] == "outside-cwd" for f in outside)
    inside = capability.prompt_findings(
        "devin", f"write the output to {tmp_path}\\x.md", tmp_path, policy, "implement"
    )
    assert inside == []


def test_research_refuses_even_inside_cwd_writes(tmp_path: Path) -> None:
    import json as _json

    policy = _json.loads(Path("policy.json").read_text(encoding="utf-8"))
    findings = capability.prompt_findings(
        "devin", f"write the report to {tmp_path}\\notes.md", tmp_path, policy, "research"
    )
    assert any(f["kind"] == "outside-cwd" for f in findings)


# ---------- ocrun refusal ----------


def test_ocrun_devin_refuses_q196_before_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replay of the documented case: the brief dies at admission, not mid-run."""
    launched: list[str] = []

    def _forbid(*args, **kwargs):  # noqa: ARG001
        launched.append("run_devin")
        raise AssertionError("worker launched despite refused preflight")

    monkeypatch.setattr(ocrun, "run_devin", _forbid)
    routing_log = tmp_path / "routing.jsonl"
    result = ocrun.run(
        Q196_BRIEF,
        "swe-2-high",
        cwd=tmp_path,
        worker="devin",
        title="q196-setup",
        routing_log=routing_log,
    )
    assert launched == []
    assert result["ok"] is False
    assert result["failure"]["kind"] == "admission"
    assert "git clone" in result["failure"]["reason"]
    assert "--allow-preflight-risk" in result["failure"]["reason"]
    entry = json.loads(routing_log.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["kind"] == "outcome"
    assert entry["ok"] is False
    assert entry["failure"]["kind"] == "admission"


def test_ocrun_refuses_checkout_brief_before_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ocrun,
        "run_devin",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("launched")),
    )
    result = ocrun.run(
        CHECKOUT_BRIEF, "swe-2-high", cwd=tmp_path, worker="devin",
        routing_log=tmp_path / "routing.jsonl",
    )
    assert result["ok"] is False
    assert result["failure"]["kind"] == "admission"
    assert "git checkout" in result["failure"]["reason"]


def test_ocrun_main_returns_2_with_named_fixes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(
        ocrun,
        "run_devin",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("launched")),
    )
    brief = tmp_path / "brief.md"
    brief.write_text(CHECKOUT_BRIEF, encoding="utf-8")
    code = ocrun.main(
        [
            "--prompt-file",
            str(brief),
            "--worker",
            "devin",
            "--model",
            "swe-2-high",
            "--cwd",
            str(tmp_path),
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "git checkout" in err
    assert "Fix:" in err


def test_allow_preflight_risk_launches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The explicit override lets the launch proceed to the worker's own guards."""
    prompt = "run `herdr agent prompt dev-x hi` for me"
    seen: list[str] = []

    def fake_execute(cmd, cwd, timeout, env=None):  # noqa: ARG001
        seen.append("execute")
        return 0, "", "", False, True

    monkeypatch.setattr(ocrun, "execute", fake_execute)
    result = ocrun.run(
        prompt,
        "qwen3.8-flash",
        cwd=tmp_path,
        worker="opencode",
        allow_preflight_risk=True,
        routing_log=tmp_path / "routing.jsonl",
    )
    assert seen == ["execute"]
    assert (result.get("failure") or {}).get("kind") != "admission"


# ---------- ocrun: the named profile is the boundary ----------


def test_unknown_profile_is_a_refusal_not_a_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--allow-preflight-risk skips brief checks, never the profile boundary."""
    monkeypatch.setattr(
        ocrun,
        "execute",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("launched")),
    )
    result = ocrun.run(
        "x",
        "qwen3.8-flash",
        cwd=tmp_path,
        worker="opencode",
        profile="nope",
        allow_preflight_risk=True,
        routing_log=tmp_path / "r.jsonl",
    )
    assert result["failure"]["kind"] == "admission"
    assert "unknown profile" in result["failure"]["reason"]


def test_lead_only_profile_is_a_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ocrun,
        "execute",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("launched")),
    )
    result = ocrun.run(
        "x",
        "qwen3.8-flash",
        cwd=tmp_path,
        worker="opencode",
        profile="release",
        allow_preflight_risk=True,
        routing_log=tmp_path / "r.jsonl",
    )
    assert result["failure"]["kind"] == "admission"
    assert "lead-only" in result["failure"]["reason"]


def test_main_passes_the_real_policy_to_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_run(*args: object, **kw: object) -> dict:
        captured.update(kw)
        return {"ok": True, "failure": None}

    monkeypatch.setattr(ocrun, "run", fake_run)
    ocrun.main(["--model", "glm-5.3-flash", "p", "--profile", "research", "--headless"])
    assert captured["profile"] == "research"
    assert captured["policy"] is not None and "profiles" in captured["policy"]


# ---------- route: retries by cause ----------


def _denied_clone_outcome(worker: str = "devin") -> dict:
    return {
        "kind": "outcome",
        "task_id": "q196-setup",
        "ok": False,
        "worker": worker,
        "failure": {
            "kind": "tool",
            "reason": 'permission denied: exec_command {"command": "git clone --depth 1 https://github.com/sgomez/herdr-triage"}',
        },
    }


def test_route_refuses_same_denied_cause_on_devin() -> None:
    """q196 attempt shape: last_failure=tool, devin picked again, same denial."""
    task = _feature_task(last_outcome=_denied_clone_outcome())
    result = route(task, _usage(), _route_policy())
    assert result["method"] == "refuse"
    assert result["worker"] is None
    assert "git clone" in result["reason"]
    assert "admission:denied-cause" in result["stages"]


def test_route_refuses_devin_tool_denial_retry() -> None:
    """The webfetch shape from the real q196 outcome: a denied tool, not exec."""
    outcome = _denied_clone_outcome()
    outcome["failure"] = {
        "kind": "tool",
        "reason": 'permission denied: webfetch {"url": "https://api.github.com/repos/x/tags"}',
    }
    task = _feature_task(last_outcome=outcome)
    result = route(task, _usage(), _route_policy())
    assert result["method"] == "refuse"
    assert result["worker"] is None
    assert "webfetch" in result["reason"]


def test_route_allows_retry_on_worker_that_may() -> None:
    """A webfetch denial on devin does not block an opencode retry."""
    outcome = _denied_clone_outcome()
    outcome["failure"] = {
        "kind": "tool",
        "reason": 'permission denied: webfetch {"url": "https://api.github.com/x"}',
    }
    usage = _usage()
    usage["devin"]["status"] = "busy"  # force the opencode pick
    task = _feature_task(last_outcome=outcome)
    result = route(task, usage, _route_policy())
    assert result["worker"] == "opencode"
    assert result["method"] == "ocrun"


def test_route_without_previous_denial_routes_normally() -> None:
    """A tool failure without a denied cause keeps the fix-the-input retry."""
    task = _feature_task(last_failure="tool", last_outcome={
        "kind": "outcome",
        "task_id": "t",
        "ok": False,
        "worker": "devin",
        "failure": {"kind": "tool", "reason": "timeout"},
    })
    result = route(task, _usage(), _route_policy())
    assert result["method"] != "refuse"


def test_decide_reads_last_outcome_for_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """decide() pulls the denied cause out of routing.jsonl by task_id."""
    monkeypatch.setattr(route_pkg, "_repo_root", lambda: tmp_path)
    log = tmp_path / ".hx" / "routing.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps(_denied_clone_outcome()) + "\n", encoding="utf-8")
    monkeypatch.setattr(route_pkg.hx_usage, "load_policy", lambda: _route_policy())
    monkeypatch.setattr(
        route_pkg.hx_usage, "usage_snapshot", lambda policy=None, now=None: _usage()
    )
    result = route_pkg.decide(_feature_task(task_id="q196-setup", last_failure="tool"))
    assert result["method"] == "refuse"
    assert "git clone" in result["reason"]


def test_retry_cause_parsing() -> None:
    profile = capability.worker_profile("devin")
    reason = 'permission denied: exec_command {"command": "gh api repos/x"}'
    assert capability.retry_denied_cause(reason, profile) == "gh api repos/x"
    assert capability.retry_denied_cause("timeout", profile) is None
    assert capability.retry_denied_cause(None, profile) is None
