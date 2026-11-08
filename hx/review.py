"""hx.review: one review record per change, stored on the ledger task (C6).

The measured fault: reviews happen as ad hoc pane messages (~40 rounds) with
no record of what was reviewed, by which family, against which snapshot, or
what happened to each finding. This module keeps the record on the existing
ledger task (``task["review"]``): the artifact identity (base/head refs or a
working-tree content digest), the author's agent and model family, the
acceptance contract from the brief, the verdict with its findings and their
dispositions, and whether the verdict still covers the current artifact.

A verdict is only as good as what it saw: ``status`` recomputes the artifact
and marks the verdict stale when the head or the content digest changed --
a substantive edit or a new commit invalidates it. The reviewer must be a
different model family than the author, because same-family review is the
blind spot this record exists to break.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hx import ledger as ledger_mod
from hx import paths
from hx import usage as usage_mod

CONTRACT_MAX = 1500


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
    return current["head"] != opened["head"] or current["digest"] != opened["digest"]


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
    """Record the artifact, author family and acceptance contract on the task."""
    led = led or ledger_mod.Ledger(repo_root=_repo_root())
    task = led.get_task(task_id)
    if task is None:
        raise ledger_mod.TaskNotFound(task_id)
    snap = artifact(repo)
    snap["base"] = base
    agent = author or task.get("owner") or "unknown"
    author_model = model or task.get("model")
    review = {
        "opened_at": _now(),
        "artifact": snap,
        "author": {
            "agent": agent,
            "family": _resolve_family(agent, author_model, policy),
            "model": author_model,
        },
        "contract": _contract_from_brief(brief),
        "verdict": None,
        "findings": [],
    }
    led.update_task(task_id, review=review)
    return review


def verdict(
    task_id: str,
    reviewer: str,
    value: str,
    findings: list[str],
    led: ledger_mod.Ledger | None = None,
    policy: dict[str, Any] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Record the verdict on the current snapshot; refuse same-family review."""
    led = led or ledger_mod.Ledger(repo_root=_repo_root())
    task = led.get_task(task_id)
    review = (task or {}).get("review")
    if review is None:
        raise ValueError(f"{task_id} has no open review; run hx review open first")
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
        snap = review["artifact"]
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
    review["verdict"] = {
        "at": _now(),
        "reviewer": {
            "agent": reviewer,
            "family": reviewer_family,
            "model": reviewer_model,
        },
        "verdict": value,
        "artifact": snap,
    }
    review["findings"].extend(parsed)
    led.update_task(task_id, review=review)
    return review


def status(task_id: str, led: ledger_mod.Ledger | None = None) -> dict[str, Any]:
    """The verdict plus whether it still covers the artifact as it is now."""
    led = led or ledger_mod.Ledger(repo_root=_repo_root())
    task = led.get_task(task_id)
    review = (task or {}).get("review")
    if review is None:
        return {"task": task_id, "state": "none", "reason": "no review opened"}
    out: dict[str, Any] = {
        "task": task_id,
        "author": review["author"],
        "artifact": review["artifact"],
        "findings": review["findings"],
        "verdict": review["verdict"],
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
    review["findings"][number - 1].update(
        status=disposition, disposition_note=note, resolved_at=_now()
    )
    led.update_task(task_id, review=review)
    return review


def _summary(report: dict[str, Any]) -> str:
    lines = [f"review {report['task']}: {report['state']} — {report['reason']}"]
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hx review", description="One review record per change.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    open_p = sub.add_parser("open", help="record the artifact, author family and contract")
    open_p.add_argument("task")
    open_p.add_argument("--repo", required=True, type=Path)
    open_p.add_argument("--base", default="main")
    open_p.add_argument("--brief", type=Path)
    open_p.add_argument("--author")
    open_p.add_argument("--model", help="the author's actual model id, if known")

    verdict_p = sub.add_parser("verdict", help="record a cross-family verdict with findings")
    verdict_p.add_argument("task")
    verdict_p.add_argument("--reviewer", required=True)
    verdict_p.add_argument("--verdict", required=True, choices=["ship", "fix"])
    verdict_p.add_argument("--finding", action="append", default=[], dest="findings")
    verdict_p.add_argument(
        "--model",
        help="the reviewer's actual model id (for OpenCode, its model id); defaults to HX_REVIEWER_MODEL",
    )

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
        if args.cmd == "open":
            review = open_review(args.task, args.repo, args.base, args.brief, args.author, model=args.model)
            print(f"review opened on {args.task}: {review['artifact']['mode']} snapshot, author {review['author']['agent']} ({review['author']['family']})")
            return 0
        if args.cmd == "verdict":
            review = verdict(args.task, args.reviewer, args.verdict, args.findings, model=args.model)
            print(f"verdict recorded on {args.task}: {args.verdict} by {args.reviewer}; {len(review['findings'])} findings")
            return 0
        if args.cmd == "resolve":
            resolve(args.task, args.finding, args.disposition, args.note)
            print(f"finding {args.finding} on {args.task}: {args.disposition}")
            return 0
        report = status(args.task)
        print(json.dumps(report, indent=2) if args.json else _summary(report))
        return 0 if report["state"] != "stale" else 1
    except (ValueError, ledger_mod.TaskNotFound, OSError) as exc:
        print(str(exc), file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
