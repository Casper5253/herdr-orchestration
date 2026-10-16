"""Hidden acceptance check for the bugfix task.

Runs pytest on the edited repo copy given as argv[1]. Exits 0 on pass,
prints a one-line reason on failure.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    cmd = [sys.executable, "-m", "pytest", "-q", "test_dates.py"]
    proc = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True)
    if proc.returncode == 0:
        return 0
    out = (proc.stdout or "") + (proc.stderr or "")
    last = [ln.strip() for ln in out.splitlines() if ln.strip()]
    tail = last[-1] if last else "pytest failed with no output"
    print(f"FAIL: {tail}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
