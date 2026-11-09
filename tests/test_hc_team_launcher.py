"""Exercise the Herdr-app team launcher with recording hc and herdr-app stubs.

The stubs record every invocation as a JSON line in calls.jsonl and answer
`ls` from fixture files, so the launcher's real logic runs end to end without
touching a live core or app.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
POLICY = {
    "persistent_opencode_model": "deepseek-v4.1-flash",
    "codex_bin_dir": "~/Documents/Codex/CLI/codex/0.167.0/bin",
    "devin": {
        "enabled": True,
        "allowed_models": ["swe-2-high"],
        "models": {"medium": "swe-2-high"},
        "promo": {"ends_at": "2099-11-30T10:00:00Z"},
    },
}
RUNNER = r"""
param($Project, $HomePath, $PolicyPath, $Launcher, $Recording, $HcLsFile, $AppLsFile, $CounterFile, $FaultFile, $ModelsFile, [switch]$InHc, [switch]$Dry, $Team = 'dev')
$env:USERPROFILE = $HomePath
$env:HX_POLICY_PATH = $PolicyPath
$global:LASTEXITCODE = 0
if ($InHc) { $env:HC_TERMINAL_ID = 't1' } else { Remove-Item Env:HC_TERMINAL_ID -ErrorAction SilentlyContinue }
function hc {
    ('[' + (($args | ForEach-Object { ConvertTo-Json -Compress $_ }) -join ',') + ']') |
        Add-Content -LiteralPath $Recording
    if (Test-Path $FaultFile) {
        $fault = Get-Content -Raw $FaultFile | ConvertFrom-Json
        if ($fault.query -eq ('hc-' + $args[0])) { $global:LASTEXITCODE = 1; return '{}' }
    }
    if ($args.Count -ge 1 -and $args[0] -eq 'ls') { Get-Content -Raw $HcLsFile; return }
    if ($args[0] -eq 'get') {
        $roster = Get-Content -Raw $HcLsFile | ConvertFrom-Json
        $address = $args[1]
        $t = $roster.terminals | Where-Object { $_.address -eq $address }
        $t | ConvertTo-Json -Depth 20 -Compress
        return
    }
    if ($args.Count -ge 1 -and $args[0] -eq 'run') {
        $n = 10
        if (Test-Path $CounterFile) { $n = [int](Get-Content $CounterFile -Raw).Trim() }
        ($n + 1) | Set-Content $CounterFile
        '{"address":"t' + $n + '","name":"new"}'
        return
    }
}
function herdr-app {
    ('[' + (($args | ForEach-Object { ConvertTo-Json -Compress $_ }) -join ',') + ']') |
        Add-Content -LiteralPath $Recording
    if (Test-Path $FaultFile) {
        $fault = Get-Content -Raw $FaultFile | ConvertFrom-Json
        if ($fault.query -eq ('app-' + $args[0])) { $global:LASTEXITCODE = 1; return '{}' }
    }
    if ($args.Count -ge 1 -and $args[0] -eq 'ls') { Get-Content -Raw $AppLsFile; return }
    if (Test-Path $FaultFile) {
        $fault = Get-Content -Raw $FaultFile | ConvertFrom-Json
        if ($args[1] -eq 'terminal.keep' -and $args[3] -eq $fault.terminal) {
            $global:LASTEXITCODE = 1
            return 'placement failed'
        }
    }
    if ($args[1] -eq 'terminal.keep') {
        $app = Get-Content -Raw $AppLsFile | ConvertFrom-Json
        $app.spaces[0].tabs[0].terminals += [pscustomobject]@{terminal=$args[3]; pane='p'+$args[3]}
        $app | ConvertTo-Json -Depth 20 | Set-Content $AppLsFile
    }
    '{"pane":"px","terminal":"t42"}'
}
function devin {
    if (Test-Path $FaultFile) {
        $fault = Get-Content -Raw $FaultFile | ConvertFrom-Json
        if ($fault.query -eq 'models') { $global:LASTEXITCODE = 1; return '{}' }
    }
    Get-Content -Raw $ModelsFile
}
try { & $Launcher -Project $Project -Team $Team -DryRun:$Dry } catch { Write-Output $_.Exception.Message; exit 1 }
"""


def _powershell():
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("PowerShell is required for the team launcher")
    return exe


def _hc_ls(*members):
    return {
        "hidden_finished": 0,
        "terminals": [
            {"address": "t1", "name": None, "state": "live", "agent": "claude-code"},
            *[
                {"address": addr, "name": name, "state": "live", "agent": name.rsplit("-", 1)[-1]}
                for addr, name in members
            ],
        ],
    }


def _app_ls(*addresses):
    return {
        "spaces": [
            {
                "id": "sp1",
                "tabs": [
                    {
                        "id": "tb1",
                        "terminals": [
                            {"terminal": a, "pane": "p" + a, "sub_agents": []} for a in addresses
                        ],
                    }
                ],
            }
        ]
    }


class Stage:
    def __init__(self, tmp_path):
        project = self.project = tmp_path / "project"
        (project / ".git").mkdir(parents=True)
        self.home = tmp_path / "home"
        self.home.mkdir()
        team_dir = tmp_path / "team"
        team_dir.mkdir()
        launcher = team_dir / "Start-HerdrAppTeam.ps1"
        launcher.write_bytes((REPO / "team" / "Start-HerdrAppTeam.ps1").read_bytes())
        (team_dir / "devin-pane.config.json").write_bytes(
            (REPO / "team" / "devin-pane.config.json").read_bytes()
        )
        policy = self.policy = tmp_path / "policy.json"
        policy.write_text(json.dumps(POLICY), encoding="utf-8")
        self._hc_ls = tmp_path / "hc-ls.json"
        app_ls = self.app_ls = tmp_path / "app-ls.json"
        app_ls.write_text(json.dumps(_app_ls("t1")), encoding="utf-8")
        runner = tmp_path / "run.ps1"
        runner.write_text(RUNNER, encoding="utf-8")
        self._recording = tmp_path / "calls.jsonl"
        self.fault = tmp_path / "fault.json"
        self.models = tmp_path / "models.json"
        self.models.write_text(json.dumps({"families": [{"variants": [
            {"model_uid": "swe-2-high", "cost_tier": "Free"}]}]}))
        self._args = [
            _powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner),
            str(project), str(self.home), str(policy), str(launcher), str(self._recording),
            str(self._hc_ls), str(app_ls), str(tmp_path / "counter.txt"),
            str(self.fault), str(self.models),
        ]

    def run(self, hc_roster, in_hc=True, dry=True, team="dev"):
        for t in (hc_roster.get("terminals") or []) if isinstance(hc_roster, dict) else []:
            if t.get("state") == "live" and t.get("name"):
                role = t["name"].rsplit("-", 1)[-1]
                t.setdefault("cwd", str(self.project))
                t.setdefault("labels", {"team": "dev", "role": role})
                t.setdefault("argv", ["devin"] if role == "devin" else [
                    "powershell", "-Command", "codex --no-daemon -p trusted-dev"
                    if role == "codex" else "opencode -m opencode-go/deepseek-v4.1-flash"])
        self._hc_ls.write_text(json.dumps(hc_roster), encoding="utf-8")
        if self._recording.exists():
            self._recording.unlink()
        args = list(self._args)
        args.extend(["-Team", team])
        if in_hc:
            args.append("-InHc")
        if dry:
            args.append("-Dry")
        proc = subprocess.run(args, capture_output=True, text=True, timeout=60)
        calls = (
            [json.loads(line) for line in self._recording.read_text().splitlines()]
            if self._recording.exists()
            else []
        )
        return proc, calls


@pytest.fixture
def stage(tmp_path):
    return Stage(tmp_path)


def test_refuses_outside_hc(stage):
    proc, calls = stage.run(_hc_ls(), in_hc=False)
    assert proc.returncode == 1
    assert "HC_TERMINAL_ID" in proc.stdout
    assert calls == []


def test_dry_run_no_team_starts_all_three(stage):
    proc, calls = stage.run(_hc_ls())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = proc.stdout.splitlines()
    runs = [line for line in lines if line.startswith("hc run ")]
    assert len(runs) == 3
    # The devin line must start with exactly this prefix; a permission rule matches it.
    assert any(line.startswith("hc run --bg --outlive-parent --name dev-devin") for line in runs)
    assert {line.split("--name ", 1)[1].split(" ", 1)[0] for line in runs} == {
        "dev-codex", "dev-devin", "dev-opencode"}
    assert all("--label team=dev" in line for line in runs)
    assert "codex --no-daemon -p trusted-dev" in proc.stdout
    assert "opencode -m opencode-go/deepseek-v4.1-flash" in proc.stdout
    assert "OPENCODE_CONFIG=" in proc.stdout
    devin = next(line for line in runs if "dev-devin" in line)
    assert "-- devin --model swe-2-high" in devin
    assert "--permission-mode dangerous" in devin
    keeps = [line for line in lines if line.startswith("herdr-app do terminal.keep")]
    assert [line.rsplit(" ", 1)[-1] for line in keeps] == ["right", "down", "down"]
    assert "hc label t1 team=dev role=claude" in lines
    assert calls == [["ls", "--json"], ["ls"]]


def test_dry_run_all_live_starts_nothing(stage):
    stage.app_ls.write_text(json.dumps(_app_ls("t1", "t9", "t11", "t13")))
    roster = _hc_ls(("t9", "dev-codex"), ("t11", "dev-devin"), ("t13", "dev-opencode"))
    proc, calls = stage.run(roster)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not [line for line in proc.stdout.splitlines() if line.startswith("hc run ")]
    assert not [line for line in proc.stdout.splitlines() if line.startswith("herdr-app do")]
    assert "already live t9" in proc.stdout
    assert "already live t11" in proc.stdout
    assert "already live t13" in proc.stdout
    assert calls == [["ls", "--json"], ["ls"], ["get", "t9", "--json"],
                     ["get", "t11", "--json"], ["get", "t13", "--json"]]


def test_dry_run_one_missing_role(stage):
    stage.app_ls.write_text(json.dumps(_app_ls("t1", "t9", "t13")))
    roster = _hc_ls(("t9", "dev-codex"), ("t13", "dev-opencode"))
    proc, calls = stage.run(roster)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = proc.stdout.splitlines()
    runs = [line for line in lines if line.startswith("hc run ")]
    assert len(runs) == 1
    assert runs[0].startswith("hc run --bg --outlive-parent --name dev-devin")
    keeps = [line for line in lines if line.startswith("herdr-app do terminal.keep")]
    assert keeps == ["herdr-app do terminal.keep --terminal dev-devin --place down"]


def test_finished_terminal_does_not_count_as_live(stage):
    roster = _hc_ls()
    roster["terminals"].append({"address": "t7", "name": "dev-codex", "state": "finished"})
    proc, _ = stage.run(roster)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    runs = [line for line in proc.stdout.splitlines() if line.startswith("hc run ")]
    assert any("--name dev-codex" in line for line in runs)


def test_live_run_places_the_grid(stage):
    proc, calls = stage.run(_hc_ls(), dry=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "started t10" in proc.stdout
    assert "started t11" in proc.stdout
    assert "started t12" in proc.stdout
    runs = [c for c in calls if c[0] == "run"]
    assert [c[4] for c in runs] == ["dev-codex", "dev-devin", "dev-opencode"]
    codex_command = runs[0][runs[0].index("-Command") + 1]
    assert "codex --no-daemon -p trusted-dev" in codex_command
    assert str(stage.home) in codex_command  # policy codex_bin_dir, tilde-expanded
    devin_argv = runs[1][runs[1].index("--") + 1:]
    assert devin_argv[:2] == ["devin", "--model"]
    assert devin_argv[devin_argv.index("--config") + 1].endswith("dev-devin\\config.json")
    assert devin_argv[devin_argv.index("--export") + 1].endswith("dev-devin\\transcript.json")
    assert devin_argv[devin_argv.index("--permission-mode") + 1] == "dangerous"
    # The pane writes setup state into its config, so it gets a fresh template copy.
    assert (stage.home / ".herdr-team" / "state" / "dev-devin" / "config.json").exists()
    dos = [c for c in calls if c[0] == "do"]
    assert dos == [
        ["do", "pane.focus", "--terminal", "t1"],
        ["do", "terminal.keep", "--terminal", "t10", "--place", "right"],
        ["do", "pane.focus", "--terminal", "t1"],
        ["do", "terminal.keep", "--terminal", "t11", "--place", "down"],
        ["do", "pane.focus", "--terminal", "t10"],
        ["do", "terminal.keep", "--terminal", "t12", "--place", "down"],
    ]
    # A second run on a full roster changes nothing.
    proc2, calls2 = stage.run(
        _hc_ls(("t10", "dev-codex"), ("t11", "dev-devin"), ("t12", "dev-opencode")),
        dry=False,
    )
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    assert not [c for c in calls2 if c[0] == "run"]
    assert not [c for c in calls2 if c[0] == "do"]
    assert "already live t10" in proc2.stdout


@pytest.mark.parametrize("change", ["disabled", "disallowed", "expired", "paid", "unavailable"])
def test_devin_admission_refuses_before_any_mutation(stage, change):
    policy = json.loads(stage.policy.read_text())
    if change == "disabled":
        policy["devin"]["enabled"] = False
    elif change == "disallowed":
        policy["devin"]["models"]["medium"] = "swe-2-high-lightning"
    elif change == "expired":
        policy["devin"]["promo"]["ends_at"] = "2000-01-01T00:00:00Z"
    else:
        stage.models.write_text(json.dumps({"families": [{"variants": [] if change == "unavailable" else [
            {"model_uid": "swe-2-high", "cost_tier": "Paid"}]}]}))
    stage.policy.write_text(json.dumps(policy))
    proc, calls = stage.run(_hc_ls(), dry=False)
    assert proc.returncode != 0, proc.stdout
    assert not [c for c in calls if c[0] in {"run", "label", "do"}], calls


@pytest.mark.parametrize("conflict", ["cwd", "labels", "argv", "duplicate"])
def test_conflicting_live_identity_is_refused(stage, conflict):
    roster = _hc_ls(("t9", "dev-codex"))
    t = roster["terminals"][1]
    if conflict == "cwd":
        t["cwd"] = str(stage.home)
    elif conflict == "labels":
        t["labels"] = {"team": "other", "role": "codex"}
    elif conflict == "argv":
        t["argv"] = ["powershell", "-Command", "Write-Host codex"]
    else:
        roster["terminals"].append(dict(t, address="t99"))
    proc, calls = stage.run(roster, dry=False)
    assert proc.returncode != 0, proc.stdout
    assert "t9" in proc.stdout
    assert not [c for c in calls if c[0] in {"run", "label", "do"}]


@pytest.mark.parametrize("boundary", ["t10", "t11", "t12"])
def test_retry_completes_missing_panes_after_each_placement_failure(stage, boundary):
    stage.app_ls.write_text(json.dumps(_app_ls("t1")))
    stage.fault.write_text(json.dumps({"terminal": boundary}))
    first, _ = stage.run(_hc_ls(), dry=False)
    assert first.returncode != 0
    stage.fault.unlink()
    roster = _hc_ls(("t10", "dev-codex"), ("t11", "dev-devin"), ("t12", "dev-opencode"))
    second, calls = stage.run(roster, dry=False)
    assert second.returncode == 0, second.stdout + second.stderr
    assert not [c for c in calls if c[0] == "run"]
    panes = json.loads(stage.app_ls.read_text(encoding="utf-8-sig"))["spaces"][0]["tabs"][0]["terminals"]
    assert sorted(p["terminal"] for p in panes) == ["t1", "t10", "t11", "t12"]


def test_only_authorized_dev_team_is_supported(stage):
    proc, calls = stage.run(_hc_ls(), team="other", dry=False)
    assert proc.returncode != 0
    assert calls == []


@pytest.mark.parametrize("source", ["hc", "app"])
@pytest.mark.parametrize("document", [{}, {"error": "failed"}, {"terminals": None}, []])
def test_malformed_rosters_fail_closed(stage, source, document):
    if source == "app":
        stage.app_ls.write_text(json.dumps(document))
    proc, calls = stage.run(document if source == "hc" else _hc_ls(), dry=False)
    assert proc.returncode != 0, proc.stdout
    assert not [c for c in calls if c[0] in {"run", "label", "do"}]


@pytest.mark.parametrize("query", ["hc-ls", "hc-get", "app-ls", "models"])
def test_failed_queries_refuse_before_mutation(stage, query):
    stage.fault.write_text(json.dumps({"query": query}))
    proc, calls = stage.run(_hc_ls(("t9", "dev-codex")), dry=False)
    assert proc.returncode != 0
    assert not [c for c in calls if c[0] in {"run", "label", "do"}]


def test_missing_codex_pane_is_placed_before_new_opencode(stage):
    roster = _hc_ls(("t9", "dev-codex"), ("t11", "dev-devin"))
    stage.app_ls.write_text(json.dumps(_app_ls("t1", "t11")))
    proc, calls = stage.run(roster, dry=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert [c[4] for c in calls if c[0] == "run"] == ["dev-opencode"]
    assert [c for c in calls if c[:2] == ["do", "terminal.keep"]] == [
        ["do", "terminal.keep", "--terminal", "t9", "--place", "right"],
        ["do", "terminal.keep", "--terminal", "t10", "--place", "down"],
    ]


def test_live_identity_accepts_canonical_cwd(stage):
    roster = _hc_ls(("t9", "dev-codex"), ("t11", "dev-devin"), ("t13", "dev-opencode"))
    for t in roster["terminals"][1:]:
        t["cwd"] = str(stage.project) + "\\."
    stage.app_ls.write_text(json.dumps(_app_ls("t1", "t9", "t11", "t13")))
    proc, calls = stage.run(roster, dry=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not [c for c in calls if c[0] in {"run", "do"}]
