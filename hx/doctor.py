"""hx.doctor: read-only configuration drift report (C8, first slice).

The measured fault: the launch adapters drift from policy.json and nothing
checks. For each worker hx reports the model policy selects against the model
observed where it can be read (launch overlays and live pane configs),
whether context telemetry is known, whether the required skills are present
for that host, and whether the C4 denied-command profile agrees with the
live config. A second pass flags generated/overlay prose that contradicts
policy, from a small table of rules rather than ad hoc checks.

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
from pathlib import Path
from typing import Any

from hx import capability
from hx import devin as devin_mod
from hx import paths

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
        "overlay": paths.package_root() / "team" / "opencode-trusted-dev.jsonc",
        "codex_profile": home / ".codex" / "trusted-dev.config.toml",
        "devin_pane": devin_mod.state_dir() / "dev-devin" / "config.json",
        "hx_ocrun": paths.package_root() / "hx" / "ocrun.py",
    }


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


def _observed_model(worker: str, roots: dict[str, Path]) -> tuple[str | None, str | None]:
    """(model, source) read from launch/overlay/pane files, or (None, None)."""
    if worker == "codex":
        return _toml_value(roots["codex_profile"], "model"), str(roots["codex_profile"])
    if worker == "opencode":
        config = _load_jsonc(roots["overlay"])
        model = config.get("model") if config else None
        model = model.split("/", 1)[-1] if isinstance(model, str) else None
        return model, str(roots["overlay"])
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
        observed, source = _observed_model(worker, roots)
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
    drift = any(
        part.get("status") == "drift"
        for worker in workers.values()
        for part in (worker["model"], worker["skills"], worker["deny_profile"])
    ) or any(f.get("status") == "drift" for f in prose)
    return {"workers": workers, "prose": prose, "drift": drift}


def _summary(report: dict[str, Any]) -> str:
    lines: list[str] = []
    for worker, checks in report["workers"].items():
        model = checks["model"]
        lines.append(
            f"{worker:9s} model:{model['status']:7s} selected={model['selected']} observed={model['observed']}"
        )
        if checks["context_telemetry"] != "known":
            lines.append(f"{'':9s} context:{checks['context_telemetry']}")
        skills = checks["skills"]
        if skills["status"] != "ok":
            lines.append(f"{'':9s} skills:{skills['status']} missing={skills.get('missing')}")
        deny = checks["deny_profile"]
        if deny["status"] != "ok":
            lines.append(f"{'':9s} deny_profile:{deny['status']} {deny.get('mismatches') or deny.get('reason')}")
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
