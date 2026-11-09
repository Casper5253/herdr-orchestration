"""F02/F16: own() proves the terminal is ours; hc-binding v2 reader rules.

F02: a stale HC_TERMINAL_ID (the audit's finished-t1/live-t24 case) is an
IdentityError, never another terminal's labels and never a silent fallback.
F16: a v2 binding counts only for this terminal, this generation, this
provider, and a provider pid that is the terminal's own, its foreground, or
a live descendant — the PowerShell-wrapper case.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hx import host
from test_host_hc import FakeHc, _terminal


def _chain(*pids: int) -> dict[int, int]:
    """A parent map placing `pids` in order above this process."""
    parents: dict[int, int] = {}
    previous = os.getpid()
    for pid in pids:
        parents[previous] = pid
        previous = pid
    parents[previous] = 0
    return parents


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeHc:
    fake = FakeHc()
    fake.add(_terminal(
        "t24", team="dev", role="codex", app="codex", state="done",
        pid=1944, created_ms=1794205754477,
    ))
    fake.terminals["t24"]["labels"]["herdr.seen"] = "7"
    fake.terminals["t24"]["foreground"] = {
        "argv": ["powershell.exe"], "cwd": "C:\\Users\\caspe",
        "name": "powershell.exe", "pid": 1944,
    }
    monkeypatch.setenv("HC_TERMINAL_ID", "t24")
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HX_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(host.subprocess, "run", fake.run)
    monkeypatch.setattr(host, "tool_path", lambda name: "hc")
    monkeypatch.setattr(host, "_process_parents", lambda: _chain(1944))
    return fake


def _binding(tmp_path: Path, address: str, data: dict) -> None:
    directory = tmp_path / "state" / "hc-bindings"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{address}.json").write_text(json.dumps(data), encoding="utf-8")


# ---------- F02: own() validates the real terminal shape ----------


def test_own_accepts_the_real_t24_shape(fake: FakeHc) -> None:
    member = host.HcHost().own()
    assert member is not None
    assert member["name"] == "dev-codex"
    assert member["kind"] == "codex"
    assert member["address"] == "t24"


def test_a_finished_terminal_id_is_an_identity_error(fake: FakeHc, monkeypatch) -> None:
    monkeypatch.setenv("HC_TERMINAL_ID", "t1")
    fake.terminals["t1"]["state"] = "finished"
    with pytest.raises(host.IdentityError, match="finished"):
        host.HcHost().own()


def test_a_terminal_pid_that_is_not_an_ancestor_is_an_identity_error(
    fake: FakeHc, monkeypatch
) -> None:
    monkeypatch.setattr(host, "_process_parents", lambda: _chain(777))
    with pytest.raises(host.IdentityError, match="not this process or an ancestor"):
        host.HcHost().own()
    assert isinstance(host.IdentityError("x"), ValueError)


def test_an_unverifiable_terminal_is_an_identity_error(fake: FakeHc, monkeypatch) -> None:
    monkeypatch.setenv("HC_TERMINAL_ID", "t99")
    with pytest.raises(host.IdentityError, match="could not verify"):
        host.HcHost().own()


def test_details_for_another_terminal_are_an_identity_error(fake: FakeHc, monkeypatch) -> None:
    monkeypatch.setattr(host.HcHost, "_detail", lambda self, terminal: fake.terminals["t1"])
    with pytest.raises(host.IdentityError):
        host.HcHost().own()


def test_a_terminal_without_a_pid_is_an_identity_error(fake: FakeHc) -> None:
    fake.terminals["t24"]["pid"] = None
    with pytest.raises(host.IdentityError):
        host.HcHost().own()


def test_an_empty_process_inventory_never_proves_identity(fake: FakeHc, monkeypatch) -> None:
    monkeypatch.setattr(host, "_process_parents", lambda: {})
    with pytest.raises(host.IdentityError):
        host.HcHost().own()


def test_a_live_unlabelled_terminal_is_genuinely_not_a_member(
    fake: FakeHc, monkeypatch
) -> None:
    fake.terminals["t24"]["labels"] = {}
    assert host.HcHost().own() is None


def test_no_terminal_id_is_none_not_an_error() -> None:
    assert host.HcHost(terminal_id=None).own() is None


# ---------- F16: hc-binding v2 reader ----------


def _v2(**fields) -> dict:
    data = {
        "v": 2,
        "terminal": "t24",
        "terminal_created_ms": 1794205754477,
        "provider": "codex",
        "session_id": "ses_t24",
        "provider_pid": 1944,
        "written_ms": 1794206001990,
    }
    data.update(fields)
    return data


def test_a_v2_binding_for_the_terminal_pid_counts(
    fake: FakeHc, tmp_path: Path
) -> None:
    _binding(tmp_path, "t24", _v2())
    member = {m["address"]: m for m in host.HcHost().all_members()}["t24"]
    assert member["session_id"] == "ses_t24"
    assert member["session_reason"] == "v2 binding"


def test_a_v2_binding_for_the_foreground_pid_counts(
    fake: FakeHc, tmp_path: Path, monkeypatch
) -> None:
    fake.terminals["t24"]["foreground"]["pid"] = 2001
    monkeypatch.setattr(host, "_process_parents", lambda: {1944: 0, 2001: 1944})
    _binding(tmp_path, "t24", _v2(provider_pid=2001))
    member = {m["address"]: m for m in host.HcHost().all_members()}["t24"]
    assert member["session_id"] == "ses_t24"


def test_a_powershell_wrapped_provider_pid_counts(
    fake: FakeHc, tmp_path: Path, monkeypatch
) -> None:
    """The F16 case: terminal pid is the shell, the provider is its descendant."""
    fake.terminals["t24"]["pid"] = 111
    fake.terminals["t24"]["foreground"]["pid"] = 111
    _binding(tmp_path, "t24", _v2(provider_pid=222))
    monkeypatch.setattr(host, "_process_parents", lambda: {222: 111, 111: 0})
    member = {m["address"]: m for m in host.HcHost().all_members()}["t24"]
    assert member["session_id"] == "ses_t24"


@pytest.mark.parametrize("data,reason_part", [
    (_v2(terminal="t99"), "different terminal"),
    (_v2(terminal_created_ms=1), "another terminal generation"),
    (_v2(provider="claude"), "is not 'codex'"),
    (_v2(provider_pid="1944"), "no provider pid"),
    (_v2(session_id=""), "no session id"),
    (_v2(v=3), "unsupported binding version"),
])
def test_a_v2_binding_is_rejected_per_rule(
    fake: FakeHc, tmp_path: Path, data: dict, reason_part: str
) -> None:
    _binding(tmp_path, "t24", data)
    member = {m["address"]: m for m in host.HcHost().all_members()}["t24"]
    assert member["session_id"] is None
    assert reason_part in str(member["session_reason"])


def test_a_reused_address_rejects_the_old_binding(
    fake: FakeHc, tmp_path: Path
) -> None:
    """A new terminal generation has a different created_ms; the old binding is stale."""
    fake.terminals["t24"]["created_ms"] = 1794210000000
    _binding(tmp_path, "t24", _v2())
    member = {m["address"]: m for m in host.HcHost().all_members()}["t24"]
    assert member["session_id"] is None
    assert "generation" in str(member["session_reason"])


def test_a_foreign_live_provider_pid_is_rejected(
    fake: FakeHc, tmp_path: Path, monkeypatch
) -> None:
    _binding(tmp_path, "t24", _v2(provider_pid=555))
    monkeypatch.setattr(host, "_process_parents", lambda: {555: 777, 777: 0})
    member = {m["address"]: m for m in host.HcHost().all_members()}["t24"]
    assert member["session_id"] is None
    assert "descendant" in str(member["session_reason"])


def test_a_dead_provider_pid_is_rejected(
    fake: FakeHc, tmp_path: Path, monkeypatch
) -> None:
    _binding(tmp_path, "t24", _v2(provider_pid=555))
    monkeypatch.setattr(host, "_process_parents", lambda: {111: 0})
    member = {m["address"]: m for m in host.HcHost().all_members()}["t24"]
    assert member["session_id"] is None


@pytest.mark.parametrize("provider_pid", [1944, 2001])
def test_dead_terminal_or_foreground_pid_cannot_validate_a_binding(
    fake: FakeHc, tmp_path: Path, monkeypatch, provider_pid: int,
) -> None:
    fake.terminals["t24"]["foreground"]["pid"] = 2001
    _binding(tmp_path, "t24", _v2(provider_pid=provider_pid))
    monkeypatch.setattr(host, "_process_parents", lambda: {777: 0})
    member = {m["address"]: m for m in host.HcHost().all_members()}["t24"]
    assert member["session_id"] is None
    assert "live" in member["session_reason"]


@pytest.mark.parametrize("version", [0, 3, "2", True, None])
def test_unknown_binding_versions_cannot_downgrade_to_v1(
    fake: FakeHc, tmp_path: Path, version,
) -> None:
    _binding(tmp_path, "t24", _v2(v=version, pid=1944,
                                terminal_created_ms=1, provider="claude"))
    member = {m["address"]: m for m in host.HcHost().all_members()}["t24"]
    assert member["session_id"] is None
    assert "version" in member["session_reason"]


def test_no_binding_file_says_so(fake: FakeHc) -> None:
    member = {m["address"]: m for m in host.HcHost().all_members()}["t24"]
    assert member["session_id"] is None
    assert "binding file" in str(member["session_reason"])


def test_a_v1_binding_counts_under_the_old_pid_rule(
    fake: FakeHc, tmp_path: Path
) -> None:
    _binding(tmp_path, "t24", {
        "terminal": "t24", "session_id": "ses_v1", "pid": 1944, "cwd": "C:\\repo",
    })
    member = {m["address"]: m for m in host.HcHost().all_members()}["t24"]
    assert member["session_id"] == "ses_v1"
    assert "v1" in str(member["session_reason"])


def test_a_v1_binding_for_a_relaunched_pid_is_rejected(
    fake: FakeHc, tmp_path: Path
) -> None:
    _binding(tmp_path, "t24", {
        "terminal": "t24", "session_id": "ses_stale", "pid": 999, "cwd": "C:\\repo",
    })
    member = {m["address"]: m for m in host.HcHost().all_members()}["t24"]
    assert member["session_id"] is None
