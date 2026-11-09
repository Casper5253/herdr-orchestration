"""Codex in the Herdr app reports to the lead the same way as every other worker."""
from pathlib import Path

RULES = (Path(__file__).resolve().parents[1] / "team" / "herdr-team.rules").read_text(encoding="utf-8")


def test_codex_reports_with_hx_say_not_raw_hc_send():
    assert 'prefix_rule(pattern=["hc", "send"], decision="prompt")' in RULES
    assert 'prefix_rule(pattern=["hx"], decision="allow")' in RULES


def test_codex_reads_hc_freely_and_asks_before_terminal_control():
    assert 'prefix_rule(pattern=["hc", ["ls", "get", "read", "wait"' in RULES
    control = next(line for line in RULES.splitlines() if line.startswith('prefix_rule(pattern=["hc", ["run"'))
    assert 'decision="prompt"' in control
    for verb in ("kill", "keys", "interrupt", "server"):
        assert f'"{verb}"' in control, verb
