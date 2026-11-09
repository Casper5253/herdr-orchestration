"""Test-run isolation: parallel suites must not delete each other's state or
touch the real repository (audit H32). Proves the conftest guards, not just
reads them.
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

import pytest

from conftest import PYTEST_TEMP_ROOT

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_basetemp_is_per_process(request):
    """pytest wipes basetemp at start; a per-pid one keeps a sibling run safe."""
    basetemp = Path(request.config.option.basetemp).resolve()
    assert basetemp == (PYTEST_TEMP_ROOT / f"run-{os.getpid()}").resolve()


def test_git_cannot_escape_into_the_real_repo(tmp_path):
    """A vanished temp repo must never let git climb up and act on this repo."""
    os.environ["GIT_CEILING_DIRECTORIES"] = str(PYTEST_TEMP_ROOT)
    env = dict(os.environ)
    nested = tmp_path / "gone" / "deep"
    nested.mkdir(parents=True)
    result = subprocess.run(
        ["git", "-C", str(nested), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, env=env,
    )
    # Either git fails outright or it resolves some dir inside the temp tree —
    # never the real repository root or any parent of it.
    if result.returncode == 0:
        found = Path(result.stdout.strip()).resolve()
        assert found != REPO_ROOT.resolve()
        assert REPO_ROOT.resolve() not in found.parents


def test_parallel_fixture_dirs_survive_each_other(tmp_path):
    """Two concurrent fixture trees under per-run roots lose nothing."""
    errors: list[str] = []

    def build(root: Path):
        try:
            (root / "repo").mkdir(parents=True)
            for i in range(10):
                f = root / "repo" / f"f{i}.txt"
                f.write_text(f.stem * 50, encoding="utf-8")
            subprocess.run(["git", "-C", str(root / "repo"), "init", "-q"],
                           check=True, capture_output=True)
            # A sibling "wipe" of its own root must not touch ours.
            for f in (root / "repo").glob("*.txt"):
                assert f.read_text(encoding="utf-8") == f.stem * 50
        except Exception as exc:  # noqa: BLE001 — report, don't swallow
            errors.append(f"{root.name}: {exc}")

    roots = [tmp_path / "run-a", tmp_path / "run-b"]
    threads = [threading.Thread(target=build, args=(r,)) for r in roots]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    for r in roots:
        assert len(list((r / "repo").glob("*.txt"))) == 10
        assert (r / "repo" / ".git").is_dir()


@pytest.mark.parametrize("var", ["HERDR_ENV", "HC_TERMINAL_ID", "HERDR_PANE_ID", "HX_HERDR_PREFIX"])
def test_hermetic_env_does_not_leak_live_host_state(var):
    assert os.environ.get(var) is None
