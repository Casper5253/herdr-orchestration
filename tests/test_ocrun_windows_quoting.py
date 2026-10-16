"""Windows PowerShell 5.1 quoting for visible runs, exercised with real powershell.exe."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from hx import ocrun

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell 5.1 behaviour")

ARGV_DUMP = "import json, sys; open(sys.argv[1], 'w', encoding='utf-8').write(json.dumps(sys.argv[2:]))"


def _spaced(tmp_path: Path) -> Path:
    path = tmp_path / "dir with spaces"
    path.mkdir()
    return path


@pytest.mark.parametrize(
    "prompt",
    [
        'Heading "Aggregated asset allocation" then text',
        'path C:\\a b\\ and "quoted \\" part" end',
        "ends with a backslash \\",
        "single 'quotes' and $dollar `backtick",
    ],
)
def test_prompt_arg_reaches_native_program_as_one_argument(tmp_path: Path, prompt: str) -> None:
    root = _spaced(tmp_path)
    prompt_path = root / "prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    out = root / "argv.json"
    script = root / "run.ps1"
    script.write_text(
        "& '%s' -c \"%s\" '%s' %s" % (sys.executable, ARGV_DUMP, out, ocrun._prompt_arg(prompt_path)),
        encoding="utf-8",
    )
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)], check=True, timeout=60)
    assert json.loads(out.read_text(encoding="utf-8")) == [prompt]


def test_launch_visible_runs_a_script_whose_path_has_spaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _spaced(tmp_path)
    marker = root / "ran.txt"
    script = root / "run.ps1"
    script.write_text("Set-Content -LiteralPath '%s' -Value ok\n[Environment]::Exit(0)" % marker, encoding="utf-8")
    real_popen = subprocess.Popen

    def hidden_and_waiting(argv: list[str], **kwargs: object) -> subprocess.Popen:
        argv = list(argv)
        argv[-1] += " -WindowStyle Hidden -Wait"
        return real_popen(argv, **kwargs)

    monkeypatch.setattr(ocrun.subprocess, "Popen", hidden_and_waiting)
    assert ocrun.launch_visible(script)
    assert marker.read_text(encoding="utf-8").strip() == "ok"


def test_codex_run_script_puts_pinned_codex_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pinned = _spaced(tmp_path)
    monkeypatch.setattr(ocrun, "codex_bin_dir", lambda: pinned)
    files = ocrun.write_codex_run_script(tmp_path / "run", "brief", "gpt-6-luna", "low", "t", tmp_path)
    lines = files["script"].read_text(encoding="utf-8").splitlines()
    assert lines[2] == "$env:PATH = '%s' + ';' + $env:PATH" % pinned
    assert lines[3].startswith("& codex exec")
