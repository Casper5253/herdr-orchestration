"""A test's git commands can never reach the real repo (a 9 Nov run committed and set user.name there)."""
import subprocess
from pathlib import Path


def test_git_in_a_temp_dir_without_its_own_repo_finds_no_repo(tmp_path: Path):
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(tmp_path), capture_output=True, text=True)
    assert result.returncode != 0, f"git escaped the temp dir into {result.stdout.strip()}"


def test_each_run_has_its_own_basetemp(tmp_path: Path):
    assert tmp_path.parent.name.startswith("run-"), tmp_path
