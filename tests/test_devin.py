"""Tests for hx.devin: promo gate, preflight, export parsing, slots, usage."""

from __future__ import annotations

import datetime
import json
import os
import time
from pathlib import Path

import pytest

from hx import devin
from hx import ocrun

MODEL = "swe-2-high"
ENDS_AT = "2026-11-30T10:00:00Z"
NOW = datetime.datetime(2026, 11, 1, tzinfo=datetime.timezone.utc)


@pytest.fixture(autouse=True)
def synthetic_herdr_agents(monkeypatch):
    monkeypatch.setattr("hx.usage.herdr_agents", lambda: [])


def _policy(tmp_path: Path, **overrides) -> dict:
    """A policy that passes preflight for a Free SWE-2 model."""
    bin_path = tmp_path / "devin.exe"
    bin_path.write_text("dummy", encoding="utf-8")
    config = tmp_path / "worker.config.json"
    config.write_text("{}", encoding="utf-8")
    conf = {
        "enabled": True,
        "bin": str(bin_path),
        "worker_config": str(config),
        "kinds": list(devin.DEFAULT_KINDS),
        "models": dict(devin.DEFAULT_MODELS),
        "allowed_models": ["swe-2-medium", "swe-2-high", "swe-2-max"],
        "permission_mode": "accept-edits",
        "max_concurrent": 1,
        "promo": {"ends_at": ENDS_AT},
    }
    conf.update(overrides)
    return {"devin": conf}


def _free_lister(bin_path):  # noqa: ARG001
    return (
        {
            "swe-2-medium": {"model_uid": "swe-2-medium", "cost_tier": "Free"},
            "swe-2-high": {"model_uid": "swe-2-high", "cost_tier": "Free"},
            "swe-2-max": {"model_uid": "swe-2-max", "cost_tier": "Free"},
            "swe-2-high-lightning": {"model_uid": "swe-2-high-lightning", "cost_tier": "High cost"},
        },
        None,
    )


# ---------- promo_status ----------


def test_promo_active(tmp_path: Path) -> None:
    status = devin.promo_status(_policy(tmp_path), now=NOW)
    assert status["active"] is True
    assert status["reason"] == "promotion active"
    assert status["hours_left"] > 0


def test_promo_inactive_exactly_at_ends_at(tmp_path: Path) -> None:
    ends = datetime.datetime(2026, 11, 30, 10, 0, tzinfo=datetime.timezone.utc)
    status = devin.promo_status(_policy(tmp_path), now=ends)
    assert status["active"] is False
    assert "promotion ended" in status["reason"]


def test_promo_inactive_after_ends_at(tmp_path: Path) -> None:
    after = datetime.datetime(2026, 12, 1, tzinfo=datetime.timezone.utc)
    status = devin.promo_status(_policy(tmp_path), now=after)
    assert status["active"] is False
    assert "promotion ended" in status["reason"]


@pytest.mark.parametrize(
    "promo",
    [
        {},
        {"ends_at": ""},
        {"ends_at": "sometime in december"},
        {"ends_at": "2026-11-30 10:00:00"},
        {"verified_at": "2026-10-31"},
    ],
)
def test_promo_missing_or_garbage_ends_at_is_inactive(tmp_path: Path, promo: dict) -> None:
    status = devin.promo_status(_policy(tmp_path, promo=promo), now=NOW)
    assert status["active"] is False
    assert status["reason"] == "no valid policy.devin.promo.ends_at"


# ---------- preflight ----------


def test_preflight_ok_for_free_swe2(tmp_path: Path) -> None:
    assert devin.preflight(_policy(tmp_path), MODEL, now=NOW, models_lister=_free_lister) is None


def test_preflight_refuses_model_outside_allowed_models(tmp_path: Path) -> None:
    failure = devin.preflight(_policy(tmp_path), "claude-opus-5-5", now=NOW, models_lister=_free_lister)
    assert failure["kind"] == "policy"
    assert "not in policy.devin.allowed_models" in failure["reason"]


def test_preflight_refuses_lightning_id(tmp_path: Path) -> None:
    """A lightning id is refused twice over: it is never in allowed_models, and
    even if someone adds it the live list tags it High cost."""
    failure = devin.preflight(_policy(tmp_path), "swe-2-high-lightning", now=NOW, models_lister=_free_lister)
    assert failure["kind"] == "policy"
    assert "not in policy.devin.allowed_models" in failure["reason"]

    policy = _policy(tmp_path, allowed_models=["swe-2-high", "swe-2-high-lightning"])
    failure = devin.preflight(policy, "swe-2-high-lightning", now=NOW, models_lister=_free_lister)
    assert failure["kind"] == "policy"
    assert "refusing a paid run" in failure["reason"]


def test_preflight_refuses_non_free_cost_tier(tmp_path: Path) -> None:
    def lister(bin_path):  # noqa: ARG001
        return ({MODEL: {"model_uid": MODEL, "cost_tier": "High cost"}}, None)

    failure = devin.preflight(_policy(tmp_path), MODEL, now=NOW, models_lister=lister)
    assert failure["kind"] == "policy"
    assert "refusing a paid run" in failure["reason"]


def test_preflight_refuses_model_not_offered(tmp_path: Path) -> None:
    def lister(bin_path):  # noqa: ARG001
        return ({"swe-2-medium": {"model_uid": "swe-2-medium", "cost_tier": "Free"}}, None)

    failure = devin.preflight(_policy(tmp_path), MODEL, now=NOW, models_lister=lister)
    assert failure == {"kind": "policy", "reason": "swe-2-high is not offered to this account"}


def test_preflight_lister_error_is_tool_failure(tmp_path: Path) -> None:
    def lister(bin_path):  # noqa: ARG001
        return (None, "devin models list failed: boom")

    failure = devin.preflight(_policy(tmp_path), MODEL, now=NOW, models_lister=lister)
    assert failure == {"kind": "tool", "reason": "devin models list failed: boom"}


def test_preflight_disabled_policy(tmp_path: Path) -> None:
    failure = devin.preflight(_policy(tmp_path, enabled=False), MODEL, now=NOW, models_lister=_free_lister)
    assert failure == {"kind": "policy", "reason": "policy.devin.enabled is false"}


def test_preflight_ended_promo(tmp_path: Path) -> None:
    policy = _policy(tmp_path, promo={"ends_at": "2020-01-01T00:00:00Z"})
    failure = devin.preflight(policy, MODEL, now=NOW, models_lister=_free_lister)
    assert failure["kind"] == "policy"
    assert "promotion ended" in failure["reason"]


# ---------- parse_export + detect_failure ----------


def _agent_step(
    message: str = "",
    model: str = MODEL,
    tool_calls: list | None = None,
    results: list | None = None,
) -> dict:
    return {
        "source": "agent",
        "model_name": model,
        "extra": {"generation_model": model},
        "message": message,
        "tool_calls": tool_calls or [],
        "observation": {"results": results or []},
    }


def _exec_step(model: str = MODEL, output: str = "Output from command `ls`: file1.py") -> dict:
    return _agent_step(
        model=model,
        tool_calls=[
            {"tool_call_id": "c1", "function_name": "exec_command", "arguments": {"command": "ls"}}
        ],
        results=[{"source_call_id": "c1", "content": output}],
    )


def _export_doc(steps: list, **overrides) -> dict:
    doc = {
        "session_id": "ses_devin_1",
        "agent": {"model_name": "SWE-2 High", "extra": {"permission_mode": "accept-edits"}},
        "steps": steps,
        "final_metrics": {
            "total_prompt_tokens": 120,
            "total_completion_tokens": 30,
            "total_cached_tokens": 40,
        },
    }
    doc.update(overrides)
    return doc


def _write_export(tmp_path: Path, doc: dict) -> Path:
    path = tmp_path / "export.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_parse_export_success(tmp_path: Path) -> None:
    path = _write_export(
        tmp_path,
        _export_doc([_exec_step(), _agent_step(message="done: wrote the tests")]),
    )
    parsed = devin.parse_export(path)
    assert parsed["session_id"] == "ses_devin_1"
    assert parsed["permission_mode"] == "accept-edits"
    assert parsed["models"] == [MODEL]
    assert parsed["agent_steps"] == 2
    assert parsed["tool_calls"] == 1
    assert parsed["rejected"] == []
    assert parsed["ended_on_tool_call"] is False
    assert parsed["final_message"] == "done: wrote the tests"
    assert parsed["tokens"] == {
        "total": 150,
        "input": 120,
        "output": 30,
        "reasoning": 0,
        "cache_read": 40,
        "cache_write": 0,
    }
    assert devin.detect_failure(parsed, MODEL, 0, False, True) is None


def test_detect_failure_model_mismatch_on_any_step(tmp_path: Path) -> None:
    path = _write_export(
        tmp_path,
        _export_doc(
            [
                _agent_step(message="working", model=MODEL),
                _agent_step(message="done", model="swe-2-max"),
            ]
        ),
    )
    parsed = devin.parse_export(path)
    assert parsed["models"] == ["swe-2-high", "swe-2-max"]
    failure = devin.detect_failure(parsed, MODEL, 0, False, True)
    assert failure["kind"] == "model-mismatch"
    assert "swe-2-max" in failure["reason"]


REJECTION_WORDINGS = [
    "Permission denied for this tool.",
    "Tool execution was rejected by the user",
    "Permission to run the command `git push` was denied. The user needs to approve command execution.",
]


@pytest.mark.parametrize("wording", REJECTION_WORDINGS)
def test_detect_failure_rejection_wordings(tmp_path: Path, wording: str) -> None:
    step = _agent_step(
        model=MODEL,
        tool_calls=[
            {
                "tool_call_id": "c1",
                "function_name": "exec_command",
                "arguments": {"command": "git push"},
            }
        ],
        results=[{"source_call_id": "c1", "content": wording}],
    )
    path = _write_export(tmp_path, _export_doc([step, _agent_step(message="")]))
    parsed = devin.parse_export(path)
    assert len(parsed["rejected"]) == 1
    assert "exec_command" in parsed["rejected"][0]
    failure = devin.detect_failure(parsed, MODEL, 0, False, True)
    assert failure["kind"] == "tool"
    assert failure["reason"].startswith("permission denied:")


def test_command_output_permission_denied_is_not_a_rejection(tmp_path: Path) -> None:
    """A command's own "permission denied" text always carries the exec tool's
    "Output from command" header and must not count as a rejection."""
    step = _exec_step(output="Output from command `git push`: permission denied (publickey).")
    path = _write_export(
        tmp_path,
        _export_doc([step, _agent_step(message="push failed; reported to the lead")]),
    )
    parsed = devin.parse_export(path)
    assert parsed["rejected"] == []
    assert devin.detect_failure(parsed, MODEL, 0, False, True) is None


def test_file_view_mentioning_rejections_is_not_a_rejection(tmp_path: Path) -> None:
    """Regression, 2026-10-31 live run: reading hx/devin.py (which says
    "denied" and "rejected") was classified as a permission denial."""
    step = _agent_step(
        model=MODEL,
        tool_calls=[{"tool_call_id": "r1", "function_name": "read", "arguments": {"file_path": "hx/devin.py"}}],
        results=[
            {
                "source_call_id": "r1",
                "content": '<file-view path="hx/devin.py">\nTool execution was rejected by the user\n'
                "Permission denied for this tool.\n</file-view>",
            }
        ],
    )
    path = _write_export(tmp_path, _export_doc([step, _agent_step(message="tests written")]))
    parsed = devin.parse_export(path)
    assert parsed["rejected"] == []
    assert devin.detect_failure(parsed, MODEL, 0, False, True) is None


def test_detect_failure_ended_on_tool_call(tmp_path: Path) -> None:
    path = _write_export(tmp_path, _export_doc([_exec_step()]))
    parsed = devin.parse_export(path)
    assert parsed["ended_on_tool_call"] is True
    failure = devin.detect_failure(parsed, MODEL, 0, False, True)
    assert failure == {"kind": "tool", "reason": "run ended without a final answer"}


def test_detect_failure_last_step_with_message_and_tool_calls(tmp_path: Path) -> None:
    """Most working steps carry text AND tool calls; the run only finished
    when the last step is a message on its own."""
    last = _agent_step(
        message="let me check one more thing",
        tool_calls=[
            {"tool_call_id": "c1", "function_name": "exec_command", "arguments": {"command": "ls"}}
        ],
    )
    parsed = devin.parse_export(_write_export(tmp_path, _export_doc([_exec_step(), last])))
    assert parsed["ended_on_tool_call"] is True
    assert parsed["final_message"] == "let me check one more thing"
    failure = devin.detect_failure(parsed, MODEL, 0, False, True)
    assert failure == {"kind": "tool", "reason": "run ended without a final answer"}

    finished = devin.parse_export(
        _write_export(tmp_path, _export_doc([_exec_step(), _agent_step(message="done")]))
    )
    assert finished["ended_on_tool_call"] is False
    assert devin.detect_failure(finished, MODEL, 0, False, True) is None


@pytest.mark.parametrize("payload", ["null", "[]"])
def test_parse_export_non_object_document_returns_none(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "export.json"
    path.write_text(payload, encoding="utf-8")
    assert devin.parse_export(path) is None


@pytest.mark.parametrize("field", ["extra", "observation"])
def test_parse_export_non_dict_step_field_returns_none(tmp_path: Path, field: str) -> None:
    step = _agent_step(message="done")
    step[field] = "malformed"
    assert devin.parse_export(_write_export(tmp_path, _export_doc([step]))) is None


def test_parse_export_string_final_metrics_returns_none(tmp_path: Path) -> None:
    doc = _export_doc([_agent_step(message="done")], final_metrics="malformed")
    assert devin.parse_export(_write_export(tmp_path, doc)) is None


def test_detect_failure_no_agent_steps(tmp_path: Path) -> None:
    path = _write_export(tmp_path, _export_doc([{"source": "user", "message": "hi"}]))
    parsed = devin.parse_export(path)
    assert parsed["agent_steps"] == 0
    failure = devin.detect_failure(parsed, MODEL, 0, False, True)
    assert failure == {"kind": "tool", "reason": "no agent turns"}


def test_detect_failure_missing_export_quota_text(tmp_path: Path) -> None:
    assert devin.parse_export(tmp_path / "export.json") is None
    failure = devin.detect_failure(
        None,
        MODEL,
        0,
        False,
        True,
        output_text="Error: HTTP 429 quota exceeded",
        classify=ocrun.classify_error_text,
    )
    assert failure["kind"] == "quota"
    assert "429" in failure["reason"]


def test_detect_failure_missing_export_no_output(tmp_path: Path) -> None:
    failure = devin.detect_failure(None, MODEL, 0, False, True, output_text="")
    assert failure == {"kind": "tool", "reason": "no readable export (run did not start, or export malformed)"}


def test_detect_failure_timeout_terminated_vs_unconfirmed() -> None:
    terminated = devin.detect_failure(None, MODEL, None, True, True)
    assert terminated == {"kind": "tool", "reason": "timeout"}
    unconfirmed = devin.detect_failure(None, MODEL, None, True, False)
    assert unconfirmed == {"kind": "tool", "reason": "termination-unconfirmed"}


def test_detect_failure_429_output_promotes_parsed_failures(tmp_path: Path) -> None:
    """A 429 on the process output upgrades tool/exit failures to quota even
    when the export parsed; a clean run mentioning 429 stays ok."""
    stopped = devin.parse_export(_write_export(tmp_path, _export_doc([_exec_step()])))
    failure = devin.detect_failure(
        stopped,
        MODEL,
        0,
        False,
        True,
        output_text="Error: HTTP 429 Too Many Requests",
        classify=ocrun.classify_error_text,
    )
    assert failure["kind"] == "quota"
    assert "429" in failure["reason"]

    finished = devin.parse_export(
        _write_export(tmp_path, _export_doc([_exec_step(), _agent_step(message="done")]))
    )
    exited = devin.detect_failure(
        finished,
        MODEL,
        1,
        False,
        True,
        output_text="Error: HTTP 429 Too Many Requests",
        classify=ocrun.classify_error_text,
    )
    assert exited["kind"] == "quota"

    assert (
        devin.detect_failure(
            finished,
            MODEL,
            0,
            False,
            True,
            output_text="note: the build log shows a 429 Too Many Requests warning",
            classify=ocrun.classify_error_text,
        )
        is None
    )


@pytest.mark.parametrize("export_mode", ["AcceptEdits", "accept-edits"])
def test_detect_failure_matching_permission_mode_is_ok(tmp_path: Path, export_mode: str) -> None:
    doc = _export_doc([_exec_step(), _agent_step(message="done")])
    doc["agent"]["extra"]["permission_mode"] = export_mode
    parsed = devin.parse_export(_write_export(tmp_path, doc))
    assert devin.detect_failure(parsed, MODEL, 0, False, True, expected_mode="accept-edits") is None


@pytest.mark.parametrize("export_mode", ["Normal", "Dangerous"])
def test_detect_failure_wrong_permission_mode_is_policy(tmp_path: Path, export_mode: str) -> None:
    doc = _export_doc([_exec_step(), _agent_step(message="done")])
    doc["agent"]["extra"]["permission_mode"] = export_mode
    parsed = devin.parse_export(_write_export(tmp_path, doc))
    failure = devin.detect_failure(parsed, MODEL, 0, False, True, expected_mode="accept-edits")
    assert failure["kind"] == "policy"
    assert export_mode in failure["reason"]


# ---------- concurrency slots ----------


def test_slots_acquire_up_to_max_then_refuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    info = devin.new_slot_info(tmp_path, "run-a", MODEL)
    first = devin.acquire_slot(2, 60.0, info)
    second = devin.acquire_slot(2, 60.0, info)
    assert first is not None and second is not None
    assert first != second
    assert devin.acquire_slot(2, 60.0, info) is None
    assert len(devin.active_slots()) == 2


def test_slots_release_frees_slot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    slot = devin.acquire_slot(1, 60.0, {"id": "a"})
    assert slot is not None
    assert devin.acquire_slot(1, 60.0, {"id": "b"}) is None
    devin.release_slot(slot)
    again = devin.acquire_slot(1, 60.0, {"id": "b"})
    assert again is not None
    devin.release_slot(again)
    devin.release_slot(None)


def test_slots_expired_slot_is_reclaimed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    t0 = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    slot = devin.acquire_slot(1, 10.0, {"id": "a"}, now=t0)
    assert slot is not None
    held = devin.acquire_slot(1, 10.0, {"id": "b"}, now=t0 + datetime.timedelta(seconds=5))
    assert held is None
    after = t0 + datetime.timedelta(seconds=10 + devin.SLOT_GRACE_S + 1)
    reclaimed = devin.acquire_slot(1, 10.0, {"id": "b"}, now=after)
    assert reclaimed == slot
    payload = json.loads(reclaimed.read_text(encoding="utf-8"))
    assert payload["id"] == "b"


def test_release_slot_with_foreign_id_keeps_slot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A release call only frees the slot while it still belongs to that run:
    a slot re-taken by another run must not be deleted by a stale release."""
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    slot = devin.acquire_slot(1, 60.0, {"id": "a"})
    assert slot is not None
    devin.release_slot(slot, "somebody-else")
    assert slot.exists()
    assert devin.acquire_slot(1, 60.0, {"id": "b"}) is None
    devin.release_slot(slot, "a")
    assert not slot.exists()


def test_acquire_slot_max_concurrent_zero_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    assert devin.acquire_slot(0, 60.0, {"id": "a"}) is None
    assert not devin.slots_dir().exists()


def test_acquire_slot_breaks_stale_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A .lock older than SLOT_LOCK_STALE_S belongs to a dead holder."""
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    directory = devin.slots_dir()
    directory.mkdir(parents=True)
    lock = directory / ".lock"
    lock.write_text("held by a crashed hx", encoding="utf-8")
    stale = time.time() - devin.SLOT_LOCK_STALE_S - 5
    os.utime(lock, (stale, stale))
    slot = devin.acquire_slot(1, 60.0, {"id": "a"})
    assert slot is not None
    assert not lock.exists()


@pytest.mark.parametrize("content", ["", "not json {"])
def test_acquire_slot_reclaims_unreadable_slot_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    directory = devin.slots_dir()
    directory.mkdir(parents=True)
    slot = directory / "slot-0.json"
    slot.write_text(content, encoding="utf-8")
    reclaimed = devin.acquire_slot(1, 60.0, {"id": "a"})
    assert reclaimed == slot
    assert json.loads(reclaimed.read_text(encoding="utf-8"))["id"] == "a"


# ---------- environment scrubbing ----------


def test_scrubbed_env_names_drops_secrets_keeps_questlog() -> None:
    environ = {
        "DEVIN_MODEL": "swe-2-max",
        "GH_TOKEN": "ghp_x",
        "OPENAI_API_KEY": "sk-x",
        "MY_SECRET": "x",
        "QUESTLOG_TOKEN_OPENCODE": "kept for the MCP launcher",
        "PATH": "C:\\bin",
    }
    names = devin.scrubbed_env_names(environ)
    for gone in ("DEVIN_MODEL", "GH_TOKEN", "OPENAI_API_KEY", "MY_SECRET"):
        assert gone in names
    assert "QUESTLOG_TOKEN_OPENCODE" not in names
    assert "PATH" not in names


# ---------- usage status ----------


def test_usage_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    status = devin.usage(_policy(tmp_path), now=NOW)
    assert status["provider"] == "devin"
    assert status["status"] == "available"
    assert status["active_runs"] == 0
    assert status["max_concurrent"] == 1
    assert status["models"] == devin.DEFAULT_MODELS


def test_usage_busy_when_slots_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    slot = devin.acquire_slot(1, 600.0, {"id": "a"}, now=NOW)
    assert slot is not None
    status = devin.usage(_policy(tmp_path), now=NOW)
    assert status["status"] == "busy"
    assert status["active_runs"] == 1
    assert "1/1 run slots in use" in status["reason"]


def test_usage_exhausted_after_quota_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    devin.record_error({"kind": "quota", "reason": "HTTP 429 too many requests"}, MODEL, now=NOW)
    status = devin.usage(_policy(tmp_path), now=NOW)
    assert status["status"] == "exhausted"
    assert status["last_error"]["model"] == MODEL


def test_usage_non_quota_error_does_not_exhaust(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    devin.record_error({"kind": "tool", "reason": "timeout"}, MODEL, now=NOW)
    status = devin.usage(_policy(tmp_path), now=NOW)
    assert status["status"] == "available"
    assert status["last_error"] is None


def test_usage_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    status = devin.usage(_policy(tmp_path, enabled=False), now=NOW)
    assert status["status"] == "disabled"
    assert "enabled is false" in status["reason"]

    ended = devin.usage(_policy(tmp_path, promo={"ends_at": "2020-01-01T00:00:00Z"}), now=NOW)
    assert ended["status"] == "disabled"
    assert "promotion ended" in ended["reason"]


def test_usage_zero_max_concurrent_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    status = devin.usage(_policy(tmp_path, max_concurrent=0), now=NOW)
    assert status["status"] == "disabled"
    assert "max_concurrent" in status["reason"]


def test_usage_models_map_outside_allowed_models_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An effort mapping pointing at a non-allowed id fails closed."""
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    models = dict(devin.DEFAULT_MODELS)
    models["medium"] = "swe-2-high-lightning"
    status = devin.usage(_policy(tmp_path, models=models), now=NOW)
    assert status["status"] == "disabled"
    assert "allowed_models" in status["reason"]
    assert "swe-2-high-lightning" in status["reason"]


@pytest.mark.parametrize("override", [{"max_concurrent": "abc"}, {"bin": 5}])
def test_usage_malformed_policy_reports_error_not_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, override: dict
) -> None:
    """A malformed policy.devin must not take hx usage/route down for every
    other pool; it reports status error instead."""
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path))
    status = devin.usage(_policy(tmp_path, **override), now=NOW)
    assert status["provider"] == "devin"
    assert status["status"] == "error"
    assert "unreadable" in status["reason"]
