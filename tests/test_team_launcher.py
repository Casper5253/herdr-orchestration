"""Exercise the real launcher with a recording Herdr stub and a temporary home."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_team_launchers_use_the_shared_runtime_policy_resolver() -> None:
    repo = Path(__file__).resolve().parents[1]
    for name in ("Start-HerdrTeam.ps1", "Start-HerdrAppTeam.ps1", "Set-HerdrLead.ps1"):
        text = (repo / "team" / name).read_text(encoding="utf-8")
        assert "Resolve-HxPolicy.ps1" in text
        assert "PYTHONPATH" not in text


def test_runtime_policy_resolver_prefers_the_activated_export(tmp_path):
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is required for the policy resolver")
    repo = Path(__file__).resolve().parents[1]
    home = tmp_path / "home"
    runtime_install = home / ".hx-runtime" / "abc123"
    runtime_install.mkdir(parents=True)
    runtime_policy = runtime_install / "policy.json"
    runtime_policy.write_text('{"persistent_opencode_model":"runtime-model"}', encoding="utf-8")
    (home / ".hx-runtime" / "current").write_text(str(runtime_install) + "\n\n", encoding="utf-8")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts.parent / "policy.json").write_text(
        '{"persistent_opencode_model":"checkout-model"}', encoding="utf-8")
    command = (
        ". '" + str(repo / "team" / "Resolve-HxPolicy.ps1") + "'; "
        "Resolve-HxPolicyPath -ScriptDir '" + str(scripts) + "'"
    )
    result = subprocess.run([
        powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "USERPROFILE": str(home), "HX_RUNTIME_ROOT": str(tmp_path / "wrong-runtime")})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(runtime_policy)


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
    (tmp_path / "DevinModelGate.ps1").write_bytes(
        (repo / "team/DevinModelGate.ps1").read_bytes())
    (tmp_path / "Resolve-HxPolicy.ps1").write_bytes(
        (repo / "team/Resolve-HxPolicy.ps1").read_bytes())
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


def test_devin_launch_refuses_a_model_outside_allowed_models(tmp_path):
    """A policy model id that is not in allowed_models is never launched."""
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
    (tmp_path / "DevinModelGate.ps1").write_bytes(
        (repo / "team/DevinModelGate.ps1").read_bytes())
    (tmp_path / "Resolve-HxPolicy.ps1").write_bytes(
        (repo / "team/Resolve-HxPolicy.ps1").read_bytes())
    (tmp_path / "opencode-trusted-dev.jsonc").write_text("{}", encoding="utf-8")
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({
        "persistent_opencode_model": "deepseek-v4.1-flash",
        "devin": {
            "models": {"medium": "swe-2-high"},
            "allowed_models": ["swe-2-1-xhigh", "swe-2-1-max"],
            "promo": {"ends_at": "2999-01-01T00:00:00Z"},
        },
    }), encoding="utf-8")
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
try { & $Launcher -Project $Project -Devin } catch { Write-Output $_.Exception.Message; exit 1 }
''', encoding="utf-8")
    result = subprocess.run([
        powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner),
        str(project), str(home), str(policy), str(launcher), str(recording),
    ], capture_output=True, text=True, timeout=30)
    calls = [json.loads(line) for line in recording.read_text().splitlines()] if recording.exists() else []
    assert result.returncode == 1
    assert "allowed_models" in result.stdout
    assert calls == []


def test_devin_launch_refuses_an_allowlisted_model_the_live_list_tags_paid(tmp_path):
    """Allowlisted is not enough: the live `devin models list` must tag it Free."""
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
    (tmp_path / "DevinModelGate.ps1").write_bytes(
        (repo / "team/DevinModelGate.ps1").read_bytes())
    (tmp_path / "Resolve-HxPolicy.ps1").write_bytes(
        (repo / "team/Resolve-HxPolicy.ps1").read_bytes())
    (tmp_path / "devin-pane.config.json").write_bytes(
        (repo / "team/devin-pane.config.json").read_bytes())
    (tmp_path / "opencode-trusted-dev.jsonc").write_text("{}", encoding="utf-8")
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({
        "persistent_opencode_model": "deepseek-v4.1-flash",
        "devin": {
            "models": {"medium": "swe-2-1-xhigh"},
            "allowed_models": ["swe-2-1-xhigh", "swe-2-1-max"],
            "promo": {"ends_at": "2999-01-01T00:00:00Z"},
        },
    }), encoding="utf-8")
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
function devin {
    '{"families":[{"variants":[{"model_uid":"swe-2-1-xhigh","cost_tier":"Paid"}]}]}'
    $global:LASTEXITCODE = 0
}
try { & $Launcher -Project $Project -Devin } catch { Write-Output $_.Exception.Message; exit 1 }
''', encoding="utf-8")
    result = subprocess.run([
        powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner),
        str(project), str(home), str(policy), str(launcher), str(recording),
    ], capture_output=True, text=True, timeout=30)
    calls = [json.loads(line) for line in recording.read_text().splitlines()] if recording.exists() else []
    assert result.returncode == 1
    assert "not Free" in result.stdout
    assert calls == []
