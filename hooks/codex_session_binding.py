"""Codex SessionStart hook: bind the hc terminal to the Codex session id.

Reads Codex's hook payload on stdin and publishes the v2 binding. Installed as a
SessionStart hook for the ``startup|resume|clear|compact`` matcher; hooks must
never fail the session, so every error is caught and exit code is 0.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hc_binding


def _read_payload() -> dict[str, Any]:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> int:
    payload = _read_payload()
    event = payload.get("hook_event_name")
    if event and event != "SessionStart":
        return 0
    session_id = payload.get("session_id") or payload.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        return 0
    hc_binding.publish("codex", session_id)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BaseException:
        sys.exit(0)
