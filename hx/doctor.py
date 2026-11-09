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
import datetime as dt
import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from hx import capability, context, host, paths, usage
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
        sid = agent.get("session_id")
        if agent.get("kind") != "opencode" or not isinstance(sid, str) or not sid:
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


def _age_seconds(timestamp: str | None, now: dt.datetime) -> int | None:
    if not timestamp:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return max(0, int((now - parsed).total_seconds()))


def _model_unknown(reason: str, source: str | None = None) -> dict[str, Any]:
    """A model that could not be observed: unknown, never drift, never verified."""
    return {"status": "unknown", "observed": None, "source": source,
            "age_seconds": None, "reason": reason}


def _member_model(member: dict[str, Any], roots: dict[str, Path],
                  policy: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
    """The model behind a member's bound provider session, with provenance."""
    sid = member.get("session_id")
    if not sid and member.get("kind") == "devin":
        # As in context._session: the pane is Devin's only stable identity.
        pane = str(member.get("address") or member.get("name") or "")
        sid = f"devin-pane:{pane}" if pane else None
    if not sid:
        return _model_unknown(
            f"no session binding: {member.get('session_reason') or 'provider reports none'}")
    kind = member.get("kind")
    if kind == "claude":
        return _claude_member_model(roots, str(sid), now)
    if kind == "codex":
        return _codex_member_model(str(sid), now)
    if kind == "opencode":
        return _opencode_member_model(roots, str(sid), now)
    if kind == "devin":
        return _devin_member_model(member, roots, now)
    return _model_unknown("provider kind unresolved; no model source")


def _devin_model_id(label: Any) -> str | None:
    """A Devin model label ("SWE-2 High") normalized to the policy id ("swe-2-high")."""
    if not isinstance(label, str) or not label.strip():
        return None
    return re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-") or None


def _devin_transcript_path(member: dict[str, Any], roots: dict[str, Path]) -> Path:
    """The pane's --export transcript: <state>/<member>/transcript.json."""
    name = str(member.get("name") or "")
    pane = roots.get("devin_pane")
    if pane is not None and Path(pane).parent.name == name:
        return Path(pane).parent / "transcript.json"
    return devin_mod.state_dir() / name / "transcript.json"


def _transcript_model(doc: Any, member: dict[str, Any], transcript: Path) -> str | None:
    """The export's model only when it belongs to the current terminal generation.

    The file must have been written after the terminal's ``created_ms`` — an
    older export is a previous generation's pane on the same state dir — and
    when the transcript carries a session_id it must match the member's bound
    session whenever the member has a real (non-pane) one to compare. Anything
    unexpected in the shape yields no model, never a crash.
    """
    if not isinstance(doc, dict):
        return None
    created_ms = member.get("created_ms")
    if not isinstance(created_ms, (int, float)) or isinstance(created_ms, bool):
        return None
    try:
        mtime_ms = transcript.stat().st_mtime * 1000
    except OSError:
        return None
    if mtime_ms <= float(created_ms):
        return None
    sid = str(member.get("session_id") or "")
    tsid = doc.get("session_id")
    if tsid is not None and not isinstance(tsid, str):
        return None
    if tsid and sid and not sid.startswith("devin-pane:") and tsid != sid:
        return None
    agent = doc.get("agent")
    label = agent.get("model_name") if isinstance(agent, dict) else None
    return _devin_model_id(label)


def _devin_member_model(member: dict[str, Any], roots: dict[str, Path],
                        now: dt.datetime) -> dict[str, Any]:
    """Devin's live model: the export transcript's agent.model_name, else the
    footer's model label — each normalized to the policy ids, source named."""
    name = str(member.get("name") or "")
    transcript = _devin_transcript_path(member, roots)
    doc = stamp = None
    try:
        doc = json.loads(transcript.read_text(encoding="utf-8"))
        stamp = dt.datetime.fromtimestamp(
            transcript.stat().st_mtime, dt.timezone.utc).isoformat()
    except (OSError, ValueError):
        pass
    model = _transcript_model(doc, member, transcript)
    if model:
        return {"status": "ok", "observed": model,
                "source": f"export transcript {transcript}",
                "age_seconds": _age_seconds(stamp, now), "reason": None}
    try:
        screen = str(context._herdr_read(name, lines=8))
    except Exception:
        screen = ""
    model = _devin_model_id(context._devin_footer_label(screen))
    if model:
        return {"status": "ok", "observed": model,
                "source": "pane footer model label",
                "age_seconds": 0, "reason": None}
    return _model_unknown(
        "devin pane model unreadable: no export transcript model or footer label",
        str(transcript))


def _claude_family(model: Any) -> str | None:
    """Normalize a Claude model id to its family: claude-sonnet-4-6 -> sonnet."""
    if isinstance(model, dict):
        model = model.get("id") or model.get("name")
    if not isinstance(model, str) or not model:
        return None
    for name in ("sonnet", "opus", "haiku"):
        if re.search(rf"\b{name}\b", model):
            return name
    return model


def _claude_member_model(roots: dict[str, Path], sid: str, now: dt.datetime) -> dict[str, Any]:
    env = os.environ.get("HX_STATE_DIR")
    state_dir = Path(env) if env else (roots.get("home") or Path.home()) / ".herdr-team" / "state"
    path = state_dir / f"claude-status-{usage._safe_session_id(sid)}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return _model_unknown(f"claude status file missing for the bound session", str(path))
    except ValueError:
        return _model_unknown("claude status file unreadable", str(path))
    if data.get("session_id") != sid:
        return _model_unknown("claude status file names a different session", str(path))
    model = _claude_family(data.get("model"))
    if not model:
        return _model_unknown("claude status carries no model field", str(path))
    timestamp = data.get("timestamp") or dt.datetime.fromtimestamp(
        path.stat().st_mtime, dt.timezone.utc).isoformat()
    return {"status": "ok", "observed": model, "source": str(path),
            "age_seconds": _age_seconds(timestamp, now), "reason": None}


def _codex_member_model(sid: str, now: dt.datetime) -> dict[str, Any]:
    sessions = usage.default_codex_sessions_dir()
    newest: tuple[str, str] | None = None
    try:
        paths_ = list(sessions.rglob(f"*{sid}*.jsonl"))
    except OSError:
        paths_ = []
    for path in paths_:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            payload = event.get("payload") or {}
            model = payload.get("model")
            if isinstance(model, dict):
                model = model.get("name") or model.get("id")
            if isinstance(model, str) and model:
                stamp = str(event.get("timestamp") or payload.get("timestamp") or "")
                if newest is None or stamp > newest[0]:
                    newest = (stamp, model)
    if newest is None:
        return _model_unknown(f"codex rollout for the bound session carries no model field")
    return {"status": "ok", "observed": newest[1], "source": f"{sessions} session={sid}",
            "age_seconds": _age_seconds(newest[0], now), "reason": None}


def _opencode_member_model(roots: dict[str, Path], sid: str, now: dt.datetime) -> dict[str, Any]:
    db = roots.get("opencode_db")
    if db is None or not db.is_file():
        return _model_unknown(f"opencode database not found: {db}",
                              str(db) if db else None)
    try:
        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            rows = conn.execute(
                "SELECT time_created, data FROM message WHERE session_id = ? "
                "ORDER BY time_created DESC", (sid,))
            for created, raw in rows:
                data = json.loads(raw)
                model = data.get("modelID") or (data.get("model") or {}).get("modelID")
                provider = data.get("providerID") or (data.get("model") or {}).get("providerID")
                if not (isinstance(model, str) and model and isinstance(provider, str) and provider):
                    continue
                observed = model if provider == "opencode-go" else f"{provider}/{model}"
                stamp = dt.datetime.fromtimestamp(
                    created / 1000, dt.timezone.utc).isoformat()
                return {"status": "ok", "observed": observed,
                        "source": f"{db} session={sid}",
                        "age_seconds": _age_seconds(stamp, now), "reason": None}
    except Exception as exc:
        return _model_unknown(f"opencode database read failed: {exc}", str(db))
    return _model_unknown(f"bound opencode session {sid} has no model row", str(db))


def _check_members(policy: dict[str, Any], roots: dict[str, Path],
                   now: dt.datetime, detected: Any = None) -> dict[str, Any]:
    """Live model evidence for every team member, from bound sessions only."""
    detected = detected if detected is not None else host.detect()
    if detected is None:
        return {"status": "unknown", "reason": "no team host detected", "members": {}}
    try:
        prefix = usage.team_prefix(policy)
    except Exception as exc:
        return {"status": "unknown", "reason": str(exc), "members": {}}
    try:
        roster = detected.team(prefix)
    except Exception as exc:
        return {"status": "unknown", "reason": f"team roster unavailable: {exc}", "members": {}}
    selected = _selected_models(policy)
    lead_role = str(policy.get("lead_role", "lead"))
    out: dict[str, Any] = {}
    for name, member in roster.items():
        model = _member_model(member, roots, policy, now)
        want = selected.get(str(member.get("kind")))
        if model["observed"] is None or want is None:
            model["status"] = "unknown"
        else:
            model["status"] = "ok" if model["observed"] == want else "drift"
        if member.get("role") == lead_role and model["observed"] is not None:
            # The lead seat is information only: its model is never a verdict.
            model["status"] = "info"
        model["selected"] = want
        out[name] = {
            "model": model,
            "status": member.get("status"),
            "kind": member.get("kind"),
            "address": member.get("address"),
        }
    return {"status": "ok", "reason": None, "members": out}


def ready(policy: dict[str, Any] | None = None, roots: dict[str, Path] | None = None,
          detected: Any = None) -> dict[str, Any]:
    """One kickoff verdict: ready, or not ready with the exact missing pieces."""
    if policy is None:
        try:
            policy = usage.load_policy()
        except Exception:
            policy = {}
    roots = roots or _roots()
    detected = detected if detected is not None else host.detect()
    missing: list[str] = []
    if detected is None:
        return {"ready": False, "missing": ["team host: no hc or herdr host detected"]}
    if not isinstance(detected, host.HerdrHost):
        try:
            host.tool_path("hc")
        except Exception as exc:
            missing.append(f"hc executable: {exc}")
    try:
        own = detected.own()
    except Exception as exc:
        missing.append(f"lead identity unavailable: {exc}")
        own = None
    if own is None:
        missing.append("lead identity unavailable: this pane is not a validated team member")
    try:
        roster = detected.team(usage.team_prefix(policy))
    except Exception as exc:
        missing.append(f"team roster: {exc}")
        roster = {}
    if not roster and detected is not None:
        missing.append("team roster: empty; no live members on this team")
    selected = _selected_models(policy)
    lead_role = str(policy.get("lead_role", "lead"))
    # Required workers are the team's worker kinds, never the lead's family:
    # a Claude lead does not mean a Claude worker must exist.
    lead_kind = next((m.get("kind") for m in roster.values()
                      if m.get("role") == lead_role), None)
    required = {lead_role} | {kind for kind, model in selected.items()
                              if model and kind != lead_kind}
    present_roles = {str(m.get("role") or "") for m in roster.values()}
    for role in sorted(required - present_roles):
        missing.append(f"worker role missing from roster: {role}")
    now = dt.datetime.now(dt.timezone.utc)
    for name, member in sorted(roster.items()):
        if not member.get("status") or member.get("status") == "unknown":
            missing.append(f"worker {name}: status unknown")
        sid = member.get("session_id")
        if not sid and member.get("kind") == "devin" and member.get("address"):
            # hx binds Devin panes itself: devin-pane:<address>.
            sid = f"devin-pane:{member['address']}"
        if not sid:
            missing.append(
                f"worker {name}: no session binding "
                f"({member.get('session_reason') or 'provider reports none'})")
            continue
        if member.get("role") == lead_role:
            continue  # the lead's model is information, not worker evidence
        model = _member_model(member, roots, policy, now)
        want = selected.get(str(member.get("kind")))
        if model["observed"] is None:
            missing.append(f"worker {name}: live model unknown ({model['reason']})")
        elif want is None:
            missing.append(f"worker {name}: no model selected in policy for {member.get('kind')}")
        elif model["observed"] != want:
            missing.append(
                f"worker {name}: live model {model['observed']} does not match policy {want}")
    return {"ready": not missing, "missing": missing}


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
        # These sources are installed configuration, never a live bound model:
        # "config" is equality of configs, not proof of the running model.
        if observed is None:
            status = "unknown"
        elif want is None:
            status = "config"
        elif observed == want:
            status = "config" if worker in ("codex", "devin") else "ok"
        else:
            status = "drift"
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
    members = _check_members(policy, roots, dt.datetime.now(dt.timezone.utc))
    prose = _scan_prose(policy, roots)
    installed = _installed_files(roots)
    drift = any(
        part.get("status") == "drift"
        for worker in workers.values()
        for part in (worker["model"], worker["skills"], worker["deny_profile"])
    ) or any(
        member["model"].get("status") == "drift"
        for member in members.get("members", {}).values()
    ) or any(f.get("status") == "drift" for f in prose + installed)
    return {"workers": workers, "members": members, "prose": prose,
            "installed_files": installed, "drift": drift}


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
    members = report.get("members", {})
    for name, member in members.get("members", {}).items():
        model = member["model"]
        observed = model["observed"]
        detail = f"observed={observed} age={model['age_seconds']}s" if observed else model["reason"]
        lines.append(f"{name} model:{model['status']:7s} {detail} source={model['source']}")
    if members.get("status") == "unknown":
        lines.append(f"team members: unknown ({members['reason']})")
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
    parser.add_argument("--ready", action="store_true",
                        help="one kickoff-readiness verdict; exits 1 with the exact missing pieces")
    args = parser.parse_args(argv)
    if args.ready:
        verdict = ready()
        if args.json:
            print(json.dumps(verdict, indent=2))
        elif verdict["ready"]:
            print("ready")
        else:
            print("not ready:")
            for piece in verdict["missing"]:
                print(f"  - {piece}")
        return 0 if verdict["ready"] else 1
    report = doctor()
    print(json.dumps(report, indent=2) if args.json else _summary(report))
    return 1 if report["drift"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
