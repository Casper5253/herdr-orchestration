"""F03/F11/F18: executable resolution, read completeness, slash transport.

F03: `tool_path` resolves hc/herdr-app once to full paths — PATH first, then
the known `%LOCALAPPDATA%/Programs` install dirs — and a missing executable
says exactly what was searched.
F11: `read()` carries `hc read --json` `complete`/`why`/`how` so a caller can
tell a full transcript from an in-progress screen.
F18: in hc mode `prompt_command` never emits a standalone `herdr` command and
`send_slash` types slash commands as keys, because `hc send` wraps text so
providers cannot run them.
Review finding 1: a send whose target vanished before `hc send` ran raises
SendNotAttempted, which say.py can mark pending instead of uncertain.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from hx import host
from test_host_hc import FakeHc, _completed


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeHc:
    fake = FakeHc()
    monkeypatch.setenv("HC_TERMINAL_ID", "t7")
    monkeypatch.setenv("HX_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HX_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(host.subprocess, "run", fake.run)
    monkeypatch.setattr(host, "tool_path", lambda name: "hc")
    return fake


# ---------- F03: executable resolution ----------


def test_tool_path_prefers_path(monkeypatch: pytest.MonkeyPatch) -> None:
    host.tool_path.cache_clear()
    monkeypatch.setattr(host.shutil, "which", lambda name: "C:\\bin\\hc.exe")
    assert host.tool_path("hc") == "C:\\bin\\hc.exe"


def test_tool_path_falls_back_to_the_known_install_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    host.tool_path.cache_clear()
    monkeypatch.setattr(host.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    installed = tmp_path / "Programs" / "herdr-core" / "hc.exe"
    installed.parent.mkdir(parents=True)
    installed.write_text("x", encoding="utf-8")
    assert host.tool_path("hc") == str(installed)


def test_tool_path_names_what_was_searched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    host.tool_path.cache_clear()
    monkeypatch.setattr(host.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    with pytest.raises(host.HostToolMissing) as excinfo:
        host.tool_path("hc")
    message = str(excinfo.value)
    assert "PATH" in message
    assert "herdr-core" in message


def test_tool_path_is_resolved_once(monkeypatch: pytest.MonkeyPatch) -> None:
    host.tool_path.cache_clear()
    calls: list[str] = []
    monkeypatch.setattr(
        host.shutil, "which", lambda name: calls.append(name) or "C:\\bin\\hc.exe")
    assert host.tool_path("hc") == host.tool_path("hc") == "C:\\bin\\hc.exe"
    assert calls == ["hc"]


def test_tool_path_without_a_known_dir_reports_path_only(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    host.tool_path.cache_clear()
    monkeypatch.setattr(host.shutil, "which", lambda name: None)
    with pytest.raises(host.HostToolMissing, match="PATH"):
        host.tool_path("herdr")


def test_run_uses_the_resolved_executable(
    fake: FakeHc, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved = str(Path("C:\\tools\\hc.exe"))
    monkeypatch.setattr(host, "tool_path", lambda name: resolved)
    host.HcHost().all_members()
    assert fake.runs[0] == [resolved, "ls", "--json"]


# ---------- F11: read completeness ----------


def test_read_returns_text_with_completeness(fake: FakeHc) -> None:
    fake.read_payload = {
        "how": "screen", "complete": False, "why": "working", "text": "partial"}
    pane = host.HcHost().read("dev-codex", 40)
    assert pane == "partial"
    assert pane.complete is False
    assert pane.why == "working"
    assert pane.how == "screen"


def test_a_complete_read_is_marked_complete(fake: FakeHc) -> None:
    pane = host.HcHost().read("dev-codex", 40)
    assert pane == "pane text"
    assert pane.complete is True
    assert pane.why is None


def test_a_failed_read_raises(fake: FakeHc) -> None:
    fake.read_fail = True
    with pytest.raises(RuntimeError):
        host.HcHost().read("dev-codex", 40)


def test_a_read_of_an_unknown_member_is_a_lookup_error(fake: FakeHc) -> None:
    with pytest.raises(ValueError, match="no live terminal"):
        host.HcHost().read("hx-ghost", 40)


def test_a_malformed_read_response_raises(fake: FakeHc) -> None:
    fake.read_raw = "not json"
    with pytest.raises(RuntimeError):
        host.HcHost().read("dev-codex", 40)


def test_the_standalone_read_marks_its_source(fake: FakeHc, monkeypatch) -> None:
    monkeypatch.delenv("HC_TERMINAL_ID", raising=False)

    def run(argv, **kwargs):
        return _completed("pane text")

    monkeypatch.setattr(host.subprocess, "run", run)
    pane = host.HerdrHost().read("dev-codex", 10)
    assert pane == "pane text"
    assert pane.how == "recent-unwrapped"
    assert pane.complete is None


# ---------- F18: slash transport ----------


def test_prompt_command_stays_herdr_off_the_app_core(monkeypatch) -> None:
    monkeypatch.delenv("HC_TERMINAL_ID", raising=False)
    assert host.prompt_command("dev-lead", "/compact") == \
        "herdr agent prompt dev-lead '/compact'"


def test_prompt_command_types_a_slash_command_as_keys(fake: FakeHc) -> None:
    command = host.prompt_command("dev-codex", "/compact")
    assert "herdr" not in command
    assert command == "& 'hc' keys t7 / c o m p a c t; & 'hc' keys t7 enter"


def test_prompt_command_unquotes_a_shell_quoted_name(fake: FakeHc) -> None:
    assert host.prompt_command("'dev-codex'", "/clear") == \
        "& 'hc' keys t7 / c l e a r; & 'hc' keys t7 enter"


def test_prompt_command_uses_hc_send_for_plain_text(fake: FakeHc) -> None:
    assert host.prompt_command("dev-codex", "hello") == "& 'hc' send t7 'hello'"


def test_prompt_command_refuses_an_unknown_member(fake: FakeHc) -> None:
    with pytest.raises(ValueError):
        host.prompt_command("dev-ghost", "/compact")


@pytest.mark.parametrize("text,expected", [
    ("/clear", "& 'C:/installed tools/O''Brien/hc.exe' keys t7 / c l e a r; "
     "& 'C:/installed tools/O''Brien/hc.exe' keys t7 enter"),
    ("it's ready", "& 'C:/installed tools/O''Brien/hc.exe' send t7 'it''s ready'"),
])
def test_generated_commands_use_the_resolved_powershell_quoted_path(
    fake: FakeHc, monkeypatch, text: str, expected: str,
) -> None:
    monkeypatch.setattr(host.HcHost, "_member_named", lambda self, name: {"address": "t7"})
    monkeypatch.setattr(host, "tool_path", lambda name: "C:/installed tools/O'Brien/hc.exe")
    assert host.prompt_command("dev-codex", text) == expected


_CODEX_IDLE = "› \n  gpt-5.2-codex high · 91% context left"
_CODEX_STAGED = "› /compact\n  gpt-5.2-codex high · 91% context left"
_DEVIN_RULE = "─" * 20
_DEVIN_IDLE = "❭ \n" + _DEVIN_RULE + "\nSWE-2 High · accept edits"
_DEVIN_STAGED = "❭ /compact\n" + _DEVIN_RULE + "\nSWE-2 High · accept edits"


def test_send_slash_types_each_character_then_enter(fake: FakeHc) -> None:
    fake.read_payload = {"how": "screen", "complete": True, "text": _CODEX_IDLE}
    assert host.HcHost().send_slash("dev-codex", "/compact") is True
    assert fake.keys == [
        ["hc", "keys", "t7", "/", "c", "o", "m", "p", "a", "c", "t"],
        ["hc", "keys", "t7", "enter"],
    ]


def test_send_slash_uses_a_member_address_without_a_roster_rescan(fake: FakeHc) -> None:
    fake.read_payload = {"how": "screen", "complete": True, "text": _CODEX_IDLE}
    member = host.HcHost().info("dev-codex")
    before = len(fake.runs)
    assert host.HcHost().send_slash(member, "/clear") is True
    assert fake.keys == [
        ["hc", "keys", "t7", "/", "c", "l", "e", "a", "r"],
        ["hc", "keys", "t7", "enter"],
    ]
    assert all(run[1] != "ls" for run in fake.runs[before:])


def test_send_slash_requires_a_slash_command(fake: FakeHc) -> None:
    with pytest.raises(ValueError):
        host.HcHost().send_slash("dev-codex", "compact")
    assert fake.keys == []


def test_send_slash_reports_a_failed_key_call(fake: FakeHc) -> None:
    fake.keys_fail = True
    assert host.HcHost().send_slash("dev-codex", "/compact") is False
    assert len(fake.keys) == 1


def test_send_slash_presses_enter_once_more_when_the_command_stays_staged(
    fake: FakeHc, monkeypatch
) -> None:
    """Live repro: the first Enter landed in Devin's slash-command menu."""
    enters = [0]
    original = fake.run

    def run(argv, **kwargs):
        if argv[1] == "read":
            staged = enters[0] < 2
            return _completed(json.dumps({
                "how": "screen", "complete": True,
                "text": _DEVIN_STAGED if staged else _DEVIN_IDLE}))
        result = original(argv, **kwargs)
        if argv[1] == "keys" and argv[3] == "enter":
            enters[0] += 1
        return result

    monkeypatch.setattr(host.subprocess, "run", run)
    monkeypatch.setattr(host.time, "sleep", lambda seconds: None)
    assert host.HcHost().send_slash("dev-devin", "/compact") is True
    assert fake.keys == [
        ["hc", "keys", "t9", "/", "c", "o", "m", "p", "a", "c", "t"],
        ["hc", "keys", "t9", "enter"],
        ["hc", "keys", "t9", "enter"],
    ]


def test_send_slash_is_unverified_when_the_command_stays_staged(
    fake: FakeHc, monkeypatch
) -> None:
    """Still staged after the retry: unverified, and Enter was pressed exactly once more."""
    fake.read_payload = {"how": "screen", "complete": True, "text": _DEVIN_STAGED}
    monkeypatch.setattr(host.time, "sleep", lambda seconds: None)
    assert host.HcHost().send_slash("dev-devin", "/compact") is False
    enters = [run for run in fake.keys if run[3:] == ["enter"]]
    assert len(enters) == 2


def test_send_slash_never_retries_enter_for_a_submitted_command(
    fake: FakeHc, monkeypatch
) -> None:
    fake.read_payload = {"how": "screen", "complete": True,
                         "text": "some output\n" + _CODEX_IDLE}
    monkeypatch.setattr(host.time, "sleep", lambda seconds: None)
    assert host.HcHost().send_slash("dev-codex", "/compact") is True
    enters = [run for run in fake.keys if run[3:] == ["enter"]]
    assert len(enters) == 1


def test_send_slash_never_enters_for_prompt_shaped_output_while_working(
    fake: FakeHc, monkeypatch
) -> None:
    """Review finding 2: a Markdown `> /compact` quote in task output is not a
    composer. The pane is working, so no live composer exists to stage it."""
    fake.terminals["t7"]["status"]["state"] = "working"
    fake.read_payload = {"how": "screen", "complete": True,
                         "text": "Model output only\n> /compact\nworking..."}
    monkeypatch.setattr(host.time, "sleep", lambda seconds: None)
    assert host.HcHost().send_slash("dev-codex", "/compact") is False
    enters = [run for run in fake.keys if run[3:] == ["enter"]]
    assert len(enters) == 1


@pytest.mark.parametrize("text", [
    "Model output only\n> /compact\nworking...",
    "Model output only\n> /compact",
])
def test_send_slash_never_enters_for_prompt_shaped_output_while_idle(
    fake: FakeHc, monkeypatch, text: str
) -> None:
    """Round-2 review finding 1: an idle screen whose `> /compact` sits in
    output — not at the codex composer position above its model status line —
    never earns the second Enter."""
    fake.read_payload = {"how": "screen", "complete": True, "text": text}
    monkeypatch.setattr(host.time, "sleep", lambda seconds: None)
    assert host.HcHost().send_slash("dev-codex", "/compact") is False
    enters = [run for run in fake.keys if run[3:] == ["enter"]]
    assert len(enters) == 1


def test_send_slash_ignores_a_devin_prompt_not_above_the_footer_rule(
    fake: FakeHc, monkeypatch
) -> None:
    """Round-2 review finding 1: `❭ /compact` in output — above ordinary text,
    not the footer rule — is not Devin's composer."""
    fake.read_payload = {"how": "screen", "complete": True,
                         "text": "❭ /compact\nmore output\n" + _DEVIN_RULE +
                                 "\nSWE-2 High · accept edits"}
    monkeypatch.setattr(host.time, "sleep", lambda seconds: None)
    assert host.HcHost().send_slash("dev-devin", "/compact") is False
    enters = [run for run in fake.keys if run[3:] == ["enter"]]
    assert len(enters) == 1


def test_send_slash_retries_enter_for_the_codex_composer_position(
    fake: FakeHc, monkeypatch
) -> None:
    """`› /compact` directly above the codex model status line is the staged composer."""
    fake.read_payload = {"how": "screen", "complete": True, "text": _CODEX_STAGED}
    monkeypatch.setattr(host.time, "sleep", lambda seconds: None)
    assert host.HcHost().send_slash("dev-codex", "/compact") is False
    enters = [run for run in fake.keys if run[3:] == ["enter"]]
    assert len(enters) == 2


def test_send_slash_retries_enter_for_the_opencode_input_box(
    fake: FakeHc, monkeypatch
) -> None:
    """`┃  /compact` inside the input box directly above `Build` is the staged
    composer — the OpenCode menu needs the second Enter."""
    fake.terminals["t8"]["status"] = {"app": "opencode", "state": "idle"}
    enters = [0]
    original = fake.run

    def run(argv, **kwargs):
        if argv[1] == "read":
            staged = enters[0] < 2
            return _completed(json.dumps({
                "how": "screen", "complete": True,
                "text": ("┃  /compact\nBuild · opencode" if staged
                         else "┃  \nBuild · opencode")}))
        result = original(argv, **kwargs)
        if argv[1] == "keys" and argv[3] == "enter":
            enters[0] += 1
        return result

    monkeypatch.setattr(host.subprocess, "run", run)
    monkeypatch.setattr(host.time, "sleep", lambda seconds: None)
    assert host.HcHost().send_slash("dev-opencode", "/compact") is True
    enters_sent = [run for run in fake.keys if run[3:] == ["enter"]]
    assert len(enters_sent) == 2


def test_send_slash_is_unverified_for_a_provider_without_a_composer_position(
    fake: FakeHc, monkeypatch
) -> None:
    """A kind with no defined composer position never earns Enter: unverified,
    not submitted."""
    fake.read_payload = {"how": "screen", "complete": True, "text": "> /compact"}
    monkeypatch.setattr(host.time, "sleep", lambda seconds: None)
    assert host.HcHost().send_slash("dev-claude", "/compact") is False
    enters = [run for run in fake.keys if run[3:] == ["enter"]]
    assert len(enters) == 1


def test_send_slash_is_unverified_without_a_visible_composer(
    fake: FakeHc, monkeypatch
) -> None:
    """Review finding 2: no composer line means unverified, never submitted."""
    fake.read_payload = {"how": "screen", "complete": True,
                         "text": "No composer currently rendered"}
    monkeypatch.setattr(host.time, "sleep", lambda seconds: None)
    assert host.HcHost().send_slash("dev-codex", "/compact") is False
    enters = [run for run in fake.keys if run[3:] == ["enter"]]
    assert len(enters) == 1


def test_send_slash_is_unverified_when_the_screen_cannot_be_read(
    fake: FakeHc, monkeypatch
) -> None:
    fake.read_fail = True
    monkeypatch.setattr(host.time, "sleep", lambda seconds: None)
    assert host.HcHost().send_slash("dev-codex", "/compact") is False


def test_send_slash_ignores_the_command_in_output_above_the_composer(
    fake: FakeHc, monkeypatch
) -> None:
    """A submitted command's echo in output never earns another Enter."""
    fake.read_payload = {"how": "screen", "complete": True,
                         "text": "Finished /compact successfully.\n" + _DEVIN_IDLE}
    monkeypatch.setattr(host.time, "sleep", lambda seconds: None)
    assert host.HcHost().send_slash("dev-devin", "/compact") is True
    enters = [run for run in fake.keys if run[3:] == ["enter"]]
    assert len(enters) == 1


def test_send_slash_ignores_a_scrollback_echo_above_an_empty_prompt(
    fake: FakeHc, monkeypatch
) -> None:
    """A submitted `❭ /compact` in scrollback above the live composer is not staged."""
    fake.read_payload = {"how": "screen", "complete": True,
                         "text": "❭ /compact\nFinished /compact successfully.\n" + _DEVIN_IDLE}
    monkeypatch.setattr(host.time, "sleep", lambda seconds: None)
    assert host.HcHost().send_slash("dev-devin", "/compact") is True
    enters = [run for run in fake.keys if run[3:] == ["enter"]]
    assert len(enters) == 1


def test_staged_command_matches_the_exact_command_only(
    fake: FakeHc, monkeypatch
) -> None:
    """`❭ /compactx` is not the `/compact` send_slash typed."""
    fake.read_payload = {"how": "screen", "complete": True,
                         "text": "❭ /compactx\n" + _DEVIN_RULE + "\nSWE-2 High · accept edits"}
    monkeypatch.setattr(host.time, "sleep", lambda seconds: None)
    assert host.HcHost().send_slash("dev-devin", "/compact") is True
    enters = [run for run in fake.keys if run[3:] == ["enter"]]
    assert len(enters) == 1


def test_send_slash_to_a_vanished_member_was_never_attempted(fake: FakeHc) -> None:
    del fake.terminals["t1"]
    fake.order.remove("t1")
    with pytest.raises(host.SendNotAttempted):
        host.HcHost().send_slash("dev-claude", "/compact")
    assert fake.keys == []


def test_the_standalone_host_prompts_a_slash_command_natively(monkeypatch) -> None:
    monkeypatch.delenv("HC_TERMINAL_ID", raising=False)
    calls: list[list[str]] = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        return _completed("")

    monkeypatch.setattr(host.subprocess, "run", run)
    assert host.HerdrHost().send_slash("dev-codex", "/compact") is True
    assert calls == [["herdr", "agent", "prompt", "dev-codex", "/compact"]]


# ---------- review finding 1: a never-attempted send ----------


def test_a_send_whose_target_vanished_was_never_attempted(fake: FakeHc) -> None:
    del fake.terminals["t1"]
    fake.order.remove("t1")
    with pytest.raises(host.SendNotAttempted):
        host.HcHost().send_checked("dev-claude", "report")
    assert fake.sent == []
    assert isinstance(host.SendNotAttempted("x"), ValueError)


def test_a_missing_executable_was_never_attempted(
    fake: FakeHc, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(name: str) -> str:
        raise host.HostToolMissing(f"no {name} executable found; searched PATH")

    monkeypatch.setattr(host, "tool_path", missing)
    with pytest.raises(host.SendNotAttempted):
        host.HcHost().send_checked("dev-claude", "report")
    assert fake.sent == []


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired(["hc", "ls"], 30),
                                  OSError("roster unavailable")])
def test_roster_failure_before_send_is_not_attempted(fake: FakeHc, monkeypatch, error) -> None:
    def run(*args, **kwargs):
        raise error

    monkeypatch.setattr(host.HcHost, "_run", run)
    with pytest.raises(host.SendNotAttempted):
        host.HcHost().send_checked("dev-claude", "report")
    assert fake.sent == []
