"""Live acceptance checks for the Devin SWE-2 worker (real CLI, real model).

    python -I tests/live_devin_acceptance.py [--skip-live]

Builds a disposable git repo under a temp dir and checks, in order: CLI
discoverable and logged in, pinned SWE-2 ids offered as Free, paid and
non-SWE-2 models refused before launch, an ended promotion refused by ocrun
and skipped by route, a small task implemented and verified on swe-2-high,
a denied git push classified as a permission failure, and a timeout that
kills the run. Live runs use SWE-2 only (free during the promotion) and take
about a minute. Prints one PASS/FAIL line per check; exit 1 on any FAIL.
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hx import devin, ocrun, route  # noqa: E402
from hx.usage import load_policy, usage_snapshot  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def scratch_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".hx/\n__pycache__/\n", encoding="utf-8")
    git = ["git", "-c", "user.email=hx@local", "-c", "user.name=hx"]
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(git + ["add", "."], cwd=repo, check=True)
    subprocess.run(git + ["commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-live", action="store_true", help="only the checks that start no model run")
    args = parser.parse_args()
    policy = load_policy()
    bin_path = devin.devin_bin(policy)
    check("devin CLI found", bin_path is not None, str(bin_path))
    if bin_path is None:
        return 1
    auth = subprocess.run([str(bin_path), "auth", "status"], capture_output=True, text=True, timeout=60)
    check("devin logged in", auth.returncode == 0 and "Logged in" in auth.stdout)
    variants, error = devin.list_models(bin_path)
    check("models list readable", variants is not None, error or "")
    for model in devin.allowed_models(policy):
        tier = (variants or {}).get(model, {}).get("cost_tier")
        check(f"{model} offered as Free", tier == devin.FREE_TIER, f"cost_tier={tier}")

    with tempfile.TemporaryDirectory(prefix="hx-devin-") as tmp:
        repo = scratch_repo(Path(tmp))
        log = Path(tmp) / "routing.jsonl"

        def run(prompt: str, model: str = "swe-2-high", pol=None, timeout: float = 600, title=None):
            return ocrun.run_devin(prompt, model, repo, timeout=timeout, title=title, routing_log=log, policy=pol or policy)

        for model in ("swe-2-high-lightning", "swe-1-7", "claude-opus-5-5"):
            result = run("say hi", model=model)
            check(f"{model} refused before launch", result["failure"]["kind"] == "policy" and result["latency_s"] < 5)

        expired = copy.deepcopy(policy)
        expired["devin"]["promo"]["ends_at"] = "2026-01-01T00:00:00Z"
        result = run("say hi", pol=expired)
        check("ended promotion refused by ocrun", result["failure"]["kind"] == "policy", result["failure"]["reason"])
        snap = usage_snapshot(policy=expired)
        decision = route.route({"kind": "feature"}, snap, expired)
        check("ended promotion skipped by route", decision["worker"] != "devin", f"-> {decision['worker']} {decision['model']}")

        live = usage_snapshot(policy=policy)
        decision = route.route({"kind": "feature"}, live, policy)
        check("route picks devin for a feature", decision["worker"] == "devin", f"-> {decision['model']}")

        if args.skip_live:
            return report()

        result = run(
            "calc.py has a bug: add() subtracts. Fix it and add mul(a, b). Add test_calc.py with plain asserts "
            "run by `python test_calc.py`. Run it.",
            title="small-task",
        )
        verify = subprocess.run([sys.executable, "test_calc.py"], cwd=repo, capture_output=True, text=True)
        check("small task ok", result["ok"], json.dumps(result["failure"]))
        check("model verified swe-2-high", result["models_seen"] == ["swe-2-high"], str(result["models_seen"]))
        check("lead's own test run passes", verify.returncode == 0, verify.stdout.strip() or verify.stderr.strip())
        check("final message returned", bool(result.get("final_message")))

        result = run("Run exactly: git push origin master. Report the result.", model="swe-2-medium", title="deny")
        failure = result["failure"] or {}
        check("git push denied and classified", "permission denied" in failure.get("reason", ""), json.dumps(failure))

        result = run(
            'Run: python -c "import time; time.sleep(300)" and then report done.',
            model="swe-2-medium",
            timeout=30,
            title="timeout",
        )
        failure = result["failure"] or {}
        check("timeout kills the run", failure.get("reason") == "timeout", json.dumps(failure))

        outcomes = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        check("outcomes logged with provider", all(o.get("provider") == "devin" for o in outcomes), f"{len(outcomes)} lines")
    return report()


def report() -> int:
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
