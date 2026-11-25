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
import time
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
    _git(repo, "checkout", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "seed")
    return repo


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def reviewed_export(tmp_path, monkeypatch):
    """Stub Git's answers and exports without touching the worktree's Git state."""
    source = tmp_path / "source"
    source.mkdir()
    commits = {
        "1" * 40: "STATE_FORMAT = 1\n",
        "2" * 40: "STATE_FORMAT = 1\n",
        "3" * 40: "STATE_FORMAT = 1\n",
        "4" * 40: "",
    }

    def export(repo, commit, staging, root):
        package = staging / "hx"
        package.mkdir()
        (package / "reviewed.txt").write_text("reviewed\n", encoding="utf-8")
        (package / "__init__.py").write_text(commits[commit], encoding="utf-8")
        (package / "__main__.py").write_text("# reviewed\n", encoding="utf-8")

    monkeypatch.delenv("HX_RELEASE_REF", raising=False)
    monkeypatch.setattr(runtime, "_git", lambda repo, *args: args[-1].split("^")[0])
    monkeypatch.setattr(runtime, "_is_ancestor", lambda repo, commit, ref: True)
    monkeypatch.setattr(runtime, "dirty_hx_changes", lambda repo: [])
    monkeypatch.setattr(runtime, "_export", export)
    monkeypatch.setattr(runtime, "_self_check", lambda install, cwd: None)
    return source, tmp_path / "runtime"


def test_team_skills_use_only_the_pinned_hx_entry_point() -> None:
    for name in ("hx-router", "herdr-team"):
        skill = (REPO / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert "python -m hx" not in skill, name


@pytest.mark.parametrize("change", ["changed", "added", "missing"])
@pytest.mark.parametrize("operation", ["activate", "rollback"])
def test_runtime_rejects_a_modified_export(reviewed_export, change, operation) -> None:
    source, root = reviewed_export
    first = runtime.activate("1" * 40, root=root, repo=source)
    active = runtime.activate("2" * 40, root=root, repo=source)
    package = Path(first["activated"]) / "hx"
    if change == "missing":
        (package / "reviewed.txt").unlink()
    else:
        file = "__main__.py" if change == "changed" else "_extra.py"
        (package / file).write_text("# modified\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="export.*differs"):
        if operation == "activate":
            runtime.activate("1" * 40, root=root, repo=source)
        else:
            runtime.rollback(root)
    assert runtime.current_install(root) == Path(active["activated"])
    assert runtime.previous_install(root) == Path(first["activated"])


@pytest.mark.parametrize("operation", ["activate", "rollback"])
def test_failed_current_replacement_preserves_rollback_target(
        reviewed_export, monkeypatch, operation) -> None:
    source, root = reviewed_export
    first = runtime.activate("1" * 40, root=root, repo=source)
    active = runtime.activate("2" * 40, root=root, repo=source)
    replace = os.replace

    def fail_current(src, dst):
        if Path(dst) == root / "current":
            raise OSError("current replacement failed")
        return replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_current)
        with pytest.raises(OSError, match="current replacement failed"):
            if operation == "activate":
                runtime.activate("3" * 40, root=root, repo=source)
            else:
                runtime.rollback(root)

    assert runtime.current_install(root) == Path(active["activated"])
    assert runtime.previous_install(root) == Path(first["activated"])
    assert runtime.rollback(root)["activated"] == first["activated"]


def test_rollback_accepts_old_bytecode_caches(reviewed_export) -> None:
    source, root = reviewed_export
    first = runtime.activate("1" * 40, root=root, repo=source)
    second = runtime.activate("2" * 40, root=root, repo=source)
    package = Path(first["activated"]) / "hx"
    cache = package / "__pycache__"
    cache.mkdir()
    cached = cache / "__main__.cpython-311.pyc"
    standalone = package / "_legacy.pyc"
    for path in (cached, standalone):
        path.write_bytes(b"stale bytecode")

    assert runtime.rollback(root)["activated"] == first["activated"]
    assert runtime.current_install(root) == Path(first["activated"])
    assert runtime.previous_install(root) == Path(second["activated"])
    assert not cache.exists()
    assert not standalone.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows readers deny pointer replacement")
@pytest.mark.parametrize("operation", ["activate", "rollback"])
def test_runtime_retries_when_a_pointer_reader_closes(
        reviewed_export, monkeypatch, operation) -> None:
    source, root = reviewed_export
    first = runtime.activate("1" * 40, root=root, repo=source)
    second = runtime.activate("2" * 40, root=root, repo=source)
    with (root / "current").open(encoding="utf-8") as reader:
        # Release the real Windows handle at the first retry without a timer race.
        monkeypatch.setattr(time, "sleep", lambda delay: reader.close())
        if operation == "activate":
            result = runtime.activate("3" * 40, root=root, repo=source)
            assert result["commit"] == "3" * 40
        else:
            result = runtime.rollback(root)
            assert result["activated"] == first["activated"]
        assert reader.closed

    assert runtime.current_install(root) == Path(result["activated"])
    assert runtime.previous_install(root) == Path(second["activated"])


@pytest.mark.skipif(os.name != "nt", reason="Windows readers deny pointer replacement")
def test_rollback_keeps_both_pointers_when_reader_stays_open(
        reviewed_export, monkeypatch) -> None:
    source, root = reviewed_export
    first = runtime.activate("1" * 40, root=root, repo=source)
    second = runtime.activate("2" * 40, root=root, repo=source)
    delays = []

    def record_delay(delay):
        delays.append(delay)
        assert sum(delays) <= 1.1, "pointer retry exceeded its time budget"

    monkeypatch.setattr(time, "sleep", record_delay)
    with (root / "current").open(encoding="utf-8"):
        with pytest.raises(PermissionError):
            runtime.rollback(root)

    assert 0 < sum(delays) <= 1.1
    assert runtime.current_install(root) == Path(second["activated"])
    assert runtime.previous_install(root) == Path(first["activated"])
    assert not list(root.glob("current.tmp-*"))


@pytest.mark.parametrize("override", ["argument", "environment"])
def test_activate_refuses_an_alternate_release_ref(reviewed_export, monkeypatch, override):
    source, root = reviewed_export
    kwargs = {}
    if override == "argument":
        kwargs["release"] = "side"
    else:
        monkeypatch.setenv("HX_RELEASE_REF", "side")
    with pytest.raises(RuntimeError, match="reviewed release ref"):
        runtime.activate("1" * 40, root=root, repo=source, **kwargs)
    assert runtime.current_install(root) is None


@pytest.mark.parametrize("operation", ["activate-candidate", "activate-current", "rollback"])
def test_runtime_refuses_an_unknown_state_format(reviewed_export, operation) -> None:
    source, root = reviewed_export
    active = runtime.activate("1" * 40, root=root, repo=source)
    if operation == "rollback":
        active = runtime.activate("2" * 40, root=root, repo=source)
        target = runtime.previous_install(root)
    else:
        target = Path(active["activated"])
    if operation != "activate-candidate":
        (target / "hx" / "__init__.py").write_text("# no format\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unknown state format"):
        if operation == "rollback":
            runtime.rollback(root)
        else:
            runtime.activate("4" * 40 if operation == "activate-candidate" else "2" * 40,
                             root=root, repo=source, allow_state_change=True)
    assert runtime.current_install(root) == Path(active["activated"])


def test_activate_refuses_a_commit_off_the_release_ref(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    _git(repo, "checkout", "-b", "side")
    (repo / "hx" / "_side.py").write_text("VALUE = 1\n", encoding="utf-8")
    side = _commit(repo, "side work")
    _git(repo, "checkout", "main")
    with pytest.raises(RuntimeError, match="not on main"):
        runtime.activate(side, root=tmp_path / "rt", repo=repo)
    assert runtime.current_install(tmp_path / "rt") is None


def test_activate_refuses_dirty_tracked_hx_changes(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    with open(repo / "hx" / "__init__.py", "a", encoding="utf-8") as fh:
        fh.write("\n# unreviewed edit\n")
    with pytest.raises(RuntimeError, match="uncommitted tracked hx changes"):
        runtime.activate(root=tmp_path / "rt", repo=repo)


def test_activate_switches_pointer_and_keeps_the_previous_build(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    root = tmp_path / "rt"
    first = _git(repo, "rev-parse", "HEAD")
    result = runtime.activate(root=root, repo=repo)
    install = root / first[:12]
    assert result["activated"] == str(install) and result["switched"] is True
    assert (install / "hx" / "__main__.py").is_file()
    build = json.loads((install / "runtime.json").read_text(encoding="utf-8"))
    assert build["commit"] == first and build["source_repo"] == str(repo.resolve())
    assert runtime.current_install(root) == install

    (repo / "hx" / "_marker.txt").write_text("second\n", encoding="utf-8")
    second = _commit(repo, "second build")
    result = runtime.activate(root=root, repo=repo)
    new_install = root / second[:12]
    assert runtime.current_install(root) == new_install
    assert runtime.previous_install(root) == install
    assert result["previous"] == str(install)

    back = runtime.rollback(root)
    assert back["activated"] == str(install)
    assert runtime.current_install(root) == install
    assert runtime.previous_install(root) == new_install

    again = runtime.activate(root=root, repo=repo)
    assert again["created"] is False and again["switched"] is True
    assert runtime.current_install(root) == new_install
    assert runtime.previous_install(root) == install


def test_activate_refuses_a_state_format_change_unless_allowed(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    root = tmp_path / "rt"
    runtime.activate(root=root, repo=repo)
    init = repo / "hx" / "__init__.py"
    init.write_text(
        init.read_text(encoding="utf-8").replace("STATE_FORMAT = 1", "STATE_FORMAT = 2"),
        encoding="utf-8")
    _commit(repo, "state format 2")
    with pytest.raises(RuntimeError, match="state format changes 1->2"):
        runtime.activate(root=root, repo=repo)
    result = runtime.activate(
        root=root, repo=repo, allow_state_change=True)
    assert result["state_format"] == 2
    back = runtime.rollback(root)
    assert back["state_format"] == 1
    assert "state format changes 2->1" in back["warning"]


def test_doctor_reports_runtime_drift_informationally(tmp_path, monkeypatch) -> None:
    repo = _scratch_repo(tmp_path)
    root = tmp_path / "rt"
    result = runtime.activate(root=root, repo=repo)
    install = Path(result["activated"])
    (repo / "hx" / "_marker.txt").write_text("ahead of the build\n", encoding="utf-8")
    _commit(repo, "advance main")
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


def test_migrates_legacy_pointer_files_with_one_replacement(reviewed_export) -> None:
    source, root = reviewed_export
    first = runtime.activate("1" * 40, root=root, repo=source)
    second = runtime.activate("2" * 40, root=root, repo=source)
    (root / "current").write_text(second["activated"] + "\n", encoding="utf-8")
    (root / "previous").write_text(first["activated"] + "\n", encoding="utf-8")

    assert runtime.rollback(root)["activated"] == first["activated"]
    assert runtime.current_install(root) == Path(first["activated"])
    assert runtime.previous_install(root) == Path(second["activated"])


def test_new_shim_loads_the_activated_commit_not_a_dirty_checkout(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    root = tmp_path / "rt"
    runtime.activate(root=root, repo=repo)

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
    assert "-I -B -c" in cmd_text and "-I -B -c" in sh_text
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

    git_bash = Path(shutil.which("git")).resolve().parent.parent / "bin" / "bash.exe"
    if git_bash.is_file():
        env = dict(os.environ, HX_RUNTIME_ROOT=root.as_posix(), PYTHONPATH=str(repo))
        posix = subprocess.run(
            [str(git_bash), (home / ".local" / "bin" / "hx").as_posix(), "--version"],
            cwd=str(repo), env=env, capture_output=True, text=True, timeout=120)
        assert posix.returncode == 0, posix.stderr
        assert posix.stdout.strip() == "hx 0.1.0"
    assert not list(runtime.current_install(root).rglob("__pycache__"))


def test_both_shims_follow_the_current_build_after_activation_and_rollback(
        tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path)
    root = tmp_path / "runtime with spaces"
    first = runtime.activate(root=root, repo=repo)
    init = repo / "hx" / "__init__.py"
    init.write_text(
        init.read_text(encoding="utf-8").replace('"0.1.0"', '"0.1.1"'),
        encoding="utf-8")
    second = runtime.activate(_commit(repo, "second runtime"), root=root, repo=repo)
    assert second["previous"] == first["activated"]

    home = tmp_path / "home"
    home.mkdir()
    installed = _install_shims(home)
    assert installed.returncode == 0, installed.stderr
    git_bash = Path(shutil.which("git")).resolve().parent.parent / "bin" / "bash.exe"
    shell_shim = home / ".local" / "bin" / "hx"
    env = dict(os.environ, HX_RUNTIME_ROOT=root.as_posix(), PYTHONPATH=str(repo))
    env.pop("HX_RUNTIME_DIR", None)

    def check_shims(version: str) -> None:
        posix = subprocess.run(
            [str(git_bash), shell_shim.as_posix(), "--version"],
            cwd=repo, env=env, capture_output=True, text=True, timeout=120)
        windows = _run_new_shim(home, root, repo, repo)
        for result in (posix, windows):
            assert result.returncode == 0, result.stderr
            assert result.stdout.strip() == f"hx {version}"

    check_shims("0.1.1")
    assert runtime.rollback(root)["activated"] == first["activated"]
    check_shims("0.1.0")

    pointer = root / "current"
    for ending in ("", "\n", "\r\n"):
        pointer.write_bytes((first["activated"] + ending).encode("utf-8"))
        check_shims("0.1.0")
    pointer.write_bytes(f"\n{second['activated']}\n".encode("utf-8"))
    assert _run_new_shim(home, root, repo, repo).returncode != 0
