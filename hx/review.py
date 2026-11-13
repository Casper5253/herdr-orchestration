"""hx.review: one review record per change, stored on the ledger task (C6).

The measured fault: reviews happen as ad hoc pane messages (~40 rounds) with
no record of what was reviewed, by which family, against which snapshot, or
what happened to each finding. This module keeps the record on the existing
ledger task (``task["review"]``): the artifact identity (base/head refs or a
working-tree content digest), the author's agent and model family, the
acceptance contract from the brief, the verdict with its findings and their
dispositions, and whether the verdict still covers the current artifact.

The record is append-only. Every transition adds a round to
``review["rounds"]`` (request, verdict, correction, recheck, park, exception),
so the history survives a process restart and the correction budget can be
replayed. A verdict is only as good as what it saw: ``status`` recomputes the
artifact and marks the verdict stale when the head or the content digest
changed. The reviewer must be a different model family than the author, the
correction budget is two, and a third correction is refused before any round
is appended; a fix verdict past the budget is refused the same way. An artifact
that moved is not an error: ``open_review`` appends a recheck round on the new
snapshot, returning the record to the open phase. A move after a fix verdict is
itself a correction journey, so an implicit correction round is recorded and the
count covers every correction, not only dispatched ones.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hx import ledger as ledger_mod
from hx import paths
from hx import usage as usage_mod

CONTRACT_MAX = 1500
CORRECTION_LIMIT = 2


class RedesignRequired(ValueError):
    """Raised when a review has spent its two correction dispatches."""


def _repo_root() -> Path:
    return paths.project_root()


def _run_git(args: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", "-C", str(cwd)] + args, capture_output=True, text=True, timeout=30
    )
    return proc.returncode, proc.stdout.strip()


def _family(agent: str, policy: dict[str, Any] | None) -> str:
    try:
        prefix = usage_mod.team_prefix(policy)
    except Exception:
        prefix = "dev-"
    name = agent[len(prefix) :] if agent.startswith(prefix) else agent
    family = name.split("-")[0]
    return {"claude": "anthropic", "codex": "openai", "devin": "devin-swe"}.get(family, family)


_PROVIDER_FAMILIES = {
    "anthropic": "anthropic",
    "openai": "openai",
    "codex": "openai",
    "devin": "devin-swe",
    "deepseek": "deepseek",
    "qwen": "alibaba",
    "zai": "zhipu",
    "moonshotai": "moonshot",
    "xiaomi": "xiaomi",
    "minimax": "minimax",
    "google": "google",
    "xai": "xai",
}

_MODEL_PREFIX_FAMILIES = (
    ("swe-", "devin-swe"),
    ("claude", "anthropic"),
    ("gpt-", "openai"),
    ("codex", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("deepseek", "deepseek"),
    ("qwen", "alibaba"),
    ("glm", "zhipu"),
    ("kimi", "moonshot"),
    ("mimo", "xiaomi"),
    ("minimax", "minimax"),
    ("gemini", "google"),
    ("grok", "xai"),
)


def _model_family(model: str | None) -> str | None:
    """The family a model id actually belongs to, when the id reveals it."""
    if not model:
        return None
    text = model.strip().lower()
    name = text.rsplit("/", 1)[-1]
    for prefix, family in _MODEL_PREFIX_FAMILIES:
        if name.startswith(prefix):
            return family
    for provider in reversed(text.split("/")[:-1]):
        if provider in _PROVIDER_FAMILIES:
            return _PROVIDER_FAMILIES[provider]
    return None


def _resolve_family(agent: str, model: str | None, policy: dict[str, Any] | None) -> str:
    """Prefer the actual model family; fall back to the declared agent family."""
    return _model_family(model) or _family(agent, policy)


def _run_git_raw(args: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", "-C", str(cwd)] + args, capture_output=True, text=True, timeout=30
    )
    return proc.returncode, proc.stdout


def artifact(repo: Path) -> dict[str, Any]:
    """The reviewable snapshot: branch head plus a digest of working-tree content."""
    code, head = _run_git(["rev-parse", "HEAD"], repo)
    if code != 0:
        raise ValueError(f"{repo} is not a git repository")
    _, branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
    _, porcelain = _run_git_raw(["status", "--porcelain=v1", "-z", "-uall"], repo)
    digest = None
    if porcelain:
        hasher = hashlib.sha256()
        _, unstaged = _run_git(["diff"], repo)
        _, staged = _run_git(["diff", "--cached"], repo)
        for part in (unstaged, staged):
            hasher.update(part.encode("utf-8"))
            hasher.update(b"\0")
        for entry in porcelain.split("\0"):
            if not entry:
                continue
            hasher.update(entry.encode("utf-8"))
            hasher.update(b"\0")
            if entry[:2] == "??":
                try:
                    content = (repo / entry[3:]).read_bytes()
                except OSError:
                    hasher.update(b"unreadable\0")
                else:
                    hasher.update(hashlib.sha256(content).digest())
        digest = hasher.hexdigest()[:16]
    return {
        "repo": str(repo),
        "branch": branch,
        "head": head,
        "digest": digest,
        "mode": "worktree" if digest else "commit",
    }


def _artifact_stale(opened: dict[str, Any], current: dict[str, Any]) -> bool:
    return current.get("head") != opened.get("head") or current.get("digest") != opened.get("digest")


def _contract_from_brief(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"file": str(path), "error": "unreadable"}
    match = re.search(r"## (?:Acceptance|Goal|The first slice)\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    contract = (match.group(1) if match else text).strip()[:CONTRACT_MAX]
    return {"file": str(path), "contract": contract}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _rounds_of(review: dict[str, Any] | None) -> list[dict[str, Any]]:
    return list((review or {}).get("rounds") or [])


def _last_round(review: dict[str, Any] | None) -> dict[str, Any] | None:
    rounds = _rounds_of(review)
    return rounds[-1] if rounds else None


def _corrections(review: dict[str, Any] | None) -> int:
    if not review:
        return 0
    rounds = _rounds_of(review)
    if rounds:
        count = 0
        previous: dict[str, Any] | None = None
        for round_ in rounds:
            if round_.get("kind") == "correction":
                count += 1
            elif (
                round_.get("kind") == "recheck"
                and (previous or {}).get("kind") == "verdict"
                and previous.get("verdict") == "fix"
            ):
                count += 1
            previous = round_
        return count
    return int(review.get("corrections") or 0)


def _normalize_rounds(review: dict[str, Any]) -> None:
    """Give an older review record the round history the transitions expect."""
    if review.get("rounds"):
        return
    rounds: list[dict[str, Any]] = []
    artifact = review.get("artifact") or {}
    if review.get("opened_at") or artifact:
        rounds.append(
            {
                "kind": "request",
                "at": review.get("opened_at") or _now(),
                "artifact": artifact,
                "author": (review.get("author") or {}).get("agent") or "unknown",
            }
        )
    verdict = review.get("verdict")
    if verdict:
        rounds.append(
            {
                "kind": "verdict",
                "at": verdict.get("at") or _now(),
                "artifact": verdict.get("artifact") or artifact,
                "reviewer": (verdict.get("reviewer") or {}).get("agent") or "unknown",
                "verdict": verdict.get("verdict"),
                "findings": [dict(finding) for finding in (review.get("findings") or [])],
            }
        )
    review["rounds"] = rounds
    review["corrections"] = sum(1 for round_ in rounds if round_.get("kind") == "correction")


def _phase(review: dict[str, Any] | None) -> str:
    if not review:
        return "none"
    last = _last_round(review)
    if last is not None:
        kind = last.get("kind")
        if kind in ("request", "recheck"):
            return "open"
        if kind == "verdict":
            value = last.get("verdict") or (review.get("verdict") or {}).get("verdict")
            if value in ("merge", "ship"):
                return "merged"
            if value == "park":
                return "parked"
            return "fix"
        if kind == "correction":
            return "corrected"
        if kind == "park":
            return "parked"
        if kind == "exception":
            return "exception"
    verdict_rec = review.get("verdict")
    if not verdict_rec:
        return "open"
    value = verdict_rec.get("verdict")
    if value in ("merge", "ship"):
        return "merged"
    if value == "park":
        return "parked"
    return "fix"


def _exception_complete(exception: dict[str, Any] | None) -> bool:
    required = ("lead", "reason", "follow_up", "artifact")
    return bool(exception) and all(exception.get(key) for key in required)


def _artifact_state(snapshot: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Recompute the current artifact for a snapshot; return (current, error)."""
    try:
        repo = Path(snapshot["repo"])
    except (KeyError, TypeError):
        return None, "reviewed artifact is unreadable: no repository recorded"
    try:
        return artifact(repo), None
    except ValueError as exc:
        return None, f"reviewed artifact is unreadable: {exc}"


def close_guard(task: dict[str, Any]) -> dict[str, Any]:
    """Whether a task may close as done, given its review record."""
    if not task.get("review_required"):
        return {"ok": True, "reason": "review not required"}
    review = task.get("review")
    if not review or not review.get("verdict"):
        return {"ok": False, "reason": "no review verdict recorded"}

    phase = _phase(review)
    if phase == "merged":
        snapshot = review["verdict"].get("artifact") or review.get("artifact") or {}
        current, error = _artifact_state(snapshot)
        if error:
            return {"ok": False, "reason": error}
        if _artifact_stale(snapshot, current or {}):
            return {
                "ok": False,
                "reason": "review verdict is stale: the artifact changed since it was reviewed",
            }
        return {"ok": True, "reason": "review verdict covers the current artifact"}

    if phase in ("parked", "exception"):
        exception = review.get("exception")
        if not _exception_complete(exception):
            return {
                "ok": False,
                "reason": "review is parked; an exception is required to close",
            }
        snapshot = exception["artifact"]
        current, error = _artifact_state(snapshot)
        if error:
            return {"ok": False, "reason": error}
        if _artifact_stale(snapshot, current or {}):
            return {
                "ok": False,
                "reason": "review exception is stale: the artifact changed since it was recorded",
            }
        return {"ok": True, "reason": "park exception covers the current artifact"}

    return {
        "ok": False,
        "reason": "review has no merge verdict; a merge verdict or park exception is required",
    }


def open_review(
    task_id: str,
    repo: Path,
    base: str = "main",
    brief: Path | None = None,
    author: str | None = None,
    led: ledger_mod.Ledger | None = None,
    policy: dict[str, Any] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Open a review, or record a recheck after a correction or a moved artifact."""
    led = led or ledger_mod.Ledger(repo_root=_repo_root())
    task = led.get_task(task_id)
    snap = artifact(repo)
    snap["base"] = base
    agent = author or task.get("owner") or "unknown"
    author_model = model or task.get("model")
    now = _now()
    result: dict[str, Any] = {}

    def mutator(current: dict[str, Any]) -> None:
        current["review_required"] = True
        existing = current.get("review")
        if existing:
            _normalize_rounds(existing)
            last = _last_round(existing)
            kind = (last or {}).get("kind")
            pending = (
                (last or {}).get("artifact")
                or (existing.get("verdict") or {}).get("artifact")
                or existing.get("artifact")
                or {}
            )
            moved = bool(pending) and _artifact_stale(pending, snap)
            if moved or kind == "correction":
                rounds = existing.setdefault("rounds", [])
                if moved and kind == "verdict" and (last or {}).get("verdict") == "fix":
                    rounds.append(
                        {
                            "kind": "correction",
                            "at": now,
                            "artifact": pending,
                            "by": "moved-artifact",
                            "brief": None,
                            "note": "artifact moved after a fix verdict",
                            "correction": _corrections(existing) + 1,
                        }
                    )
                    existing["corrections"] = _corrections(existing)
                rounds.append(
                    {
                        "kind": "recheck",
                        "at": now,
                        "artifact": snap,
                        "correction": _corrections(existing),
                    }
                )
                result.update(existing)
                return
            if kind in ("request", "recheck"):
                result.update(existing)
                return
            verdict_value = (existing.get("verdict") or {}).get("verdict")
            if kind in ("park", "exception") or verdict_value in ("merge", "ship", "park"):
                raise ValueError(
                    f"{task_id} review is closed; a linked new-design task is needed"
                )
            if kind == "verdict":
                raise ValueError(
                    f"{task_id} last verdict is fix; dispatch a correction or park before rechecking"
                )
            existing.setdefault("rounds", []).append(
                {
                    "kind": "request",
                    "at": existing.get("opened_at") or now,
                    "artifact": existing.get("artifact") or snap,
                    "author": (existing.get("author") or {}).get("agent") or agent,
                    "base": base,
                }
            )
            result.update(existing)
            return
        review = {
            "opened_at": now,
            "artifact": snap,
            "author": {
                "agent": agent,
                "family": _resolve_family(agent, author_model, policy),
                "model": author_model,
            },
            "contract": _contract_from_brief(brief),
            "verdict": None,
            "findings": [],
            "rounds": [
                {
                    "kind": "request",
                    "at": now,
                    "artifact": snap,
                    "author": agent,
                    "base": base,
                }
            ],
            "corrections": 0,
            "park": None,
            "exception": None,
        }
        current["review"] = review
        result.update(review)

    led.mutate_task(task_id, mutator)
    return result

def verdict(
    task_id: str,
    reviewer: str,
    value: str,
    findings: list[str],
    led: ledger_mod.Ledger | None = None,
    policy: dict[str, Any] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Record a merge or fix verdict on the current snapshot; refuse same-family review."""
    led = led or ledger_mod.Ledger(repo_root=_repo_root())
    value = "merge" if value == "ship" else value
    if value not in ("merge", "fix"):
        raise ValueError(f"invalid review verdict: {value}")
    task = led.get_task(task_id)
    review = (task or {}).get("review")
    if review is None:
        raise ValueError(f"{task_id} has no open review; run hx review request first")
    _normalize_rounds(review)
    last = _last_round(review)
    if last is not None and last.get("kind") not in ("request", "recheck"):
        raise ValueError(f"{task_id} review is not open; open a recheck before a new verdict")
    if value == "fix" and _corrections(review) >= CORRECTION_LIMIT:
        raise RedesignRequired(
            f"{task_id} has used both corrections; a redesign is required before another round"
        )
    reviewer_model = model or os.environ.get("HX_REVIEWER_MODEL")
    reviewer_family = _resolve_family(reviewer, reviewer_model, policy)
    author_family = _resolve_family(review["author"]["agent"], review["author"].get("model"), policy)
    if reviewer_family == author_family:
        raise ValueError(
            f"same-family review refused: reviewer {reviewer} is {reviewer_family}, "
            f"the same family as author {review['author']['agent']}"
        )
    repo = Path(review["artifact"]["repo"])
    try:
        snap = artifact(repo)
    except ValueError:
        snap = review.get("artifact") or {}
    requested = (last or {}).get("artifact") or review.get("artifact") or {}
    if _artifact_stale(requested, snap):
        raise ValueError(
            f"{task_id} review artifact is stale: it changed since the review was requested"
        )
    parsed = []
    for raw in findings:
        severity, _, rest = raw.partition("|")
        location, _, text = rest.partition("|")
        parsed.append(
            {
                "severity": severity.strip(),
                "location": location.strip(),
                "text": (text or rest).strip(),
                "status": "open",
            }
        )
    now = _now()
    result: dict[str, Any] = {}

    def mutator(current: dict[str, Any]) -> None:
        record = current["review"]
        _normalize_rounds(record)
        last_now = _last_round(record)
        if last_now is not None and last_now.get("kind") not in ("request", "recheck"):
            raise ValueError(f"{task_id} review is not open; open a recheck before a new verdict")
        if value == "fix" and _corrections(record) >= CORRECTION_LIMIT:
            raise RedesignRequired(
                f"{task_id} has used both corrections; a redesign is required before another round"
            )
        record["verdict"] = {
            "at": now,
            "reviewer": {
                "agent": reviewer,
                "family": reviewer_family,
                "model": reviewer_model,
            },
            "verdict": value,
            "artifact": snap,
        }
        record.setdefault("findings", []).extend(parsed)
        record.setdefault("rounds", []).append(
            {
                "kind": "verdict",
                "at": now,
                "artifact": snap,
                "reviewer": reviewer,
                "verdict": value,
                "findings": [dict(finding) for finding in parsed],
            }
        )
        result.update(record)

    led.mutate_task(task_id, mutator)
    return result


def correct(
    task_id: str,
    led: ledger_mod.Ledger | None = None,
    brief: Path | str | None = None,
    note: str | None = None,
    by: str = "lead",
) -> dict[str, Any]:
    """Dispatch a correction against a current fix verdict, at most twice."""
    led = led or ledger_mod.Ledger(repo_root=_repo_root())
    task = led.get_task(task_id)
    review = (task or {}).get("review")
    if review is None:
        raise ValueError(f"{task_id} has no review; run hx review request first")
    _normalize_rounds(review)
    last = _last_round(review)
    if last is not None and last.get("kind") == "correction":
        return {"status": "duplicate", "correction": _corrections(review)}
    if (last or {}).get("kind") != "verdict" or last.get("verdict") != "fix":
        raise ValueError(f"{task_id} correction requires a current fix verdict")
    verdict_rec = review.get("verdict") or {}
    if _corrections(review) >= CORRECTION_LIMIT:
        raise RedesignRequired(
            f"{task_id} has used both corrections; a redesign is required before another round"
        )
    reviewed = verdict_rec.get("artifact") or review.get("artifact") or {}
    current, error = _artifact_state(reviewed)
    if error:
        raise ValueError(f"{task_id} {error}; a recheck is required")
    if _artifact_stale(reviewed, current or {}):
        raise ValueError(
            f"{task_id} fix verdict is stale: the artifact changed since it was reviewed; "
            "run hx review request to recheck it"
        )
    now = _now()
    result: dict[str, Any] = {}

    def mutator(current_task: dict[str, Any]) -> None:
        record = current_task["review"]
        _normalize_rounds(record)
        last_now = _last_round(record)
        if last_now is not None and last_now.get("kind") == "correction":
            result.update({"status": "duplicate", "correction": _corrections(record)})
            return
        if (last_now or {}).get("kind") != "verdict" or last_now.get("verdict") != "fix":
            raise ValueError(f"{task_id} correction requires a current fix verdict")
        if _corrections(record) >= CORRECTION_LIMIT:
            raise RedesignRequired(
                f"{task_id} has used both corrections; a redesign is required before another round"
            )
        number = _corrections(record) + 1
        record.setdefault("rounds", []).append(
            {
                "kind": "correction",
                "at": now,
                "artifact": reviewed,
                "by": by,
                "brief": str(brief) if brief is not None else None,
                "note": note,
                "correction": number,
            }
        )
        record["corrections"] = number
        result.update({"status": "dispatched", "correction": number})

    led.mutate_task(task_id, mutator)
    return result

def park(
    task_id: str,
    lead: str,
    reason: str,
    new_task: str | None = None,
    led: ledger_mod.Ledger | None = None,
) -> dict[str, Any]:
    """Park a current fix verdict, optionally linking a new-design task."""
    led = led or ledger_mod.Ledger(repo_root=_repo_root())
    if not lead.strip():
        raise ValueError("park requires a lead")
    if not reason.strip():
        raise ValueError("park requires a reason")
    task = led.get_task(task_id)
    review = (task or {}).get("review")
    if review is None:
        raise ValueError(f"{task_id} has no review; run hx review request first")
    _normalize_rounds(review)
    if _phase(review) == "parked":
        return review
    last = _last_round(review)
    if (last or {}).get("kind") != "verdict" or last.get("verdict") != "fix":
        raise ValueError(f"{task_id} park requires a current fix verdict")
    verdict_rec = review.get("verdict") or {}
    if verdict_rec.get("verdict") != "fix":
        raise ValueError(f"{task_id} has no fix verdict to park")
    if new_task:
        linked = led.get_task(new_task)
        if task_id not in (linked.get("depends") or []):
            raise ValueError(f"{new_task} does not depend on {task_id}")
    snapshot = verdict_rec.get("artifact") or review.get("artifact") or {}
    current, error = _artifact_state(snapshot)
    if error:
        raise ValueError(f"{task_id} {error}; a recheck is required")
    if _artifact_stale(snapshot, current or {}):
        raise ValueError(
            f"{task_id} fix verdict is stale: the artifact changed since it was reviewed; "
            "run hx review request to recheck it"
        )
    now = _now()
    park_record = {
        "at": now,
        "lead": lead,
        "reason": reason,
        "new_task": new_task,
        "artifact": snapshot,
    }
    result: dict[str, Any] = {}

    def mutator(current_task: dict[str, Any]) -> None:
        record = current_task["review"]
        _normalize_rounds(record)
        if _phase(record) == "parked":
            result.update(record)
            return
        record["park"] = park_record
        record["verdict"] = {
            "at": now,
            "reviewer": {
                "agent": lead,
                "family": _family(lead, None),
                "model": None,
            },
            "verdict": "park",
            "artifact": snapshot,
        }
        record.setdefault("rounds", []).append({"kind": "park", **park_record})
        result.update(record)

    led.mutate_task(task_id, mutator)
    led.set_status(task_id, "blocked")
    return result


def exception(
    task_id: str,
    lead: str,
    reason: str,
    follow_up: str,
    led: ledger_mod.Ledger | None = None,
) -> dict[str, Any]:
    """Record why a parked review may still close, with the reviewed snapshot."""
    led = led or ledger_mod.Ledger(repo_root=_repo_root())
    if not lead.strip():
        raise ValueError("exception requires a lead")
    if not reason.strip():
        raise ValueError("exception requires a reason")
    if not follow_up.strip():
        raise ValueError("exception requires a follow-up task")
    if follow_up == task_id:
        raise ValueError("exception follow-up must be a different task")
    task = led.get_task(task_id)
    review = (task or {}).get("review")
    if review is None:
        raise ValueError(f"{task_id} has no review; run hx review request first")
    _normalize_rounds(review)
    if _phase(review) == "exception":
        return review
    if (review.get("verdict") or {}).get("verdict") != "park":
        raise ValueError(f"{task_id} review is not parked; an exception does not apply")
    linked = led.get_task(follow_up)
    if task_id not in (linked.get("depends") or []):
        raise ValueError(f"{follow_up} does not depend on {task_id}")
    snapshot = (review.get("verdict") or {}).get("artifact") or review.get("artifact") or {}
    current, error = _artifact_state(snapshot)
    if error:
        raise ValueError(f"{task_id} {error}; a recheck is required")
    if _artifact_stale(snapshot, current or {}):
        raise ValueError(
            f"{task_id} park verdict is stale: the artifact changed since it was recorded"
        )
    now = _now()
    record = {
        "at": now,
        "lead": lead,
        "reason": reason,
        "follow_up": follow_up,
        "artifact": snapshot,
    }
    result: dict[str, Any] = {}

    def mutator(current_task: dict[str, Any]) -> None:
        review_record = current_task["review"]
        _normalize_rounds(review_record)
        if _phase(review_record) == "exception":
            result.update(review_record)
            return
        review_record["exception"] = record
        review_record.setdefault("rounds", []).append({"kind": "exception", **record})
        result.update(review_record)

    led.mutate_task(task_id, mutator)
    return result


def status(task_id: str, led: ledger_mod.Ledger | None = None) -> dict[str, Any]:
    """The verdict plus whether it still covers the artifact as it is now."""
    led = led or ledger_mod.Ledger(repo_root=_repo_root())
    task = led.get_task(task_id)
    review = (task or {}).get("review")
    guard = close_guard(task)
    if review is None:
        return {
            "task": task_id,
            "state": "none",
            "reason": "no review opened",
            "phase": "none",
            "corrections": 0,
            "rounds": [],
            "review_required": bool(task.get("review_required")),
            "close_ok": guard["ok"],
            "close_reason": guard["reason"],
        }
    out: dict[str, Any] = {
        "task": task_id,
        "author": review["author"],
        "artifact": review["artifact"],
        "findings": review["findings"],
        "verdict": review["verdict"],
        "phase": _phase(review),
        "corrections": _corrections(review),
        "rounds": _rounds_of(review),
        "park": review.get("park"),
        "exception": review.get("exception"),
        "review_required": bool(task.get("review_required")),
        "close_ok": guard["ok"],
        "close_reason": guard["reason"],
    }
    verdict_rec = review.get("verdict")
    if verdict_rec is None:
        out["state"] = "open"
        out["reason"] = "no verdict recorded"
        return out
    try:
        current = artifact(Path(verdict_rec["artifact"]["repo"]))
    except ValueError:
        out["state"] = "unknown"
        out["reason"] = "reviewed repository is unreadable"
        return out
    stale = _artifact_stale(verdict_rec["artifact"], current)
    out["state"] = "stale" if stale else "valid"
    out["reason"] = (
        "artifact changed since the verdict (new head or edited tree)"
        if stale
        else "verdict covers the current artifact"
    )
    out["current_artifact"] = current
    return out


def rounds(task_id: str, led: ledger_mod.Ledger | None = None) -> dict[str, Any]:
    """The append-only round history, correction count, park and exception."""
    led = led or ledger_mod.Ledger(repo_root=_repo_root())
    task = led.get_task(task_id)
    review = (task or {}).get("review")
    return {
        "task": task_id,
        "rounds": _rounds_of(review),
        "corrections": _corrections(review),
        "park": (review or {}).get("park"),
        "exception": (review or {}).get("exception"),
    }

def resolve(
    task_id: str,
    number: int,
    disposition: str,
    note: str | None,
    led: ledger_mod.Ledger | None = None,
) -> dict[str, Any]:
    """Set a finding's disposition: fixed or dismissed, with a note."""
    led = led or ledger_mod.Ledger(repo_root=_repo_root())
    task = led.get_task(task_id)
    review = (task or {}).get("review")
    if review is None or not (1 <= number <= len(review["findings"])):
        raise ValueError(f"{task_id} has no finding {number}")
    result: dict[str, Any] = {}

    def mutator(current: dict[str, Any]) -> None:
        record = current.get("review")
        if record is None or not (1 <= number <= len(record.get("findings") or [])):
            raise ValueError(f"{task_id} has no finding {number}")
        record["findings"][number - 1].update(
            status=disposition, disposition_note=note, resolved_at=_now()
        )
        result.update(record)

    led.mutate_task(task_id, mutator)
    return result


def _summary(report: dict[str, Any]) -> str:
    lines = [f"review {report['task']}: {report['state']} - {report['reason']}"]
    if report.get("phase"):
        lines.append(f"  phase: {report['phase']}, corrections: {report.get('corrections', 0)}")
    if "close_ok" in report:
        state = "allowed" if report["close_ok"] else "blocked"
        lines.append(f"  close: {state} - {report.get('close_reason', '')}")
    verdict_rec = report.get("verdict")
    if verdict_rec:
        model = verdict_rec["reviewer"].get("model")
        by = f" by {verdict_rec['reviewer']['agent']}"
        lines.append(
            f"  verdict: {verdict_rec['verdict']}{by} "
            f"({verdict_rec['reviewer']['family']}{', ' + model if model else ''}) "
            f"at {verdict_rec['at']}"
        )
    for index, finding in enumerate(report.get("findings") or [], 1):
        note = f" [{finding['status']}]" if finding.get("status") != "open" else ""
        lines.append(f"  {index}. {finding['severity']} {finding['location']} {finding['text']}{note}")
    return "\n".join(lines)


def _rounds_summary(report: dict[str, Any]) -> str:
    lines = [
        f"review {report['task']}: {len(report['rounds'])} rounds, "
        f"{report['corrections']} corrections"
    ]
    for round_ in report["rounds"]:
        detail = (
            round_.get("verdict")
            or round_.get("reason")
            or round_.get("brief")
            or round_.get("follow_up")
            or ""
        )
        suffix = f" - {detail}" if detail else ""
        lines.append(f"  {round_.get('at')} {round_.get('kind')}{suffix}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hx review", description="One review record per change.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    open_p = sub.add_parser(
        "request",
        aliases=["open"],
        help="open a review or record a recheck after a correction",
    )
    open_p.add_argument("task")
    open_p.add_argument("--repo", required=True, type=Path)
    open_p.add_argument("--base", default="main")
    open_p.add_argument("--brief", type=Path)
    open_p.add_argument("--author")
    open_p.add_argument("--model", help="the author's actual model id, if known")

    verdict_p = sub.add_parser("verdict", help="record a cross-family verdict with findings")
    verdict_p.add_argument("task")
    verdict_p.add_argument("--reviewer", required=True)
    verdict_p.add_argument("--verdict", required=True, choices=["merge", "ship", "fix"])
    verdict_p.add_argument("--finding", action="append", default=[], dest="findings")
    verdict_p.add_argument(
        "--model",
        help="the reviewer's actual model id (for OpenCode, its model id); defaults to HX_REVIEWER_MODEL",
    )

    correct_p = sub.add_parser("correct", help="dispatch a correction against a fix verdict")
    correct_p.add_argument("task")
    correct_p.add_argument("--brief", type=Path)
    correct_p.add_argument("--note")
    correct_p.add_argument("--by", default="lead")

    park_p = sub.add_parser("park", help="park a fix verdict and block the task")
    park_p.add_argument("task")
    park_p.add_argument("--lead", required=True)
    park_p.add_argument("--reason", required=True)
    park_p.add_argument("--new-task")
    park_p.add_argument("--json", action="store_true")

    except_p = sub.add_parser("except", help="record an exception that lets a parked task close")
    except_p.add_argument("task")
    except_p.add_argument("--lead", required=True)
    except_p.add_argument("--reason", required=True)
    except_p.add_argument("--follow-up", required=True)
    except_p.add_argument("--json", action="store_true")

    rounds_p = sub.add_parser("rounds", help="show the round history")
    rounds_p.add_argument("task")
    rounds_p.add_argument("--json", action="store_true")

    status_p = sub.add_parser("status", help="show the verdict and whether it is still valid")
    status_p.add_argument("task")
    status_p.add_argument("--json", action="store_true")

    resolve_p = sub.add_parser("resolve", help="mark a finding fixed or dismissed")
    resolve_p.add_argument("task")
    resolve_p.add_argument("--finding", required=True, type=int)
    resolve_p.add_argument("--disposition", required=True, choices=["fixed", "dismissed"])
    resolve_p.add_argument("--note")

    args = parser.parse_args(argv)
    try:
        if args.cmd in ("request", "open"):
            record = open_review(
                args.task, args.repo, args.base, args.brief, args.author, model=args.model
            )
            print(
                f"review opened on {args.task}: {record['artifact']['mode']} snapshot, "
                f"author {record['author']['agent']} ({record['author']['family']})"
            )
            return 0
        if args.cmd == "verdict":
            record = verdict(args.task, args.reviewer, args.verdict, args.findings, model=args.model)
            print(
                f"verdict recorded on {args.task}: {args.verdict} by {args.reviewer}; "
                f"{len(record['findings'])} findings"
            )
            return 0
        if args.cmd == "correct":
            result = correct(args.task, brief=args.brief, note=args.note, by=args.by)
            print(f"correction {result['correction']} on {args.task}: {result['status']}")
            return 0
        if args.cmd == "park":
            result = park(args.task, args.lead, args.reason, args.new_task)
            print(
                json.dumps(result["park"], indent=2)
                if args.json
                else f"review parked on {args.task}: {args.reason}"
            )
            return 0
        if args.cmd == "except":
            record = exception(args.task, args.lead, args.reason, args.follow_up)
            print(
                json.dumps(record["exception"], indent=2)
                if args.json
                else f"review exception recorded on {args.task}: follow-up {args.follow_up}"
            )
            return 0
        if args.cmd == "rounds":
            report = rounds(args.task)
            print(json.dumps(report, indent=2) if args.json else _rounds_summary(report))
            return 0
        if args.cmd == "resolve":
            resolve(args.task, args.finding, args.disposition, args.note)
            print(f"finding {args.finding} on {args.task}: {args.disposition}")
            return 0
        report = status(args.task)
        print(json.dumps(report, indent=2) if args.json else _summary(report))
        return 0 if report["state"] != "stale" else 1
    except RedesignRequired as exc:
        print(f"redesign-required: {exc}")
        return 1
    except (ValueError, ledger_mod.TaskNotFound, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
