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


def validate(profile: dict[str, Any]) -> str | None:
    """Why a profile cannot define a boundary, or None when it can.

    Profiles fail closed: deny, allow and write_roots must all be present as
    string lists. An empty or malformed profile is an error, never a run with
    no denies.
    """
    name = profile.get("name", "?")
    for key in ("deny", "allow", "write_roots"):
        value = profile.get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return f"profile {name} has a missing or malformed {key!r} list"
    return None


def _require_valid(profile: dict[str, Any]) -> None:
    error = validate(profile)
    if error:
        raise ValueError(error)


def seq_rules(profile: dict[str, Any]) -> list[list[str]]:
    return [m.group(1).split() for cmd in profile.get("deny") or [] if (m := _EXEC_RE.match(cmd))]


def allow_seq_rules(profile: dict[str, Any]) -> list[list[str]]:
    return [m.group(1).split() for cmd in profile.get("allow") or [] if (m := _EXEC_RE.match(cmd))]


# Write() deny globs approximating "nowhere outside the run cwd" for a
# write-capable profile. Devin's deny always wins over allow, so the scope
# cannot be expressed as allow-minus-deny; these cover the usual escapes.
_OUTSIDE_CWD_WRITES = ("Write(~/**)", "Write(*:/**)", "Write(//**)", "Write(../**)", "Write(**/../**)")

# Secret paths the rendered configs always exclude, mirroring the deny-path
# rules the Devin worker template carries.
_SECRET_WRITE_GLOBS = (".env*", "**/.env*", "**/.git/**", "**/.ssh/**", "**/credentials.toml")
_SECRET_READ_GLOBS = (
    ".env*",
    "**/.env*",
    "**/.ssh/**",
    "**/credentials.toml",
    "~/.ssh/**",
    "~/AppData/Roaming/devin/credentials.toml",
)


def render_devin_config(profile: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """The worker config for one profiled run, failing closed.

    The base secret-path denies stay. Devin's deny always wins over allow, so
    a profile deny an allow rule covers (``Exec(git checkout)`` with
    ``Exec(git checkout -b)`` allowed) moves to ask, where the more specific
    allow carves out. A read-only profile (empty write_roots) loses the edit
    tool, gets ``Write(**)`` denied and its exec surface shrinks to the
    profile's allow list through an ``exec`` ask.
    """
    _require_valid(profile)
    config = json.loads(json.dumps(base))
    perms = config.setdefault("permissions", {})
    base_allow = [rule for rule in perms.get("allow") or []]
    deny = [rule for rule in perms.get("deny") or [] if not _EXEC_RE.match(rule)]
    ask = list(perms.get("ask") or [])
    allow = [rule for rule in base_allow if not _EXEC_RE.match(rule)]

    writable = bool(profile["write_roots"])
    if writable:
        if set(profile["write_roots"]) == {"cwd"}:
            deny += [rule for rule in _OUTSIDE_CWD_WRITES if rule not in deny]
    else:
        allow = [tool for tool in allow if tool not in ("edit", "exec")]
        deny += ["edit", "Write(**)"]
        ask.append("exec")

    allow_words = allow_seq_rules(profile)
    for rule in profile["deny"]:
        match = _EXEC_RE.match(rule)
        covered = match is not None and any(
            a[: len(match.group(1).split())] == match.group(1).split() for a in allow_words
        )
        (ask if covered else deny).append(rule)
    allow += [f"Exec({' '.join(words)})" for words in allow_words]

    perms["allow"] = allow
    perms["deny"] = deny
    if ask:
        perms["ask"] = ask
    return config


def render_opencode_permission(profile: dict[str, Any]) -> dict[str, Any]:
    """The permission block, default deny and scoped to the profile.

    Bash: ``*`` denies, profile denies and allows follow so the last matching
    rule wins for the carved-out commands. Edit: ``deny`` when the profile
    grants no write roots, else allows inside the worktree only and always
    denies the secret paths. ``external_directory`` stays denied unless a
    write root lives outside the run directory.
    """
    _require_valid(profile)
    bash: dict[str, str] = {"*": "deny"}
    for words in seq_rules(profile):
        bash[" ".join(words) + "*"] = "deny"
    for words in allow_seq_rules(profile):
        bash[" ".join(words) + "*"] = "allow"

    roots = [root for root in profile["write_roots"]]
    permission: dict[str, Any] = {
        "bash": bash,
        "read": {"*": "allow", **{glob: "deny" for glob in _SECRET_READ_GLOBS}},
    }
    if not roots:
        permission["edit"] = "deny"
        permission["external_directory"] = "deny"
        return permission

    edit: dict[str, str] = {"*": "deny"}
    external: dict[str, str] = {"*": "deny"}
    for root in roots:
        if root == "cwd":
            # opencode runs with --dir <cwd>: the project dir is the worktree.
            edit["**"] = "allow"
        else:
            glob = root.replace("\\", "/").rstrip("/")
            edit[glob + "/**"] = "allow"
            external[glob + "/**"] = "allow"
    for glob in _SECRET_WRITE_GLOBS + ("~/.ssh/**", "~/AppData/Roaming/devin/credentials.toml"):
        edit[glob] = "deny"
    permission["edit"] = edit
    permission["external_directory"] = "deny" if external == {"*": "deny"} else external
    return permission


def render(
    worker: str,
    profile: dict[str, Any],
    base: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The rendered worker config for one profiled run."""
    _require_valid(profile)
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
    error = validate(profile)
    if error:
        print(error, file=sys.stderr)
        return 2
    print(json.dumps(render(args.worker, profile, policy=load_policy()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
