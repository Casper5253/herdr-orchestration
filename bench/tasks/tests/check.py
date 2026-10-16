"""Hidden acceptance check for the tests task.

Verifies that the model wrote a pytest test file that:
  * passes against the original stats.py, AND
  * fails against at least one of two seeded mutants.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


MUTANTS = {
    # off-by-one in the strict-inequality direction
    "ineq": (
        'abs(x - mean) > 2 * stdev',
        'abs(x - mean) >= 2 * stdev',
    ),
    # population stdev instead of sample stdev
    "bessel": (
        "variance = sum((x - mean) ** 2 for x in xs) / (n - 1)",
        "variance = sum((x - mean) ** 2 for x in xs) / n",
    ),
}


def find_test_files(repo: Path) -> list[Path]:
    return [
        p for p in repo.iterdir()
        if p.suffix == ".py" and p.name.startswith("test_") and p.name != "check.py"
    ]


def run_pytest(repo: Path) -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "-x"],
        cwd=str(repo), capture_output=True, text=True,
    )
    return proc.returncode


def mutate(stats_path: Path, original: str, replacement: str) -> bool:
    text = stats_path.read_text(encoding="utf-8")
    if original not in text:
        return False
    stats_path.write_text(text.replace(original, replacement, 1), encoding="utf-8")
    return True


def main() -> int:
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    test_files = find_test_files(repo)
    if not test_files:
        print("FAIL: no test_*.py file found in repo")
        return 1

    # 1. Run against the original module — must pass.
    rc = run_pytest(repo)
    if rc != 0:
        print(f"FAIL: model tests do not pass on the original stats.py (rc={rc})")
        return 1

    # 2. Run against each mutant; count how many the suite catches.
    caught = 0
    for name, (original, replacement) in MUTANTS.items():
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            for p in repo.iterdir():
                if p.name == "check.py":
                    continue
                target = td_path / p.name
                if p.is_dir():
                    shutil.copytree(p, target)
                else:
                    shutil.copy2(p, target)
            stats_path = td_path / "stats.py"
            if not mutate(stats_path, original, replacement):
                print(f"FAIL: could not apply mutant {name!r} (pattern not found)")
                return 1
            rc = run_pytest(td_path)
            if rc != 0:
                caught += 1

    if caught < 1:
        print(f"FAIL: model tests passed against both mutants ({list(MUTANTS)}) — tests are too weak")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
