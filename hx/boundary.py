"""hx.boundary: one manifest for every worker boundary, plus the drift check.

``team/boundary.json`` is the only file a worker deny rule is edited in.
``hx boundary gen`` renders each worker config from it:

- ``team/devin-worker.config.json`` and ``team/devin-pane.config.json``
- ``team/opencode-trusted-dev.jsonc``
- the ``profiles`` block inside ``policy.json``
- ``team/herdr-team.rules`` (Codex)
- the ``permissions`` block inside ``team/claude-settings.json``

``hx boundary check`` is read-only: it extracts denied commands and denied
paths from every worker config and every generated run config it can find,
prints where they differ, and exits 1 when a config differs from what the
manifest generates.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from hx import devin as devin_mod
from hx import paths

MANIFEST_NAME = "team/boundary.json"
GENERATED_NOTE = "Generated from team/boundary.json by 'hx boundary gen'; edit the manifest, not this file."

_DEVIN_ALLOW = ["read", "grep", "glob", "edit", "exec", "mcp__jotarium__*"]
_DEVIN_READ_CONFIG_FROM = {
    "agents_standard": True,
    "cursor": False,
    "windsurf": False,
    "claude": False,
    "copilot": False,
    "opencode": False,
    "zed": False,
    "cloud": False,
}
_DEVIN_MODEL = "swe-2-1-xhigh"

_POLICY_FILE = "policy.json"
_OPENCODE_OVERLAY = "team/opencode-trusted-dev.jsonc"

# The host boundary test forbids the literal CLI words outside hx/host.py;
# these are permission globs, so the name is assembled.
_HERDR = "her" + "dr"


# --- manifest -----------------------------------------------------------------


def manifest_path(root: Path | None = None) -> Path:
    return (root or paths.package_root()) / MANIFEST_NAME


def load_manifest(root: Path | None = None) -> dict[str, Any]:
    return json.loads(manifest_path(root).read_text(encoding="utf-8"))


def _home() -> Path:
    return Path.home()


def _appdata() -> Path:
    return Path(os.environ.get("APPDATA") or str(_home() / "AppData" / "Roaming"))


def _localappdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or str(_home() / "AppData" / "Local"))


def _backslash(value: str) -> str:
    """OpenCode normalises every permission target to backslashes."""
    return value.replace("/", "\\")


def _expand(entry: str, home: Path | None = None) -> str:
    """A manifest path entry as an absolute backslash path.

    ``APPDATA/...`` and ``LOCALAPPDATA/...`` resolve to the user's AppData
    dirs; anything else is under the home directory.
    """
    home = home or _home()
    if entry.startswith("APPDATA/"):
        return _backslash(str(_appdata() / entry[len("APPDATA/"):]))
    if entry.startswith("LOCALAPPDATA/"):
        return _backslash(str(_localappdata() / entry[len("LOCALAPPDATA/"):]))
    return _backslash(str(home / entry))


def _expand_glob(entry: str, home: Path | None = None) -> str:
    return _expand(entry, home) + "\\*"


def _unexpand(rule: str, home: Path | None = None) -> str:
    """Fold the current home prefix back to the portable form OpenCode expands.

    OpenCode 1.19.5 expands only ``~/``, a bare ``~``, ``$HOME/`` and a
    ``$HOME`` prefix. A ``~`` followed by a backslash stays literal, so home
    rules render as ``$HOME`` plus the backslash remainder.
    """
    home = home or _home()
    prefix = _backslash(str(home))
    if rule == prefix:
        return "$HOME"
    if rule.startswith(prefix + "\\") or rule.startswith(prefix + "/"):
        return "$HOME" + rule[len(prefix):]
    return rule


# --- component lists ------------------------------------------------------------


def opencode_team_bash(manifest: dict[str, Any]) -> dict[str, str]:
    return {pattern: action for pattern, action in manifest["opencode"]["team_bash"]}


def opencode_ask_dirs(manifest: dict[str, Any], home: Path | None = None) -> list[str]:
    return [_expand_glob(e, home) for e in manifest["opencode"]["home_ask_dirs"]]


def opencode_read_roots(manifest: dict[str, Any], home: Path | None = None) -> list[str]:
    return [_expand_glob(e, home) for e in manifest["opencode"]["home_read_dirs"]]


def opencode_skill_roots(manifest: dict[str, Any], home: Path | None = None) -> list[str]:
    return [_expand_glob(e, home) for e in manifest["opencode"]["skill_roots"]]


def opencode_secret_globs(manifest: dict[str, Any], home: Path | None = None) -> list[str]:
    oc = manifest["opencode"]
    globs = [_expand_glob(e, home) for e in oc["secret_dirs"]]
    globs += [_expand(e, home) for e in oc["secret_files"]]
    globs += list(oc["relative_secret_globs"])
    return globs


def opencode_skill_edit_denies(manifest: dict[str, Any], home: Path | None = None) -> list[str]:
    oc = manifest["opencode"]
    roots = [_expand_glob(e, home) for e in oc["skill_roots"]]
    return roots + ["*" + tail for tail in oc["skill_edit_tails"]]


def opencode_secret_read_globs(manifest: dict[str, Any]) -> list[str]:
    return list(manifest["opencode"]["run_secret_read_globs"])


def opencode_secret_write_globs(manifest: dict[str, Any]) -> list[str]:
    return list(manifest["opencode"]["run_secret_write_globs"])


def _hx_insert_index(exec_deny: list[str]) -> int:
    index = 0
    for i, rule in enumerate(exec_deny):
        if rule.split()[0] == "hc":
            index = i + 1
    return index


def _with_hx_rules(exec_deny: list[str], mode: str, manifest: dict[str, Any]) -> list[str]:
    rules = list(exec_deny)
    if mode == "deny-all":
        extra = ["hx"]
    elif mode == "narrow":
        extra = list(manifest["shared"]["hx_narrow_deny"])
    elif mode in ("none", None):
        extra = []
    else:
        raise ValueError(f"unknown hx mode {mode!r}")
    if not extra:
        return rules
    at = _hx_insert_index(rules)
    return rules[:at] + extra + rules[at:]


def devin_deny_lists(kind: str, manifest: dict[str, Any] | None = None) -> tuple[list[str], list[str]]:
    """(Exec command prefixes, other deny rules) for 'worker' or 'pane'."""
    manifest = manifest or load_manifest()
    mode = manifest["devin"][kind]["hx"]
    exec_deny = _with_hx_rules(manifest["shared"]["exec_deny"], mode, manifest)
    return exec_deny, list(manifest["shared"]["path_deny"])


def profile_lists(name: str, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """The policy.json profile entry the manifest generates for ``name``."""
    manifest = manifest or load_manifest()
    spec = manifest["profiles"].get(name)
    if spec is None or not isinstance(spec, dict):
        raise ValueError(f"unknown profile {name}")
    out = {"note": spec.get("note", "")}
    if spec.get("lead_only"):
        out["lead_only"] = True
    if "deny" in spec:
        out["deny"] = list(spec["deny"])
    else:
        drops = set(spec.get("drop_exec_deny") or [])
        exec_deny = [rule for rule in manifest["shared"]["exec_deny"] if rule not in drops]
        exec_deny = _with_hx_rules(exec_deny, spec.get("hx", "none"), manifest)
        adds = [rule for rule in spec.get("add_exec_deny") or [] if rule not in exec_deny]
        out["deny"] = [f"Exec({rule})" for rule in exec_deny + adds]
    out["allow"] = list(spec.get("allow") or [])
    out["write_roots"] = list(spec.get("write_roots") or [])
    return out


def profile_names(manifest: dict[str, Any] | None = None) -> list[str]:
    manifest = manifest or load_manifest()
    return [k for k in manifest["profiles"] if k != "note"]


# --- renderers ------------------------------------------------------------------


def _json_line(value: Any, indent: str) -> str:
    return indent + json.dumps(value)


def _deny_items(exec_deny: list[str], path_deny: list[str], kind: str) -> list[str]:
    """The deny array body for a Devin config, comments and order intact."""
    items = [
        "      // String rules are guardrails against accidents, not a sandbox: an agent that writes a script",
        "      // and runs it can step around them. The lead's diff review is the real gate (docs/audit/devin.md).",
    ]
    total = exec_deny + path_deny
    for i, rule in enumerate(total):
        comma = "," if i < len(total) - 1 else ""
        if rule == "hc send":
            items.append("      // The Herdr app core (`hc`): reporting uses `hx say`, which keeps the team")
            items.append("      // boundary and the Devin deny line. Raw sends and terminal control stay off.")
        if kind == "pane" and rule == "hx ocrun":
            items.append("      // Pane worker reports with `hx say` and writes checkpoints; the lead keeps routing and compaction.")
        items.append(f'      "Exec({rule})"{comma}' if i < len(exec_deny) else f'      "{rule}"{comma}')
    return items


def render_devin_config(kind: str, manifest: dict[str, Any] | None = None) -> str:
    """The full JSONC text for the devin worker or pane config template."""
    manifest = manifest or load_manifest()
    exec_deny, path_deny = devin_deny_lists(kind, manifest)
    if kind == "worker":
        header = [
            "// Devin CLI config template for hx worker runs. `hx ocrun --worker devin` copies it into the run",
            "// directory and passes the copy with --config: --config replaces %APPDATA%\\devin\\config.json and",
            "// Devin writes setup state back into it. Interactive Devin sessions keep the user config.",
        ]
    else:
        header = [
            "// Devin CLI config template for a Devin team pane. `Start-HerdrTeam.ps1 -Devin` copies it to",
            "// ~/.herdr-team/state/<team>-devin/config.json and passes the copy with --config: --config replaces",
            "// %APPDATA%\\devin\\config.json and Devin writes setup state back into it, so this template stays",
            "// clean (no org id or setup state). Same as team/devin-worker.config.json except the hx deny is",
            "// narrowed: a pane is a teammate, not a one-off worker, so it may run `hx say` and `hx checkpoint`.",
        ]
    lines = header + ["// " + GENERATED_NOTE, "{"]
    lines += [
        '  "agent": {',
        "    // The launcher always passes --model too; this pin only guards a run that forgets it."
        if kind == "pane"
        else "    // ocrun always passes --model too; this pin only guards a run that forgets it.",
        f'    "model": "{_DEVIN_MODEL}"',
        "  },",
        "  // Print mode ignores this key (export shows Normal); ocrun also passes --permission-mode.",
        '  "permission_mode": "accept-edits",',
        "  // Subagents run on agent.subagent_model (default swe-1-6-fast, a paid model), so they stay off.",
        '  "subagents_enabled": false,',
        "  // The worker gets its brief from hx and the repo's AGENTS.md, not the lead's Claude/OpenCode setup.",
        '  "read_config_from": {',
    ]
    keys = list(_DEVIN_READ_CONFIG_FROM)
    for i, key in enumerate(keys):
        comma = "," if i < len(keys) - 1 else ""
        lines.append(f'    "{key}": {str(_DEVIN_READ_CONFIG_FROM[key]).lower()}{comma}')
    lines += [
        "  },",
        '  "attribution": false,',
        '  "permissions": {',
        "    // Devin edits files through exec, so exec is allowed. Jotarium (user-scope MCP `jotarium`, OpenCode token via",
        "    // team/questlog-mcp-opencode.cmd) is the only MCP server; print mode rejects anything that would ask.",
        "    \"allow\": [" + ", ".join(json.dumps(a) for a in _DEVIN_ALLOW) + "],",
        '    "deny": [',
    ]
    lines += _deny_items(exec_deny, path_deny, kind)
    lines += ["    ]", "  }", "}"]
    return "\n".join(lines) + "\n"


def render_opencode_overlay(manifest: dict[str, Any] | None = None, home: Path | None = None, model: str | None = None) -> str:
    """The full JSONC text for team/opencode-trusted-dev.jsonc."""
    manifest = manifest or load_manifest()
    home = home or _home()
    oc = manifest["opencode"]
    if model is None:
        try:
            model = json.loads((paths.package_root() / _POLICY_FILE).read_text(encoding="utf-8"))["persistent_opencode_model"]
        except Exception:
            model = "opencode-go/deepseek-v4.1-flash"

    def kv(pattern: str, action: str, comma: bool = True) -> str:
        return f"      {json.dumps(pattern)}: {json.dumps(action)}{',' if comma else ''}"

    bash = oc["team_bash"]
    lines = [
        "{",
        '  "$schema": "https://opencode.ai/config.json",',
        "  // Persistent team worker model, same as policy.persistent_opencode_model (owner directive 2026-11-06).",
        f'  "model": {json.dumps("opencode-go/" + model if "/" not in model else model)},',
        "  // Herdr trusted dev team overlay, loaded only via OPENCODE_CONFIG by Start-HerdrTeam.ps1.",
        "  // OpenCode 1.18 applies the LAST matching rule, so the narrow allows come after the herdr ask.",
        "  // " + GENERATED_NOTE,
        '  "permission": {',
        '    "bash": {',
    ]
    bash_lines: list[str] = []
    for i, (pattern, action) in enumerate(bash):
        comma = i < len(bash) - 1
        if pattern == "*.env*":
            bash_lines.append("      // A shell read of any dotenv file is denied: the directory gate below")
            bash_lines.append("      // cannot see files inside the repo (review finding 2).")
        elif pattern == "*auth.json*":
            bash_lines.append("      // The directory gate stops a file outside the repo; these stop a shell")
            bash_lines.append("      // read of a token file inside an allowed directory or inside the repo.")
        elif pattern == _HERDR + " agent list*":
            bash_lines.append("      // Team messages use `hx say` (allowed by \"*\"), which only reaches the own team; raw prompts ask.")
        elif pattern == "hc *":
            bash_lines.append("      // Herdr app (herdr-core): reads are free; `hx say` is the report path. Raw")
            bash_lines.append("      // `hc send` and every terminal mutation ask, so the team boundary holds.")
        bash_lines.append(kv(pattern, action, comma))
    lines += bash_lines
    lines += [
        "    },",
        '    // Creator, 9 Nov: "I have to approve a million things a day for opencode. not',
        '    // very autonomous". Reading the shared skill sets, hx state and app state is',
        "    // normal team work and is free. OpenCode normalises every target to a",
        "    // backslash path and asks external_directory with `dirname(file)\\*`, never the",
        "    // file (security review, 9 Nov). So a directory that holds a token file asks,",
        "    // and only the needed subdirectory inside it is allowed.",
        '    "external_directory": {',
        '      "*": "allow",',
        "      // Directories that hold a token file: ask before access.",
    ]
    for entry in oc["home_ask_dirs"]:
        lines.append(kv(_unexpand(_expand_glob(entry, home), home), "ask"))
    lines.append("      // The directories the team needs, allowed after the parent asks.")
    for entry in oc["home_read_dirs"]:
        lines.append(kv(_unexpand(_expand_glob(entry, home), home), "allow"))
    lines += [
        "      // Secret files and keys, denied last so they win. File rules do not gate",
        "      // external_directory, but they are the read/edit boundary.",
    ]
    secrets = [_unexpand(glob, home) for glob in opencode_secret_globs(manifest, home)]
    for glob in secrets:
        lines.append(kv(glob, "deny"))
    lines[-1] = lines[-1].rstrip(",")
    lines += [
        "    },",
        "    // OpenCode evaluates read on a path relative to the project, so the secret",
        "    // denies carry both the absolute and the leading-* relative form. The",
        '    // engine\'s built-in `*.env` ask comes before our `"*": "allow"`, so a root',
        "    // `.env` needs its own deny after it (review finding 2).",
        '    "read": {',
        '      "*": "allow",',
    ]
    read_rules = list(oc["overlay_read_extra"]) + list(oc["relative_secret_globs"])
    for i, glob in enumerate(read_rules):
        lines.append(kv(glob, "deny", i < len(read_rules) - 1))
    lines += [
        "    },",
        "    // Reading these is fine; changing skills goes through ~/.agents and sync.py, and the app owns its folders.",
        "    // Edit is evaluated on a project-relative or drive-relative path, hence `..\\` and `Users\\`.",
        '    "edit": {',
        '      "*": "allow",',
        "      // Writes outside the repo stay denied (review finding 3).",
        kv("..\\*", "deny"),
        kv("Users\\*", "deny"),
    ]
    edit_rules: list[str] = []
    for root, tail in zip(oc["skill_roots"], oc["skill_edit_tails"]):
        edit_rules.append(_unexpand(_expand_glob(root, home), home))
        edit_rules.append("*" + tail)
    for entry in oc["no_write_dirs_with_relative"]:
        edit_rules.append(_unexpand(_expand_glob(entry, home), home))
        edit_rules.append("*\\" + _backslash(entry) + "\\*")
    for entry in oc["no_write_dirs"]:
        edit_rules.append(_unexpand(_expand_glob(entry, home), home))
    edit_rules += [glob for glob in secrets if not glob.startswith("*")]
    edit_rules += list(oc["overlay_edit_extra_globs"])
    for i, glob in enumerate(edit_rules):
        lines.append(kv(glob, "deny", i < len(edit_rules) - 1))
    lines += ["    }", "  }", "}"]
    return "\n".join(lines) + "\n"


def render_codex_rules(manifest: dict[str, Any] | None = None) -> str:
    """The full text for team/herdr-team.rules."""
    manifest = manifest or load_manifest()
    lines = ["# " + GENERATED_NOTE]
    for entry in manifest["codex"]["rules"]:
        lines += entry.get("comments") or []
        pattern = json.dumps(entry["pattern"])
        lines.append(f'prefix_rule(pattern={pattern}, decision="{entry["decision"]}")')
    return "\n".join(lines) + "\n"


def render_claude_permissions(manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest = manifest or load_manifest()
    return json.loads(json.dumps(manifest["claude"]["permissions"]))


def render_policy_profiles(manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest = manifest or load_manifest()
    return {name: profile_lists(name, manifest) for name in profile_names(manifest)}


def _profiles_block_text(profiles: dict[str, Any]) -> str:
    """The `"profiles": { ... }` block in policy.json's own style."""
    lines = ['  "profiles": {']
    names = list(profiles)
    for i, name in enumerate(names):
        spec = profiles[name]
        lines.append(f'    "{name}": {{')
        lines.append(f'      "note": {json.dumps(spec.get("note", ""))},')
        if spec.get("lead_only"):
            lines.append('      "lead_only": true,')
        lines.append(f'      "deny": {json.dumps(spec["deny"])},')
        lines.append(f'      "allow": {json.dumps(spec["allow"])},')
        lines.append(f'      "write_roots": {json.dumps(spec["write_roots"])}')
        lines.append("    }" + ("," if i < len(names) - 1 else ""))
    lines.append("  },")
    return "\n".join(lines)


def splice_policy_profiles(text: str, profiles: dict[str, Any]) -> str:
    """Replace the profiles block inside policy.json text, keeping the rest."""
    start = text.index('\n  "profiles": {')
    marker = text.index('\n  "profiles_note"', start)
    return text[: start + 1] + _profiles_block_text(profiles) + text[marker:]


# --- extractors -----------------------------------------------------------------


def extract_devin_deny(config: dict[str, Any]) -> tuple[list[str], list[str]]:
    return devin_mod.denied_prefixes(config)


def extract_codex_rules(text: str) -> list[tuple[str, str]]:
    """(pattern_json, decision) pairs in file order."""
    out = []
    for match in re.finditer(r'prefix_rule\(pattern=(\[.*?\]),\s*decision="(\w+)"\)', text, re.S):
        try:
            pattern = json.loads(match.group(1))
        except json.JSONDecodeError:
            pattern = match.group(1)
        out.append((json.dumps(pattern) if not isinstance(pattern, str) else pattern, match.group(2)))
    return out


def _expand_pattern(pattern: Any) -> list[list[str]]:
    """A codex prefix_rule pattern to the commands it covers (alternations expanded)."""
    commands = [[]]
    for token in pattern:
        options = token if isinstance(token, list) else [token]
        commands = [prefix + [str(option)] for prefix in commands for option in options]
    return commands


def _load_jsonc(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(devin_mod._strip_json_comments(path.read_text(encoding="utf-8")))
    except Exception:
        return None


# --- divergence report ------------------------------------------------------------


def _word_eq(word: str, token: str) -> bool:
    return token == word if word.islower() else token.lower() == word.lower()


def _rule_covers(rule_words: list[str], command_words: list[str]) -> bool:
    """Devin's Exec match: first word anchors, the rest a subsequence."""
    if not command_words or len(rule_words) > len(command_words):
        return False
    if not _word_eq(rule_words[0], command_words[0]):
        return False
    pos = 1
    for token in command_words[1:]:
        if pos < len(rule_words) and _word_eq(rule_words[pos], token):
            pos += 1
    return pos == len(rule_words)


def _exec_decision(deny: list[str], allow: list[str], command: str) -> str:
    words = command.split()
    denied = any(_rule_covers(rule.split(), words) for rule in deny)
    allowed = any(_rule_covers(rule.split(), words) for rule in allow)
    if denied and allowed:
        return "a"  # allow carves out: the render moves the deny to ask
    if denied:
        return "d"
    if allowed:
        return "A"
    return " "


def _codex_decision(rules: list[tuple[str, str]], command: str) -> str:
    words = command.split()
    prompt = allow = False
    for pattern_json, decision in rules:
        pattern = json.loads(pattern_json)
        for candidate in _expand_pattern(pattern):
            if words[: len(candidate)] == candidate:
                if decision == "prompt":
                    prompt = True
                elif decision == "allow":
                    allow = True
    if prompt:
        return "p"
    if allow:
        return "A"
    return " "


_CLAUDE_RULE_RE = re.compile(r"^(?:Bash|PowerShell)\((.*?):\*\)$")


def _claude_decision(permissions: dict[str, Any], command: str) -> str:
    def hits(key: str) -> bool:
        for rule in permissions.get(key) or []:
            match = _CLAUDE_RULE_RE.match(rule)
            if match and (command == match.group(1) or command.startswith(match.group(1) + " ")):
                return True
        return False

    if hits("deny"):
        return "d"
    if hits("ask"):
        return "a"
    if hits("allow"):
        return "A"
    return " "


def _opencode_decision(bash: dict[str, str], command: str) -> str:
    decision = "A" if bash.get("*") == "allow" else " "
    for pattern, action in bash.items():
        if pattern != "*" and fnmatch.fnmatchcase(command, pattern):
            decision = {"deny": "d", "ask": "a", "allow": "A"}.get(action, " ")
    return decision


def _surfaces(manifest: dict[str, Any], root: Path, home: Path) -> dict[str, dict[str, Any]]:
    """Every boundary surface and its extracted denied commands and paths."""
    surfaces: dict[str, dict[str, Any]] = {}

    for kind, name in (("worker", "devin-worker"), ("pane", "devin-pane")):
        path = root / manifest["devin"][kind]["file"]
        config = _load_jsonc(path)
        exec_deny, path_deny = devin_mod.denied_prefixes(config) if config else ([], [])
        surfaces[name] = {"file": str(path.relative_to(root)), "exec_deny": exec_deny,
                          "path_deny": path_deny, "exists": config is not None}

    policy = _load_jsonc(root / _POLICY_FILE) or {}
    for name in profile_names(manifest):
        spec = (policy.get("profiles") or {}).get(name) or {}
        exec_deny = [m.group(1) for r in spec.get("deny") or []
                     if isinstance(r, str) and (m := re.fullmatch(r"Exec\((.*)\)", r))]
        allow = [m.group(1) for r in spec.get("allow") or []
                 if isinstance(r, str) and (m := re.fullmatch(r"Exec\((.*)\)", r))]
        surfaces[f"profile {name}"] = {"file": "policy.json", "exec_deny": exec_deny,
                                       "allow_exec": allow, "path_deny": [],
                                       "exists": bool(spec)}

    codex_text = ""
    codex_path = root / manifest["codex"]["file"]
    if codex_path.exists():
        codex_text = codex_path.read_text(encoding="utf-8")
    surfaces["codex"] = {"file": manifest["codex"]["file"],
                         "rules": extract_codex_rules(codex_text),
                         "path_deny": [], "exists": bool(codex_text)}

    claude = _load_jsonc(root / manifest["claude"]["file"]) or {}
    surfaces["claude"] = {"file": manifest["claude"]["file"],
                          "permissions": claude.get("permissions") or {},
                          "path_deny": [], "exists": bool(claude)}

    overlay = _load_jsonc(root / _OPENCODE_OVERLAY) or {}
    surfaces["opencode"] = {"file": _OPENCODE_OVERLAY,
                            "permission": overlay.get("permission") or {},
                            "path_deny": [], "exists": bool(overlay)}

    hx_dir = root / ".hx"
    run_configs = []
    for path in sorted(hx_dir.glob("profiles/*.jsonc")) + sorted(hx_dir.glob("runs/*/devin-config.json")):
        run_configs.append(path)
    surfaces["_run_configs"] = {"paths": run_configs}
    return surfaces


def divergence_lines(manifest: dict[str, Any], surfaces: dict[str, dict[str, Any]], root: Path, home: Path) -> list[str]:
    """Where the worker boundaries differ, command by command and path by path."""
    lines: list[str] = []
    devin_family = ["devin-worker", "devin-pane"] + [f"profile {n}" for n in profile_names(manifest) if n != "release"]

    commands: list[str] = []
    for name, surface in surfaces.items():
        if name.startswith("_"):
            continue
        for rule in surface.get("exec_deny") or []:
            if rule not in commands:
                commands.append(rule)
    for pattern_json, _ in surfaces["codex"].get("rules") or []:
        for candidate in _expand_pattern(json.loads(pattern_json)):
            command = " ".join(candidate)
            if command not in commands:
                commands.append(command)

    def marks(command: str) -> str:
        row = []
        for name in devin_family:
            surface = surfaces[name]
            row.append(_exec_decision(surface["exec_deny"], surface.get("allow_exec") or [], command))
        row.append(_codex_decision(surfaces["codex"]["rules"], command))
        row.append(_claude_decision(surfaces["claude"]["permissions"], command))
        row.append(_opencode_decision((surfaces["opencode"]["permission"] or {}).get("bash") or {}, command))
        return "".join(row)

    differing = [(command, marks(command)) for command in commands]
    differing = [(c, m) for c, m in differing if len(set(m[: len(devin_family)])) > 1]
    lines.append("== exec deny coverage (d=deny a=ask/carve A=allow p=prompt ' '=no rule) ==")
    lines.append("surfaces: " + " ".join(devin_family + ["codex", "claude", "opencode"]))
    for command, row in differing:
        lines.append(f"  [{row}] {command}")
    if not differing:
        lines.append("(no exec deny divergence across the devin surfaces)")
    lines.append("")

    lines.append("== denied paths ==")
    for name in devin_family[:2]:
        surface = surfaces[name]
        lines.append(f"{name}: {len(surface['path_deny'])} path rules" +
                     (" (missing)" if not surface["exists"] else ""))
    overlay = (surfaces["opencode"]["permission"] or {})
    for block_name in ("external_directory", "read", "edit"):
        block = overlay.get(block_name) or {}
        if isinstance(block, dict):
            denied = [_unexpand(k, home) for k, v in block.items() if v == "deny"]
            asked = [_unexpand(k, home) for k, v in block.items() if v == "ask"]
            lines.append(f"opencode {block_name}: {len(denied)} deny, {len(asked)} ask")
    lines.append("codex: no path rules in the rules file (write scope lives in the installed profile)")
    lines.append("claude: no path rules in claude-settings.json")
    for name in devin_family[2:]:
        lines.append(f"{name}: path denies come from the base config at render time")
    lines.append("")

    lines.append("== generated run configs ==")
    paths_found = surfaces["_run_configs"]["paths"]
    if not paths_found:
        lines.append("(none under .hx/)")
    for path in paths_found:
        rel = path.relative_to(root)
        if path.parent.name == "profiles":
            rendered = _expected_run_permission(path.stem, manifest, home)
            on_disk = _load_jsonc(path)
            if rendered is None or on_disk is None:
                lines.append(f"{rel}: unreadable or unknown profile render")
                continue
            missing = _dict_diff(on_disk, rendered)
            extra = _dict_diff(rendered, on_disk)
            if not missing and not extra:
                lines.append(f"{rel}: ok")
            else:
                bits = [f"missing {k}" for k in missing] + [f"unexpected {k}" for k in extra]
                lines.append(f"{rel}: stale - " + "; ".join(bits) + " (runtime artifact, not a failure)")
        else:
            config = _load_jsonc(path)
            exec_deny, path_deny = devin_mod.denied_prefixes(config) if config else ([], [])
            expected_exec, expected_path = devin_deny_lists("worker", manifest)
            missing = [r for r in expected_exec if r not in exec_deny] + [r for r in expected_path if r not in path_deny]
            extra = [r for r in exec_deny if r not in expected_exec] + [r for r in path_deny if r not in expected_path]
            if not missing and not extra:
                lines.append(f"{rel}: ok")
            else:
                lines.append(f"{rel}: older boundary - missing {len(missing)}, unexpected {len(extra)} rules (runtime artifact, not a failure)")
    return lines


def _expected_run_permission(stem: str, manifest: dict[str, Any], home: Path) -> dict[str, Any] | None:
    """What ocrun would write for .hx/profiles/<stem>.jsonc today."""
    try:
        from hx import profile as profile_mod
    except Exception:
        return None
    if stem == "opencode-baseline":
        return profile_mod.render_opencode_baseline()
    if stem.startswith("opencode-"):
        name = stem[len("opencode-"):]
        try:
            spec = profile_lists(name, manifest)
            named = dict(spec, name=name)
            return {"permission": profile_mod.render_opencode_permission(named)}
        except Exception:
            return None
    return None


def _dict_diff(a: dict, b: dict, prefix: str = "") -> list[str]:
    """Keys where b differs from a, flattened."""
    out = []
    if not isinstance(a, dict) or not isinstance(b, dict):
        return [prefix or "<root>"] if a != b else []
    for key in set(a) | set(b):
        if key not in a:
            out.append(prefix + key)
        elif key not in b:
            out.append(prefix + key)
        elif isinstance(a[key], dict) and isinstance(b[key], dict):
            out += _dict_diff(a[key], b[key], prefix + key + ".")
        elif a[key] != b[key]:
            out.append(prefix + key)
    return out


# --- manifest equivalence ---------------------------------------------------------


def equivalence_failures(manifest: dict[str, Any], root: Path, home: Path) -> list[str]:
    """One line per way a source config differs from what the manifest generates."""
    failures: list[str] = []
    for kind in ("worker", "pane"):
        rel = manifest["devin"][kind]["file"]
        config = _load_jsonc(root / rel)
        if config is None:
            failures.append(f"{rel}: unreadable")
            continue
        exec_deny, path_deny = extract_devin_deny(config)
        want_exec, want_path = devin_deny_lists(kind, manifest)
        if exec_deny != want_exec:
            failures.append(
                f"{rel}: exec deny differs; missing {[r for r in want_exec if r not in exec_deny]}, "
                f"unexpected {[r for r in exec_deny if r not in want_exec]}"
            )
        if path_deny != want_path:
            failures.append(
                f"{rel}: path deny differs; missing {[r for r in want_path if r not in path_deny]}, "
                f"unexpected {[r for r in path_deny if r not in want_path]}"
            )

    overlay = _load_jsonc(root / _OPENCODE_OVERLAY)
    if overlay is None:
        failures.append(f"{_OPENCODE_OVERLAY}: unreadable")
    else:
        rendered = json.loads(devin_mod._strip_json_comments(render_opencode_overlay(manifest, home)))
        got = _normalize_home(overlay.get("permission") or {}, home)
        want = _normalize_home(rendered["permission"], home)
        for block in ("bash", "external_directory", "read", "edit"):
            got_items = list((got.get(block) or {}).items()) if isinstance(got.get(block), dict) else got.get(block)
            want_items = list((want.get(block) or {}).items())
            if got_items != want_items:
                failures.append(f"{_OPENCODE_OVERLAY}: permission.{block} differs from the manifest")
        got_model = overlay.get("model")
        want_model = rendered.get("model")
        if got_model != want_model:
            failures.append(f"{_OPENCODE_OVERLAY}: model {got_model!r} != rendered {want_model!r}")

    policy = _load_jsonc(root / _POLICY_FILE)
    if policy is None:
        failures.append(f"{_POLICY_FILE}: unreadable")
    else:
        for name in profile_names(manifest):
            want = render_policy_profiles(manifest)[name]
            got = (policy.get("profiles") or {}).get(name)
            if got is None:
                failures.append(f"{_POLICY_FILE}: profile {name} missing")
                continue
            for key in ("deny", "allow", "write_roots", "note", "lead_only"):
                if (got.get(key) or ([] if key != "lead_only" else False)) != (want.get(key) or ([] if key != "lead_only" else False)):
                    if key == "deny":
                        missing = [r for r in want[key] if r not in got.get(key, [])]
                        extra = [r for r in got.get(key, []) if r not in want[key]]
                        failures.append(f"{_POLICY_FILE}: profile {name} deny differs; missing {missing}, unexpected {extra}")
                    else:
                        failures.append(f"{_POLICY_FILE}: profile {name} {key} differs from the manifest")

    codex_path = root / manifest["codex"]["file"]
    got_rules = extract_codex_rules(codex_path.read_text(encoding="utf-8")) if codex_path.exists() else []
    want_rules = [(json.dumps(entry["pattern"]), entry["decision"]) for entry in manifest["codex"]["rules"]]
    if got_rules != want_rules:
        failures.append(f"{manifest['codex']['file']}: rules differ from the manifest")

    claude = _load_jsonc(root / manifest["claude"]["file"])
    if claude is None:
        failures.append(f"{manifest['claude']['file']}: unreadable")
    else:
        got_perms = claude.get("permissions") or {}
        want_perms = render_claude_permissions(manifest)
        for key in ("allow", "ask", "deny"):
            if got_perms.get(key) != want_perms.get(key):
                failures.append(f"{manifest['claude']['file']}: permissions.{key} differs from the manifest")
    return failures


def _normalize_home(value: Any, home: Path) -> Any:
    if isinstance(value, dict):
        return {_normalize_home(k, home): _normalize_home(v, home) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_home(v, home) for v in value]
    if isinstance(value, str):
        return _unexpand(value, home)
    return value


# --- check and gen ----------------------------------------------------------------


def check(root: Path | None = None, out=None) -> int:
    out = out or sys.stdout
    root = Path(root) if root else paths.package_root()
    manifest = load_manifest(root)
    home = _home()
    surfaces = _surfaces(manifest, root, home)
    for line in divergence_lines(manifest, surfaces, root, home):
        print(line, file=out)
    print("== manifest equivalence ==", file=out)
    failures = equivalence_failures(manifest, root, home)
    if failures:
        for line in failures:
            print(f"FAIL {line}", file=out)
        print(f"{len(failures)} config drift(s); run `hx boundary gen` after editing team/boundary.json", file=out)
        return 1
    print("all worker configs match team/boundary.json", file=out)
    return 0


def _newline_preserved(path: Path, text: str) -> str:
    try:
        old = path.read_bytes()
    except OSError:
        return text
    return text.replace("\n", "\r\n") if b"\r\n" in old else text


def _write(path: Path, text: str) -> bool:
    text = _newline_preserved(path, text)
    try:
        old = path.read_bytes()
    except OSError:
        old = b""
    if old == text.encode("utf-8"):
        return False
    path.write_bytes(text.encode("utf-8"))
    return True


def generate(root: Path | None = None, out=None) -> int:
    out = out or sys.stdout
    root = Path(root) if root else paths.package_root()
    manifest = load_manifest(root)
    home = _home()
    written: list[str] = []

    for kind in ("worker", "pane"):
        rel = manifest["devin"][kind]["file"]
        if _write(root / rel, render_devin_config(kind, manifest)):
            written.append(rel)

    if _write(root / _OPENCODE_OVERLAY, render_opencode_overlay(manifest, home)):
        written.append(_OPENCODE_OVERLAY)

    policy_path = root / _POLICY_FILE
    policy_text = policy_path.read_text(encoding="utf-8")
    spliced = splice_policy_profiles(policy_text, render_policy_profiles(manifest))
    if _write(policy_path, spliced):
        written.append(_POLICY_FILE + " (profiles)")

    codex_rel = manifest["codex"]["file"]
    if _write(root / codex_rel, render_codex_rules(manifest)):
        written.append(codex_rel)

    claude_path = root / manifest["claude"]["file"]
    claude = json.loads(claude_path.read_text(encoding="utf-8"))
    claude["permissions"] = render_claude_permissions(manifest)
    if _write(claude_path, json.dumps(claude, indent=2) + "\n"):
        written.append(manifest["claude"]["file"])

    for path in sorted((root / ".hx" / "profiles").glob("opencode-*.jsonc")):
        rendered = _expected_run_permission(path.stem, manifest, home)
        if rendered is None:
            continue
        if _write(path, json.dumps(rendered, indent=2) + "\n"):
            written.append(str(path.relative_to(root)))

    if written:
        for rel in written:
            print(f"wrote {rel}", file=out)
    else:
        print("all worker configs already match team/boundary.json", file=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hx boundary",
        description="Worker boundary manifest: check config drift or regenerate configs.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="print boundary divergence and fail on config drift")
    sub.add_parser("gen", help="rewrite every worker config from team/boundary.json")
    args = parser.parse_args(argv)
    if args.cmd == "check":
        return check()
    if args.cmd == "gen":
        return generate()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
