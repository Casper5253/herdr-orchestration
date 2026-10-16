"""Summarise bench/runs/results.jsonl as a markdown report.

Per model, prints: pass rate, median latency, total cost, total tool
errors. Also prints a per-task pass/fail matrix.

Usage:
    python bench/report.py [--in bench/runs/results.jsonl] [--out bench/runs/report.md]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_IN = ROOT / "runs" / "results.jsonl"
DEFAULT_OUT = ROOT / "runs" / "report.md"

TASK_NAMES = ["bugfix", "feature", "debug", "refactor", "tests"]


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def median(xs: list[float]) -> float:
    return float(statistics.median(xs)) if xs else 0.0


def fmt_money(x: float) -> str:
    return f"${x:,.4f}"


def fmt_pct(num: int, den: int) -> str:
    return f"{(100.0 * num / den):.0f}%" if den else "n/a"


def build_report(records: list[dict]) -> str:
    if not records:
        return "# Benchmark report\n\n_no runs recorded yet._\n"

    models = sorted({r["model"] for r in records})
    tasks = [t for t in TASK_NAMES if t in {r["task"] for r in records}]

    lines: list[str] = []
    lines.append("# OpenCode-Go model benchmark")
    lines.append("")
    lines.append(f"Runs: **{len(records)}**  •  Models: **{len(models)}**  •  Tasks: **{len(tasks)}**")
    lines.append("")

    # per-model summary
    lines.append("## Per-model summary")
    lines.append("")
    lines.append("| model | pass rate | median latency | total cost | tool errors | runs |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for m in models:
        ms = [r for r in records if r["model"] == m]
        passed = sum(1 for r in ms if r["pass"])
        lines.append(
            f"| `{m}` | {fmt_pct(passed, len(ms))} ({passed}/{len(ms)}) | "
            f"{median([r['latency_s'] for r in ms]):.1f}s | "
            f"{fmt_money(sum(r['cost'] for r in ms))} | "
            f"{sum(r['tool_errors'] for r in ms)} | {len(ms)} |"
        )
    lines.append("")

    # per-task pass matrix
    lines.append("## Pass/fail by task")
    lines.append("")
    header = "| model | " + " | ".join(tasks) + " | total |"
    sep = "| --- |" + "|".join([" --- "] * (len(tasks) + 1)) + "|"
    lines.append(header)
    lines.append(sep)
    for m in models:
        cells = []
        passed_total = 0
        for t in tasks:
            tr = [r for r in records if r["model"] == m and r["task"] == t]
            if not tr:
                cells.append("—")
                continue
            ok = sum(1 for r in tr if r["pass"])
            passed_total += ok
            cells.append(f"{ok}/{len(tr)}")
        total_runs = sum(1 for r in records if r["model"] == m)
        lines.append(f"| `{m}` | " + " | ".join(cells) + f" | {passed_total}/{total_runs} |")
    lines.append("")

    # per-task latency / cost
    lines.append("## Per-task latency (median) and cost (total)")
    lines.append("")
    lines.append("| model | " + " | ".join(f"{t} lat" for t in tasks) + " | " + " | ".join(f"{t} cost" for t in tasks) + " |")
    lines.append("| --- |" + "|".join([" --- "] * (2 * len(tasks))) + "|")
    for m in models:
        lat_cells = []
        cost_cells = []
        for t in tasks:
            tr = [r for r in records if r["model"] == m and r["task"] == t]
            lat_cells.append(f"{median([r['latency_s'] for r in tr]):.1f}s" if tr else "—")
            cost_cells.append(fmt_money(sum(r['cost'] for r in tr)) if tr else "—")
        lines.append(f"| `{m}` | " + " | ".join(lat_cells) + " | " + " | ".join(cost_cells) + " |")
    lines.append("")

    # failure reasons
    failures = [r for r in records if not r["pass"]]
    if failures:
        lines.append("## Failure reasons")
        lines.append("")
        for r in failures:
            reason = (r.get("reason") or "").replace("\n", " ")
            if len(reason) > 200:
                reason = reason[:200] + "…"
            lines.append(f"- `{r['model']}` / `{r['task']}` — {reason}")
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="src", default=str(DEFAULT_IN))
    p.add_argument("--out", default=str(DEFAULT_OUT))
    args = p.parse_args()

    records = load(Path(args.src))
    md = build_report(records)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
