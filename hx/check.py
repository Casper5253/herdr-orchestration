"""hx check: run a command and leave a receipt tied to the exact commit.

A receipt is one JSON file under the repo's ``.hx/receipts``. It names the
repo, the HEAD commit and tree, the command, its start and end, its exit code
and the last 40 output lines. A dirty tracked tree is refused because a
receipt must name exact code. A rerun for the same commit and command replaces
its receipt atomically, so a retry converges on the latest result.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from hx import spawn

TAIL_LINES = 40
RECEIPT_VERSION = 1
_RECEIPT_DIR = Path(".hx") / "receipts"


class CheckError(RuntimeError):
    pass


def _git(cwd: Path, *args: str) -> str:
    try:
        completed = spawn.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False,
        )
    except OSError as exc:
        raise CheckError(f"git is not available: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise CheckError(detail or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def repo_root(cwd: Path | None = None) -> Path:
    """The repo root for a command run in ``cwd``."""
    start = Path(cwd) if cwd is not None else Path.cwd()
    return Path(_git(start, "rev-parse", "--show-toplevel")).resolve()


def tracked_changes(root: Path) -> str:
    """Porcelain lines for tracked changes only; untracked receipts do not count."""
    return _git(root, "status", "--porcelain", "--untracked-files=no")


def resolve_commit(root: Path, ref: str) -> str:
    return _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")


def commit_tree(root: Path, commit: str) -> str:
    return _git(root, "rev-parse", f"{commit}^{{tree}}")


def _iso(timestamp: float) -> str:
    return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).isoformat()


def _command_text(command: Sequence[str]) -> str:
    return " ".join(str(part) for part in command)


def _same_command(recorded: str, wanted: str) -> bool:
    return " ".join(recorded.split()) == " ".join(wanted.split())


def _slug(command: Sequence[str]) -> str:
    text = _command_text(command).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:48].strip("-") or "check"
    digest = hashlib.sha256("\0".join(command).encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def receipt_path(root: Path, commit: str, command: Sequence[str]) -> Path:
    return root / _RECEIPT_DIR / f"{commit}-{_slug(command)}.json"


def _write_receipt(root: Path, receipt: dict[str, Any]) -> Path:
    path = receipt_path(root, receipt["commit"], receipt["command"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def _receipt(root: Path, cwd: Path, commit: str, tree: str,
             command: Sequence[str], started: float, ended: float,
             exit_code: int, tail: list[str]) -> dict[str, Any]:
    return {
        "version": RECEIPT_VERSION,
        "repo": str(root),
        "cwd": str(cwd),
        "commit": commit,
        "tree": tree,
        "command": [str(part) for part in command],
        "command_text": _command_text(command),
        "started_at": _iso(started),
        "ended_at": _iso(ended),
        "duration_seconds": round(ended - started, 3),
        "exit_code": exit_code,
        "passed": exit_code == 0,
        "output_tail": tail,
    }


def run(command: Sequence[str], *, cwd: Path | None = None,
        stream: Any = None) -> int:
    """Run one command and write its receipt. Returns the command's exit code."""
    argv = [str(part) for part in command]
    if not argv:
        print("hx check: no command given", file=sys.stderr)
        return 2
    start_dir = Path(cwd) if cwd is not None else Path.cwd()
    output = sys.stdout if stream is None else stream
    try:
        root = repo_root(start_dir)
        dirty = tracked_changes(root)
        if dirty:
            print("hx check: working tree has tracked changes; "
                  "commit or stash them first:", file=sys.stderr)
            for line in dirty.splitlines():
                print(f"  {line}", file=sys.stderr)
            return 1
        commit = _git(root, "rev-parse", "HEAD")
        tree = commit_tree(root, commit)
    except CheckError as exc:
        print(f"hx check: {exc}", file=sys.stderr)
        return 1

    started = time.time()
    tail: list[str] = []
    executable = shutil.which(argv[0])
    launch = [executable or argv[0], *argv[1:]]
    try:
        process = spawn.Popen(
            launch, cwd=str(start_dir), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", bufsize=1,
        )
    except OSError as exc:
        ended = time.time()
        message = f"hx check: could not run {argv[0]}: {exc}"
        print(message, file=sys.stderr)
        path = _write_receipt(root, _receipt(
            root, start_dir, commit, tree, argv, started, ended, 127, [message]))
        print(f"hx check: receipt {path}", file=output)
        return 127

    assert process.stdout is not None
    for line in process.stdout:
        output.write(line)
        output.flush()
        tail.append(line.rstrip("\n"))
        if len(tail) > TAIL_LINES:
            tail.pop(0)
    exit_code = process.wait()
    ended = time.time()
    path = _write_receipt(root, _receipt(
        root, start_dir, commit, tree, argv, started, ended, exit_code, tail))
    print(f"hx check: receipt {path}", file=output)
    return exit_code


def _load_receipts(root: Path) -> list[dict[str, Any]]:
    directory = root / _RECEIPT_DIR
    receipts: list[dict[str, Any]] = []
    if not directory.is_dir():
        return receipts
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            receipts.append(data)
    return receipts


def _recorded_command(receipt: dict[str, Any]) -> str:
    text = receipt.get("command_text")
    if isinstance(text, str):
        return text
    command = receipt.get("command")
    return _command_text(command) if isinstance(command, list) else ""


def verify(commit_ref: str, command: str | None = None, *,
           cwd: Path | None = None) -> int:
    """Print a passing receipt for the commit and command; 0 only when one exists."""
    start_dir = Path(cwd) if cwd is not None else Path.cwd()
    try:
        root = repo_root(start_dir)
        commit = resolve_commit(root, commit_ref)
        tree = commit_tree(root, commit)
    except CheckError as exc:
        print(f"hx check: {exc}", file=sys.stderr)
        return 1

    wanted = command.strip() if command else None
    matches = []
    for receipt in _load_receipts(root):
        if receipt.get("commit") != commit or receipt.get("tree") != tree:
            continue
        if wanted is not None and not _same_command(
                _recorded_command(receipt), wanted):
            continue
        matches.append(receipt)

    passing = [receipt for receipt in matches if receipt.get("exit_code") == 0]
    if passing:
        chosen = max(passing, key=lambda receipt: str(receipt.get("ended_at") or ""))
        print(json.dumps(chosen, indent=2, sort_keys=True))
        return 0

    what = f"commit {commit[:12]}"
    if wanted is not None:
        what += f" and command {wanted}"
    if matches:
        chosen = max(matches, key=lambda receipt: str(receipt.get("ended_at") or ""))
        print(json.dumps(chosen, indent=2, sort_keys=True))
        print(f"hx check: no passing receipt for {what}; "
              f"latest exit code {chosen.get('exit_code')}", file=sys.stderr)
    else:
        print(f"hx check: no receipt for {what}", file=sys.stderr)
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "verify":
        parser = argparse.ArgumentParser(
            prog="hx check verify",
            description="Exit 0 only when a passing receipt exists for the exact commit and command.")
        parser.add_argument("commit", help="Commit or HEAD to verify.")
        parser.add_argument("--command", help="Exact command text recorded in the receipt.")
        parsed = parser.parse_args(args[1:])
        return verify(parsed.commit, parsed.command)
    if args and args[0] == "--":
        command = args[1:]
        if not command:
            print("hx check: no command after --", file=sys.stderr)
            return 2
        return run(command)
    print("usage: hx check -- <command ...>\n"
          "       hx check verify <commit-or-HEAD> [--command <command>]",
          file=sys.stderr)
    return 2