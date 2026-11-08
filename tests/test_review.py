"""Tests for hx.review: the record, the family rule and staleness."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hx import ledger as ledger_mod
from hx import review

POLICY = {"team_prefix": "dev-"}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo)] + list(args), check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "code"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


def _led(tmp_path: Path) -> ledger_mod.Ledger:
    led = ledger_mod.Ledger(
        repo_root=tmp_path,
        ledger_path=tmp_path / "ledger.json",
        leases_path=tmp_path / "leases.json",
        lock_path=tmp_path / "ledger.lock",
    )
    led.add_task("q-206", "drafts review", "dev-codex", ["src/app/drafts.ts"])
    return led


def test_same_family_refusal(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    led = _led(tmp_path)
    review.open_review("q-206", repo, led=led, policy=POLICY)
    with pytest.raises(ValueError, match="same-family"):
        review.verdict("q-206", "dev-codex-review", "ship", [], led=led, policy=POLICY)


def test_cross_family_verdict_then_stale_on_edit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    led = _led(tmp_path)
    review.open_review("q-206", repo, led=led, policy=POLICY)
    review.verdict("q-206", "dev-devin", "fix", ["medium|src/app/drafts.ts:17|no bound"], led=led, policy=POLICY)
    report = review.status("q-206", led=led)
    assert report["state"] == "valid"
    assert report["verdict"]["verdict"] == "fix"

    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    report = review.status("q-206", led=led)
    assert report["state"] == "stale"

    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change")
    assert review.status("q-206", led=led)["state"] == "stale"


def test_finding_dispositions(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    led = _led(tmp_path)
    review.open_review("q-206", repo, led=led, policy=POLICY)
    review.verdict(
        "q-206",
        "dev-devin",
        "fix",
        ["medium|drafts.ts:17|no bound", "nit|drafts.ts:9|corrupt entry lingers"],
        led=led,
        policy=POLICY,
    )
    review.resolve("q-206", 1, "fixed", "added eviction cap", led=led)
    review.resolve("q-206", 2, "dismissed", "page never crashes", led=led)
    report = review.status("q-206", led=led)
    assert report["findings"][0]["status"] == "fixed"
    assert report["findings"][0]["disposition_note"] == "added eviction cap"
    assert report["findings"][1]["status"] == "dismissed"
    with pytest.raises(ValueError):
        review.resolve("q-206", 3, "fixed", None, led=led)


def test_status_none_and_open(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    led = _led(tmp_path)
    assert review.status("q-206", led=led)["state"] == "none"
    review.open_review("q-206", repo, brief=None, led=led, policy=POLICY)
    assert review.status("q-206", led=led)["state"] == "open"


def test_contract_from_brief(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    led = _led(tmp_path)
    brief = tmp_path / "brief.md"
    brief.write_text("# Brief\n## Goal\nShip one review record.\n## Verify\npytest\n", encoding="utf-8")
    rec = review.open_review("q-206", repo, brief=brief, led=led, policy=POLICY)
    assert "Ship one review record" in rec["contract"]["contract"]
    assert rec["author"]["family"] == "codex"
