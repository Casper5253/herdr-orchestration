"""Benchmark runner: run each model on each task and log a JSON line per run.

Usage:
    python bench/run.py --models m1,m2 --tasks all|t1,t2 [--variant v] [--timeout 600]

For every (model, task) pair it:
  1. Copies bench/tasks/<task>/repo to bench/runs/<stamp>/<model>/<task>/
  2. git init + initial commit in the copy
  3. Runs `opencode run -m opencode-go/<model> --format json --dir <copy>
     "<prompt.md contents>"` with the timeout
  4. Captures the JSON event stream to events.jsonl
  5. Runs the task's check.py against the copy
  6. Appends one JSON line to bench/runs/results.jsonl with model, task,
     pass, reason, latency_s, tokens, cost, tool_calls, tool_errors,
     exit_code.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
TASKS_DIR = ROOT / "tasks"
RUNS_DIR = ROOT / "runs"
RESULTS_PATH = RUNS_DIR / "results.jsonl"

TASK_NAMES = ["bugfix", "feature", "debug", "refactor", "tests"]


# ---------- event stream parsing --------------------------------------------


def parse_events(events_path: Path) -> dict[str, Any]:
    """Aggregate the JSON event stream into the metrics we record.

    Inspects each event at runtime and tolerates missing fields by
    treating them as null / 0. Returns a dict with: total_tokens,
    input_tokens, output_tokens, cache_read, cache_write, cost,
    tool_calls, tool_errors.
    """
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read": 0,
        "cache_write": 0,
        "total_tokens": 0,
        "reasoning_tokens": 0,
        "cost": 0.0,
        "tool_calls": 0,
        "tool_errors": 0,
    }
    if not events_path.exists():
        return totals
    with events_path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = evt.get("type")
            part = evt.get("part") or {}

            if kind == "step_finish":
                tokens = part.get("tokens") or {}
                cache = tokens.get("cache") or {}
                for src, key in (
                    (tokens, "input"),
                    (tokens, "output"),
                    (tokens, "total"),
                    (tokens, "reasoning"),
                    (cache, "read"),
                    (cache, "write"),
                ):
                    val = src.get(key)
                    if isinstance(val, (int, float)):
                        totals[f"{key}_tokens" if key in {"input", "output", "total", "reasoning"} else f"cache_{key}"] += val
                cost = part.get("cost")
                if isinstance(cost, (int, float)):
                    totals["cost"] += float(cost)

            # tool_use events come in two flavours in the stream we saw:
            # `{"type":"tool_use", ...}` and `{"type":"tool", ...}`. The
            # signal we care about lives in `part.tool` + `part.callID`
            # (or `part.state`), so we count anything that looks like a
            # tool invocation.
            part_type = part.get("type")
            if kind in {"tool_use", "tool"} or part_type in {"tool", "tool_use"}:
                totals["tool_calls"] += 1
                state = part.get("state") or {}
                status = state.get("status")
                if status and status != "completed":
                    totals["tool_errors"] += 1
                # some events carry an `error` field directly
                if part.get("error") or state.get("error"):
                    totals["tool_errors"] += 1

    return totals


# ---------- run one model x task --------------------------------------------


def copy_repo(task: str, dest: Path) -> None:
    src = TASKS_DIR / task / "repo"
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)


def git_init(repo: Path) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "bench",
        "GIT_AUTHOR_EMAIL": "bench@local",
        "GIT_COMMITTER_NAME": "bench",
        "GIT_COMMITTER_EMAIL": "bench@local",
    }
    subprocess.run(["git", "init", "-q"], cwd=str(repo), env=env, check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(repo), env=env, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "bench seed"],
        cwd=str(repo), env=env, check=True,
    )


def run_opencode(
    model: str, repo: Path, prompt: str, timeout: int, events_path: Path,
    variant: str | None = None,
) -> tuple[int, str]:
    cmd = [
        "opencode", "run",
        "-m", f"opencode-go/{model}",
        "--format", "json",
        "--dir", str(repo),
    ]
    if variant:
        cmd += ["--variant", variant]
    cmd.append(prompt)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("wb") as out:
        try:
            proc = subprocess.run(
                cmd, stdout=out, stderr=subprocess.PIPE,
                timeout=timeout, check=False,
            )
            return proc.returncode, ""
        except subprocess.TimeoutExpired:
            return 124, f"timeout after {timeout}s"


def run_check(task: str, repo: Path) -> tuple[bool, str, int]:
    check = TASKS_DIR / task / "check.py"
    proc = subprocess.run(
        [sys.executable, str(check), str(repo)],
        capture_output=True, text=True, timeout=120,
    )
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    reason = out or err or f"check exited {proc.returncode}"
    return proc.returncode == 0, reason, proc.returncode


def run_one(
    model: str, task: str, stamp: str, timeout: int, variant: str | None,
) -> dict[str, Any]:
    run_dir = RUNS_DIR / stamp / (f"{model}@{variant}" if variant else model) / task
    copy_repo(task, run_dir)
    git_init(run_dir)

    prompt_text = (TASKS_DIR / task / "prompt.md").read_text(encoding="utf-8")

    events_path = run_dir / "events.jsonl"
    t0 = time.monotonic()
    exit_code, oc_err = run_opencode(model, run_dir, prompt_text, timeout, events_path, variant)
    latency = time.monotonic() - t0

    metrics = parse_events(events_path)
    passed, reason, check_rc = run_check(task, run_dir)

    record = {
        "stamp": stamp,
        "model": model,
        "variant": variant,
        "task": task,
        "pass": passed,
        "reason": reason,
        "latency_s": round(latency, 3),
        "tokens": {
            "total": metrics["total_tokens"],
            "input": metrics["input_tokens"],
            "output": metrics["output_tokens"],
            "reasoning": metrics["reasoning_tokens"],
            "cache_read": metrics["cache_read"],
            "cache_write": metrics["cache_write"],
        },
        "cost": round(metrics["cost"], 6),
        "tool_calls": metrics["tool_calls"],
        "tool_errors": metrics["tool_errors"],
        "exit_code": exit_code,
        "check_exit_code": check_rc,
        "opencode_stderr_tail": oc_err[-200:] if oc_err else "",
    }
    return record


# ---------- top-level --------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--models", required=True, help="comma-separated model names (without opencode-go/ prefix)")
    p.add_argument("--tasks", default="all", help="'all' or comma-separated task names")
    p.add_argument("--variant", default=None)
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--stamp", default=None, help="override the run stamp (default: utc timestamp)")
    args = p.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if args.tasks == "all":
        tasks = TASK_NAMES
    else:
        tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]
    unknown = [t for t in tasks if t not in TASK_NAMES]
    if unknown:
        print(f"unknown task(s): {unknown}; valid: {TASK_NAMES}", file=sys.stderr)
        return 2

    stamp = args.stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"# stamp={stamp} models={models} tasks={tasks} timeout={args.timeout}", flush=True)
    RESULTS_PATH.touch(exist_ok=True)
    with RESULTS_PATH.open("a", encoding="utf-8") as fh:
        for model in models:
            for task in tasks:
                print(f"-> {model} / {task} ...", flush=True)
                record = run_one(model, task, stamp, args.timeout, args.variant)
                fh.write(json.dumps(record) + "\n")
                fh.flush()
                status = "PASS" if record["pass"] else "FAIL"
                print(
                    f"   {status} latency={record['latency_s']}s cost=${record['cost']} "
                    f"tools={record['tool_calls']} errs={record['tool_errors']} "
                    f"reason={record['reason'][:120]}",
                    flush=True,
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
