"""hx.profile: named execution profiles and the capability boundary (C5).

The other half of C4: each kind of task gets the permissions it really
needs. ``policy.json`` ``profiles`` names them -- research and review are
read-only (review may run tests), implement writes in its worktree and owns
its branch but never commits, pushes, resets or clones, release is lead
only. Each profile's deny list is self-contained; its allow list carves out
of deny and is checked first, so ``git checkout -b`` can pass where
``git checkout`` cannot.

``hx ocrun --profile <name>`` renders the worker's config for that one run
(a Devin config JSON, an OpenCode permission block) and the C4 preflight
checks the brief against the profile instead of the static list. ``hx
profile show <name> --worker <w>`` prints the rendered config so the lead
can review it before installing it for the persistent panes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from hx import devin as devin_mod

_EXEC_RE = re.compile(r"^Exec\((.*)\)$")


def get(name: str, policy: dict[str, Any] | None) -> dict[str, Any] | None:
    """The named profile from policy.json, or None."""
    profiles = (policy or {}).get("profiles") or {}
    profile = profiles.get(name)
    if profile is None:
        return None
    return dict(profile, name=name)


def seq_rules(profile: dict[str, Any]) -> list[list[str]]:
    return [m.group(1).split() for cmd in profile.get("deny") or [] if (m := _EXEC_RE.match(cmd))]


def allow_seq_rules(profile: dict[str, Any]) -> list[list[str]]:
    return [m.group(1).split() for cmd in profile.get("allow") or [] if (m := _EXEC_RE.match(cmd))]


def render_devin_config(profile: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """The worker config for one profiled run: profile denies plus the secret
    path rules the base template carries, so .env/.ssh stay forbidden."""
    deny = [rule for rule in (base.get("permissions") or {}).get("deny") or [] if not _EXEC_RE.match(rule)]
    config = json.loads(json.dumps(base))
    config.setdefault("permissions", {})["deny"] = deny + list(profile.get("deny") or [])
    return config


def render_opencode_permission(profile: dict[str, Any]) -> dict[str, Any]:
    """The permission block: default deny, profile denies, allows last so the
    last matching rule wins for the carved-out commands."""
    bash: dict[str, str] = {"*": "deny"}
    for words in seq_rules(profile):
        bash[" ".join(words) + "*"] = "deny"
    for words in allow_seq_rules(profile):
        bash[" ".join(words) + "*"] = "allow"
    return {"bash": bash, "edit": "deny" if not profile.get("write_roots") else "allow"}


def render(
    worker: str,
    profile: dict[str, Any],
    base: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The rendered worker config for one profiled run."""
    if worker == "devin":
        return render_devin_config(profile, base or devin_mod._worker_config(policy))
    if worker == "opencode":
        return {"permission": render_opencode_permission(profile)}
    if worker == "codex":
        return {"profile": profile["name"], "sandbox": "read-only" if not profile.get("write_roots") else "workspace-write"}
    raise ValueError(f"unknown worker {worker}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hx profile", description="Named execution profiles.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    show = sub.add_parser("show", help="print the rendered worker config for a profile")
    show.add_argument("name")
    show.add_argument("--worker", required=True, choices=["devin", "opencode", "codex"])
    args = parser.parse_args(argv)
    if args.cmd != "show":
        return 2
    from hx.usage import load_policy

    profile = get(args.name, load_policy())
    if profile is None:
        print(f"unknown profile {args.name}", file=sys.stderr)
        return 2
    if profile.get("lead_only"):
        print(f"profile {args.name} is lead-only: no worker config is rendered", file=sys.stderr)
        return 2
    print(json.dumps(render(args.worker, profile, policy=load_policy()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
