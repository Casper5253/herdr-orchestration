"""install.ps1 against scratch homes: the Codex SessionStart hook merge must be
idempotent, keep existing hooks.json entries, refuse unparseable JSON, and a
-DryRun must print but change nothing. Nothing here touches the real profile.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
INSTALL = REPO / "install.ps1"
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"


def _install(home: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(INSTALL), "-HomeDir", str(home), *extra],
        capture_output=True, text=True, timeout=120)


def _hooks(home: Path) -> dict:
    return json.loads((home / ".codex" / "hooks.json").read_text(encoding="utf-8"))


def _binding_blocks(hooks: dict) -> list:
    return [b for b in hooks["hooks"]["SessionStart"]
            if any("codex_session_binding.py" in (h.get("command") or "")
                   for h in b.get("hooks", []))]


@pytest.fixture
def home(tmp_path: Path) -> Path:
    home = tmp_path / "profile"
    home.mkdir()
    return home


def test_fresh_home_installs_the_binding_entry(home: Path) -> None:
    result = _install(home)
    assert result.returncode == 0, result.stderr
    (block,) = _binding_blocks(_hooks(home))
    assert block["matcher"] == "startup|resume|clear|compact"
    command = block["hooks"][0]["command"]
    assert "codex_session_binding.py" in command and command.startswith("python")
    assert block["hooks"][0]["timeout"] == 10
    # The rest of the install still ran into the scratch home.
    assert (home / ".herdr-team" / "Start-HerdrAppTeam.ps1").exists()
    assert (home / ".local" / "bin" / "hx.cmd").exists()


def test_existing_entries_are_kept_and_ours_appended_once(home: Path) -> None:
    hooks = home / ".codex" / "hooks.json"
    hooks.parent.mkdir(parents=True)
    hooks.write_text(json.dumps({"hooks": {"SessionStart": [
        {"matcher": "startup", "hooks": [{"type": "command", "command": "agentmem record"}]},
        {"matcher": "resume", "hooks": [{"type": "command", "command": "herdr bind"}]},
    ]}}), encoding="utf-8")
    assert _install(home).returncode == 0
    data = _hooks(home)
    session_start = data["hooks"]["SessionStart"]
    assert len(session_start) == 3
    assert [b["hooks"][0]["command"] for b in session_start[:2]] == [
        "agentmem record", "herdr bind"]
    assert len(_binding_blocks(data)) == 1
    # The change was backed up next to the file.
    assert list(hooks.parent.glob("hooks.json.bak-*"))


def test_rerun_is_a_noop_for_hooks_json(home: Path) -> None:
    assert _install(home).returncode == 0
    hooks = home / ".codex" / "hooks.json"
    first = hooks.read_text(encoding="utf-8")
    backups = set(hooks.parent.glob("hooks.json.bak-*"))
    result = _install(home)
    assert result.returncode == 0
    assert hooks.read_text(encoding="utf-8") == first
    assert set(hooks.parent.glob("hooks.json.bak-*")) == backups
    assert "already present" in result.stdout


def test_unparseable_hooks_json_is_refused_untouched(home: Path) -> None:
    hooks = home / ".codex" / "hooks.json"
    hooks.parent.mkdir(parents=True)
    hooks.write_text("{ not json !!!", encoding="utf-8")
    result = _install(home)
    assert result.returncode != 0
    assert "refusing" in result.stderr + result.stdout
    assert hooks.read_text(encoding="utf-8") == "{ not json !!!"
    assert not list(hooks.parent.glob("hooks.json.bak-*"))


def test_dry_run_prints_and_changes_nothing(home: Path) -> None:
    result = _install(home, "-DryRun")
    assert result.returncode == 0, result.stderr
    assert "would install" in result.stdout
    assert "SessionStart binding" in result.stdout
    assert any("would install" in line and "hx(.cmd)" in line
               for line in result.stdout.splitlines())
    assert not any(line.startswith("installed") for line in result.stdout.splitlines())
    assert list(home.rglob("*")) == []
