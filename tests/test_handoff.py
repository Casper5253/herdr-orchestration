"""Tests for hx.handoff: the artifact survives a fresh owner."""

from __future__ import annotations

import json
from pathlib import Path

from hx import handoff

CHECKPOINT = """ledger_rev: 4

## Objective
Wire the usage bar into the pane.

## Owner
dev-devin

## Status
in progress

## Files changed
- hx/usage.py

## Decisions
Keep the estimator; measure the console figure later.

## Exact errors
AttributeError: 'dict' has no attribute 'models' at hx/usage.py:212

## Tests
- pytest tests/test_usage.py -q

## Next action
Fix usage.py:212 to read snapshot["models"], then rerun the focused test.

## Constraints
- no git writes
- worktree only

## References
- docs/audit/x.md
"""


def _plant(tmp_path: Path) -> dict:
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    (checkpoints / "q-77.md").write_text(CHECKPOINT, encoding="utf-8")
    routing = tmp_path / ".hx" / "routing.jsonl"
    routing.parent.mkdir(parents=True)
    routing.write_text(
        json.dumps(
            {
                "kind": "outcome",
                "task_id": "q-77",
                "failure": {"kind": "tool", "reason": "permission denied: Write(.env)"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return {"checkpoints": checkpoints, "routing": routing}


def test_handoff_carries_constraint_error_and_next_action(tmp_path: Path) -> None:
    plant = _plant(tmp_path)
    path = handoff.write_handoff(
        "dev-devin",
        "q-77",
        root=tmp_path,
        checkpoints_dir=plant["checkpoints"],
        routing_log=plant["routing"],
    )
    text = path.read_text(encoding="utf-8")
    assert path.name == "q-77-1.md"
    assert "no git writes" in text
    assert "AttributeError" in text
    assert "usage.py:212" in text
    assert "Fix usage.py:212" in text
    assert "permission denied: Write(.env)" in text


def test_handoff_increments_sequence(tmp_path: Path) -> None:
    plant = _plant(tmp_path)
    first = handoff.write_handoff("a", "q-77", root=tmp_path, checkpoints_dir=plant["checkpoints"], routing_log=plant["routing"])
    second = handoff.write_handoff("a", "q-77", root=tmp_path, checkpoints_dir=plant["checkpoints"], routing_log=plant["routing"])
    assert first.name != second.name
    assert second.name == "q-77-2.md"


def test_handoff_hard_cap_and_redaction(tmp_path: Path) -> None:
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    big = "## Objective\napi_key = sk-abc123secrettoken\n" + "x" * (handoff.MAX_BYTES + 500) + "\n\n## Next action\na\n"
    (checkpoints / "t.md").write_text(big, encoding="utf-8")
    path = handoff.write_handoff("a", "t", root=tmp_path, checkpoints_dir=checkpoints, routing_log=tmp_path / "none.jsonl")
    text = path.read_text(encoding="utf-8")
    assert len(text.encode("utf-8")) <= handoff.MAX_BYTES + 100
    assert "sk-abc123secrettoken" not in text
    assert "[redacted]" in text


def test_redacts_authorization_bearer_and_secret_assignments(tmp_path: Path) -> None:
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    planted = [
        "sk-live-bearer-123",
        "op-secret-456",
        "ghp-secret-789",
        "aws-secret-abc",
        "hunter2",
        "sk-secret-xyz",
    ]
    body = (
        "## Objective\n"
        "Authorization: Bearer sk-live-bearer-123\n"
        "OPENCODE_API_KEY=op-secret-456\n"
        "GH_TOKEN=ghp-secret-789\n"
        "AWS_SECRET_ACCESS_KEY=aws-secret-abc\n"
        "password: hunter2\n"
        "api_key = sk-secret-xyz\n"
        "\n## Next action\na\n"
    )
    (checkpoints / "s.md").write_text(body, encoding="utf-8")
    path = handoff.write_handoff(
        "a", "s", root=tmp_path, checkpoints_dir=checkpoints, routing_log=tmp_path / "none.jsonl"
    )
    text = path.read_text(encoding="utf-8")
    for secret in planted:
        assert secret not in text
    assert text.count("[redacted]") >= len(planted)


def test_brief_prints_pointer_with_next_action(tmp_path: Path) -> None:
    plant = _plant(tmp_path)
    path = handoff.write_handoff("a", "q-77", root=tmp_path, checkpoints_dir=plant["checkpoints"], routing_log=plant["routing"])
    line = handoff.brief(path)
    assert line.startswith(f"Continue from handoff {path}: read it first, then")
    assert "usage.py:212" in line
