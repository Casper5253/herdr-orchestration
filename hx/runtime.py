"""hx.runtime: pin the live hx code to a reviewed commit.

The shims used to put the source checkout on PYTHONPATH, so a worker edit
there ran on every agent's next hx call, before review. This module owns the
replacement boundary:

* ``activate`` refuses a commit that is not on the release ref (``main`` by
  default) or a checkout with uncommitted tracked hx changes, exports that
  commit's tree into a versioned install dir under the runtime root, self-checks
  the import from there, then atomically switches the ``current`` pointer.
* ``rollback`` switches the pointer back to the previous build, which is kept.
* ``status`` reports the loaded commit and install path for ``hx doctor``.

Project state still lives in ``<repo>/.hx/`` and the shared state dirs: only
the code location changes. The exported tree carries ``policy.json`` and
``team/`` because ``paths.package_root()`` resolves resources against it.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

from hx import paths

CURRENT = "current"
PREVIOUS = "previous"
BUILD_FILE = "runtime.json"
HX_PATHS = ("hx", "policy.json")


def runtime_root() -> Path:
    env = os.environ.get("HX_RUNTIME_ROOT")
    return Path(env) if env else Path.home() / ".hx-runtime"


def release_ref() -> str:
    return os.environ.get("HX_RELEASE_REF") or "main"


def _read_pointer(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _write_pointer(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(value + "\n", encoding="utf-8")
    os.replace(tmp, path)


def current_install(root: Path | None = None) -> Path | None:
    root = Path(root) if root is not None else runtime_root()
    value = _read_pointer(root / CURRENT)
    if value is None:
        return None
    path = Path(value)
    return path if (path / "hx" / "__main__.py").is_file() else None


def previous_install(root: Path | None = None) -> Path | None:
    root = Path(root) if root is not None else runtime_root()
    value = _read_pointer(root / PREVIOUS)
    if value is None:
        return None
    path = Path(value)
    return path if (path / "hx" / "__main__.py").is_file() else None


def read_build(install: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((Path(install) / BUILD_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _git(repo: Path | str, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout.strip()


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


def _read_state_format(install: Path) -> int | None:
    try:
        text = (Path(install) / "hx" / "__init__.py").read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"^STATE_FORMAT\s*=\s*(\d+)", text, re.MULTILINE)
    return int(match.group(1)) if match else None


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
        [sys.executable, "-I", "-c", code], cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"self-check failed for {install}: {detail}")


def _export(repo: Path, commit: str, staging: Path, root: Path) -> None:
    archive = root / f".archive-{os.getpid()}.tar"
    try:
        _git(repo, "archive", "--format=tar", f"--output={archive}", commit)
        with tarfile.open(archive) as tar:
            try:
                tar.extractall(staging, filter="data")
            except TypeError:  # Python < 3.11.4 has no extraction filter
                tar.extractall(staging)
    finally:
        archive.unlink(missing_ok=True)


def activate(commit: str | None = None, root: str | Path | None = None,
             repo: str | Path | None = None, release: str | None = None,
             allow_state_change: bool = False) -> dict[str, Any]:
    root_path = Path(root) if root is not None else runtime_root()
    source = source_repo(repo)
    ref = release or release_ref()
    full = _git(source, "rev-parse", "--verify", f"{commit or ref}^{{commit}}")
    if not _is_ancestor(source, full, ref):
        raise RuntimeError(
            f"refusing: {full[:12]} is not on {ref}; activate only a reviewed commit")
    dirty = dirty_hx_changes(source)
    if dirty:
        raise RuntimeError(
            "refusing: uncommitted tracked hx changes in the checkout: " + ", ".join(dirty))

    short = full[:12]
    install = root_path / short
    root_path.mkdir(parents=True, exist_ok=True)
    current = current_install(root_path)

    staging: Path | None = None
    if install.exists():
        build = read_build(install)
        if not build or build.get("commit") != full:
            raise RuntimeError(f"refusing: {install} holds a different build")
        candidate_format = build.get("state_format")
    else:
        staging = root_path / f".staging-{short}-{os.getpid()}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        try:
            _export(source, full, staging, root_path)
            candidate_format = _read_state_format(staging)
            build = {
                "commit": full,
                "short": short,
                "source_repo": str(source),
                "release_ref": ref,
                "state_format": candidate_format,
                "created": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
            (staging / BUILD_FILE).write_text(
                json.dumps(build, indent=2) + "\n", encoding="utf-8")
            _self_check(staging, root_path)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    if current is not None and current.resolve() != install.resolve():
        current_format = (read_build(current) or {}).get("state_format")
        if (isinstance(current_format, int) and isinstance(candidate_format, int)
                and current_format != candidate_format and not allow_state_change):
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
            raise RuntimeError(
                f"refusing: state format changes {current_format}->{candidate_format}; "
                "review state compatibility, then pass --allow-state-change")

    created = staging is not None
    if staging is not None:
        os.replace(staging, install)

    switched = False
    if current is None or current.resolve() != install.resolve():
        if current is not None:
            _write_pointer(root_path / PREVIOUS, str(current))
        _write_pointer(root_path / CURRENT, str(install))
        switched = True

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
    root_path = Path(root) if root is not None else runtime_root()
    current = current_install(root_path)
    previous = previous_install(root_path)
    if previous is None:
        raise RuntimeError("refusing: no previous build recorded in the runtime root")
    if current is not None:
        _write_pointer(root_path / PREVIOUS, str(current))
    _write_pointer(root_path / CURRENT, str(previous))
    build = read_build(previous) or {}
    result = {
        "activated": str(previous),
        "commit": build.get("commit"),
        "short": build.get("short"),
        "state_format": build.get("state_format"),
        "previous": str(current) if current is not None else None,
    }
    current_format = (read_build(current) or {}).get("state_format") if current else None
    previous_format = build.get("state_format")
    if (isinstance(current_format, int) and isinstance(previous_format, int)
            and current_format != previous_format):
        result["warning"] = (
            f"state format changes {current_format}->{previous_format} on rollback; "
            "review state compatibility")
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
    ref = str((build or {}).get("release_ref") or release_ref())
    head: str | None = None
    if repo:
        try:
            head = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
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
