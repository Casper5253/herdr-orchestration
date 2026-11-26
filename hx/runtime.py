"""hx.runtime: pin the live hx code to a reviewed commit.

The shims used to put the source checkout on PYTHONPATH, so a worker edit
there ran on every agent's next hx call, before review. This module owns the
replacement boundary.

Threat model: every agent runs as one Windows user and can edit files in that
home folder, so hx cannot stop a hostile agent. This guard is against
accidents: a well-meaning worker's uncommitted or unreviewed edits reaching
the team's hx. Spoofing a pane id or planting a fake hc on PATH is out of
scope.

* ``activate`` checks that the commit is on ``main`` and the checkout has no
  uncommitted tracked hx changes. It exports the commit and self-checks a new
  install or compares an existing one to the export before switching.
* ``rollback`` verifies the previous build and switches back to it. The active
  and previous pointers change together.
* ``status`` reports the loaded commit and install path for ``hx doctor``.

Project state still lives in ``<repo>/.hx/`` and the shared state dirs: only
the code location changes. The exported tree carries only ``hx/``, ``policy.json`` and
``team/``: the runtime code plus the resources ``paths.package_root()`` resolves.
Other tracked or untracked checkout content never enters the runtime.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

from hx import host, paths, usage
from hx.ledger import LeaseLock

CURRENT = "current"
LEGACY_PREVIOUS = "previous"
RELEASE_REF = "refs/remotes/origin/main"
RELEASE_NAME = "origin/main"
BUILD_FILE = "runtime.json"
HX_PATHS = ("hx", "policy.json")
EXPORT_PATHS = ("hx", "hooks", "policy.json", "team")


def runtime_root() -> Path:
    env = os.environ.get("HX_RUNTIME_ROOT")
    return Path(env) if env else Path.home() / ".hx-runtime"


def _install_path(value: str) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if (path / "hx" / "__main__.py").is_file() else None


def _read_installs(root: Path) -> tuple[Path | None, Path | None]:
    try:
        lines = (root / CURRENT).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    if len(lines) == 2:
        return _install_path(lines[0]), _install_path(lines[1])
    if len(lines) > 2:
        raise RuntimeError(f"invalid runtime pointer at {root / CURRENT}")
    try:
        previous = (root / LEGACY_PREVIOUS).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        previous = ""
    return _install_path(lines[0] if lines else ""), _install_path(previous)


def _switch_installs(root: Path, install: Path, previous: Path | None) -> None:
    pointer = root / CURRENT
    tmp = pointer.with_name(f"{pointer.name}.tmp-{os.getpid()}")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{install}\n{previous or ''}\n")
        # Windows readers without delete sharing can briefly block replacement.
        for delay in (0.05, 0.1, 0.2, 0.3, 0.35, None):
            try:
                os.replace(tmp, pointer)
                break
            except PermissionError as exc:
                if delay is None or getattr(exc, "winerror", None) not in (5, 32):
                    raise
                time.sleep(delay)
    finally:
        tmp.unlink(missing_ok=True)


def current_install(root: Path | None = None) -> Path | None:
    return _read_installs(Path(root) if root is not None else runtime_root())[0]


def previous_install(root: Path | None = None) -> Path | None:
    return _read_installs(Path(root) if root is not None else runtime_root())[1]


def read_build(install: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((Path(install) / BUILD_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _git(repo: Path | str, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=60, env=env)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout.strip()


def _caller_member() -> host.Member | None:
    """The verified team member running this process, or None outside a team."""
    detected = host.detect()
    return detected.own() if detected is not None else None


def _require_lead_terminal(action: str, policy: dict[str, Any] | None = None) -> None:
    lead_role = str((policy or {}).get("lead_role", "lead"))
    try:
        member = _caller_member()
    except Exception as exc:
        raise RuntimeError(
            f"refusing: only the team lead terminal may {action} the hx runtime; "
            "caller identity could not be verified") from exc
    role = str(member.get("role") or "") if isinstance(member, dict) else ""
    team = str(member.get("team") or "") if isinstance(member, dict) else ""
    if member is None or not team:
        raise RuntimeError(
            f"refusing: only the team lead terminal may {action} the hx runtime; "
            "the caller is not a team terminal")
    if role != lead_role:
        raise RuntimeError(
            f"refusing: only the team lead terminal may {action} the hx runtime; "
            f"caller role is {role or 'unknown'}")


def _policy_from_commit(source: Path, commit: str) -> dict[str, Any]:
    try:
        text = _git(source, "show", f"{commit}:policy.json")
        data = json.loads(text)
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def _fetch_release(source: Path) -> None:
    _git(source, "fetch", "--no-tags", "origin", "main")


def _is_ancestor(repo: Path | str, commit: str, ref: str) -> bool:
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, ref],
        cwd=str(repo), capture_output=True, text=True, timeout=60)
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    detail = (proc.stderr or proc.stdout).strip()
    raise RuntimeError(f"git merge-base failed for {ref}: {detail}")


def source_repo(explicit: str | Path | None = None) -> Path:
    """The git checkout a commit is exported from.

    Explicit argument, then HX_SOURCE_REPO, then this package's own checkout
    (the bootstrap case), then the origin recorded in the build metadata.
    """
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("HX_SOURCE_REPO")
    if env:
        return Path(env).resolve()
    pkg = paths.package_root()
    if (pkg / ".git").exists():
        return pkg.resolve()
    build = read_build(pkg)
    if build and build.get("source_repo"):
        return Path(str(build["source_repo"])).resolve()
    raise RuntimeError(
        "no source checkout found: pass --source, set HX_SOURCE_REPO, "
        "or activate from a build that records its origin")


def dirty_hx_changes(repo: Path) -> list[str]:
    """Tracked, uncommitted changes to the hx runtime (untracked files are not
    exported by the commit, so they cannot go live through activation)."""
    out = _git(repo, "status", "--porcelain", "--untracked-files=no", "--", *HX_PATHS)
    return [line for line in out.splitlines() if line.strip()]


def _read_state_format(install: Path) -> int:
    try:
        text = (install / "hx" / "__init__.py").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        text = ""
    matches = re.findall(r"^STATE_FORMAT[ \t]*=[ \t]*([0-9]+)[ \t]*(?:#[^\n]*)?$",
                         text, re.MULTILINE)
    if len(matches) != 1:
        raise RuntimeError(f"refusing: unknown state format in {install}")
    return int(matches[0])


def _self_check(install: Path, cwd: Path) -> None:
    """Import the exported build in an isolated interpreter before switching.

    ``-I`` ignores an inherited PYTHONPATH and the cwd, so only the export can
    satisfy the imports. cwd is a neutral dir, never the source checkout.
    """
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    env["HX_RUNTIME_DIR"] = str(install)
    code = (
        "import os, sys\n"
        "sys.path.insert(0, os.environ['HX_RUNTIME_DIR'])\n"
        "import hx, hx.__main__, hx.doctor, hx.usage\n"
        "hx.usage.load_policy()\n"
        "print(hx.__version__)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code], cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"self-check failed for {install}: {detail}")


def _export(repo: Path, commit: str, staging: Path, root: Path) -> None:
    archive = root / f".archive-{os.getpid()}.tar"
    try:
        _git(repo, "archive", "--format=tar", f"--output={archive}", commit, "--", *EXPORT_PATHS)
        with tarfile.open(archive) as tar:
            try:
                tar.extractall(staging, filter="data")
            except TypeError:  # Python < 3.11.4 has no extraction filter
                tar.extractall(staging)
    finally:
        archive.unlink(missing_ok=True)


def _export_files(
    directory: Path, caches: list[Path] | None = None,
) -> dict[str, tuple[str, str]]:
    """Inventory the export without following symlinks or directory junctions."""
    files = {}
    for path in directory.iterdir():
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            files[path.name] = ("link", os.readlink(path))
        elif getattr(info, "st_reparse_tag", 0):
            raise RuntimeError(f"refusing: unexpected reparse point in export: {path}")
        elif stat.S_ISDIR(info.st_mode):
            if caches is not None and path.name == "__pycache__":
                caches.append(path)
                continue
            files[path.name] = ("directory", "")
            files.update((f"{path.name}/{name}", entry)
                         for name, entry in _export_files(path, caches).items())
        elif stat.S_ISREG(info.st_mode):
            if caches is not None and path.suffix == ".pyc":
                caches.append(path)
                continue
            files[path.name] = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            raise RuntimeError(f"refusing: unexpected file type in export: {path}")
    return files


def _verify_export(install: Path, reviewed: Path) -> None:
    if install.is_symlink() or getattr(install.lstat(), "st_reparse_tag", 0):
        raise RuntimeError(f"refusing: export {install} differs from the reviewed commit")
    caches: list[Path] = []
    actual = _export_files(install, caches)
    # Activation generates this metadata after exporting the commit.
    metadata = actual.pop(BUILD_FILE, None)
    if (metadata is None or metadata[0] != "file"
            or actual != _export_files(reviewed)):
        raise RuntimeError(f"refusing: export {install} differs from the reviewed commit")
    # -B prevents writing bytecode, but Python can still load existing caches.
    for path in caches:
        if path.name == "__pycache__":
            shutil.rmtree(path)
        else:
            path.unlink()


def _release_head(repo: Path) -> str:
    try:
        return _git(repo, "rev-parse", "--verify", "--end-of-options",
                    f"{RELEASE_REF}^{{commit}}")
    except RuntimeError as exc:
        raise RuntimeError(
            "refusing: origin/main is not fetched. Fetch the reviewed main, "
            "then activate.") from exc


def _require_reviewed_commit(source: Path, full: str) -> None:
    if not _is_ancestor(source, full, RELEASE_REF):
        raise RuntimeError(
            f"refusing: {full[:12]} is not on origin/main. "
            "Activate only a reviewed commit fetched from GitHub.")


def activate(commit: str | None = None, root: str | Path | None = None,
             repo: str | Path | None = None, release: str | None = None,
             allow_state_change: bool = False) -> dict[str, Any]:
    root_path = (Path(root) if root is not None else runtime_root()).resolve()
    _require_lead_terminal("activate")
    if release not in (None, RELEASE_NAME, RELEASE_REF) or os.environ.get("HX_RELEASE_REF"):
        raise RuntimeError("refusing: reviewed release ref is fixed to origin/main")
    source = source_repo(repo)
    _fetch_release(source)
    full = _git(source, "rev-parse", "--verify", "--end-of-options",
                f"{commit or RELEASE_REF}^{{commit}}")
    _require_reviewed_commit(source, full)
    _require_lead_terminal("activate", _policy_from_commit(source, full))

    short = full[:12]
    install = root_path / short
    with LeaseLock(root_path / "activation.lock"):
        current, _ = _read_installs(root_path)
        with tempfile.TemporaryDirectory(prefix=".staging-", dir=root_path) as scratch:
            staging = Path(scratch) / "export"
            staging.mkdir()
            _export(source, full, staging, root_path)
            candidate_format = _read_state_format(staging)
            if current is not None:
                current_format = _read_state_format(current)
                if current_format != candidate_format and not allow_state_change:
                    raise RuntimeError(
                        f"refusing: state format changes {current_format}->{candidate_format}. "
                        "Review state compatibility, then pass --allow-state-change.")

            created = not install.exists()
            if created:
                build = {
                    "commit": full,
                    "short": short,
                    "source_repo": str(source),
                    "release_ref": RELEASE_NAME,
                    "state_format": candidate_format,
                    "lock_first": True,
                    "created": dt.datetime.now(dt.timezone.utc).isoformat(),
                }
                (staging / BUILD_FILE).write_text(
                    json.dumps(build, indent=2) + "\n", encoding="utf-8")
                _self_check(staging, root_path)
                os.replace(staging, install)
            else:
                _verify_export(install, staging)
                build = read_build(install)
                if (not build or build.get("commit") != full
                        or build.get("state_format") != candidate_format):
                    raise RuntimeError(f"refusing: {install} holds a different build")

            switched = current is None or current.resolve() != install.resolve()
            if switched:
                _switch_installs(root_path, install, current)

    return {
        "activated": str(install),
        "commit": full,
        "short": short,
        "state_format": candidate_format,
        "created": created,
        "switched": switched,
        "previous": str(current) if switched and current is not None else None,
    }


def rollback(root: str | Path | None = None) -> dict[str, Any]:
    _require_lead_terminal("roll back")
    root_path = (Path(root) if root is not None else runtime_root()).resolve()
    with LeaseLock(root_path / "activation.lock"):
        current, previous = _read_installs(root_path)
        if previous is None:
            raise RuntimeError("refusing: no previous build recorded in the runtime root")

        current_format = _read_state_format(current) if current else None
        previous_format = _read_state_format(previous)
        build = read_build(previous) or {}
        full = build.get("commit")
        origin = build.get("source_repo")
        if (not isinstance(full, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", full)
                or not isinstance(origin, str) or not origin):
            raise RuntimeError(f"refusing: missing build identity in {previous}")
        source = Path(origin)
        if build.get("lock_first") is not True:
            raise RuntimeError("refusing: rollback target predates the first lock-first build")
        _fetch_release(source)
        _require_reviewed_commit(source, full)
        _require_lead_terminal("roll back", _policy_from_commit(source, full))
        with tempfile.TemporaryDirectory(prefix=".staging-", dir=root_path) as scratch:
            reviewed = Path(scratch) / "export"
            reviewed.mkdir()
            _export(source, full, reviewed, root_path)
            _verify_export(previous, reviewed)
        _switch_installs(root_path, previous, current)

    result = {
        "activated": str(previous),
        "commit": full,
        "short": full[:12],
        "state_format": previous_format,
        "previous": str(current) if current is not None else None,
    }
    if current_format is not None and current_format != previous_format:
        result["warning"] = (
            f"state format changes {current_format}->{previous_format} on rollback. "
            "Review state compatibility.")
    return result


def status(package_root: str | Path | None = None) -> dict[str, Any]:
    """Loaded commit and install path for the doctor. Never raises."""
    pkg = Path(package_root) if package_root is not None else paths.package_root()
    try:
        build = read_build(pkg)
    except Exception:
        build = None
    mode = "runtime" if build else "source"
    loaded = (build or {}).get("commit")
    repo: str | None = None
    if build and build.get("source_repo"):
        repo = str(build["source_repo"])
    elif (pkg / ".git").exists():
        repo = str(pkg)
    ref = RELEASE_NAME
    head: str | None = None
    if repo:
        try:
            head = _git(repo, "rev-parse", "--verify", f"{RELEASE_REF}^{{commit}}")
        except Exception:
            head = None
        if loaded is None:
            try:
                loaded = _git(repo, "rev-parse", "HEAD")
            except Exception:
                pass
    if loaded and head:
        state = "drift" if loaded != head else "ok"
    else:
        state = "unknown"
    return {
        "mode": mode,
        "install_path": str(pkg),
        "loaded_commit": loaded,
        "source_repo": repo,
        "release_ref": ref,
        "head_commit": head,
        "status": state,
    }
