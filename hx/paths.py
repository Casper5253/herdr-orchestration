"""hx.paths: root resolution for working from any project repo.

``package_root()`` is the hx install dir (policy.json lives there).
``project_root()`` is the repo the caller works in: HX_PROJECT_ROOT when set,
else ``git rev-parse --show-toplevel`` of the cwd, else the cwd itself. All
``.hx/`` state resolves against it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def project_root() -> Path:
    env = os.environ.get("HX_PROJECT_ROOT")
    if env:
        return Path(env).resolve()
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(Path.cwd()),
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        root = out.decode("utf-8").strip()
        if root:
            return Path(root)
    except Exception:
        pass
    return Path.cwd()


def hx_dir() -> Path:
    return project_root() / ".hx"
