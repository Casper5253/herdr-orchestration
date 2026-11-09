"""Set-HerdrLead.ps1: the lead seat handover against a recording hc stub.

F07/review finding 2: the old lead's family role comes from status.app, the
whole plan validates before the first label change, and a stale raw name is
reported so a launcher rerun still works.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNNER = r"""
param($PolicyPath, $Script, $Recording, $HcLsFile, $To, $Team = 'dev', [switch]$Dry)
$env:HX_POLICY_PATH = $PolicyPath
$global:LASTEXITCODE = 0
function hc {
    ('[' + (($args | ForEach-Object { ConvertTo-Json -Compress $_ }) -join ',') + ']') |
        Add-Content -LiteralPath $Recording
    if ($args.Count -ge 1 -and $args[0] -eq 'ls') { Get-Content -Raw $HcLsFile; return }
    if ($args[0] -eq 'get') {
        $roster = Get-Content -Raw $HcLsFile | ConvertFrom-Json
        $addr = $args[1]
        $roster.terminals | Where-Object { $_.address -eq $addr } |
            ConvertTo-Json -Depth 20 -Compress
        return
    }
    if ($args[0] -eq 'label') {
        $roster = Get-Content -Raw $HcLsFile | ConvertFrom-Json
        $addr = $args[1]
        $t = $roster.terminals | Where-Object { $_.address -eq $addr }
        foreach ($kv in @($args | Select-Object -Skip 2)) {
            $pair = $kv -split '=', 2
            if (-not $t.labels) { $t | Add-Member labels ([pscustomobject]@{}) }
            if ($t.labels.PSObject.Properties[$pair[0]]) { $t.labels.($pair[0]) = $pair[1] }
            else { $t.labels | Add-Member $pair[0] $pair[1] }
        }
        $roster | ConvertTo-Json -Depth 20 | Set-Content $HcLsFile
        return
    }
}
try { & $Script -To $To -Team $Team -DryRun:$Dry } catch { Write-Output $_.Exception.Message; exit 1 }
"""


def _powershell():
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("PowerShell is required for the lead handover")
    return exe


def _member(address, role=None, app="codex", name=None, state="live", team="dev"):
    return {
        "address": address, "name": name, "state": state, "cwd": "C:\\repo",
        "labels": ({"team": team, "role": role} if role else {}),
        "status": ({"app": app, "state": "done"} if app else None),
        "argv": [],
    }


class Stage:
    def __init__(self, tmp_path):
        self.roster = tmp_path / "hc-ls.json"
        self.recording = tmp_path / "calls.jsonl"
        policy = tmp_path / "policy.json"
        policy.write_text(json.dumps({"lead_role": "lead"}), encoding="utf-8")
        runner = tmp_path / "run.ps1"
        runner.write_text(RUNNER, encoding="utf-8")
        self._args = [
            _powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner),
            str(policy), str(REPO / "team" / "Set-HerdrLead.ps1"), str(self.recording),
            str(self.roster)]

    def run(self, terminals, to, team="dev", dry=False):
        self.roster.write_text(json.dumps({"terminals": terminals}), encoding="utf-8")
        if self.recording.exists():
            self.recording.unlink()
        args = list(self._args) + ["-To", to, "-Team", team]
        if dry:
            args.append("-Dry")
        proc = subprocess.run(args, capture_output=True, text=True, timeout=60)
        calls = (
            [json.loads(line) for line in self.recording.read_text().splitlines()]
            if self.recording.exists() else [])
        labels = {t["address"]: (t.get("labels") or {})
                  for t in json.loads(self.roster.read_text(encoding="utf-8-sig"))["terminals"]}
        return proc, calls, labels


@pytest.fixture
def stage(tmp_path):
    return Stage(tmp_path)


def test_handover_to_a_worker_demotes_by_status_app(stage):
    """The old lead's family comes from status.app, and the target keeps its raw name."""
    roster = [
        _member("t23", role="lead", app="claude-code", name=None),
        _member("t24", role="codex", app="codex", name="dev-codex"),
        _member("t9", role="devin", app="devin", name="dev-devin"),
    ]
    proc, calls, labels = stage.run(roster, "t24")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert labels["t23"]["role"] == "claude"
    assert labels["t24"]["role"] == "lead"
    assert labels["t24"]["team"] == "dev"
    label_calls = [c for c in calls if c[0] == "label"]
    assert label_calls[0][1] == "t23"  # the old lead is demoted before the new one rises
    assert "keeps its raw name" in (proc.stdout + proc.stderr)


def test_handover_is_idempotent(stage):
    roster = [
        _member("t23", role="lead", app="claude-code"),
        _member("t24", role="codex", app="codex", name="dev-codex"),
    ]
    first, _, _ = stage.run(roster, "t24")
    assert first.returncode == 0
    second, calls, labels = stage.run(
        json.loads(stage.roster.read_text(encoding="utf-8-sig"))["terminals"], "t24")
    assert second.returncode == 0, second.stdout + second.stderr
    assert "already holds" in second.stdout
    assert not [c for c in calls if c[0] == "label"]
    assert labels["t23"]["role"] == "claude"


def test_an_unknown_provider_blocks_the_whole_plan(stage):
    roster = [
        _member("t23", role="lead", app=None, name=None),
        _member("t24", role="codex", app="codex", name="dev-codex"),
    ]
    proc, calls, labels = stage.run(roster, "t24")
    assert proc.returncode != 0
    assert "no known provider" in proc.stdout
    assert not [c for c in calls if c[0] == "label"]
    assert labels["t23"]["role"] == "lead" and labels["t24"]["role"] == "codex"


def test_a_demoted_family_role_skips_taken_names(stage):
    roster = [
        _member("t23", role="lead", app="claude-code"),
        _member("t30", role="claude", app="claude-code"),
        _member("t24", role="codex", app="codex", name="dev-codex"),
    ]
    proc, calls, labels = stage.run(roster, "t24")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert labels["t23"]["role"] == "claude2"


def test_a_foreign_team_target_is_refused(stage):
    roster = [
        _member("t23", role="lead", app="claude-code"),
        _member("t99", role="codex", app="codex", team="other"),
    ]
    proc, calls, labels = stage.run(roster, "t99")
    assert proc.returncode != 0
    assert "team=other" in proc.stdout
    assert not [c for c in calls if c[0] == "label"]


def test_a_dead_or_missing_target_is_refused(stage):
    roster = [_member("t23", role="lead", app="claude-code"),
              _member("t24", role="codex", app="codex", state="finished")]
    proc, calls, _ = stage.run(roster, "t24")
    assert proc.returncode != 0
    assert not [c for c in calls if c[0] == "label"]
    proc2, calls2, _ = stage.run(roster, "t77")
    assert proc2.returncode != 0
    assert not [c for c in calls2 if c[0] == "label"]


def test_dry_run_prints_labels_without_mutating(stage):
    roster = [
        _member("t23", role="lead", app="claude-code"),
        _member("t24", role="codex", app="codex", name="dev-codex"),
    ]
    proc, calls, labels = stage.run(roster, "t24", dry=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "hc label t23 team=dev role=claude" in proc.stdout
    assert "hc label t24 team=dev role=lead" in proc.stdout
    assert labels["t23"]["role"] == "lead" and labels["t24"]["role"] == "codex"
