"""Replay tests for the Q-347 bounded review-round transition."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hx import ledger as ledger_mod
from hx import review
from hx import __main__ as cli


POLICY = {"team_prefix": "dev-"}


def _ledger(tmp_path: Path) -> ledger_mod.Ledger:
    return ledger_mod.Ledger(
        ledger_path=tmp_path / "ledger.json",
        leases_path=tmp_path / "leases.json",
        lock_path=tmp_path / "ledger.lock",
        repo_root=tmp_path,
    )


@pytest.fixture
def fake_artifact(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    state: dict[str, str | None] = {"head": "h1", "digest": None}

    def snapshot(repo: Path) -> dict[str, str | None]:
        return {
            "repo": str(repo),
            "branch": "main",
            "head": state["head"],
            "digest": state["digest"],
            "mode": "worktree" if state["digest"] else "commit",
        }

    monkeypatch.setattr(review, "artifact", snapshot)
    return state


@pytest.fixture
def led(tmp_path: Path) -> ledger_mod.Ledger:
    ledger = _ledger(tmp_path)
    ledger.add_task(
        "q-347",
        "review rounds",
        "dev-opencode",
        [],
        base_commit="base",
        model="deepseek/v4",
        review_required=True,
    )
    return ledger


def _request(ledger: ledger_mod.Ledger, repo: Path) -> dict:
    return review.open_review("q-347", repo, led=ledger, policy=POLICY, model="deepseek/v4")


def _verdict(ledger: ledger_mod.Ledger, value: str, findings: list[str] | None = None) -> dict:
    return review.verdict(
        "q-347",
        "dev-codex",
        value,
        findings or [],
        led=ledger,
        policy=POLICY,
        model="gpt-5-codex",
    )


def _kinds(ledger: ledger_mod.Ledger) -> list[str]:
    return [round_["kind"] for round_ in review.rounds("q-347", led=ledger)["rounds"]]


def test_initial_review_merge_allows_close(tmp_path: Path, fake_artifact, led) -> None:
    _request(led, tmp_path / "code")
    _verdict(led, "merge")
    report = review.status("q-347", led=led)
    assert report["phase"] == "merged"
    assert report["corrections"] == 0
    assert report["close_ok"] is True
    assert _kinds(led) == ["request", "verdict"]
    assert led.set_status("q-347", "done")["status"] == "done"


def test_review_required_without_review_cannot_close(tmp_path: Path, fake_artifact, led) -> None:
    with pytest.raises(ValueError, match="no review"):
        led.set_status("q-347", "done")
    assert led.get_task("q-347")["status"] != "done"


def test_housekeeping_can_close_without_review(led) -> None:
    led.add_task("housekeeping", "housekeeping", "dev-opencode", ["house.py"])
    assert led.set_status("housekeeping", "done")["status"] == "done"


def test_complete_task_guard_blocks_before_checkpoint(tmp_path: Path, fake_artifact, led) -> None:
    calls: list[int] = []

    def writer(_task: dict, rev: int) -> Path:
        calls.append(rev)
        return tmp_path / "checkpoint.md"

    with pytest.raises(ValueError, match="no review"):
        led.complete_task("q-347", "done", writer)
    assert calls == []
    assert led.get_task("q-347")["status"] != "done"


def test_two_corrections_survive_restart(tmp_path: Path, fake_artifact, led) -> None:
    repo = tmp_path / "code"
    _request(led, repo)
    _verdict(led, "fix")
    first = review.correct("q-347", led=led, brief="fix1.md", by="dev-lead")
    assert first["status"] == "dispatched"
    assert first["correction"] == 1

    restarted = _ledger(tmp_path)
    _request(restarted, repo)
    _verdict(restarted, "fix")
    second = review.correct("q-347", led=restarted, brief="fix2.md", by="dev-lead")
    assert second["status"] == "dispatched"
    assert second["correction"] == 2

    report = review.status("q-347", led=restarted)
    assert report["corrections"] == 2
    assert report["phase"] == "corrected"
    assert _kinds(restarted) == [
        "request",
        "verdict",
        "correction",
        "recheck",
        "verdict",
        "correction",
    ]


def test_third_correction_is_refused_before_a_round(tmp_path: Path, fake_artifact, led) -> None:
    repo = tmp_path / "code"
    _request(led, repo)
    _verdict(led, "fix")
    review.correct("q-347", led=led, brief="fix1.md", by="dev-lead")
    _request(led, repo)
    _verdict(led, "fix")
    review.correct("q-347", led=led, brief="fix2.md", by="dev-lead")
    _request(led, repo)

    before = review.rounds("q-347", led=led)
    with pytest.raises(review.RedesignRequired) as exc:
        _verdict(led, "fix")
    assert "redesign" in str(exc.value).lower()

    after = review.rounds("q-347", led=led)
    assert after["corrections"] == 2
    assert len(after["rounds"]) == len(before["rounds"])
    assert before["rounds"][-1]["kind"] == "recheck"


def test_old_moved_cycles_replay_as_spent_budget(tmp_path: Path, fake_artifact, led) -> None:
    repo = tmp_path / "code"
    snap = review.artifact(repo)
    reviewer = {"agent": "dev-codex", "family": "openai", "model": "gpt-5-codex"}
    old = {
        "opened_at": "2026-11-13T00:00:00+00:00",
        "artifact": snap,
        "author": {"agent": "dev-opencode", "family": "deepseek", "model": "deepseek/v4"},
        "contract": None,
        "verdict": {
            "at": "2026-11-13T00:03:00+00:00",
            "reviewer": reviewer,
            "verdict": "fix",
            "artifact": snap,
        },
        "findings": [],
        "rounds": [
            {"kind": "request", "at": "2026-11-13T00:00:00+00:00",
             "artifact": snap, "author": "dev-opencode"},
            {"kind": "verdict", "at": "2026-11-13T00:01:00+00:00",
             "artifact": snap, "reviewer": "dev-codex", "verdict": "fix"},
            {"kind": "recheck", "at": "2026-11-13T00:01:30+00:00",
             "artifact": snap, "correction": 0},
            {"kind": "verdict", "at": "2026-11-13T00:02:00+00:00",
             "artifact": snap, "reviewer": "dev-codex", "verdict": "fix"},
            {"kind": "recheck", "at": "2026-11-13T00:02:30+00:00",
             "artifact": snap, "correction": 0},
            {"kind": "verdict", "at": "2026-11-13T00:03:00+00:00",
             "artifact": snap, "reviewer": "dev-codex", "verdict": "fix"},
        ],
    }

    def plant(task: dict) -> None:
        task["review"] = old

    led.mutate_task("q-347", plant)
    assert review.rounds("q-347", led=led)["corrections"] == 2
    with pytest.raises(review.RedesignRequired):
        review.correct("q-347", led=led)
    fake_artifact["head"] = "h2"
    _request(led, repo)
    assert review.status("q-347", led=led)["phase"] == "open"
    with pytest.raises(review.RedesignRequired):
        _verdict(led, "fix")
    _verdict(led, "merge")
    assert led.set_status("q-347", "done")["status"] == "done"


def test_duplicate_correction_dispatch_does_not_increment(tmp_path: Path, fake_artifact, led) -> None:
    repo = tmp_path / "code"
    _request(led, repo)
    _verdict(led, "fix")
    first = review.correct("q-347", led=led, brief="fix1.md", by="dev-lead")
    second = review.correct("q-347", led=led, brief="fix1.md", by="dev-lead")
    assert first["correction"] == 1
    assert second["status"] == "duplicate"
    assert second["correction"] == 1
    assert review.rounds("q-347", led=led)["corrections"] == 1
    assert _kinds(led).count("correction") == 1


def test_stale_merge_cannot_close(tmp_path: Path, fake_artifact, led) -> None:
    _request(led, tmp_path / "code")
    _verdict(led, "merge")
    fake_artifact["head"] = "h2"

    report = review.status("q-347", led=led)
    assert report["state"] == "stale"
    assert report["close_ok"] is False
    with pytest.raises(ValueError, match="stale|artifact"):
        led.set_status("q-347", "done")
    assert led.get_task("q-347")["status"] != "done"


def test_stale_fix_cannot_dispatch(tmp_path: Path, fake_artifact, led) -> None:
    _request(led, tmp_path / "code")
    _verdict(led, "fix")
    fake_artifact["head"] = "h2"

    with pytest.raises(ValueError, match="stale|artifact"):
        review.correct("q-347", led=led, brief="fix1.md", by="dev-lead")
    assert review.rounds("q-347", led=led)["corrections"] == 0


def test_park_exception_survives_restart(tmp_path: Path, fake_artifact, led) -> None:
    repo = tmp_path / "code"
    _request(led, repo)
    _verdict(led, "fix")
    led.add_task(
        "q-347-next",
        "new design",
        "dev-opencode",
        [],
        depends=["q-347"],
        base_commit="base",
    )

    parked = review.park(
        "q-347",
        lead="dev-lead",
        reason="safer than main",
        new_task="q-347-next",
        led=led,
    )
    assert parked["park"]["new_task"] == "q-347-next"
    assert review.status("q-347", led=led)["phase"] == "parked"
    with pytest.raises(ValueError, match="exception"):
        led.set_status("q-347", "done")

    review.exception(
        "q-347",
        lead="dev-lead",
        reason="remaining gaps pre-exist on main",
        follow_up="q-347-next",
        led=led,
    )

    restarted = _ledger(tmp_path)
    guard = review.close_guard(restarted.get_task("q-347"))
    assert guard["ok"] is True
    assert restarted.set_status("q-347", "done")["status"] == "done"
    rounds = review.rounds("q-347", led=restarted)["rounds"]
    assert rounds[-1]["kind"] == "exception"
    assert rounds[-1]["follow_up"] == "q-347-next"


def test_parked_task_cannot_reopen_a_renamed_brief(tmp_path: Path, fake_artifact, led) -> None:
    repo = tmp_path / "code"
    _request(led, repo)
    _verdict(led, "fix")
    led.add_task(
        "q-347-next",
        "new design",
        "dev-opencode",
        [],
        depends=["q-347"],
        base_commit="base",
    )
    review.park(
        "q-347",
        lead="dev-lead",
        reason="safer than main",
        new_task="q-347-next",
        led=led,
    )

    with pytest.raises(ValueError, match="closed|linked"):
        _request(led, repo)
    assert review.rounds("q-347", led=led)["rounds"][-1]["kind"] == "park"

    review.open_review(
        "q-347-next",
        repo,
        led=led,
        policy=POLICY,
        model="deepseek/v4",
    )
    assert review.status("q-347-next", led=led)["phase"] == "open"


def test_cli_request_verdict_correct_park_except_and_close(
    tmp_path: Path, fake_artifact, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    repo = tmp_path / "proj"
    repo.mkdir()
    monkeypatch.setenv("HX_PROJECT_ROOT", str(repo))
    monkeypatch.setenv("HX_LEDGER_PATH", str(repo / ".hx" / "ledger.json"))
    monkeypatch.setenv("HX_LEASES_PATH", str(tmp_path / "leases.json"))
    monkeypatch.setenv("HX_LEASES_LOCK_PATH", str(tmp_path / "leases.lock"))
    monkeypatch.setenv("HX_LOCK_PATH", str(repo / ".hx" / "ledger.lock"))

    assert cli.main([
        "task", "add", "q-347", "--title", "review rounds", "--owner", "dev-opencode",
        "--files", "owned.py", "--model", "deepseek/v4", "--review-required",
    ]) == 0
    capsys.readouterr()
    assert cli.main([
        "task", "add", "q-347-next", "--title", "new design", "--owner", "dev-opencode",
        "--files", "next.py", "--depends", "q-347", "--model", "deepseek/v4",
    ]) == 0
    capsys.readouterr()

    assert cli.main(["review", "request", "q-347", "--repo", str(repo), "--model", "deepseek/v4"]) == 0
    capsys.readouterr()
    assert cli.main([
        "review", "verdict", "q-347", "--reviewer", "dev-codex", "--verdict", "fix",
        "--model", "gpt-5-codex",
    ]) == 0
    capsys.readouterr()
    assert cli.main(["review", "correct", "q-347", "--brief", "fix1.md"]) == 0
    capsys.readouterr()
    assert cli.main(["review", "request", "q-347", "--repo", str(repo), "--model", "deepseek/v4"]) == 0
    capsys.readouterr()
    assert cli.main([
        "review", "verdict", "q-347", "--reviewer", "dev-codex", "--verdict", "fix",
        "--model", "gpt-5-codex",
    ]) == 0
    capsys.readouterr()

    assert cli.main(["review", "rounds", "q-347", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["corrections"] == 1
    assert payload["rounds"][-1]["kind"] == "verdict"

    assert cli.main([
        "review", "park", "q-347", "--lead", "dev-lead", "--reason", "safer than main",
        "--new-task", "q-347-next",
    ]) == 0
    capsys.readouterr()
    assert cli.main([
        "review", "except", "q-347", "--lead", "dev-lead", "--reason",
        "remaining gaps pre-exist on main", "--follow-up", "q-347-next",
    ]) == 0
    capsys.readouterr()

    assert cli.main(["task", "status", "q-347", "--status", "done"]) == 0
    task = json.loads(capsys.readouterr().out)
    assert task["status"] == "done"


def test_open_after_fix_requires_a_correction(tmp_path: Path, fake_artifact, led) -> None:
    repo = tmp_path / "code"
    _request(led, repo)
    _verdict(led, "fix")
    with pytest.raises(ValueError, match="correction or park"):
        _request(led, repo)
    assert _kinds(led) == ["request", "verdict"]


def test_stale_fix_cannot_park(tmp_path: Path, fake_artifact, led) -> None:
    _request(led, tmp_path / "code")
    _verdict(led, "fix")
    fake_artifact["head"] = "h2"
    with pytest.raises(ValueError, match="stale"):
        review.park("q-347", lead="dev-lead", reason="safer than main", led=led)
    assert review.rounds("q-347", led=led)["park"] is None


def test_park_requires_a_current_verdict(tmp_path: Path, fake_artifact, led) -> None:
    repo = tmp_path / "code"
    _request(led, repo)
    _verdict(led, "fix")
    review.correct("q-347", led=led, brief="fix1.md", by="dev-lead")
    with pytest.raises(ValueError, match="current fix verdict"):
        review.park("q-347", lead="dev-lead", reason="safer than main", led=led)


def test_stale_exception_cannot_close(tmp_path: Path, fake_artifact, led) -> None:
    repo = tmp_path / "code"
    _request(led, repo)
    _verdict(led, "fix")
    led.add_task(
        "q-347-next",
        "new design",
        "dev-opencode",
        [],
        depends=["q-347"],
        base_commit="base",
    )
    review.park(
        "q-347",
        lead="dev-lead",
        reason="safer than main",
        new_task="q-347-next",
        led=led,
    )
    review.exception(
        "q-347",
        lead="dev-lead",
        reason="remaining gaps pre-exist on main",
        follow_up="q-347-next",
        led=led,
    )
    fake_artifact["head"] = "h2"
    report = review.status("q-347", led=led)
    assert report["close_ok"] is False
    with pytest.raises(ValueError, match="stale|artifact"):
        led.set_status("q-347", "done")
    assert led.get_task("q-347")["status"] != "done"


def test_old_record_can_dispatch_a_correction(tmp_path: Path, fake_artifact, led) -> None:
    repo = tmp_path / "code"
    snap = review.artifact(repo)
    old = {
        "opened_at": "2026-11-13T00:00:00+00:00",
        "artifact": snap,
        "author": {"agent": "dev-opencode", "family": "deepseek", "model": "deepseek/v4"},
        "contract": None,
        "verdict": {
            "at": "2026-11-13T00:01:00+00:00",
            "reviewer": {"agent": "dev-codex", "family": "openai", "model": "gpt-5-codex"},
            "verdict": "fix",
            "artifact": snap,
        },
        "findings": [],
    }

    def plant(task: dict) -> None:
        task["review"] = old

    led.mutate_task("q-347", plant)
    result = review.correct("q-347", led=led, brief="fix1.md", by="dev-lead")
    assert result["status"] == "dispatched"
    assert result["correction"] == 1
    assert _kinds(led) == ["request", "verdict", "correction"]
    assert review.status("q-347", led=led)["phase"] == "corrected"

def test_correction_requires_a_recheck_verdict(tmp_path: Path, fake_artifact, led) -> None:
    repo = tmp_path / "code"
    _request(led, repo)
    _verdict(led, "fix")
    review.correct("q-347", led=led, brief="fix1.md", by="dev-lead")
    _request(led, repo)
    with pytest.raises(ValueError, match="current fix verdict"):
        review.correct("q-347", led=led, brief="fix2.md", by="dev-lead")
    assert review.rounds("q-347", led=led)["corrections"] == 1

def test_fix_verdict_cannot_close(tmp_path: Path, fake_artifact, led) -> None:
    _request(led, tmp_path / "code")
    _verdict(led, "fix")
    with pytest.raises(ValueError, match="merge"):
        led.set_status("q-347", "done")
    assert led.get_task("q-347")["status"] != "done"

def test_resolving_a_finding_keeps_the_verdict_round_unchanged(tmp_path: Path, fake_artifact, led) -> None:
    _request(led, tmp_path / "code")
    _verdict(led, "fix", ["medium|app.py:1|needs a bound"])
    review.resolve("q-347", 1, "fixed", "added a bound", led=led)
    report = review.status("q-347", led=led)
    assert report["findings"][0]["status"] == "fixed"
    assert report["rounds"][1]["findings"][0]["status"] == "open"


def test_moved_artifact_after_fix_verdict_reopens(tmp_path: Path, fake_artifact, led) -> None:
    repo = tmp_path / "code"
    _request(led, repo)
    _verdict(led, "fix")
    fake_artifact["head"] = "h2"
    with pytest.raises(ValueError, match="stale"):
        review.correct("q-347", led=led)
    _request(led, repo)
    assert _kinds(led)[-1] == "recheck"
    assert review.status("q-347", led=led)["phase"] == "open"
    _verdict(led, "merge")
    assert led.set_status("q-347", "done")["status"] == "done"


def test_pending_request_refreshes_when_artifact_moves(tmp_path: Path, fake_artifact, led) -> None:
    repo = tmp_path / "code"
    _request(led, repo)
    _verdict(led, "fix")
    review.correct("q-347", led=led)
    fake_artifact["head"] = "h2"
    _request(led, repo)
    fake_artifact["head"] = "h3"
    _request(led, repo)
    last = review.rounds("q-347", led=led)["rounds"][-1]
    assert last["kind"] == "recheck"
    assert last["artifact"]["head"] == "h3"
    _verdict(led, "merge")
    assert led.set_status("q-347", "done")["status"] == "done"


def test_pending_request_without_a_move_stays_idempotent(tmp_path: Path, fake_artifact, led) -> None:
    repo = tmp_path / "code"
    _request(led, repo)
    again = _request(led, repo)
    assert len(review.rounds("q-347", led=led)["rounds"]) == 1
    assert again["rounds"][0]["kind"] == "request"


def test_reopen_keeps_the_correction_budget(tmp_path: Path, fake_artifact, led) -> None:
    repo = tmp_path / "code"
    _request(led, repo)
    _verdict(led, "fix")
    review.correct("q-347", led=led)
    _request(led, repo)
    _verdict(led, "fix")
    review.correct("q-347", led=led)
    _request(led, repo)
    with pytest.raises(review.RedesignRequired):
        _verdict(led, "fix")
    fake_artifact["head"] = "h2"
    _request(led, repo)
    with pytest.raises(review.RedesignRequired):
        _verdict(led, "fix")
    assert review.rounds("q-347", led=led)["corrections"] == 2
    _verdict(led, "merge")
    assert led.set_status("q-347", "done")["status"] == "done"


def test_stale_merged_verdict_reopens(tmp_path: Path, fake_artifact, led) -> None:
    repo = tmp_path / "code"
    _request(led, repo)
    _verdict(led, "merge")
    fake_artifact["head"] = "h2"
    with pytest.raises(ValueError, match="stale"):
        led.set_status("q-347", "done")
    _request(led, repo)
    assert review.status("q-347", led=led)["phase"] == "open"
    _verdict(led, "merge")
    assert led.set_status("q-347", "done")["status"] == "done"


def test_stale_exception_reopens(tmp_path: Path, fake_artifact, led) -> None:
    repo = tmp_path / "code"
    _request(led, repo)
    _verdict(led, "fix")
    led.add_task(
        "q-347-next",
        "new design",
        "dev-opencode",
        [],
        depends=["q-347"],
        base_commit="base",
    )
    review.park(
        "q-347",
        lead="dev-lead",
        reason="safer than main",
        new_task="q-347-next",
        led=led,
    )
    review.exception(
        "q-347",
        lead="dev-lead",
        reason="remaining gaps pre-exist on main",
        follow_up="q-347-next",
        led=led,
    )
    fake_artifact["head"] = "h2"
    with pytest.raises(ValueError, match="stale"):
        led.set_status("q-347", "done")
    _request(led, repo)
    assert review.status("q-347", led=led)["phase"] == "open"
    _verdict(led, "merge")
    assert led.set_status("q-347", "done")["status"] == "done"


def test_exception_follow_up_must_depend_on_the_task(tmp_path: Path, fake_artifact, led) -> None:
    repo = tmp_path / "code"
    _request(led, repo)
    _verdict(led, "fix")
    led.add_task("unrelated", "unrelated", "dev-opencode", [], base_commit="base")
    review.park("q-347", lead="dev-lead", reason="safer than main", led=led)
    with pytest.raises(ValueError, match="does not depend"):
        review.exception(
            "q-347",
            lead="dev-lead",
            reason="remaining gaps pre-exist on main",
            follow_up="unrelated",
            led=led,
        )


def test_moved_fix_cycles_consume_the_budget(tmp_path: Path, fake_artifact, led) -> None:
    repo = tmp_path / "code"
    _request(led, repo)
    for n in range(2):
        _verdict(led, "fix")
        fake_artifact["head"] = f"h{n + 2}"
        _request(led, repo)
    assert review.rounds("q-347", led=led)["corrections"] == 2
    with pytest.raises(review.RedesignRequired):
        _verdict(led, "fix")
    _verdict(led, "merge")
    assert led.set_status("q-347", "done")["status"] == "done"


def test_spent_budget_refuses_fix_verdicts_without_a_move(tmp_path: Path, fake_artifact, led) -> None:
    repo = tmp_path / "code"
    _request(led, repo)
    for _ in range(2):
        _verdict(led, "fix")
        review.correct("q-347", led=led)
        _request(led, repo)
    with pytest.raises(review.RedesignRequired):
        _verdict(led, "fix")
    assert review.rounds("q-347", led=led)["corrections"] == 2
    _verdict(led, "merge")
    assert led.set_status("q-347", "done")["status"] == "done"


def test_spent_budget_still_recovers_on_a_move_but_fix_stays_refused(
    tmp_path: Path, fake_artifact, led
) -> None:
    repo = tmp_path / "code"
    _request(led, repo)
    _verdict(led, "fix")
    review.correct("q-347", led=led)
    _request(led, repo)
    _verdict(led, "fix")
    review.correct("q-347", led=led)
    _request(led, repo)
    fake_artifact["head"] = "h2"
    _request(led, repo)
    assert review.status("q-347", led=led)["phase"] == "open"
    with pytest.raises(review.RedesignRequired):
        _verdict(led, "fix")
    _verdict(led, "merge")
    assert led.set_status("q-347", "done")["status"] == "done"


def test_update_task_cannot_close_a_review_required_task(led) -> None:
    for status in ("done", "failed", "blocked"):
        with pytest.raises(ValueError, match="review"):
            led.update_task("q-347", status=status)
    assert led.get_task("q-347")["status"] == "open"
    led.update_task("q-347", status="in_progress")
    assert led.get_task("q-347")["status"] == "in_progress"
    led.add_task("housekeeping", "housekeeping", "dev-opencode", ["house.py"])
    assert led.update_task("housekeeping", status="done")["status"] == "done"


def test_update_task_refuses_review_fields(led) -> None:
    with pytest.raises(ValueError, match="review"):
        led.update_task("q-347", review={"rounds": []})
    with pytest.raises(ValueError, match="review"):
        led.update_task("q-347", review_required=False)
    led.update_task("q-347", title="renamed title")
    assert led.get_task("q-347")["title"] == "renamed title"
