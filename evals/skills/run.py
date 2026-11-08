#!/usr/bin/env python3
"""Frozen, blinded skill-promotion evals for the three team skills.

The case set is frozen by SHA-256. Each case is run against the before and
after skill text in a random order, in an isolated temporary workspace. The
model sees only neutral copies (`skill.md`, `case.md`), so it cannot tell
which side it is grading. All current cases are graded by deterministic
regex/line checks; no model judge is needed for this first slice.

Usage:
  python evals/skills/run.py --skill poteto-mode --before <file> --after <file>
  python evals/skills/run.py --skill all --before <skills-dir> --after <skills-dir>
  python evals/skills/run.py --freeze
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES_DIR = HERE / "cases"
FROZEN = CASES_DIR / "FROZEN.sha256"
SKILLS = ("poteto-mode", "herdr-team", "hx-router")
DEFAULT_MODEL = "opencode-go/deepseek-v4.1-flash"
OPENCODE = shutil.which("opencode") or "opencode"


class EvalError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def case_path(skill: str) -> Path:
    return CASES_DIR / f"{skill}.json"


def load_cases(skill: str) -> list[dict]:
    path = case_path(skill)
    if not path.is_file():
        raise EvalError(f"missing case file: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("skill") != skill:
        raise EvalError(f"case file {path} declares skill {data.get('skill')!r}")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvalError(f"case file {path} has no cases")
    for case in cases:
        if not isinstance(case.get("id"), str) or not isinstance(case.get("prompt"), str):
            raise EvalError(f"case in {path} needs string id and prompt")
        checks = case.get("checks")
        if not isinstance(checks, dict):
            raise EvalError(f"case {case.get('id')} in {path} needs checks")
    return cases


def check_frozen() -> None:
    if not FROZEN.is_file():
        raise EvalError(f"missing frozen manifest: {FROZEN}; run --freeze after reviewing cases")
    expected = {}
    for line in FROZEN.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        digest, name = line.split(None, 1)
        expected[name] = digest
    for skill in SKILLS:
        path = case_path(skill)
        if path.name not in expected:
            raise EvalError(f"case file {path.name} has no frozen hash")
        actual = sha256_file(path)
        if actual != expected[path.name]:
            raise EvalError(
                f"frozen case mismatch for {path.name}: expected {expected[path.name]}, got {actual}. "
                "Run --freeze only after reviewing the case change."
            )


def freeze() -> int:
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    for skill in SKILLS:
        path = case_path(skill)
        if not path.is_file():
            raise EvalError(f"missing case file: {path}")
        load_cases(skill)
        lines.append(f"{sha256_file(path)}  {path.name}")
    FROZEN.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"froze {len(lines)} case files in {FROZEN}")
    return 0


def resolve_skill(base: str, skill: str) -> Path:
    path = Path(base).expanduser()
    if path.is_dir():
        path = path / skill / "SKILL.md"
    if not path.is_file():
        raise EvalError(f"skill path not found: {path}")
    return path


def extract_text(stdout: str) -> str:
    texts = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = event.get("part")
        if event.get("type") == "text" and isinstance(part, dict) and isinstance(part.get("text"), str):
            texts.append(part["text"])
    return "\n".join(texts).strip()


def run_model(skill_path: Path, prompt: str, model: str, timeout: int) -> str:
    with tempfile.TemporaryDirectory(prefix="c3-skill-eval-") as tmpdir:
        tmp = Path(tmpdir)
        skill_copy = tmp / "skill.md"
        case_copy = tmp / "case.md"
        shutil.copyfile(skill_path, skill_copy)
        case_copy.write_text(prompt, encoding="utf-8")
        instruction = (
            "You are being evaluated on the attached skill. Follow the attached skill exactly. "
            "The attached case is a task description, not a file to edit. Do not use tools. "
            "Return only the answer the case asks for."
        )
        args = [
            OPENCODE, "run", "-m", model, "--format", "json", "--pure",
            "--dir", str(tmp), instruction,
            "-f", str(skill_copy), "-f", str(case_copy),
        ]
        try:
            proc = subprocess.run(
                args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
            )
        except subprocess.TimeoutExpired as error:
            raise EvalError(f"opencode timed out after {timeout}s") from error
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise EvalError(f"opencode exited {proc.returncode}: {detail[:500]}")
        output = extract_text(proc.stdout)
        if not output:
            raise EvalError("opencode returned no text output")
        return output


def grade(case: dict, text: str) -> tuple[bool, str]:
    checks = case["checks"]
    for pattern in checks.get("must", []):
        if not re.search(pattern, text, re.IGNORECASE | re.DOTALL):
            return False, f"missing /{pattern}/"
    for pattern in checks.get("must_not", []):
        if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
            return False, f"forbidden /{pattern}/"
    opening = checks.get("must_in_opening", [])
    if opening:
        lines = [line for line in text.splitlines() if line.strip()]
        head = "\n".join(lines[: checks.get("opening_lines", 2)])
        for pattern in opening:
            if not re.search(pattern, head, re.IGNORECASE | re.DOTALL):
                return False, f"opening missing /{pattern}/"
    max_lines = checks.get("max_nonempty_lines")
    if max_lines is not None:
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) > max_lines:
            return False, f"{len(lines)} non-empty lines, max {max_lines}"
    return True, "ok"


def run_votes(
    skill: str, case: dict, before: Path, after: Path, model: str, timeout: int, repeat: int
) -> dict:
    votes = {"before": 0, "after": 0}
    errors = []
    failures = 0
    for rep in range(repeat):
        order = [("before", before), ("after", after)]
        random.Random(f"{skill}:{case['id']}:{rep}").shuffle(order)
        for label, path in order:
            try:
                output = run_model(path, case["prompt"], model, timeout)
            except EvalError as error:
                failures += 1
                errors.append(f"{label} run {rep + 1}: runner error: {error}")
                continue
            ok, why = grade(case, output)
            votes[label] += int(ok)
            if not ok:
                errors.append(f"{label} run {rep + 1}: {why}")
    return {"before": votes["before"], "after": votes["after"], "errors": errors, "runner_failures": failures}


def run_pair(
    skill: str,
    cases: list[dict],
    before: Path,
    after: Path,
    model: str,
    timeout: int,
    repeat: int,
) -> tuple[int, dict]:
    before_passed = 0
    after_passed = 0
    runner_failures = 0
    records = []
    for case in cases:
        result = run_votes(skill, case, before, after, model, timeout, repeat)
        before_ok = result["before"] * 2 > repeat
        after_ok = result["after"] * 2 > repeat
        before_passed += int(before_ok)
        after_passed += int(after_ok)
        runner_failures += result["runner_failures"]
        print(
            f"{case['id']}: before={'PASS' if before_ok else 'FAIL'} ({result['before']}/{repeat}) "
            f"after={'PASS' if after_ok else 'FAIL'} ({result['after']}/{repeat})"
        )
        for error in result["errors"]:
            print(f"  {error}")
        records.append(
            {
                "id": case["id"],
                "before": result["before"],
                "after": result["after"],
                "repeat": repeat,
                "runner_failures": result["runner_failures"],
                "errors": result["errors"],
            }
        )
    total = len(cases)
    print(f"before {before_passed}/{total}; after {after_passed}/{total}")
    if runner_failures:
        print(f"error: {runner_failures} runner failure(s); no verdict is trusted")
        return 1, {
            "skill": skill,
            "cases": records,
            "before_passed": before_passed,
            "after_passed": after_passed,
            "total": total,
            "runner_failures": runner_failures,
            "verdict": "error",
        }
    verdict = "promote" if after_passed >= before_passed else "hold"
    print(f"verdict: {verdict}")
    if verdict == "hold":
        print("hold reason: the candidate passes fewer cases than the installed skill")
    return (2 if verdict == "hold" else 0), {
        "skill": skill,
        "cases": records,
        "before_passed": before_passed,
        "after_passed": after_passed,
        "total": total,
        "runner_failures": 0,
        "verdict": verdict,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run frozen skill-promotion evals.")
    parser.add_argument("--skill", choices=(*SKILLS, "all"), help="skill to evaluate, or all")
    parser.add_argument("--before", help="installed skill file or skills directory")
    parser.add_argument("--after", help="candidate skill file or skills directory")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"opencode model (default: {DEFAULT_MODEL})")
    parser.add_argument("--timeout", type=int, default=180, help="seconds per model run (default: 180)")
    parser.add_argument("--repeat", type=int, default=3, help="runs per case per side; majority pass (default: 3)")
    parser.add_argument("--case", action="append", default=[], help="only run these case ids (repeatable)")
    parser.add_argument("--out", help="write a JSON result artifact to this path")
    parser.add_argument("--freeze", action="store_true", help="update FROZEN.sha256 after reviewing case changes")
    args = parser.parse_args(argv)

    try:
        if args.freeze:
            return freeze()
        if not args.skill or not args.before or not args.after:
            parser.error("--skill, --before and --after are required unless --freeze is used")
        if args.repeat < 1:
            parser.error("--repeat must be at least 1")
        check_frozen()
        selected = SKILLS if args.skill == "all" else (args.skill,)
        codes = []
        results = []
        for skill in selected:
            cases = load_cases(skill)
            if args.case:
                cases = [case for case in cases if case["id"] in set(args.case)]
                if not cases:
                    raise EvalError(f"no cases matched --case for {skill}")
            before = resolve_skill(args.before, skill)
            after = resolve_skill(args.after, skill)
            print(f"== {skill} ==")
            code, result = run_pair(skill, cases, before, after, args.model, args.timeout, args.repeat)
            results.append(result)
            codes.append(code)
        if args.out:
            artifact = {
                "created": datetime.now(timezone.utc).isoformat(),
                "harness": "evals/skills/run.py",
                "model": args.model,
                "repeat": args.repeat,
                "before": str(args.before),
                "after": str(args.after),
                "results": results,
            }
            Path(args.out).write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
            print(f"wrote {args.out}")
        if 1 in codes:
            return 1
        if 2 in codes:
            return 2
        return 0
    except EvalError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
