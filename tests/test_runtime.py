"""The pinned runtime: activation gates, pointer switch, rollback, doctor status,
and the proof that the new shim ignores a dirty source checkout and an inherited
PYTHONPATH. Every repo here is a scratch copy; the real checkout is never touched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from hx import doctor, runtime

REPO = Path(__file__).resolve().parent.parent
INSTALL = REPO / "install.ps1"
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
COMSPEC = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def _scratch_repo(base: Path) -> Path:
    repo = base / "src"
    shutil.copytree(
        REPO, repo,
        ignore=shutil.ignore_patterns(
            ".git", ".hx", ".pytest_cache", "__pycache__", "*.pyc",
            "node_modules", "vendor", "runs"))
    _git(repo, "init")
    # A copied worktree .git file would make git operate on the real checkout.
    assert Path(_git(repo, "rev-parse", "--show-toplevel")).resolve() == repo.resolve()
    _git(repo, "config", "user.email", "scratch@example.com")
    _git(repo, "config", "user.name", "scratch")
    _git(repo, "checkout", "-b", "rel")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "seed")
    return repo


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def test_activate_refuses_a_commit_off_the_release_ref(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    _git(repo, "checkout", "-b", "side")
    (repo / "hx" / "_side.py").write_text("VALUE = 1\n", encoding="utf-8")
    side = _commit(repo, "side work")
    _git(repo, "checkout", "rel")
    with pytest.raises(RuntimeError, match="not on rel"):
        runtime.activate(side, root=tmp_path / "rt", repo=repo, release="rel")
    assert runtime.current_install(tmp_path / "rt") is None


def test_activate_refuses_dirty_tracked_hx_changes(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    with open(repo / "hx" / "__init__.py", "a", encoding="utf-8") as fh:
        fh.write("\n# unreviewed edit\n")
    with pytest.raises(RuntimeError, match="uncommitted tracked hx changes"):
        runtime.activate(root=tmp_path / "rt", repo=repo, release="rel")


def test_activate_switches_pointer_and_keeps_the_previous_build(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    root = tmp_path / "rt"
    first = _git(repo, "rev-parse", "HEAD")
    result = runtime.activate(root=root, repo=repo, release="rel")
    install = root / first[:12]
    assert result["activated"] == str(install) and result["switched"] is True
    assert (install / "hx" / "__main__.py").is_file()
    build = json.loads((install / "runtime.json").read_text(encoding="utf-8"))
    assert build["commit"] == first and build["source_repo"] == str(repo.resolve())
    assert runtime.current_install(root) == install

    (repo / "hx" / "_marker.txt").write_text("second\n", encoding="utf-8")
    second = _commit(repo, "second build")
    result = runtime.activate(root=root, repo=repo, release="rel")
    new_install = root / second[:12]
    assert runtime.current_install(root) == new_install
    assert runtime.previous_install(root) == install
    assert result["previous"] == str(install)

    back = runtime.rollback(root)
    assert back["activated"] == str(install)
    assert runtime.current_install(root) == install
    assert runtime.previous_install(root) == new_install


def test_activate_refuses_a_state_format_change_unless_allowed(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    root = tmp_path / "rt"
    runtime.activate(root=root, repo=repo, release="rel")
    init = repo / "hx" / "__init__.py"
    init.write_text(
        init.read_text(encoding="utf-8").replace("STATE_FORMAT = 1", "STATE_FORMAT = 2"),
        encoding="utf-8")
    _commit(repo, "state format 2")
    with pytest.raises(RuntimeError, match="state format changes 1->2"):
        runtime.activate(root=root, repo=repo, release="rel")
    result = runtime.activate(
        root=root, repo=repo, release="rel", allow_state_change=True)
    assert result["state_format"] == 2
    back = runtime.rollback(root)
    assert back["state_format"] == 1
    assert "state format changes 2->1" in back["warning"]


def test_doctor_reports_runtime_drift_informationally(tmp_path, monkeypatch) -> None:
    repo = _scratch_repo(tmp_path)
    root = tmp_path / "rt"
    result = runtime.activate(root=root, repo=repo, release="rel")
    install = Path(result["activated"])
    (repo / "hx" / "_marker.txt").write_text("ahead of the build\n", encoding="utf-8")
    _commit(repo, "advance rel")
    status = runtime.status(install)
    assert status["mode"] == "runtime"
    assert status["status"] == "drift" and status["loaded_commit"] == result["commit"]

    monkeypatch.setattr(doctor, "_check_workers", lambda policy, roots: {})
    monkeypatch.setattr(doctor, "_check_members",
                        lambda policy, roots, now: {
                            "status": "unknown", "reason": "none", "members": {}})
    monkeypatch.setattr(doctor, "_scan_prose", lambda policy, roots: [])
    monkeypatch.setattr(doctor, "_installed_files", lambda roots: [])
    report = doctor.doctor({}, {"repo": install})
    assert report["runtime"]["status"] == "drift"
    assert report["drift"] is False
    assert "informational" in doctor._summary(report)


def _run_old_shim(repo: Path, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo)
    env.pop("HX_RUNTIME_DIR", None)
    env.pop("HX_RUNTIME_ROOT", None)
    return subprocess.run(
        [sys.executable, "-m", "hx", "--version"], cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=120)


def _install_shims(home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(INSTALL), "-HomeDir", str(home)],
        capture_output=True, text=True, timeout=180)


def _run_new_shim(home: Path, root: Path, repo: Path, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo)
    env["HX_RUNTIME_ROOT"] = str(root)
    env.pop("HX_RUNTIME_DIR", None)
    shim = home / ".local" / "bin" / "hx.cmd"
    return subprocess.run(
        [COMSPEC, "/c", str(shim), "--version"], cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=120)


def test_new_shim_loads_the_activated_commit_not_a_dirty_checkout(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    root = tmp_path / "rt"
    runtime.activate(root=root, repo=repo, release="rel")

    init = repo / "hx" / "__init__.py"
    init.write_text(
        init.read_text(encoding="utf-8").replace('__version__ = "0.1.0"',
                                                 '__version__ = "DIRTY-SENTINEL"'),
        encoding="utf-8")

    old = _run_old_shim(repo, repo)
    assert old.returncode == 0, old.stderr
    assert "DIRTY-SENTINEL" in old.stdout

    home = tmp_path / "home"
    home.mkdir()
    installed = _install_shims(home)
    assert installed.returncode == 0, installed.stderr
    cmd_text = (home / ".local" / "bin" / "hx.cmd").read_text(encoding="utf-8")
    sh_text = (home / ".local" / "bin" / "hx").read_text(encoding="utf-8")
    assert "-I -c" in cmd_text and "-I -c" in sh_text
    assert str(repo) not in cmd_text and str(repo) not in sh_text

    from_repo = _run_new_shim(home, root, repo, repo)
    assert from_repo.returncode == 0, from_repo.stderr
    assert "hx 0.1.0" in from_repo.stdout
    assert "DIRTY-SENTINEL" not in from_repo.stdout

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    from_elsewhere = _run_new_shim(home, root, repo, elsewhere)
    assert from_elsewhere.returncode == 0, from_elsewhere.stderr
    assert "hx 0.1.0" in from_elsewhere.stdout
    assert "DIRTY-SENTINEL" not in from_elsewhere.stdout
