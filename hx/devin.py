"""hx.devin: Devin CLI (Cognition SWE-2) as a one-off local worker.

Facts measured on Devin CLI 3000.11.234 (2026-10-31, docs/audit/devin.md):

- Effort is part of the model id: ``swe-2-medium``, ``swe-2-high``, ``swe-2-max``
  (``devin models list --format json`` tags them ``cost_tier: Free``). The
  ``-lightning`` variants are billed ("High cost") and never allowed here.
- ``devin -p`` exits 0 even when a tool call was rejected: the run just stops.
  Results therefore come from the ``--export`` file (ATIF JSON), never from
  the exit code. Every agent step records ``model_name`` and
  ``extra.generation_model``, so the model that served each turn is verified.
- ``--config`` replaces the user config and Devin writes setup state back
  into it, so each run gets its own copy of ``team/devin-worker.config.json``.
- Print mode ignores the config's ``permission_mode``; it is passed as a flag.

The SWE-2 promotion ("free for self-serve plans in Devin Desktop and Devin CLI
through November 30, 2026") is a time-limited price, not a contract. Free
routing stops at ``policy.devin.promo.ends_at`` (end of 30 Nov in UTC+14, the
earliest reading) and whenever the live model list stops tagging the model
Free. Either way the worker fails closed and hx routes to the other pools.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from hx import paths

PROVIDER = "devin"
FREE_TIER = "Free"
DEFAULT_KINDS = ("routine", "investigate", "feature", "debug", "refactor", "test")
DEFAULT_MODELS = {"low": "swe-2-high", "medium": "swe-2-high", "high": "swe-2-max", "max": "swe-2-max"}
CONFIG_FILE = "devin-config.json"
EXPORT_FILE = "export.json"
PID_FILE = "pid.txt"
SLOT_GRACE_S = 300
SLOT_LOCK_STALE_S = 30
SLOT_LOCK_WAIT_S = 10
LIVE_SESSIONS_FILE = "live-sessions.json"
LIVE_SESSIONS_TTL_S = 300
_AGENTS_UNSET = object()
ERROR_STATE_FILE = "devin-last-error.json"
QUOTA_ERROR_WINDOW_S = 30 * 60
# Devin's own rejection notices, matched at the start of an observation only:
# file views and command output carry arbitrary text (hx's own source says
# "denied"), and they always start with "<file-view" or "Output from command".
REJECTION_PREFIXES = (
    "tool execution was rejected",
    "permission denied for this tool",
    "permission to run the command",
)
# Environment overrides that would change what a run uses; the flags win, but
# an inherited value must never be the thing that decides.
SCRUBBED_ENV = ("DEVIN_MODEL", "DEVIN_PERMISSION_MODE", "DEVIN_SANDBOX", "DEVIN_VERBOSITY")
# Secrets in hx's environment would reach every shell command the worker runs.
# QUESTLOG_TOKEN_OPENCODE stays: the Questlog MCP launcher reads it.
SECRET_ENV_RE = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|CREDENTIAL|PRIVATE_KEY", re.IGNORECASE)
KEPT_SECRET_ENV = frozenset({"QUESTLOG_TOKEN_OPENCODE"})


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def settings(policy: dict[str, Any]) -> dict[str, Any]:
    return policy.get("devin") or {}


def state_dir() -> Path:
    env = os.environ.get("HX_STATE_DIR")
    return Path(env) if env else Path.home() / ".herdr-team" / "state"


def devin_bin(policy: dict[str, Any]) -> Path | None:
    raw = settings(policy).get("bin")
    if raw:
        path = Path(os.path.expandvars(os.path.expanduser(raw)))
        return path if path.exists() else None
    found = shutil.which("devin")
    return Path(found) if found else None


def worker_config_template(policy: dict[str, Any]) -> Path:
    raw = settings(policy).get("worker_config") or "team/devin-worker.config.json"
    path = Path(os.path.expanduser(raw))
    return path if path.is_absolute() else paths.package_root() / path


def allowed_models(policy: dict[str, Any]) -> list[str]:
    return list(settings(policy).get("allowed_models") or [])


def model_for_effort(effort: str | None, policy: dict[str, Any]) -> str:
    models = settings(policy).get("models") or DEFAULT_MODELS
    return models.get(effort or "medium") or models.get("medium") or DEFAULT_MODELS["medium"]


def _parse_ts(text: Any) -> datetime.datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        value = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        return None
    return value


def promo_status(policy: dict[str, Any], now: datetime.datetime | None = None) -> dict[str, Any]:
    """Whether free SWE-2 routing is allowed right now.

    A missing or unparseable end date counts as expired: renewed pricing
    verification and the user's approval are needed to set a new one.
    """
    now = now or _utcnow()
    promo = settings(policy).get("promo") or {}
    ends_at = _parse_ts(promo.get("ends_at"))
    if ends_at is None:
        return {"active": False, "ends_at": None, "reason": "no valid policy.devin.promo.ends_at"}
    if now >= ends_at:
        return {
            "active": False,
            "ends_at": ends_at.isoformat(),
            "reason": "SWE-2 promotion ended; re-verify pricing and get the user's approval before re-enabling",
        }
    return {
        "active": True,
        "ends_at": ends_at.isoformat(),
        "hours_left": round((ends_at - now).total_seconds() / 3600, 1),
        "reason": "promotion active",
    }


def list_models(bin_path: Path, timeout: float = 60.0) -> tuple[dict[str, Any] | None, str | None]:
    """``devin models list --format json`` as {model_uid: variant}."""
    try:
        result = subprocess.run(
            [str(bin_path), "models", "list", "--format", "json"],
            capture_output=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:
        return None, f"devin models list failed: {exc}"
    if result.returncode != 0:
        return None, f"devin models list exited {result.returncode}: {(result.stderr or '')[:200]}"
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, "devin models list returned no JSON (logged out?)"
    variants: dict[str, Any] = {}
    for family in document.get("families") or []:
        for variant in family.get("variants") or []:
            uid = variant.get("model_uid")
            if uid:
                variants[uid] = variant
    return variants, None


def preflight(
    policy: dict[str, Any],
    model: str,
    now: datetime.datetime | None = None,
    models_lister=list_models,
) -> dict[str, Any] | None:
    """None when a run may start, else a failure dict. Fails closed."""
    if not settings(policy).get("enabled"):
        return {"kind": "policy", "reason": "policy.devin.enabled is false"}
    if model not in allowed_models(policy):
        return {"kind": "policy", "reason": f"model {model} is not in policy.devin.allowed_models"}
    promo = promo_status(policy, now)
    if not promo["active"]:
        return {"kind": "policy", "reason": promo["reason"]}
    bin_path = devin_bin(policy)
    if bin_path is None:
        return {"kind": "tool", "reason": "devin CLI not found (policy.devin.bin)"}
    if not worker_config_template(policy).exists():
        return {"kind": "tool", "reason": f"worker config missing: {worker_config_template(policy)}"}
    variants, error = models_lister(bin_path)
    if variants is None:
        return {"kind": "tool", "reason": error or "devin models list failed"}
    variant = variants.get(model)
    if variant is None:
        return {"kind": "policy", "reason": f"{model} is not offered to this account"}
    tier = variant.get("cost_tier")
    if tier != FREE_TIER:
        return {"kind": "policy", "reason": f"{model} cost_tier is {tier!r}, not {FREE_TIER!r}; refusing a paid run"}
    return None


def build_command(
    bin_path: Path,
    model: str,
    prompt_path: Path,
    config_path: Path,
    export_path: Path,
    permission_mode: str,
) -> list[str]:
    # Trust is skipped because print mode cannot answer the trust prompt;
    # the worker config's permission rules are the guard instead.
    return [
        str(bin_path),
        "-p",
        "--prompt-file",
        str(prompt_path),
        "--model",
        model,
        "--permission-mode",
        permission_mode,
        "--config",
        str(config_path),
        "--respect-workspace-trust",
        "false",
        "--export",
        str(export_path),
    ]


def _strip_json_comments(text: str) -> str:
    """Remove ``//`` and ``/* */`` comments outside strings (the worker config is JSONC)."""
    out: list[str] = []
    i = 0
    in_string = False
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
        elif ch == "/" and text[i : i + 2] == "//":
            i = text.find("\n", i)
            if i == -1:
                break
            continue
        elif ch == "/" and text[i : i + 2] == "/*":
            end = text.find("*/", i + 2)
            i = len(text) if end == -1 else end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _worker_config(policy: dict[str, Any]) -> dict[str, Any]:
    return json.loads(_strip_json_comments(worker_config_template(policy).read_text(encoding="utf-8")))


def denied_prefixes(config: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(Exec command prefixes, other deny rules) from a worker config."""
    deny = (config.get("permissions") or {}).get("deny") or []
    commands: list[str] = []
    other: list[str] = []
    for rule in deny:
        if not isinstance(rule, str):
            continue
        match = re.fullmatch(r"Exec\((.*)\)", rule.strip())
        if match:
            commands.append(match.group(1))
        else:
            other.append(rule)
    return commands, other


def _denied_lists(config: dict[str, Any]) -> tuple[str, str]:
    """Backticked, comma-joined (Exec command prefixes, other deny rules) for prompt text."""
    commands, other = denied_prefixes(config)
    return ", ".join(f"`{cmd}`" for cmd in commands), ", ".join(f"`{rule}`" for rule in other)


def denied_section(policy: dict[str, Any]) -> str:
    """The generated prompt header listing the worker config's denied rules.

    ``devin -p`` ends the whole run on the first denied tool call, so every
    worker prompt carries the deny list; it is read from the config each run,
    never copied by hand.
    """
    try:
        commands, other = _denied_lists(_worker_config(policy))
    except Exception:
        return ""
    if not commands and not other:
        return ""
    lines = []
    if commands:
        lines.append(
            "Denied command prefixes (a denied call ends this run; never run a command starting with one): "
            + commands
            + "."
        )
    if other:
        lines.append("Other denied rules: " + other + ".")
    return "\n".join(lines) + "\n\n"


def pane_config_template() -> Path:
    """The shipped pane config template the launcher copies to each pane's state dir."""
    return paths.package_root() / "team" / "devin-pane.config.json"


def pane_denied_line(name: str) -> str:
    """The deny list ``hx say`` appends to a message for a Devin pane, as one line.

    A pane gets no run preamble, and a denied call cancels its turn, so the
    deny list travels on the message instead. It comes from the pane's live
    config (``<state>/<name>/config.json``, written by the launcher); the
    shipped pane template is the fallback. ``""`` means send unchanged: a
    message must never fail because the deny list could not be read.
    """
    for path in (state_dir() / name / "config.json", pane_config_template()):
        try:
            commands, other = _denied_lists(
                json.loads(_strip_json_comments(path.read_text(encoding="utf-8")))
            )
        except Exception:
            continue
        if not commands and not other:
            return ""
        line = "Denied for you (a denied call cancels your turn): " + (commands or "no Exec prefixes")
        if other:
            line += ". Also denied: " + other
        return line + "."
    return ""


def prepare_run(run_dir: Path, prompt: str, policy: dict[str, Any]) -> dict[str, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = run_dir / "prompt.md"
    prompt_path.write_text(denied_section(policy) + prompt, encoding="utf-8")
    config_path = run_dir / CONFIG_FILE
    shutil.copyfile(worker_config_template(policy), config_path)
    return {
        "prompt": prompt_path,
        "config": config_path,
        "export": run_dir / EXPORT_FILE,
        "pid": run_dir / PID_FILE,
    }


def scrubbed_env_names(environ: dict[str, str] | None = None) -> list[str]:
    environ = os.environ if environ is None else environ
    names = [k for k in environ if k.upper() in SCRUBBED_ENV]
    names += [k for k in environ if SECRET_ENV_RE.search(k) and k.upper() not in KEPT_SECRET_ENV]
    return sorted(set(names))


def run_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in scrubbed_env_names(env):
        env.pop(key, None)
    return env


# --- concurrency slots -------------------------------------------------------


def slots_dir() -> Path:
    return state_dir() / "devin-slots"


def live_sessions(agents: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Devin-kind Herdr panes from an ``herdr agent list`` agent list.

    Any name counts: a Devin pane outside the team prefix still shares the
    account's concurrency. ``None`` means the agent list itself is unknown.
    """
    if agents is None:
        return None
    return [
        {"name": a.get("name"), "status": a.get("agent_status"), "cwd": a.get("cwd")}
        for a in agents
        if isinstance(a, dict) and a.get("agent") == PROVIDER
    ]


def _live_sessions_path() -> Path:
    return slots_dir() / LIVE_SESSIONS_FILE


def _recorded_live_sessions(now: datetime.datetime) -> list[dict[str, Any]] | None:
    """Live Devin panes as of the last usage snapshot, until the record expires."""
    path = _live_sessions_path()
    try:
        if _slot_expired(path, now):
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    sessions = data.get("sessions") if isinstance(data, dict) else None
    return [s for s in sessions or [] if isinstance(s, dict)]


def _read_live_sessions() -> list[dict[str, Any]] | None:
    from hx.usage import herdr_agents

    try:
        return live_sessions(herdr_agents())
    except Exception:
        return None


def mark_live_sessions(sessions: list[dict[str, Any]], now: datetime.datetime | None = None) -> None:
    """Record the latest live Devin pane snapshot for diagnostics.

    Written by ``hx usage``/``hx route`` snapshots for diagnostics. Admission
    always refreshes the live pane list rather than trusting this cache.
    """
    now = now or _utcnow()
    expires = now + datetime.timedelta(seconds=LIVE_SESSIONS_TTL_S)
    payload = json.dumps(
        {"taken_at": now.isoformat(), "expires_at": expires.isoformat(), "sessions": sessions}
    )
    try:
        slots_dir().mkdir(parents=True, exist_ok=True)
        with _slots_lock():
            _live_sessions_path().write_text(payload, encoding="utf-8")
    except (TimeoutError, OSError):
        pass


@contextmanager
def _slots_lock():
    """Mutex for the slot table: O_EXCL lock file, stale after SLOT_LOCK_STALE_S."""
    lock = slots_dir() / ".lock"
    deadline = time.monotonic() + SLOT_LOCK_WAIT_S
    while True:
        try:
            os.close(os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > SLOT_LOCK_STALE_S:
                    lock.unlink()
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError("devin slot lock busy")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def acquire_slot(
    max_concurrent: int,
    timeout: float,
    info: dict[str, Any],
    now: datetime.datetime | None = None,
) -> Path | None:
    """Claim one of ``max_concurrent`` machine-wide run slots.

    The whole check-and-claim runs under one lock, so two runs can never take
    the same slot. Live Devin panes share the cap: run slots plus
    live sessions together must stay under ``max_concurrent``. A slot expires
    ``timeout + SLOT_GRACE_S`` after it was taken, so a crashed hx process
    cannot hold it forever.
    """
    now = now or _utcnow()
    if max_concurrent < 1:
        return None
    live = _read_live_sessions()
    if live is None:
        return None
    directory = slots_dir()
    directory.mkdir(parents=True, exist_ok=True)
    expires = now + datetime.timedelta(seconds=timeout + SLOT_GRACE_S)
    payload = json.dumps({**info, "taken_at": now.isoformat(), "expires_at": expires.isoformat()})
    try:
        with _slots_lock():
            held = len(live)
            free: Path | None = None
            for index in range(int(max_concurrent)):
                slot = directory / f"slot-{index}.json"
                if slot.exists() and not _slot_expired(slot, now):
                    held += 1
                elif free is None:
                    free = slot
            if free is not None and held < int(max_concurrent):
                free.write_text(payload, encoding="utf-8")
                return free
    except (TimeoutError, OSError):
        return None
    return None


def _slot_expired(slot: Path, now: datetime.datetime) -> bool:
    try:
        data = json.loads(slot.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    expires = _parse_ts(data.get("expires_at")) if isinstance(data, dict) else None
    return expires is None or now >= expires


def release_slot(slot: Path | None, slot_id: str | None = None) -> None:
    """Free a slot, but only while it still belongs to this run (``slot_id``)."""
    if slot is None:
        return
    try:
        with _slots_lock():
            if slot_id is not None:
                try:
                    owner = json.loads(slot.read_text(encoding="utf-8")).get("id")
                except (OSError, json.JSONDecodeError, AttributeError):
                    return
                if owner != slot_id:
                    return
            slot.unlink()
    except (TimeoutError, OSError):
        pass


def active_slots(now: datetime.datetime | None = None) -> list[dict[str, Any]]:
    now = now or _utcnow()
    directory = slots_dir()
    if not directory.exists():
        return []
    active = []
    for slot in sorted(directory.glob("slot-*.json")):
        if _slot_expired(slot, now):
            continue
        try:
            active.append(json.loads(slot.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return active


# --- quota errors -----------------------------------------------------------


def record_error(failure: dict[str, Any], model: str, now: datetime.datetime | None = None) -> None:
    """Remember a quota failure so the router skips Devin for a while."""
    if failure.get("kind") != "quota":
        return
    now = now or _utcnow()
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        (state_dir() / ERROR_STATE_FILE).write_text(
            json.dumps({"at": now.isoformat(), "model": model, "reason": failure.get("reason")}),
            encoding="utf-8",
        )
    except OSError:
        pass


def last_error(now: datetime.datetime | None = None) -> dict[str, Any] | None:
    now = now or _utcnow()
    path = state_dir() / ERROR_STATE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    at = _parse_ts(data.get("at"))
    if at is None:
        return None
    data["age_seconds"] = round((now - at).total_seconds())
    return data


# --- status for hx usage / hx route ------------------------------------------


def usage(
    policy: dict[str, Any],
    now: datetime.datetime | None = None,
    agents: Any = _AGENTS_UNSET,
) -> dict[str, Any]:
    """Devin pool status without a network call (the run preflight does that).

    ``agents`` is the ``herdr agent list`` agent list from the same snapshot
    ``usage.agents`` reports. Explicit ``None`` means unknown and refuses
    admission; an omitted agent list is read through the shared Herdr helper.

    Never raises: a malformed ``policy.devin`` must not take ``hx usage`` and
    ``hx route`` down for every other pool, so it reports status ``error``.
    """
    try:
        if agents is _AGENTS_UNSET:
            from hx.usage import herdr_agents
            try:
                agents = herdr_agents()
            except Exception:
                agents = None
        return _usage(policy, now or _utcnow(), agents)
    except Exception as exc:
        return {"provider": PROVIDER, "status": "error", "reason": f"policy.devin unreadable: {exc}", "tag": "unknown"}


def _usage(
    policy: dict[str, Any], now: datetime.datetime, agents: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    conf = settings(policy)
    promo = promo_status(policy, now)
    error = last_error(now)
    slots = active_slots(now)
    live_now = live_sessions(agents)
    max_concurrent = int(conf.get("max_concurrent", 1))
    unmapped = sorted({model_for_effort(e, policy) for e in DEFAULT_MODELS} - set(allowed_models(policy)))
    if not conf.get("enabled"):
        status, reason = "disabled", "policy.devin.enabled is false"
    elif max_concurrent < 1:
        status, reason = "disabled", "policy.devin.max_concurrent is below 1"
    elif not promo["active"]:
        status, reason = "disabled", promo["reason"]
    elif devin_bin(policy) is None:
        status, reason = "disabled", "devin CLI not found"
    elif not worker_config_template(policy).exists():
        status, reason = "disabled", "worker config template missing"
    elif unmapped:
        status, reason = "disabled", f"policy.devin.models maps to ids outside allowed_models: {unmapped}"
    elif error and error.get("age_seconds", 10**9) < QUOTA_ERROR_WINDOW_S:
        status, reason = "exhausted", f"quota/rate-limit error {error['age_seconds']}s ago"
    elif live_now is None:
        status, reason = "busy", "live Devin pane data unknown; refusing new sessions"
    elif len(slots) + len(live_now) >= max_concurrent:
        used = len(slots) + len(live_now)
        if live_now:
            reason = f"{used}/{max_concurrent} devin sessions in use ({len(slots)} runs + {len(live_now)} live sessions)"
        else:
            reason = f"{len(slots)}/{max_concurrent} run slots in use"
        status = "busy"
    else:
        status, reason = "available", "free SWE-2 promotion active"
    return {
        "provider": PROVIDER,
        "status": status,
        "reason": reason,
        "tag": "measured",
        "promo": promo,
        "models": conf.get("models") or DEFAULT_MODELS,
        "active_runs": len(slots),
        "live_sessions": live_now,
        "reuse_available": status == "available" or (status == "busy" and live_now is not None),
        "max_concurrent": max_concurrent,
        "last_error": error,
    }


# --- export parsing and failure classification -------------------------------


def parse_export(path: Path) -> dict[str, Any] | None:
    """Summary of an ATIF export, or None when it is missing or malformed."""
    try:
        return _parse_export(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def _parse_export(document: Any) -> dict[str, Any]:
    agent_steps = [s for s in document.get("steps") or [] if isinstance(s, dict) and s.get("source") == "agent"]
    models: set[str] = set()
    rejected: list[str] = []
    tool_calls = 0
    final_message = ""
    for step in agent_steps:
        for key in (step.get("model_name"), (step.get("extra") or {}).get("generation_model")):
            if key:
                models.add(key)
        calls = {c.get("tool_call_id"): c for c in step.get("tool_calls") or [] if isinstance(c, dict)}
        tool_calls += len(calls)
        for obs in (step.get("observation") or {}).get("results") or []:
            content = str(obs.get("content") or "")
            if content.strip().lower().startswith(REJECTION_PREFIXES):
                call = calls.get(obs.get("source_call_id")) or {}
                rejected.append(f"{call.get('function_name', '?')} {json.dumps(call.get('arguments'))[:200]}")
        if (step.get("message") or "").strip():
            final_message = step["message"].strip()
    last = agent_steps[-1] if agent_steps else {}
    metrics = document.get("final_metrics") or {}
    prompt_tokens = int(metrics.get("total_prompt_tokens") or 0)
    output_tokens = int(metrics.get("total_completion_tokens") or 0)
    return {
        "session_id": document.get("session_id"),
        "agent_label": (document.get("agent") or {}).get("model_name"),
        "permission_mode": ((document.get("agent") or {}).get("extra") or {}).get("permission_mode"),
        "models": sorted(models),
        "agent_steps": len(agent_steps),
        "tool_calls": tool_calls,
        "rejected": rejected,
        # A finished run ends on a message-only step. Most working steps carry
        # text and tool calls, so tool calls on the last step mean it stopped.
        "ended_on_tool_call": bool(last.get("tool_calls")),
        "final_message": final_message,
        "tokens": {
            "total": prompt_tokens + output_tokens,
            "input": prompt_tokens,
            "output": output_tokens,
            "reasoning": 0,
            "cache_read": int(metrics.get("total_cached_tokens") or 0),
            "cache_write": 0,
        },
    }


def _mode_key(mode: Any) -> str:
    return re.sub(r"[^a-z]", "", str(mode or "").lower())


def detect_failure(
    parsed: dict[str, Any] | None,
    expected_model: str,
    exit_code: int | None,
    timed_out: bool,
    terminated: bool,
    output_text: str = "",
    classify=None,
    expected_mode: str | None = None,
) -> dict[str, Any] | None:
    failure = _detect_failure(parsed, expected_model, exit_code, timed_out, terminated, expected_mode)
    text = (output_text or "").strip()
    if failure and failure["kind"] in ("tool", "exit") and text and classify is not None and classify(text) == "quota":
        return {"kind": "quota", "reason": text[:300]}
    return failure


def _detect_failure(
    parsed: dict[str, Any] | None,
    expected_model: str,
    exit_code: int | None,
    timed_out: bool,
    terminated: bool,
    expected_mode: str | None,
) -> dict[str, Any] | None:
    if timed_out:
        return {"kind": "tool", "reason": "timeout" if terminated else "termination-unconfirmed"}
    if parsed is None:
        if exit_code not in (None, 0):
            return {"kind": "exit", "reason": f"devin exited {exit_code}"}
        return {"kind": "tool", "reason": "no readable export (run did not start, or export malformed)"}
    wrong = [m for m in parsed["models"] if m != expected_model]
    if wrong:
        return {"kind": "model-mismatch", "reason": f"expected {expected_model}, export shows {wrong}"}
    mode = parsed.get("permission_mode")
    if expected_mode and mode and _mode_key(mode) != _mode_key(expected_mode):
        return {"kind": "policy", "reason": f"ran in permission mode {mode}, expected {expected_mode}"}
    if parsed["rejected"]:
        return {"kind": "tool", "reason": f"permission denied: {parsed['rejected'][0]}"}
    if exit_code not in (None, 0):
        return {"kind": "exit", "reason": f"devin exited {exit_code}"}
    if parsed["agent_steps"] == 0:
        return {"kind": "tool", "reason": "no agent turns"}
    if expected_model not in parsed["models"]:
        return {"kind": "model-mismatch", "reason": f"export does not name {expected_model}"}
    if parsed["ended_on_tool_call"] or not parsed["final_message"]:
        return {"kind": "tool", "reason": "run ended without a final answer"}
    return None


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x00000102  # WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def kill_tree(pid: int) -> bool:
    if sys.platform == "win32":
        try:
            result = subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=10)
        except Exception:
            return False
        return result.returncode == 0 or not pid_alive(pid)
    try:
        os.kill(pid, 9)
    except OSError:
        pass
    return not pid_alive(pid)


def new_slot_info(cwd: Path, title: str, model: str) -> dict[str, Any]:
    return {"id": uuid.uuid4().hex, "cwd": str(cwd), "title": title, "model": model, "hx_pid": os.getpid()}
