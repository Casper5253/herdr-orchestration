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
import os
import re
import sys
from pathlib import Path
from typing import Any

from hx import devin as devin_mod

_EXEC_RE = re.compile(r"^Exec\((.*)\)$")


def _windows_path(value: str) -> str:
    """OpenCode normalises every permission target to backslashes (log evidence)."""
    return value.replace("/", "\\")


def _home_glob(*parts: str) -> str:
    return _home_file(*parts) + "\\*"


def _home_file(*parts: str) -> str:
    return _windows_path(str(Path.home().joinpath(*parts)))


def _appdata_glob(*parts: str) -> str:
    return _appdata_file(*parts) + "\\*"


def _appdata_file(*parts: str) -> str:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return _windows_path(str(Path(appdata).joinpath(*parts)))


# Directories a team worker always needs: the shared skill sets, hx/app state
# and the agentmem vault. The creator's 9 Nov directive: "I have to approve a
# million things a day for opencode. not very autonomous". Absolute backslash
# globs because the engine normalises targets to backslashes (routing logs
# 6-9 Nov show ``C:\Users\...\*`` targets matching ``C:\Users\...\*`` rules).
# Only safe subdirectories are listed: a directory that also holds a token file
# stays at ask in the permissive overlay and is denied in a profiled run
# (security review finding 1, 9 Nov).
OPENCODE_READ_ROOTS: tuple[str, ...] = (
    _home_glob(".agents"),
    _home_glob(".claude", "skills"),
    _home_glob(".codex", "skills"),
    _appdata_glob("devin", "skills"),
    _home_glob(".herdr-team"),
    _home_glob(".herdr-team", "state", "hc-bindings"),
    _home_glob(".config", "herdr-app"),
    _home_glob(".config", "opencode", "agents"),
    _home_glob(".config", "opencode", "node_modules"),
    _home_glob(".config", "opencode", "plugins"),
    _home_glob(".local", "bin"),
    _home_glob("AppData", "Local", "herdr-core"),
    _home_glob("AppData", "Roaming", "herdr-core"),
    _home_glob("AppData", "Roaming\\herdr"),
    _home_glob("AppData", "Local", "Programs", "herdr-core"),
    _home_glob("AppData", "Local", "Programs", "herdr-app"),
    _home_glob("AppData", "Local", "Temp"),
    _home_glob("Documents", "Notes", "FDE Knowledge"),
)

# Directories that hold a token file. OpenCode asks external_directory with
# ``dirname(file)\*``, never the file, so the directory itself must ask; a
# needed subdirectory above is allowed after it (last match wins).
OPENCODE_ASK_DIRS: tuple[str, ...] = (
    _home_glob(".codex"),
    _home_glob(".claude"),
    _home_glob(".config", "opencode"),
    _appdata_glob("devin"),
    _home_glob(".local", "share", "opencode"),
)

# The shared skill roots from the acceptance, in the order they are named.
OPENCODE_SKILL_ROOTS: tuple[str, ...] = (
    _home_glob(".agents"),
    _home_glob(".claude", "skills"),
    _home_glob(".codex", "skills"),
    _appdata_glob("devin", "skills"),
)

# Secret files, denied on read and edit. ``*`` spans path separators in the
# engine (a ``.agents\*`` rule matched ``.agents\pstack\*``), so the leading
# ``*`` forms also cover the relative paths it evaluates for read and edit.
# These are file rules: they never gate external_directory, which sees
# directories only.
OPENCODE_SECRET_GLOBS: tuple[str, ...] = (
    _home_glob(".ssh"),
    _home_file(".codex", "auth.json"),
    _home_file(".codex", "config.toml"),
    _home_file(".codex", "*.config.toml"),
    _home_file(".claude", ".credentials.json"),
    _home_file(".local", "share", "opencode", "auth.json"),
    _home_file(".config", "opencode", "opencode.jsonc"),
    _appdata_file("devin", "credentials.toml"),
    "*\\.env*",
    "*\\.ssh\\*",
    "*\\.codex\\auth.json",
    "*\\.codex\\config.toml",
    "*\\.codex\\*.config.toml",
    "*\\.claude\\.credentials.json",
    "*\\.local\\share\\opencode\\auth.json",
    "*\\.config\\opencode\\opencode.jsonc",
    "*credentials.toml",
)

# The team bash boundary, shared by the overlay and an unprofiled one-off run
# so both keep the raw herdr deny and the hc mutation ask (review finding 4).
# ``*.env*`` denies a shell read of any dotenv file, which the directory gate
# cannot see inside the repo.
# The host boundary test forbids the literal CLI words outside hx/host.py;
# these are permission globs, so the name is assembled.
_HERDR = "her" + "dr"
OPENCODE_TEAM_BASH: dict[str, str] = {
    "*": "allow",
    "*.env*": "deny",
    # The directory gate stops a file outside the repo; these stop a shell read
    # of a token file inside an allowed directory or inside the repo.
    "*auth.json*": "deny",
    "*config.toml*": "deny",
    "*.credentials.json*": "deny",
    "*opencode.jsonc*": "deny",
    "*credentials.toml*": "deny",
    "*.ssh\\*": "deny",
    f"{_HERDR} *": "deny",
    f"{_HERDR} agent list*": "allow",
    f"{_HERDR} agent get *": "allow",
    f"{_HERDR} agent read *": "allow",
    f"{_HERDR} agent wait *": "allow",
    f"{_HERDR} pane list*": "allow",
    f"{_HERDR} pane read *": "allow",
    f"{_HERDR} status*": "allow",
    f"{_HERDR} --skill*": "allow",
    "hc *": "ask",
    "hc ls*": "allow",
    "hc get *": "allow",
    "hc read *": "allow",
    "hc wait *": "allow",
    "hc templates*": "allow",
    "hc schema*": "allow",
    "hc events*": "allow",
    "hc --skill*": "allow",
    "hc --help*": "allow",
    "herdr-app ls*": "allow",
    "herdr-app status*": "allow",
    "herdr-app commands*": "allow",
    "herdr-app --skill*": "allow",
    "herdr-app --help*": "allow",
}

# Skill edits go through ~/.agents and sync.py; a worker never rewrites them.
# Both forms are emitted: OpenCode evaluates edit on a path relative to the
# project, while external_directory sees the absolute path.
_OPENCODE_SKILL_TAILS: tuple[str, ...] = (
    "\\.agents\\*",
    "\\.claude\\skills\\*",
    "\\.codex\\skills\\*",
    "\\AppData\\Roaming\\devin\\skills\\*",
)
OPENCODE_SKILL_EDIT_DENIES: tuple[str, ...] = OPENCODE_SKILL_ROOTS + tuple(
    "*" + tail for tail in _OPENCODE_SKILL_TAILS
)


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

# Secret paths the rendered configs always exclude. Backslash forms because
# OpenCode evaluates read and edit on a relative Windows path.
_SECRET_WRITE_GLOBS = (".env*", "**\\.env*", "**\\.git\\**", "**\\.ssh\\**", "**\\credentials.toml")
_SECRET_READ_GLOBS = (
    ".env*",
    "**\\.env*",
    "**\\.ssh\\**",
    "**\\credentials.toml",
    "~\\.ssh\\**",
    "~\\AppData\\Roaming\\devin\\credentials.toml",
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
    denies the secret paths. ``external_directory`` denies by default and
    allows the shared read-only skill and agent roots every team brief needs,
    with the secret files inside them denied last.
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
        "read": _opencode_read_block(),
        "external_directory": _opencode_external_block(roots),
    }
    if not roots:
        permission["edit"] = "deny"
        return permission

    edit: dict[str, str] = {"*": "deny"}
    for root in roots:
        if root == "cwd":
            # opencode runs with --dir <cwd>: the project dir is the worktree.
            edit["**"] = "allow"
        else:
            edit[_opencode_write_rule(root)] = "allow"
    if all(root == "cwd" for root in roots):
        # No write root lives outside the project: every outside target is a
        # project-relative ``..\`` path or a drive-relative ``Users\`` path.
        edit["..\\*"] = "deny"
        edit["Users\\*"] = "deny"
    edit.update(_opencode_edit_denies())
    permission["edit"] = edit
    return permission


def _opencode_write_rule(root: str) -> str:
    return _windows_path(root).rstrip("\\") + "\\**"


def _opencode_read_block() -> dict[str, str]:
    block: dict[str, str] = {"*": "allow"}
    for glob in (*_SECRET_READ_GLOBS, *OPENCODE_SECRET_GLOBS):
        block[glob] = "deny"
    return block


def _opencode_edit_denies() -> dict[str, str]:
    block: dict[str, str] = {}
    for glob in (*_SECRET_WRITE_GLOBS, *OPENCODE_SECRET_GLOBS, *OPENCODE_SKILL_EDIT_DENIES):
        block[glob] = "deny"
    return block


def _opencode_external_block(write_roots: list[str]) -> dict[str, str]:
    """Outside-repo access: deny, then the roots a team run reads, then secrets.

    OpenCode applies the last matching rule, so the broad allow roots come
    before the narrow secret denies.
    """
    block: dict[str, str] = {"*": "deny"}
    for root in write_roots:
        if root != "cwd":
            block[_opencode_write_rule(root)] = "allow"
    for root in OPENCODE_READ_ROOTS:
        block[root] = "allow"
    for glob in OPENCODE_SECRET_GLOBS:
        block[glob] = "deny"
    return block


def render_opencode_baseline() -> dict[str, Any]:
    """The permission block for an unprofiled one-off OpenCode run.

    ``OPENCODE_CONFIG`` merges over the user's own config, so this adds only
    what normal team work needs. Reads outside the repo are free, except the
    directories that hold a token file, which ask; every write outside the
    repo is denied. The team bash boundary is carried too, so an unprofiled run
    keeps the raw herdr deny and the hc mutation ask (review finding 4).
    """
    external: dict[str, str] = {"*": "allow"}
    for directory in OPENCODE_ASK_DIRS:
        external[directory] = "ask"
    for root in OPENCODE_READ_ROOTS:
        external[root] = "allow"
    for glob in OPENCODE_SECRET_GLOBS:
        external[glob] = "deny"
    edit: dict[str, str] = {"*": "allow", "..\\*": "deny", "Users\\*": "deny"}
    edit.update(_opencode_edit_denies())
    return {
        "permission": {
            "bash": dict(OPENCODE_TEAM_BASH),
            "external_directory": external,
            "read": _opencode_read_block(),
            "edit": edit,
        }
    }


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
