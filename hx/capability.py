"""hx.capability: per-worker admission profiles and the dispatch preflight.

One profile per worker kind ``hx ocrun`` can launch, built from the live
config where hx can read it, with the shipped template as fallback:

- ``devin``: ``Exec(...)``/``Write(...)``/``Read(...)`` deny rules and the tool
  allow-list from ``team/devin-worker.config.json`` (the file every run gets
  via ``--config``), unioned with the live pane config
  ``<state>/<team>-devin/config.json`` when it is readable (its fallback is
  ``team/devin-pane.config.json``). Unioning is conservative: a run copies
  the worker template, so every deny it adds is real, and any extra deny the
  pane gained is honoured too.
- ``opencode``: ``permission.bash`` rules from
  ``team/opencode-trusted-dev.jsonc``, applied last-match-wins like OpenCode.
  ``ask`` counts as denied: headless and print-mode runs cannot answer.
- ``codex``: ``codex exec -p trusted-dev -s workspace-write`` has no command
  deny list; the boundary is filesystem. Writes are limited to the run cwd
  plus ``[sandbox_workspace_write].writable_roots`` from
  ``~/.codex/trusted-dev.config.toml``.

Deny-match semantics mirror the measured Devin behaviour
(docs/audit/devin.md): an ``Exec(<words>)`` rule denies a command segment
whose first word is the rule's first word and whose remaining tokens contain
the rest as a subsequence of literal words (``git -C . push`` matches
``Exec(git push)``; ``devin.exe`` does not match ``Exec(devin)``; ``claude``
as an argument of ``hx say`` does not match ``Exec(claude)``). Compound
commands are checked per ``&&``/``;``/``|`` segment.

The preflight scans only what a worker would execute: inline code spans in an
instruction position (line start, after ``:``/``;``, or after a verb like
``run``/``with``/``via``), fenced or indented code blocks, and explicit
"run X" phrases. Bare prose is never scanned: ``Herdr``, ``Devin`` and
``format`` are product names, not commands. Lines that prohibit or discuss
denials (``do not``, ``don't``, ``never``, ``no``, ``denied``…) are skipped,
and any denied command they name suppresses the same rule everywhere in the
brief, because "X is denied for you" cancels a stale instruction to run X.

``--allow-preflight-risk`` overrides the refusal.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from pathlib import Path
from typing import Any

from hx import devin as devin_mod
from hx import paths

WORKER_PROFILES: tuple[str, ...] = ("devin", "opencode", "codex")

OPENCODE_CONFIG = "team/opencode-trusted-dev.jsonc"
CODEX_PROFILE = ".codex/trusted-dev.config.toml"

# Devin's own tool surface: names a brief or an outcome can literally carry.
# Only distinctive names are scanned in prose; read/write/edit/exec are too
# common as English words.
DEVIN_KNOWN_TOOLS = (
    "webfetch",
    "websearch",
    "browser",
    "notebook",
    "ask_user",
    "upload_secrets",
)
EXEC_TOOL_NAMES = {"exec", "exec_command", "bash", "shell", "run_command", "local_shell_call"}

_WRITE_VERB_RE = re.compile(
    r"\b(write|writes|writing|edit|edits|editing|create|creates|creating|save|saves|"
    r"copy|copies|install|installs|move|moves|update|updates|modify|delete|deletes|"
    r"put|puts|mkdir|Set-Content|Out-File|Copy-Item|Move-Item|New-Item|tee|touch)\b",
    re.IGNORECASE,
)
_PATH_RE = re.compile(r"(?<![\w/.-])(?:[A-Za-z]:[\\/]|~[\\/]|\\\\)[^\s`\"'|<>*]*")
_TOKEN_SPLIT_RE = re.compile(r"[\s;&|]+")
_TOKEN_EDGE_CHARS = "`\"'#>*_():,[]{}"
_DEVIN_REJECTION_RE = re.compile(r"permission denied:\s*([A-Za-z_][\w-]*)\s*(.*)", re.DOTALL)


def _load_jsonc(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(devin_mod._strip_json_comments(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def _seq_rules(config: dict[str, Any]) -> list[list[str]]:
    """Exec deny rules as word lists, in config order."""
    commands, _ = devin_mod.denied_prefixes(config)
    return [cmd.split() for cmd in commands]


def _path_rules(config: dict[str, Any]) -> list[tuple[str, str]]:
    """Non-Exec deny rules as (tool, glob) pairs, lower-cased tool."""
    _, other = devin_mod.denied_prefixes(config)
    rules: list[tuple[str, str]] = []
    for rule in other:
        match = re.fullmatch(r"(Read|Write)\((.*)\)", rule.strip())
        if match:
            rules.append((match.group(1).lower(), match.group(2)))
    return rules


def _devin_configs(policy: dict[str, Any] | None) -> list[tuple[Path, dict[str, Any]]]:
    """Readable devin configs, primary first: the run template, then the live pane config.

    The pane name follows the team prefix (``dev-devin`` on the live team);
    the shipped pane template stands in when the live file is absent.
    """
    sources: list[Path] = []
    if policy is not None:
        sources.append(devin_mod.worker_config_template(policy))
    else:
        sources.append(devin_mod.worker_config_template({"devin": {}}))
    prefix = "dev-"
    try:
        from hx import usage as hx_usage

        if policy is not None:
            prefix = hx_usage.team_prefix(policy)
    except Exception:
        pass
    sources.append(devin_mod.state_dir() / f"{prefix}devin" / "config.json")
    sources.append(devin_mod.pane_config_template())

    found: list[tuple[Path, dict[str, Any]]] = []
    seen: set[str] = set()
    for path in sources:
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        config = _load_jsonc(path)
        if config is not None:
            found.append((path, config))
    return found


def _narrow_by_pane(worker_seq: list[list[str]], pane_seq: list[list[str]]) -> list[list[str]]:
    """Worker-wide denies the pane splits into per-subcommand denies.

    A pane rule that strictly extends a worker rule names the denied subset,
    so the rest of the family is allowed: the pane denies
    ``hx ocrun|compact|context|task|route`` where the worker denies ``hx``,
    which leaves ``hx say`` and ``hx checkpoint`` allowed for Devin.
    """
    out: list[list[str]] = []
    for words in worker_seq:
        extensions = [
            p for p in pane_seq if len(p) > len(words) and p[: len(words)] == words
        ]
        out.extend(extensions or [words])
    for words in pane_seq:
        if words not in out:
            out.append(words)
    return out


def _devin_profile(policy: dict[str, Any] | None) -> dict[str, Any]:
    configs = _devin_configs(policy)
    worker_seq: list[list[str]] = []
    pane_seq: list[list[str]] = []
    path_rules: list[tuple[str, str]] = []
    for path, config in configs:
        target = worker_seq if "worker" in path.name else pane_seq
        for words in _seq_rules(config):
            if words and words not in target:
                target.append(words)
        for rule in _path_rules(config):
            if rule not in path_rules:
                path_rules.append(rule)
    seq = _narrow_by_pane(worker_seq, pane_seq)
    allow = ((configs[0][1].get("permissions") or {}).get("allow") or []) if configs else []
    denied_tools = [
        tool
        for tool in DEVIN_KNOWN_TOOLS
        if not any(fnmatch.fnmatchcase(tool, pattern.lower()) for pattern in allow)
    ]
    return {
        "worker": "devin",
        "seq_rules": seq,
        "path_rules": path_rules,
        "allowed_tools": list(allow),
        "denied_tools": denied_tools,
        "writes_outside_cwd": True,
        "writable_roots": [],
        "sources": [str(path) for path, _ in configs],
    }


def _opencode_profile() -> dict[str, Any]:
    path = paths.package_root() / OPENCODE_CONFIG
    config = _load_jsonc(path)
    rules: list[tuple[str, str]] = []
    if config:
        for pattern, action in ((config.get("permission") or {}).get("bash") or {}).items():
            if isinstance(action, str):
                rules.append((pattern, action.lower()))
    denied = [pattern for pattern, action in rules if action != "allow"]
    return {
        "worker": "opencode",
        "seq_rules": [],
        "bash_rules": rules,
        "path_rules": [],
        "allowed_tools": None,
        "denied_tools": [],
        "denied_globs": denied,
        "writes_outside_cwd": True,
        "writable_roots": [],
        "sources": [str(path)] if config else [],
    }


def _codex_profile() -> dict[str, Any]:
    path = Path(os.path.expanduser("~")) / CODEX_PROFILE
    roots: list[str] = []
    source = None
    try:
        import tomllib

        data = tomllib.loads(path.read_text(encoding="utf-8"))
        roots = list((data.get("sandbox_workspace_write") or {}).get("writable_roots") or [])
        source = str(path)
    except Exception:
        pass
    return {
        "worker": "codex",
        "seq_rules": [],
        "bash_rules": [],
        "path_rules": [],
        "allowed_tools": None,
        "denied_tools": [],
        "writes_outside_cwd": False,
        "writable_roots": roots,
        "sources": [source] if source else [],
    }


def worker_profile(
    worker: str | None,
    policy: dict[str, Any] | None = None,
    profile_name: str | None = None,
) -> dict[str, Any] | None:
    """The capability profile for one worker kind, or None for workers without one.

    With ``profile_name`` the named execution profile (policy.json ``profiles``)
    supplies the exec rules and write scope instead of the worker's static
    config; the base deny-path rules (secrets) and denied tools stay.
    """
    if worker == "devin":
        profile = _devin_profile(policy)
    elif worker == "opencode":
        profile = _opencode_profile()
    elif worker == "codex":
        profile = _codex_profile()
    else:
        return None
    if profile_name is not None:
        from hx import profile as profile_mod

        named = profile_mod.get(profile_name, policy)
        if named is None:
            profile["profile_error"] = f"unknown profile {profile_name}"
            return profile
        profile["profile_name"] = profile_name
        profile["lead_only"] = bool(named.get("lead_only"))
        if not named.get("lead_only"):
            profile["seq_rules"] = profile_mod.seq_rules(named)
            profile["allow_seq_rules"] = profile_mod.allow_seq_rules(named)
            profile["bash_rules"] = list(profile_mod.render_opencode_permission(named)["bash"].items())
            profile["writable_roots"] = [r for r in named.get("write_roots") or [] if r != "cwd"]
            profile["writes_outside_cwd"] = bool(named.get("write_roots"))
    return profile


def _tokens(text: str) -> list[str]:
    """Shell-ish words: split on whitespace and command separators, edges cleaned."""
    out: list[str] = []
    for raw in _TOKEN_SPLIT_RE.split(text):
        token = raw.strip(_TOKEN_EDGE_CHARS)
        if token:
            out.append(token)
    return out


def _word_eq(word: str, token: str) -> bool:
    """Case rule: lowercase config words (CLIs) match lowercase tokens only.

    Briefs name the tools in prose with capitals (Herdr, Devin, Git) while
    real command words are lowercase; mixed-case rules like Remove-Item are
    PowerShell cmdlets and match any case.
    """
    if word.islower():
        return token == word
    return token.lower() == word.lower()


def _is_subsequence(words: list[str], tokens: list[str]) -> bool:
    if not words:
        return True
    pos = 0
    for token in tokens:
        if _word_eq(words[pos], token):
            pos += 1
            if pos == len(words):
                return True
    return False


_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||[;|]")


def _opencode_denied(profile: dict[str, Any], command: str) -> str | None:
    """The deciding bash rule when OpenCode would not run ``command`` unasked."""
    decision = "deny"
    decided_by = None
    for pattern, action in profile.get("bash_rules") or []:
        if fnmatch.fnmatchcase(command, pattern):
            decision, decided_by = action, pattern
    if decision == "allow":
        return None
    return decided_by


def denied_exec(profile: dict[str, Any], command: str) -> str | None:
    """The deny rule that stops ``command`` on this worker, or None.

    A rule's first word must anchor a command segment; its remaining words
    are a subsequence of the segment's tokens. Arguments of another command
    never match (``hx say claude`` is not ``claude``).
    """
    for segment in _SEGMENT_SPLIT_RE.split(command):
        segment = segment.strip()
        if not segment:
            continue
        tokens = _tokens(segment)
        allowed = False
        for words in profile.get("allow_seq_rules") or []:
            if (
                tokens
                and len(words) <= len(tokens)
                and _word_eq(words[0], tokens[0])
                and _is_subsequence(words[1:], tokens[1:])
            ):
                allowed = True
        if allowed:
            continue
        if profile["worker"] == "opencode":
            rule = _opencode_denied(profile, segment)
            if rule is not None:
                return rule
            continue
        for words in profile.get("seq_rules") or []:
            if (
                tokens
                and len(words) <= len(tokens)
                and _word_eq(words[0], tokens[0])
                and _is_subsequence(words[1:], tokens[1:])
            ):
                return f"Exec({' '.join(words)})"
    return None


def tool_denied(profile: dict[str, Any], tool: str) -> bool:
    """Whether a named tool is denied by omission from the allow list."""
    allow = profile.get("allowed_tools")
    if allow is None:
        return False
    name = tool.lower()
    return not any(fnmatch.fnmatchcase(name, pattern.lower()) for pattern in allow)


def _norm(path_text: str) -> str:
    expanded = os.path.expanduser(os.path.expandvars(path_text))
    return os.path.normcase(os.path.normpath(expanded))


def _under(path_norm: str, root_norm: str) -> bool:
    return path_norm == root_norm or path_norm.startswith(root_norm + os.sep)


def _path_rule_denied(rule: tuple[str, str], path_norm: str, home_norm: str, is_write: bool) -> str | None:
    tool, pattern = rule
    if tool == "write" and not is_write:
        return None
    label = f"{'Write' if tool == 'write' else 'Read'}({pattern})"
    pattern = pattern.replace("/", os.sep).replace("\\", os.sep)
    if pattern.startswith("~"):
        base = _norm(pattern[:-2] if pattern.endswith(os.sep + "**") else pattern.rstrip(os.sep + "*"))
        if _under(path_norm, base):
            return label
        return None
    if pattern.endswith(os.sep + "**"):
        stem = pattern[: -len(os.sep + "**")]
        if stem.startswith("**" + os.sep):
            segment = stem[len("**" + os.sep) :]
            if os.sep + segment in path_norm or path_norm.endswith(os.sep + segment) or path_norm.startswith(segment + os.sep):
                return label
            return None
    if pattern.startswith("**" + os.sep):
        leaf = pattern[len("**" + os.sep) :]
        if any(fnmatch.fnmatchcase(part, leaf) for part in path_norm.split(os.sep)):
            return label
        return None
    if os.sep not in pattern:
        if fnmatch.fnmatchcase(path_norm.split(os.sep)[-1], pattern):
            return label
        return None
    if _norm(pattern) == path_norm:
        return label
    return None


def _writable_scope(profile: dict[str, Any], cwd: Path) -> list[str]:
    roots = [_norm(str(cwd))]
    for raw in profile.get("writable_roots") or []:
        roots.append(_norm(raw))
    return roots


_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
_FENCE_RE = re.compile(r"^\s*```")
_INDENTED_CODE_RE = re.compile(r"^(?:    |\t)\S")
_RUN_PHRASE_RE = re.compile(
    r"(?i)\b(?:run|runs|running|execute|executes|invoke|invokes|start|starts|try|tries)\s+((?:[\w`~./\\:-]+\s*)+)"
)
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")

# A code span is a command only where a command would be given: line start,
# after a colon or separator, or after a verb that introduces an invocation.
_EXEC_TRIGGERS = frozenset(
    {
        "run", "runs", "execute", "executes", "invoke", "invokes", "call", "calls",
        "use", "uses", "using", "try", "tries", "start", "starts", "launch",
        "via", "with", "through", "then", "fetch", "vendor", "install", "installs",
        "update", "updates", "upgrade", "check", "checks", "test", "verify",
        "rerun", "send",
    }
)

# Lines that prohibit or discuss denials are skipped, and the denied commands
# they name suppress the same rules elsewhere in the brief.
_SKIP_RE = re.compile(
    r"\b(?:do\s+not|don'?t|never|must\s+not|denied|denial|deny|no)\b", re.IGNORECASE
)


def _exec_position(before: str) -> bool:
    before = _LIST_MARKER_RE.sub("", before).rstrip().rstrip("*_").rstrip()
    if not before:
        return True
    if before[-1] in ":;&|":
        return True
    word = before.split()[-1].strip("`'\"(#*._:,").lower()
    return word in _EXEC_TRIGGERS


def _command_candidates(lines: list[str]) -> list[tuple[int, str]]:
    """(lineno, command candidate): fenced/indented code, spans in exec position, run phrases."""
    candidates: list[tuple[int, str]] = []
    in_fence = False
    for lineno, line in enumerate(lines, 1):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            if line.strip():
                candidates.append((lineno, line))
            continue
        if _INDENTED_CODE_RE.match(line):
            candidates.append((lineno, line))
            continue
        for match in _CODE_SPAN_RE.finditer(line):
            if _exec_position(line[: match.start()]):
                candidates.append((lineno, match.group(1)))
        for match in _RUN_PHRASE_RE.finditer(line):
            candidates.append((lineno, match.group(1)))
    return candidates


def _harvest_suppressed(
    profile: dict[str, Any],
    line: str,
    suppressed_rules: set[str],
    suppressed_tools: set[str],
) -> None:
    """Denied commands and tools named on a skipped line suppress their rules."""
    tokens = _tokens(line)
    for words in profile.get("seq_rules") or []:
        if len(words) <= len(tokens) and _is_subsequence(words, tokens):
            suppressed_rules.add(f"Exec({' '.join(words)})")
    for tool in profile.get("denied_tools") or []:
        if tool in tokens:
            suppressed_tools.add(tool)
    if profile["worker"] == "opencode":
        literals = {
            pattern.split()[0]
            for pattern in profile.get("denied_globs") or []
            if pattern.split()
        }
        for index, token in enumerate(tokens):
            if token in literals:
                rule = _opencode_denied(profile, " ".join(tokens[index:]))
                if rule is not None:
                    suppressed_rules.add(f"permission.bash {rule}")


def _eval_candidate(
    profile: dict[str, Any], candidate: str, lineno: int, findings: list[dict[str, Any]]
) -> None:
    rule = denied_exec(profile, candidate)
    if rule is not None:
        label = rule if profile["worker"] != "opencode" else f"permission.bash {rule}"
        findings.append(
            {
                "kind": "denied-command",
                "command": " ".join(candidate.strip().split())[:120],
                "rule": label,
                "line": lineno,
            }
        )
    tokens = _tokens(candidate)
    for tool in profile.get("denied_tools") or []:
        if tool in tokens:
            findings.append(
                {
                    "kind": "denied-tool",
                    "command": tool,
                    "rule": "not in permissions.allow",
                    "line": lineno,
                }
            )


def _scan_paths(
    profile: dict[str, Any], line: str, lineno: int, cwd: Path, findings: list[dict[str, Any]]
) -> None:
    is_write = bool(_WRITE_VERB_RE.search(line))
    home_norm = _norm("~")
    for match in _PATH_RE.finditer(line):
        raw = match.group(0).rstrip(".,;:)\\").rstrip("\\")
        if len(raw) < 3:
            continue
        path_norm = _norm(raw)
        for rule in profile.get("path_rules") or []:
            label = _path_rule_denied(rule, path_norm, home_norm, is_write)
            if label:
                findings.append(
                    {"kind": "denied-path", "command": raw, "rule": label, "line": lineno}
                )
                break
        if profile.get("writes_outside_cwd"):
            continue
        if not is_write:
            continue
        if any(_under(path_norm, root) for root in _writable_scope(profile, cwd)):
            continue
        findings.append(
            {
                "kind": "outside-cwd",
                "command": raw,
                "rule": "workspace-write roots",
                "line": lineno,
            }
        )


def prompt_findings(
    worker: str,
    prompt: str,
    cwd: Path,
    policy: dict[str, Any] | None = None,
    profile_name: str | None = None,
) -> list[dict[str, Any]]:
    """Every denied command or out-of-scope write path the brief asks to run.

    Pass one marks lines that prohibit or discuss denials and harvests the
    commands they name as suppressed. Pass two evaluates only what a worker
    would execute and drops suppressed findings. With ``profile_name`` the
    named execution profile governs, not the static worker list.
    """
    profile = worker_profile(worker, policy, profile_name)
    if profile is None:
        return []
    if profile.get("profile_error"):
        return [{"kind": "profile", "command": profile_name, "rule": profile["profile_error"], "line": 0}]
    if profile.get("lead_only"):
        return [{"kind": "profile", "command": profile_name, "rule": f"profile {profile_name} is lead-only", "line": 0}]
    lines = prompt.splitlines()
    skipped: set[int] = set()
    suppressed_rules: set[str] = set()
    suppressed_tools: set[str] = set()
    for lineno, line in enumerate(lines, 1):
        if _SKIP_RE.search(line):
            skipped.add(lineno)
            _harvest_suppressed(profile, line, suppressed_rules, suppressed_tools)
    findings: list[dict[str, Any]] = []
    for lineno, candidate in _command_candidates(lines):
        if lineno not in skipped:
            _eval_candidate(profile, candidate, lineno, findings)
    for lineno, line in enumerate(lines, 1):
        if lineno not in skipped:
            _scan_paths(profile, line, lineno, cwd, findings)
    kept: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for finding in findings:
        if finding["rule"] in suppressed_rules or finding["command"] in suppressed_tools:
            continue
        key = (finding["kind"], finding["rule"], finding["command"])
        if key in seen:
            continue
        seen.add(key)
        kept.append(finding)
    return kept


def format_refusal(worker: str, findings: list[dict[str, Any]]) -> str:
    """The refusal text: each problem, and the fix the brief prescribes."""
    lines = [f"admission preflight refused {worker} launch:"]
    for finding in findings:
        if finding["kind"] == "outside-cwd":
            lines.append(
                f"- `{finding['command']}` is a write path outside the {worker} "
                f"workspace roots ({finding['rule']}, line {finding['line']})."
            )
        elif finding["kind"] == "denied-path":
            lines.append(
                f"- `{finding['command']}` matches denied rule {finding['rule']} "
                f"for {worker} (line {finding['line']})."
            )
        else:
            lines.append(
                f"- `{finding['command']}` is denied for {worker} by {finding['rule']} "
                f"(line {finding['line']})."
            )
    lines.append(
        "Fix: run each denied step before dispatch and put the result in the brief, "
        "or route to a worker that may. Override: --allow-preflight-risk."
    )
    return "\n".join(lines)


def retry_denied_cause(reason: str | None, profile: dict[str, Any]) -> str | None:
    """The denied command or tool inside a previous failure reason.

    ``reason`` is an outcome's failure text, e.g. Devin's
    ``permission denied: exec_command {"command": "gh api ..."}`` or
    ``permission denied: webfetch {...}``, or OpenCode's
    ``auto-rejected permission: ...``. Returns the denied token when the
    chosen worker's profile still denies it.
    """
    if not reason:
        return None
    match = _DEVIN_REJECTION_RE.match(reason.strip())
    if match:
        tool, rest = match.group(1).lower(), match.group(2)
        if tool in EXEC_TOOL_NAMES:
            command = None
            try:
                args = json.loads(rest)
                if isinstance(args, dict):
                    command = args.get("command") or args.get("cmd")
            except (TypeError, ValueError):
                command = None
            if command and denied_exec(profile, str(command)):
                return str(command).strip()
        elif tool_denied(profile, tool):
            return tool
    for words in profile.get("seq_rules") or []:
        if _is_subsequence(words, _tokens(reason)):
            return " ".join(words)
    if profile["worker"] == "opencode":
        tokens = _tokens(reason)
        for index, token in enumerate(tokens):
            candidate = " ".join(tokens[index:])
            rule = _opencode_denied(profile, candidate)
            if rule is not None and profile.get("denied_globs") and rule in profile["denied_globs"]:
                return candidate
    return None
