"""Hidden acceptance check for the debug task.

Runs the script against sample.json and requires it to exit non-zero
with a clear ValueError mentioning a missing required field, and to
print none of the report body lines.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def main() -> int:
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    proc = subprocess.run(
        [sys.executable, "pipeline.py", "sample.json"],
        cwd=str(repo), capture_output=True, text=True,
    )
    if proc.returncode == 0:
        print("FAIL: script exited 0; expected a loud error")
        return 1
    combined = proc.stdout + proc.stderr
    if "ValueError" not in combined:
        print(f"FAIL: expected a ValueError, got: {combined[-400:]}")
        return 1
    if not re.search(r"(missing|required|id)", combined, re.IGNORECASE):
        print(f"FAIL: error message does not name the missing field: {combined[-400:]}")
        return 1
    if "alpha" in proc.stdout or "gamma" in proc.stdout:
        print(f"FAIL: script printed report lines before raising: {proc.stdout!r}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
