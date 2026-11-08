"""hx.doctor: read-only configuration drift report (C8, first slice).

The measured fault: the launch adapters drift from policy.json and nothing
checks. For each worker hx reports the model policy selects against the model
observed where it can be read (live OpenCode session messages and pane configs),
whether context telemetry is known, whether the required skills are present
for that host, and whether the C4 denied-command profile agrees with the
live config. A second pass flags generated/overlay prose that contradicts
policy, from a small table of rules rather than ad hoc checks. The install
manifest drives byte comparisons between every copied file and its repo source.

Every value is ``ok``, ``drift`` (with both values) or ``unknown``. Doctor
never probes the network and never guesses: an unreadable source is unknown,
not assumed. ``main`` prints a human summary, or JSON with ``--json``, and
exits 1 when any drift is found so it can gate later steps.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from hx import capability, context, paths, usage
from hx import devin as devin_mod

REQUIRED_SKILLS = ("herdr-team", "hx-router", "poteto-mode")

# Prose rules: (id, glob-under-roots, regex, policy description, message).
# A match is stale prose contradicting the named policy fact.
PROSE_RULES: tuple[dict[str, Any], ...] = (
    {
        "id": "hx-router-per-model-allowance",
        "files": ["claude_skills/hx-router/SKILL.md", "codex_skills/hx-router/SKILL.md", "devin_skills/hx-router/SKILL.md"],
        "pattern": r"each OpenCode model has its own allowance|per-model allowance",
        "policy": "OpenCode quota is one shared pool (hx.usage pool windows)",
        "message": "hx-router copy describes a per-model allowance; live usage reads a shared pool",
    },
    {
        "id": "opencode-overlay-model-pin",
        "files": ["overlay"],
        "pattern": r'"model"\s*:\s*"([^"]+)"',
        "policy": "policy.persistent_opencode_model",
        "message": "launch overlay pins a model policy does not select",
    },
    {
        "id": "devin-preamble-review-in-notes",
        "files": ["hx_ocrun"],
        "pattern": r"How to review",
        "policy": "global typed-review rule (reviews are findings, not notes)",
        "message": "devin preamble still instructs review text in notes",
    },
)


def _roots() -> dict[str, Path]:
    home = Path(os.path.expanduser("~"))
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    return {
        "claude_skills": home / ".claude" / "skills",
        "codex_skills": home / ".codex" / "skills",
        "devin_skills": appdata / "devin" / "skills",
        "home": home,
        "repo": paths.package_root(),
        "overlay": home / ".herdr-team" / "opencode-trusted-dev.jsonc",
        "opencode_db": usage.default_opencode_db_path(),
        "codex_profile": home / ".codex" / "trusted-dev.config.toml",
        "devin_pane": devin_mod.state_dir() / "dev-devin" / "config.json",
        "hx_ocrun": paths.package_root() / "hx" / "ocrun.py",
    }


def _installed_files(roots: dict[str, Path]) -> list[dict[str, Any]]:
    if "repo" not in roots or "home" not in roots:
        return []
    manifest = roots["repo"] / "team" / "install-files.json"
    try:
        entries = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [{"status": "unknown", "source": str(manifest), "target": None}]
    checks = []
    for entry in entries:
        source = roots["repo"] / entry["source"]
        target = roots["home"] / entry["target"]
        try:
            status = "ok" if source.read_bytes() == target.read_bytes() else "drift"
        except FileNotFoundError:
            status = "drift" if source.is_file() and not target.exists() else "unknown"
        except OSError:
            status = "unknown"
        checks.append({"status": status, "source": str(source), "target": str(target)})
    return checks


def _opencode_model(roots: dict[str, Path], policy: dict[str, Any]) -> tuple[str | None, str | None]:
    db = roots.get("opencode_db")
    if db is None or not db.is_file():
        return None, str(db) if db else None
    source = str(db)
    try:
        agent = context.agent_info(usage.team_prefix(policy) + "opencode")
        sid = (agent.get("agent_session") or {}).get("value")
        if agent.get("agent") != "opencode" or not isinstance(sid, str) or not sid:
            return None, source
        source += f" session={sid}"
        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            rows = conn.execute(
                "SELECT data FROM message WHERE session_id = ? ORDER BY time_created DESC, rowid DESC", (sid,))
            for (raw,) in rows:
                data = json.loads(raw)
                if data.get("role") not in {"assistant", "user"}:
                    continue
                model = data.get("modelID") or (data.get("model") or {}).get("modelID")
                provider = data.get("providerID") or (data.get("model") or {}).get("providerID")
                if not isinstance(model, str) or not model or not isinstance(provider, str) or not provider:
                    return None, source
                return (model if provider == "opencode-go" else f"{provider}/{model}"), source
    except Exception:
        return None, source
    return None, source


def _load_jsonc(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(devin_mod._strip_json_comments(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def _toml_value(path: Path, key: str) -> str | None:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(rf"^\s*{re.escape(key)}\s*=\s*\"([^\"]+)\"", line)
            if match:
                return match.group(1)
    except OSError:
        pass
    return None


def _selected_models(policy: dict[str, Any]) -> dict[str, str | None]:
    return {
        "claude": (policy.get("claude") or {}).get("strong_model") or "sonnet",
        "codex": (policy.get("codex") or {}).get("strong_model"),
        "opencode": policy.get("persistent_opencode_model"),
        "devin": ((policy.get("devin") or {}).get("models") or {}).get("medium"),
    }


def _observed_model(worker: str, roots: dict[str, Path], policy: dict[str, Any]) -> tuple[str | None, str | None]:
    """Read the live session or host configuration; unknown is never overlay evidence."""
    if worker == "codex":
        return _toml_value(roots["codex_profile"], "model"), str(roots["codex_profile"])
    if worker == "opencode":
        return _opencode_model(roots, policy)
    if worker == "devin":
        config = _load_jsonc(roots["devin_pane"]) or _load_jsonc(devin_mod.pane_config_template())
        return ((config or {}).get("agent") or {}).get("model"), str(roots["devin_pane"])
    return None, None


def _skills_check(target: Path | None) -> dict[str, Any]:
    if target is None or not target.is_dir():
        return {"status": "unknown", "target": str(target) if target else None, "missing": []}
    missing = [name for name in REQUIRED_SKILLS if not (target / name / "SKILL.md").exists()]
    return {
        "status": "drift" if missing else "ok",
        "target": str(target),
        "missing": missing,
    }


def _deny_check(worker: str, roots: dict[str, Path], policy: dict[str, Any]) -> dict[str, Any]:
    """Whether the C4 profile's verdicts agree with the live pane config's own."""
    if worker != "devin":
        return {"status": "ok", "probes": "same source as profile"}
    live = _load_jsonc(roots["devin_pane"])
    if live is None:
        return {"status": "unknown", "reason": "live pane config unreadable"}
    profile = capability.worker_profile("devin", policy)
    live_seq = [cmd.split() for cmd in devin_mod.denied_prefixes(live)[0]]
    probes = [
        "hx say claude hi",
        "hx ocrun --worker devin",
        "hx usage --json",
        "git push",
        "python -m hx route",
        "gh api",
        "herdr-app check x",
    ]
    mismatches = []
    for command in probes:
        profile_verdict = capability.denied_exec(profile, command) is not None
        tokens = capability._tokens(command)
        live_verdict = any(
            capability._word_eq(w[0], tokens[0]) and capability._is_subsequence(w[1:], tokens[1:])
            for w in live_seq
            if tokens and len(w) <= len(tokens)
        )
        if profile_verdict != live_verdict:
            mismatches.append({"command": command, "profile": "denied" if profile_verdict else "allowed", "live": "denied" if live_verdict else "allowed"})
    return {"status": "drift" if mismatches else "ok", "mismatches": mismatches}


def _check_workers(policy: dict[str, Any], roots: dict[str, Path]) -> dict[str, Any]:
    selected = _selected_models(policy)
    limits = policy.get("context_limits") or {}
    skill_targets = {
        "claude": roots["claude_skills"],
        "codex": roots["codex_skills"],
        "devin": roots["devin_skills"],
        "opencode": roots["claude_skills"],  # opencode reads the shared Claude set
    }
    out: dict[str, Any] = {}
    for worker in ("claude", "codex", "opencode", "devin"):
        observed, source = _observed_model(worker, roots, policy)
        want = selected.get(worker)
        if want is None or observed is None:
            status = "unknown"
        else:
            status = "ok" if observed == want else "drift"
        out[worker] = {
            "model": {"status": status, "selected": want, "observed": observed, "source": source},
            "context_telemetry": "known" if (want and want in limits) else "unknown",
            "skills": _skills_check(skill_targets.get(worker)),
            "deny_profile": _deny_check(worker, roots, policy),
        }
    return out


def _scan_prose(policy: dict[str, Any], roots: dict[str, Path]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for rule in PROSE_RULES:
        for ref in rule["files"]:
            path = roots.get(ref)
            if path is None:
                first, _, rest = ref.partition("/")
                path = roots[first] / rest if first in roots and rest else paths.package_root() / ref
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                findings.append({"rule": rule["id"], "status": "unknown", "file": str(path), "line": None})
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                match = re.search(rule["pattern"], line)
                if not match:
                    continue
                finding = {
                    "rule": rule["id"],
                    "status": "drift",
                    "file": str(path),
                    "line": lineno,
                    "found": match.group(1) if match.groups() else match.group(0)[:80],
                    "policy": rule["policy"],
                    "message": rule["message"],
                }
                if rule["id"] == "opencode-overlay-model-pin":
                    observed = (finding["found"] or "").split("/", 1)[-1]
                    want = policy.get("persistent_opencode_model")
                    finding["policy_value"] = want
                    if want is None or observed == want:
                        finding["status"] = "ok" if want else "unknown"
                findings.append(finding)
    return findings


def doctor(policy: dict[str, Any] | None = None, roots: dict[str, Path] | None = None) -> dict[str, Any]:
    """The full read-only drift report; never raises on unreadable inputs."""
    if policy is None:
        from hx.usage import load_policy

        try:
            policy = load_policy()
        except Exception:
            policy = {}
    roots = roots or _roots()
    workers = _check_workers(policy, roots)
    prose = _scan_prose(policy, roots)
    installed = _installed_files(roots)
    drift = any(
        part.get("status") == "drift"
        for worker in workers.values()
        for part in (worker["model"], worker["skills"], worker["deny_profile"])
    ) or any(f.get("status") == "drift" for f in prose + installed)
    return {"workers": workers, "prose": prose, "installed_files": installed, "drift": drift}


def _summary(report: dict[str, Any]) -> str:
    lines: list[str] = []
    for worker, checks in report["workers"].items():
        model = checks["model"]
        lines.append(
            f"{worker:9s} model:{model['status']:7s} selected={model['selected']} observed={model['observed']}"
        )
        if worker == "opencode":
            lines.append(f"{'':9s} source:{model['source']}")
        if checks["context_telemetry"] != "known":
            lines.append(f"{'':9s} context:{checks['context_telemetry']}")
        skills = checks["skills"]
        if skills["status"] != "ok":
            lines.append(f"{'':9s} skills:{skills['status']} missing={skills.get('missing')}")
        deny = checks["deny_profile"]
        if deny["status"] != "ok":
            lines.append(f"{'':9s} deny_profile:{deny['status']} {deny.get('mismatches') or deny.get('reason')}")
    for check in report.get("installed_files", []):
        lines.append(f"installed {check['status']} {check['target']} source={check['source']}")
    for finding in report["prose"]:
        if finding["status"] == "drift":
            lines.append(f"drift  {finding['file']}:{finding['line']} {finding['message']}")
        elif finding["status"] == "unknown":
            lines.append(f"unknown {finding['file']} (unreadable)")
    lines.append("drift found" if report["drift"] else "no drift")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hx doctor", description="Read-only drift report.")
    parser.add_argument("--json", action="store_true", help="print JSON instead of the summary")
    args = parser.parse_args(argv)
    report = doctor()
    print(json.dumps(report, indent=2) if args.json else _summary(report))
    return 1 if report["drift"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
