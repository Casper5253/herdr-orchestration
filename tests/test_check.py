"""Q-843: hx check leaves and verifies receipts tied to exact code.

Q-1206: a dirty run names the working tree, and verification matches the tree.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from hx import check


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, text=True, capture_output=True)


def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / ".gitignore").write_text(".hx/\n", encoding="utf-8")
    (root / "a.txt").write_text("one\n", encoding="utf-8")
    git(root, "add", ".gitignore", "a.txt")
    git(root, "commit", "-q", "-m", "first")
    return root


def receipts(root):
    return sorted((root / ".hx" / "receipts").glob("*.json"))


def head(root):
    return git(root, "rev-parse", "HEAD").stdout.strip()


def tree(root):
    return git(root, "rev-parse", "HEAD^{tree}").stdout.strip()


def test_dirty_tree_receipt_verifies_after_committing_exact_files(tmp_path):
    root = repo(tmp_path)
    original = head(root)
    command = [sys.executable, "-c", "print('dirty receipt')"]
    (root / "a.txt").write_text("changed\n", encoding="utf-8")
    (root / "new.txt").write_text("new\n", encoding="utf-8")

    assert check.run(command, cwd=root) == 0

    files = receipts(root)
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["dirty"] is True
    assert data["commit"] == original
    assert data["base_commit"] == original
    assert data["tree"] != tree(root)
    assert files[0].name.startswith(data["tree"])

    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "dirty state")
    assert check.verify("HEAD", " ".join(command), cwd=root) == 0


def test_a_subset_commit_does_not_verify_and_says_the_trees_differ(tmp_path, capsys):
    root = repo(tmp_path)
    command = [sys.executable, "-c", "print('dirty receipt')"]
    (root / "a.txt").write_text("changed\n", encoding="utf-8")
    (root / "new.txt").write_text("new\n", encoding="utf-8")
    assert check.run(command, cwd=root) == 0

    git(root, "add", "a.txt")
    git(root, "commit", "-q", "-m", "subset")
    capsys.readouterr()
    assert check.verify("HEAD", " ".join(command), cwd=root) == 1
    assert "trees differ" in capsys.readouterr().err


def test_an_untracked_file_is_part_of_the_dirty_tree(tmp_path):
    root = repo(tmp_path)
    command = [sys.executable, "-c", "print('new file')"]
    (root / "new.txt").write_text("new\n", encoding="utf-8")

    assert check.run(command, cwd=root) == 0

    data = json.loads(receipts(root)[0].read_text(encoding="utf-8"))
    assert data["dirty"] is True
    assert data["tree"] != tree(root)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "new file")
    assert check.verify("HEAD", " ".join(command), cwd=root) == 0


def test_a_dirty_run_leaves_the_real_index_unchanged(tmp_path):
    root = repo(tmp_path)
    command = [sys.executable, "-c", "print('dirty receipt')"]
    (root / "a.txt").write_text("changed\n", encoding="utf-8")
    (root / "new.txt").write_text("new\n", encoding="utf-8")
    assert git(root, "diff", "--cached").stdout == ""

    assert check.run(command, cwd=root) == 0

    assert git(root, "diff", "--cached").stdout == ""
    assert "a.txt" in git(root, "status", "--porcelain").stdout
    assert "new.txt" in git(root, "status", "--porcelain").stdout


def test_receipt_fields_and_exit_code_passthrough(tmp_path):
    root = repo(tmp_path)
    passing = [sys.executable, "-c", "print('hello receipt')"]
    assert check.run(passing, cwd=root) == 0

    files = receipts(root)
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["repo"] == str(root.resolve())
    assert data["commit"] == head(root)
    assert data["base_commit"] == head(root)
    assert data["tree"] == tree(root)
    assert data["dirty"] is False
    assert data["command"] == passing
    assert data["command_text"] == " ".join(passing)
    assert data["started_at"]
    assert data["ended_at"]
    assert data["duration_seconds"] >= 0
    assert data["exit_code"] == 0
    assert data["passed"] is True
    assert "hello receipt" in data["output_tail"]

    failing = [sys.executable, "-c", "import sys; sys.exit(7)"]
    assert check.run(failing, cwd=root) == 7
    failed = next(
        json.loads(path.read_text(encoding="utf-8")) for path in receipts(root)
        if json.loads(path.read_text(encoding="utf-8"))["command"] == failing)
    assert failed["exit_code"] == 7
    assert failed["passed"] is False


def test_receipt_keeps_the_last_forty_output_lines(tmp_path):
    root = repo(tmp_path)
    command = [sys.executable, "-c", "for i in range(50): print(i)"]
    assert check.run(command, cwd=root) == 0

    data = json.loads(receipts(root)[0].read_text(encoding="utf-8"))
    assert len(data["output_tail"]) == 40
    assert data["output_tail"][0] == "10"
    assert data["output_tail"][-1] == "49"


def test_verify_matches_commit_and_command(tmp_path, capsys):
    root = repo(tmp_path)
    command = [sys.executable, "-c", "print('verified')"]
    check.run(command, cwd=root)
    capsys.readouterr()

    assert check.verify("HEAD", " ".join(command), cwd=root) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["commit"] == head(root)
    assert printed["command"] == command


def test_verify_without_a_command_accepts_any_passing_receipt(tmp_path):
    root = repo(tmp_path)
    check.run([sys.executable, "-c", "print('ok')"], cwd=root)
    assert check.verify("HEAD", cwd=root) == 0


def test_verify_requires_the_exact_command(tmp_path):
    root = repo(tmp_path)
    check.run([sys.executable, "-c", "print('ok')"], cwd=root)
    assert check.verify("HEAD", "a different command", cwd=root) == 1


def test_stale_commit_fails_verify(tmp_path):
    root = repo(tmp_path)
    old = head(root)
    command = [sys.executable, "-c", "print('ok')"]
    check.run(command, cwd=root)
    (root / "a.txt").write_text("two\n", encoding="utf-8")
    git(root, "add", "a.txt")
    git(root, "commit", "-q", "-m", "second")

    assert check.verify("HEAD", " ".join(command), cwd=root) == 1
    assert check.verify(old, " ".join(command), cwd=root) == 0


def test_failed_run_fails_verify(tmp_path, capsys):
    root = repo(tmp_path)
    command = [sys.executable, "-c", "import sys; sys.exit(3)"]
    assert check.run(command, cwd=root) == 3
    capsys.readouterr()

    assert check.verify("HEAD", " ".join(command), cwd=root) == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["exit_code"] == 3


def test_no_receipt_says_what_is_missing(tmp_path, capsys):
    root = repo(tmp_path)

    assert check.verify("HEAD", "npm run check", cwd=root) == 1
    assert "no receipt" in capsys.readouterr().err


def test_a_rerun_replaces_the_receipt(tmp_path):
    root = repo(tmp_path)
    command = [sys.executable, "-c", "print('ok')"]
    assert check.run(command, cwd=root) == 0
    assert check.run(command, cwd=root) == 0

    assert len(receipts(root)) == 1
    assert check.verify("HEAD", " ".join(command), cwd=root) == 0

def test_windows_command_shim_is_resolved(tmp_path, monkeypatch):
    if os.name != "nt":
        return
    root = repo(tmp_path)
    shim = root / "tool.cmd"
    shim.write_text("@echo off\r\necho shim-ok\r\n", encoding="ascii")
    git(root, "add", "tool.cmd")
    git(root, "commit", "-q", "-m", "shim")
    monkeypatch.setenv("PATH", str(root) + os.pathsep + os.environ.get("PATH", ""))

    assert check.run(["tool"], cwd=root) == 0

    data = json.loads(receipts(root)[0].read_text(encoding="utf-8"))
    assert data["command"] == ["tool"]
    assert "shim-ok" in data["output_tail"]
    assert check.verify("HEAD", "tool", cwd=root) == 0
