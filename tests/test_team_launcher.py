"""Exercise the real launcher with a recording Herdr stub and a temporary home."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("model", ["deepseek-v4.1-flash", "another-policy-model", None, "opencode-go/wrong"])
def test_opencode_launch_uses_policy_over_stale_overlay(tmp_path, model):
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is required for the team launcher")
    repo = Path(__file__).resolve().parents[1]
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    launcher = tmp_path / "Start-HerdrTeam.ps1"
    launcher.write_bytes((repo / "team/Start-HerdrTeam.ps1").read_bytes())
    (tmp_path / "opencode-trusted-dev.jsonc").write_text(
        '{"model":"opencode-go/qwen3.7-plus"}', encoding="utf-8")
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"persistent_opencode_model": model}), encoding="utf-8")
    recording = tmp_path / "calls.jsonl"
    runner = tmp_path / "run.ps1"
    runner.write_text(r'''
param($Project, $HomePath, $PolicyPath, $Launcher, $Recording)
$env:HERDR_ENV = '1'
$env:USERPROFILE = $HomePath
$env:HX_POLICY_PATH = $PolicyPath
$global:LASTEXITCODE = 0
function herdr {
    ConvertTo-Json -Compress -InputObject @($args) | Add-Content -LiteralPath $Recording
    '{"result":{"root_pane":{"pane_id":"fake-lead"},"pane":{"pane_id":"fake-worker"}}}'
}
try { & $Launcher -Project $Project } catch { Write-Output $_.Exception.Message; exit 1 }
''', encoding="utf-8")
    result = subprocess.run([
        powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner),
        str(project), str(home), str(policy), str(launcher), str(recording),
    ], capture_output=True, text=True, timeout=30)
    calls = [json.loads(line) for line in recording.read_text().splitlines()] if recording.exists() else []
    if model is None or "/" in model:
        assert result.returncode == 1
        assert "policy.persistent_opencode_model" in result.stdout
        assert calls == []
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        start = next(c for c in calls if c[:3] == ["agent", "start", "dev-opencode"])
        # PowerShell consumes -- for a function stub, while native commands receive it.
        assert start[-2:] == ["-m", "opencode-go/" + model]
