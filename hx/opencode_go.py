"""Read OpenCode Go quota without persisting authentication or responses."""

from __future__ import annotations

import datetime
import json
import math
from http.client import HTTPException
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener


ENDPOINT = "https://opencode.ai/zen/go/v1/usage"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def parse_usage(payload: object) -> dict:
    windows = {}
    if not isinstance(payload, dict) or not isinstance(payload.get("usage"), dict):
        raise ValueError("missing quota")
    for remote, local in (("rolling", "5h"), ("weekly", "7d"), ("monthly", "30d")):
        window = payload["usage"].get(remote)
        if not isinstance(window, dict):
            raise ValueError("missing quota window")
        percent, status, reset = window.get("percent"), window.get("status"), window.get("resetsAt")
        if (isinstance(percent, bool) or not isinstance(percent, (int, float))
                or not math.isfinite(percent) or percent < 0
                or status not in ("ok", "rate-limited") or not isinstance(reset, str)):
            raise ValueError("invalid quota window")
        timestamp = datetime.datetime.fromisoformat(reset.replace("Z", "+00:00"))
        if timestamp.utcoffset() is None:
            raise ValueError("quota reset requires timezone")
        windows[local] = {
            "utilisation": max(1.0, percent / 100) if status == "rate-limited" else percent / 100,
            "status": status, "resets_at": reset,
            "cap": None, "spend": None, "remaining": None,
        }
    return windows


def fetch_usage(auth_path: Path) -> dict | None:
    try:
        auth = json.loads(auth_path.read_text(encoding="utf-8"))
        login = auth.get("opencode-go")
        if not isinstance(login, dict) or login.get("type") != "api":
            return None
        key = login.get("key")
        if not isinstance(key, str) or not key.strip():
            return None
        request = Request(ENDPOINT, headers={
            "Authorization": "Bearer " + key,
            "Accept": "application/json", "User-Agent": "opencode/hx",
        })
        with build_opener(_NoRedirect()).open(request, timeout=2) as response:
            body = response.read(65537)
        if len(body) > 65536:
            return None
        return parse_usage(json.loads(body))
    except (OSError, HTTPException, ValueError, TypeError, AttributeError, OverflowError):
        return None
