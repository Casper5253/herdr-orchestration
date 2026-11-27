"""hx reconcile: settle send records against the receiver's own transcript.

Read-only. A record is delivered when the receiver's log holds the message
text near the send time, lost when the receiver's log covers the send time and
does not, and unconfirmed when no log covers that time. Nothing here sends,
writes a receipt, or touches the outbox.

The same readers also collect the worker's own steps (commands with exit codes,
files edited, tests run), which ``hx step harvest`` appends to the task
checkpoint so a dead worker's progress survives. Only command lines, file
paths, exit codes and timestamps leave a transcript; message text never does.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from hx import paths, usage

DATE_FORMAT = "%Y-%m-%d"
STATUSES = ("delivered", "lost", "unconfirmed")
ROLE_FAMILY = {"claude": "claude", "codex": "codex", "opencode": "opencode", "devin": "devin"}
# The stated figure this unit is measured against (Q-1118): briefs with no matching reply.
STATED_UPPER_BOUND = "128 of 316"
# A message lands in the transcript when it is submitted, so a match much later
# is the receiver quoting it, not delivery. The slack absorbs queueing.
MATCH_BEFORE = dt.timedelta(minutes=5)
MATCH_AFTER = dt.timedelta(hours=2)
# A session created by a just-arrived message puts its first event shortly after the send.
COVER_SLACK = dt.timedelta(minutes=2)
SEARCH_AFTER = dt.timedelta(minutes=10)
SEARCH_BEFORE = dt.timedelta(minutes=1)
# A prefix this long identifies a message whose tail the TUI shortened or rewrapped.
FRAGMENT_CHARS = 160

# A test command is a command line whose program names a test runner.
TEST_COMMAND = re.compile(
    r"\b(pytest|py\.test|unittest|npm\s+(?:run\s+)?test|npx\s+\S*(?:jest|vitest|playwright)"
    r"|node\s+\S*test|go\s+test|cargo\s+test|dotnet\s+test|ctest|tox|nox|make\s+test|rspec)"
    r"\b(?![.\w/-])",
    re.IGNORECASE)
EXIT_CODE = re.compile(r"(?:Process )?[Ee]xit(?:ed with)? code[:\s]+(-?\d+)")
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
HERE_START = re.compile(r"<<-?\s*['\"]?\w+['\"]?|@[\'\"](?=\s*$)")
SECRET_ARGUMENT = re.compile(
    r"\b(?:bearer|basic)\s+[^\s'\"]+|\bauthorization\s*:|"
    r"(?<![\w-])(?:[\w-]*(?:token|password|passwd|secret|api[_-]?key|client[_-]?key)"
    r"[\w-]*)\s*[:=]|--(?:[\w-]*(?:token|password|passwd|secret|api[_-]?key|credential)"
    r"[\w-]*|user|proxy-user)(?:[=\s]|$)|"
    r"\b(?:sk-|gh[pousr]_|github_pat_|xox[baprs]-)[\w-]+|"
    r"://[^/\s@]+:[^/\s@]+@|\b[A-Za-z0-9_-]{32,}\b|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b(?:x-api-key|cookie|set-cookie)\s*:|"
    r"\b(?:curl|sshpass|mysql)\b[^\r\n]*\s-(?:u|p)(?=[\w\s'\"])",
    re.IGNORECASE,
)
INLINE_PAYLOAD = re.compile(
    r"\b(?:Set-Content|Add-Content|Out-File|WriteAllText|WriteAllBytes)\b|"
    r"\b(?:echo|printf)\b.*[>\|]|"
    r"(?:^|\s)(?:-encodedcommand|-enc)(?:\s|$)|"
    r"\b(?:python(?:3|\.exe)?|node|perl|ruby)\s+(?:-c|-e|--eval)\s",
    re.IGNORECASE,
)
REPORT_COMMAND = re.compile(
    r"^(?:&\s*)?(?:hx(?:\.exe|\.cmd)?\s+say|hc(?:\.exe|\.cmd)?\s+send)\s+"
    r".*?\[team report(?: from [^\]]+)?\]", re.IGNORECASE)
TASK_BRIEF = re.compile(
    r"^(?:<hc\b[^>]*>\s*)?(?:\[team from [^\]]+\]\s*)?"
    r"(?:new\s+brief\s*:|(?:team\s+brief\s+)?task\s*:|"
    r"read\s+[^\r\n]*[\\/]\.hx[\\/]briefs[\\/])", re.IGNORECASE)
JS_LITERAL = r""" "(?:[^"\\]|\\[\s\S])*" | '(?:[^'\\]|\\[\s\S])*' | `(?:[^`\\]|\\[\s\S])*` """
JS_NON_CODE = re.compile(
    JS_LITERAL + r""" | //[^\r\n]* | /\*[\s\S]*?\*/ |
    (?:(?<=^)|(?<=[=(:,!;{>\[\s])) / (?![/*]) (?:\\.|\[(?:\\.|[^\]\\\r\n])*\]|[^/\\[\r\n])+ / [a-z]* """,
    re.VERBOSE,
)
JS_TOOL = re.compile(r"(?<![\w$.])tools\s*\.\s*(exec_command|apply_patch)\s*\(")
JS_CMD = re.compile(r"""(?:^|,)\s*(?:cmd|['"]cmd['"])\s*:\s*""")
CODEX_PATCH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
CODEX_SHELL_TOOLS = {"shell", "shell_command", "exec_command", "terminal.exec"}
DETAIL_LIMIT = 300


@dataclass(frozen=True)
class Entry:
    at: dt.datetime | None
    text: str
    sequence: int


@dataclass(frozen=True)
class StepEvent:
    at: dt.datetime
    kind: str
    detail: str
    exit: int | None = None
    invocation: str = ""
    report: bool = False
    sequence: int = 0


@dataclass(frozen=True)
class SessionLog:
    family: str
    session_id: str
    path: str
    first: dt.datetime | None
    last: dt.datetime | None
    entries: tuple[Entry, ...]
    steps: tuple[StepEvent, ...] = ()


@dataclass(frozen=True)
class Finding:
    status: str
    evidence: str | None
    evidence_at: dt.datetime | None = None


def _squash(text: Any) -> str:
    return "".join(str(text).split())


def _timestamp(value: Any) -> dt.datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        try:
            return dt.datetime.fromtimestamp(seconds, dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else None


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_strings(item))
        return out
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_strings(item))
        return out
    return []


def _span(stamps: list[dt.datetime]) -> tuple[dt.datetime | None, dt.datetime | None]:
    return (min(stamps), max(stamps)) if stamps else (None, None)


def _log(family: str, session_id: str, path: str, stamps: list[dt.datetime],
         entries: list[Entry], steps: list[StepEvent] | None = None) -> SessionLog | None:
    first, last = _span(stamps)
    if first is None:
        return None
    ordered = tuple(sorted(steps or (), key=lambda step: (step.at, step.sequence)))
    return SessionLog(family, session_id, path, first, last, tuple(entries), ordered)


def _one_line(value: Any, limit: int = DETAIL_LIMIT) -> str:
    """One line, keeping both ends of a long command so its last action survives."""
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head - 5
    return text[:head] + " ... " + text[-tail:]


def _step(stamp: dt.datetime | None, kind: str, detail: Any,
          exit_code: int | None = None) -> StepEvent | None:
    if stamp is None:
        return None
    text = _one_line(detail) if detail is not None else ""
    if not text:
        return None
    return StepEvent(stamp, kind, text, exit_code)


def _command_step(stamp: dt.datetime | None, command: Any,
                  exit_code: int | None = None) -> StepEvent | None:
    if isinstance(command, (list, tuple)):
        command = " ".join(str(part) for part in command)
    if not isinstance(command, str) or not command.strip():
        return None
    line = command.lstrip().splitlines()[0]
    here = HERE_START.search(line)
    if here and here.group().startswith("@"):
        line = line[:here.end()]
    report = REPORT_COMMAND.search(line) is not None
    program = QUOTED.sub("", line)
    kind = "test" if TEST_COMMAND.search(program) else "command"
    if SECRET_ARGUMENT.search(line) or INLINE_PAYLOAD.search(line):
        line = "[redacted command]"
    step = _step(stamp, kind, line, exit_code)
    return replace(step, report=report) if step is not None else None


def _edit_step(stamp: dt.datetime | None, path: Any) -> StepEvent | None:
    if not isinstance(path, str) or not path.strip() or len(path.splitlines()) != 1:
        return None
    return _step(stamp, "edit", path.strip())


def _exit_code(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        lines = value.rstrip().splitlines()
        match = EXIT_CODE.fullmatch(lines[-1].strip()) if lines else None
        if match:
            return int(match.group(1))
    return None


def _output_texts(output: Any) -> list[str]:
    if isinstance(output, str):
        return [output]
    if isinstance(output, list):
        return [block["text"] for block in output
                if isinstance(block, dict) and isinstance(block.get("text"), str)]
    return []


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(_output_texts(content))


def _literal(value: str) -> str | None:
    if not value or value[0] not in "'\"\x60" or (value.startswith("\x60") and "\x24{" in value):
        return None
    escapes = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}

    def unescape(match: re.Match) -> str:
        token = match[1]
        if token.startswith(("x", "u")) and len(token) > 1:
            return chr(int(token[1:], 16))
        if token in ("\r\n", "\r", "\n"):
            return ""
        return escapes.get(token, token)

    decoded = re.sub(r"\\(x[\da-fA-F]{2}|u[\da-fA-F]{4}|\r\n|[\s\S])", unescape, value[1:-1])
    if any(0xD800 <= ord(char) <= 0xDFFF for char in decoded):
        return None
    return decoded


def _js_close(code: str, start: int, opening: str, closing: str) -> int | None:
    depth = 0
    for index in range(start, len(code)):
        if code[index] == opening:
            depth += 1
        elif code[index] == closing:
            depth -= 1
            if depth == 0:
                return index
    return None


def _codex_steps(stamp: dt.datetime | None, payload: dict[str, Any]) -> tuple[list[StepEvent], bool]:
    name = payload.get("name")
    if name in CODEX_SHELL_TOOLS:
        try:
            arguments = json.loads(payload.get("arguments", ""))
        except (ValueError, TypeError):
            return [], False
        if not isinstance(arguments, dict):
            return [], False
        step = _command_step(stamp, arguments.get("command") or arguments.get("cmd"))
        return ([step] if step is not None else []), True
    if name == "apply_patch":
        source = payload.get("input")
        if not isinstance(source, str):
            return [], False
        return [step for path in CODEX_PATCH.findall(source)
                if (step := _edit_step(stamp, path)) is not None], False
    if name != "exec" or not isinstance(source := payload.get("input"), str):
        return [], False

    non_code = {match.start(): match for match in JS_NON_CODE.finditer(source)}
    code = JS_NON_CODE.sub(lambda match: " " * len(match[0]), source)
    steps: list[StepEvent] = []
    direct = False
    for match in JS_TOOL.finditer(code):
        end = _js_close(code, match.end() - 1, "(", ")")
        if end is None:
            continue
        start = match.end()
        while start < end and source[start].isspace():
            start += 1
        if match[1] == "apply_patch":
            literal = non_code.get(start)
            body = _literal(literal[0]) if literal else None
            steps.extend(step for path in CODEX_PATCH.findall(body or "")
                         if (step := _edit_step(stamp, path)) is not None)
            continue
        if code[start] != "{":
            continue
        object_end = _js_close(code, start, "{", "}")
        if object_end is None or object_end >= end:
            continue
        props = source[start + 1:object_end]
        masked_props = code[start + 1:object_end]
        for prop in JS_CMD.finditer(props):
            comma = start + 1 + prop.start()
            if prop[0].startswith(",") and code[comma] != ",":
                continue
            prefix = masked_props[:prop.start()]
            if any(prefix.count(left) != prefix.count(right) for left, right in ("{}", "[]", "()")):
                continue
            value_start = start + 1 + prop.end()
            literal = non_code.get(value_start)
            if literal is None or not re.match(r"\s*(?:,|$)", source[literal.end():object_end]):
                continue
            if (step := _command_step(stamp, _literal(literal[0]))) is not None:
                steps.append(step)
                before = re.sub(r"^\s*//\s*@exec:[^\r\n]*", "", source[:match.start()])
                direct = bool(re.fullmatch(r"\s*text\(\s*await\s*", before)
                              and re.fullmatch(r"\s*\)\s*;?\s*", source[end + 1:]))
            break
    return steps, direct


def _codex_exit(output: Any, *, structured: bool) -> int | None:
    texts = _output_texts(output)
    if not structured:
        return _exit_code(texts[-1]) if texts else None
    results: list[int | None] = []
    for text in texts:
        try:
            decoded = json.loads(text)
        except ValueError:
            continue
        if not isinstance(decoded, dict) or not {"chunk_id", "exit_code"} & decoded.keys():
            continue
        code = decoded.get("exit_code")
        results.append(code if isinstance(code, int) and not isinstance(code, bool) else None)
    return results[0] if len(results) == 1 else None


def _opencode_tool_step(stamp: dt.datetime | None, data: dict[str, Any]) -> StepEvent | None:
    state = data.get("state") if isinstance(data.get("state"), dict) else {}
    payload = state.get("input") if isinstance(state.get("input"), dict) else {}
    tool = str(data.get("tool") or "")
    if tool in ("bash", "shell", "bash_command"):
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        code = metadata.get("exit")
        return _command_step(stamp, payload.get("command"),
                             code if isinstance(code, int) and not isinstance(code, bool) else None)
    if tool in ("edit", "write", "multiedit", "str_replace_editor", "apply_patch"):
        return _edit_step(stamp, payload.get("filePath") or payload.get("path")
                          or payload.get("file_path"))
    return None


def claude_logs(projects_dir: Path) -> list[SessionLog]:
    logs = []
    for name in sorted(glob.glob(str(projects_dir / "*" / "*.jsonl"))):
        path = Path(name)
        stamps: list[dt.datetime] = []
        entries: list[Entry] = []
        calls: list[tuple[str, dt.datetime | None, str, Any, int]] = []
        exits: dict[str, int] = {}
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                for sequence, line in enumerate(handle):
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    stamp = _timestamp(event.get("timestamp"))
                    if stamp:
                        stamps.append(stamp)
                    content = (event.get("message") or {}).get("content")
                    if isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            if block.get("type") == "tool_use":
                                calls.append((str(block.get("id") or ""), stamp,
                                              str(block.get("name") or ""), block.get("input"), sequence))
                                continue
                            if block.get("type") != "tool_result":
                                continue
                            code = _exit_code(_tool_result_text(block.get("content")))
                            if block.get("is_error") is False:
                                code = 0
                            elif block.get("is_error") is True and code == 0:
                                code = None
                            call_id = str(block.get("tool_use_id") or "")
                            if call_id and code is not None:
                                exits[call_id] = code
                    if event.get("type") != "user":
                        continue
                    text = ""
                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, list):
                        text = "".join(block.get("text") or "" for block in content
                                       if isinstance(block, dict) and block.get("type") == "text")
                    if text:
                        entries.append(Entry(stamp, text, sequence))
        except OSError:
            continue
        steps: list[StepEvent] = []
        for call_id, stamp, name, payload, sequence in calls:
            if not isinstance(payload, dict):
                continue
            if name in ("Bash", "PowerShell", "shell"):
                step = _command_step(stamp, payload.get("command"), exits.get(call_id))
            elif name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                step = _edit_step(stamp, payload.get("file_path") or payload.get("path"))
            else:
                step = None
            if step is not None and call_id:
                steps.append(replace(step, invocation=call_id, sequence=sequence))
        log = _log("claude", path.stem, str(path), stamps, entries, steps)
        if log is not None:
            logs.append(log)
    return logs


def codex_logs(sessions_dir: Path) -> list[SessionLog]:
    logs = []
    for name in sorted(glob.glob(str(sessions_dir / "**" / "rollout-*.jsonl"), recursive=True)):
        path = Path(name)
        stamps: list[dt.datetime] = []
        entries: list[Entry] = []
        session_id = ""
        calls: dict[str, tuple[list[StepEvent], bool, bool]] = {}
        outputs: dict[str, Any] = {}
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                for sequence, line in enumerate(handle):
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    stamp = _timestamp(event.get("timestamp"))
                    if stamp:
                        stamps.append(stamp)
                    payload = event.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    text = ""
                    if event.get("type") == "session_meta" and not session_id:
                        session_id = str(payload.get("id") or "")
                    elif event.get("type") == "response_item" and payload.get("type") in (
                            "function_call", "custom_tool_call"):
                        found, direct = _codex_steps(stamp, payload)
                        call_id = str(payload.get("call_id") or "")
                        if call_id and found:
                            calls[call_id] = ([replace(step, sequence=sequence) for step in found],
                                              direct, payload.get("name") == "exec")
                    elif event.get("type") == "response_item" and payload.get("type") in (
                            "function_call_output", "custom_tool_call_output"):
                        call_id = str(payload.get("call_id") or "")
                        if call_id:
                            outputs[call_id] = payload.get("output")
                    elif event.get("type") == "response_item" and payload.get("role") == "user":
                        text = "".join(_strings(payload.get("content")))
                    elif event.get("type") == "event_msg" and payload.get("type") == "user_message":
                        message = payload.get("message")
                        text = message if isinstance(message, str) else ""
                    if text:
                        entry = Entry(stamp, text, sequence)
                        previous = entries[-1] if entries else None
                        duplicate = (previous and previous.at and stamp
                                     and sequence - previous.sequence <= 2
                                     and abs(stamp - previous.at) <= dt.timedelta(seconds=1)
                                     and _squash(text) == _squash(previous.text))
                        if not duplicate:
                            entries.append(entry)
        except OSError:
            continue
        steps: list[StepEvent] = []
        for call_id, (found, direct, structured) in calls.items():
            code = _codex_exit(outputs.get(call_id), structured=structured) if direct and len(found) == 1 else None
            steps.extend(replace(step, invocation=f"{call_id}:{index}", exit=code)
                         for index, step in enumerate(found))
        log = _log("codex", session_id or path.stem, str(path), stamps, entries, steps)
        if log is not None:
            logs.append(log)
    return logs


def opencode_logs(db_path: Path, since: dt.datetime | None, until: dt.datetime | None) -> list[SessionLog]:
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return []
    logs = []
    try:
        sessions = conn.execute("SELECT id, time_created, time_updated FROM session").fetchall()
        for session in sessions:
            session_id = str(session["id"])
            messages = conn.execute(
                "SELECT id, time_created, data FROM message WHERE session_id=? ORDER BY time_created",
                (session_id,)).fetchall()
            stamps = [stamp for stamp in (_timestamp(row["time_created"]) for row in messages) if stamp]
            if not stamps:
                stamps = [stamp for stamp in (
                    _timestamp(session["time_created"]), _timestamp(session["time_updated"])) if stamp]
            first, last = _span(stamps)
            if first is None:
                continue
            if since and last < since - COVER_SLACK:
                continue
            if until and first > until + COVER_SLACK:
                continue
            roles = {}
            for row in messages:
                try:
                    roles[str(row["id"])] = json.loads(row["data"]).get("role")
                except (ValueError, TypeError):
                    continue
            entries: list[Entry] = []
            steps: list[StepEvent] = []
            for part in conn.execute(
                    "SELECT rowid AS part_row, message_id, time_created, data FROM part WHERE session_id=? ORDER BY time_created, rowid",
                    (session_id,)):
                try:
                    data = json.loads(part["data"])
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                stamp = _timestamp(part["time_created"])
                sequence = part["part_row"]
                part_id = str(data.get("id") or f"row:{sequence}")
                if data.get("type") == "tool":
                    step = _opencode_tool_step(stamp, data)
                    if step is not None:
                        steps.append(replace(step, invocation=part_id, sequence=sequence))
                elif data.get("type") == "patch":
                    files = data.get("files")
                    if isinstance(files, list):
                        for index, name in enumerate(files):
                            edit = _edit_step(stamp, name)
                            if edit is not None:
                                steps.append(replace(edit, invocation=f"{part_id}:{index}", sequence=sequence))
                if (roles.get(str(part["message_id"])) == "user"
                        and data.get("type") == "text" and isinstance(data.get("text"), str)):
                    entries.append(Entry(stamp, data["text"], sequence))
            log = _log("opencode", session_id, f"{db_path}#{session_id}", stamps, entries, steps)
            if log is not None:
                logs.append(log)
    except sqlite3.Error:
        return logs
    finally:
        conn.close()
    return logs


def devin_logs(cli_dir: Path) -> list[SessionLog]:
    logs = []
    for name in sorted(glob.glob(str(cli_dir / "transcripts" / "*.json"))):
        path = Path(name)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        steps = data.get("steps")
        if not isinstance(steps, list):
            continue
        stamps: list[dt.datetime] = []
        entries: list[Entry] = []
        calls: list[tuple[str, dt.datetime | None, str, Any, int]] = []
        exits: dict[str, int] = {}
        for sequence, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            stamp = _timestamp(step.get("timestamp"))
            if stamp:
                stamps.append(stamp)
            observation = step.get("observation")
            if isinstance(observation, dict):
                for result in observation.get("results") or []:
                    if not isinstance(result, dict):
                        continue
                    call_id = str(result.get("source_call_id") or "")
                    code = _exit_code(result.get("content"))
                    if call_id and code is not None:
                        exits[call_id] = code
            for call in step.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                calls.append((str(call.get("tool_call_id") or ""), stamp,
                              str(call.get("function_name") or ""), call.get("arguments"), sequence))
            if step.get("source") == "user" and isinstance(step.get("message"), str):
                entries.append(Entry(stamp, step["message"], sequence))
        found: list[StepEvent] = []
        for call_id, stamp, name, arguments, sequence in calls:
            payload = arguments if isinstance(arguments, dict) else {}
            if name in ("exec", "shell", "bash"):
                event = _command_step(stamp, payload.get("command") or payload.get("cmd"),
                                      exits.get(call_id))
            elif name in ("write", "edit", "str_replace_editor", "apply_patch"):
                event = _edit_step(stamp, payload.get("file_path") or payload.get("path"))
            else:
                event = None
            if event is not None and call_id:
                found.append(replace(event, invocation=call_id, sequence=sequence))
        log = _log("devin", str(data.get("session_id") or path.stem), str(path), stamps, entries, found)
        if log is not None:
            logs.append(log)
    return logs


def default_roots() -> dict[str, Path]:
    appdata = os.environ.get("APPDATA")
    devin = (Path(appdata) / "devin" / "cli" if appdata else Path.home() / ".devin" / "cli")
    return {
        "claude": Path(os.environ.get("HX_CLAUDE_PROJECTS_DIR", Path.home() / ".claude" / "projects")),
        "codex": usage.default_codex_sessions_dir(),
        "opencode": usage.default_opencode_db_path(),
        "devin": Path(os.environ.get("HX_DEVIN_CLI_DIR", devin)),
    }


def load_logs(roots: dict[str, Path], since: dt.datetime | None = None,
              until: dt.datetime | None = None) -> list[SessionLog]:
    return (claude_logs(roots["claude"]) + codex_logs(roots["codex"])
            + opencode_logs(roots["opencode"], since, until) + devin_logs(roots["devin"]))


def load_records(hx_dir: Path, since: dt.datetime | None = None,
                 until: dt.datetime | None = None) -> list[dict[str, Any]]:
    records = []
    for name in sorted((hx_dir / "outbox").glob("*/*.json")):
        try:
            data = json.loads(name.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        sent = _timestamp(data.get("created_at"))
        if sent is None:
            try:
                sent = dt.datetime.fromtimestamp(name.stat().st_mtime, dt.timezone.utc)
            except OSError:
                continue
        if since and sent < since:
            continue
        if until and sent > until:
            continue
        data["_sent"] = sent
        data["_path"] = str(name)
        records.append(data)
    return records


def role_of(name: str) -> str:
    return str(name or "").rsplit("-", 1)[-1]


def same_agent(left: str, right: str) -> bool:
    if role_of(left) == role_of(right):
        return True
    left_family, right_family = role_family(left), role_family(right)
    return left_family is not None and left_family == right_family


def names_task(text: str, task_id: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(task_id)}(?![\w-])", text) is not None


def brief_for_task(records: list[dict[str, Any]], task_id: str,
                   target: str) -> dict[str, Any] | None:
    """The newest send to this agent whose text names the task.

    A lead's brief wins over any peer send that names the same task.
    """
    candidates = [record for record in records
                  if same_agent(str(record.get("target") or ""), target)
                  and names_task(str(record.get("text") or ""), task_id)]
    leads = [record for record in candidates
             if role_of(str(record.get("sender") or "")) in ("lead", "claude")]
    pool = [record for record in leads if TASK_BRIEF.match(str(record.get("text") or ""))]
    pool = pool or leads or candidates
    return max(pool, key=lambda record: record["_sent"]) if pool else None


def brief_receipt(record: dict[str, Any], family: str,
                  logs: list[SessionLog]) -> tuple[SessionLog, Entry] | None:
    sent = record["_sent"]
    needles = _needles(record)
    matches = [(log, receipt) for log in logs
               if log.family == family and _near(log, sent)
               and (receipt := _receipt(log, needles, sent)) is not None]
    if len(matches) > 1:
        raise ValueError(f"ambiguous {family} worker session for send record {record.get('id')}")
    return matches[0] if matches else None


def session_for_brief(record: dict[str, Any], family: str,
                      logs: list[SessionLog]) -> SessionLog | None:
    """Return the unique session that received the brief, if it can be identified."""
    match = brief_receipt(record, family, logs)
    return match[0] if match else None


def steps_in_window(log: SessionLog, receipt: Entry,
                    until: dt.datetime) -> tuple[list[StepEvent], dt.datetime]:
    start = (receipt.at, receipt.sequence)
    origin = _squash(receipt.text)
    next_brief = min(((entry.at, entry.sequence) for entry in log.entries
                      if entry.at is not None and start < (entry.at, entry.sequence)
                      and entry.at <= until and TASK_BRIEF.match(entry.text)
                      and not (entry.at == receipt.at and _squash(entry.text) == origin)),
                     default=None)
    found = []
    for step in log.steps:
        position = (step.at, step.sequence)
        if position <= start:
            continue
        if next_brief is not None and position >= next_brief:
            return found, next_brief[0]
        if step.at > until:
            break
        found.append(step)
        if step.report:
            return found, step.at
    return found, next_brief[0] if next_brief else until


def _needles(record: dict[str, Any]) -> list[str]:
    out = []
    for key in ("text", "body"):
        value = record.get(key)
        if isinstance(value, str) and value:
            squashed = _squash(value)
            if squashed and squashed not in out:
                out.append(squashed)
    return out


def _entry_matches(text: str, needles: list[str]) -> bool:
    text = _squash(text)
    if not text:
        return False
    for needle in needles:
        if needle in text:
            return True
        if len(needle) >= FRAGMENT_CHARS + 40 and needle[:FRAGMENT_CHARS] in text:
            return True
    return False


def _receipt(log: SessionLog, needles: list[str], sent: dt.datetime) -> Entry | None:
    return next((entry for entry in log.entries
                 if entry.at is not None and sent - MATCH_BEFORE <= entry.at <= sent + MATCH_AFTER
                 and _entry_matches(entry.text, needles)), None)


def _find(log: SessionLog, needles: list[str], sent: dt.datetime) -> dt.datetime | None:
    receipt = _receipt(log, needles, sent)
    return receipt.at if receipt else None


def _near(log: SessionLog, sent: dt.datetime) -> bool:
    """A log whose span could hold an event sent at this time."""
    return (log.first is not None and log.last is not None
            and log.first <= sent + SEARCH_AFTER and log.last >= sent - SEARCH_BEFORE)


def _covers(log: SessionLog, sent: dt.datetime) -> bool:
    """The log spans the send time and holds user text, so absence is evidence.

    A transcript with no user turns at all (an auto-started Devin session, a
    pruned log) cannot prove that a message was absent.
    """
    return (bool(log.entries) and log.first is not None and log.last is not None
            and log.first <= sent + COVER_SLACK and log.last >= sent - COVER_SLACK)


def role_family(target: str) -> str | None:
    return ROLE_FAMILY.get(target.rsplit("-", 1)[-1])


def infer_families(records: list[dict[str, Any]], logs: list[SessionLog]) -> dict[str, str]:
    """Resolve roles whose name is not a family (the lead seat) from what they received.

    A message addressed to an agent lands in that agent's transcript, so the
    family holding most of a role's inbound send text is that role's family.
    This is the only local evidence of the lead seat's family, and it catches
    a seat that moved between families.
    """
    hits: dict[str, dict[str, int]] = {}
    for record in records:
        target = str(record.get("target") or "")
        if not target or role_family(target) is not None:
            continue
        sent = record["_sent"]
        needles = _needles(record)
        for log in logs:
            if _near(log, sent) and _find(log, needles, sent) is not None:
                counts = hits.setdefault(target, {})
                counts[log.family] = counts.get(log.family, 0) + 1
    return {target: max(counts.items(), key=lambda item: item[1])[0]
            for target, counts in hits.items()}


def classify(record: dict[str, Any], family: str | None,
             logs: list[SessionLog]) -> Finding:
    sent = record["_sent"]
    needles = _needles(record)
    # Another family holding the text proves the text exists, not that this
    # receiver got it; only the receiver's family can settle the record.
    pool = [log for log in logs if family is None or log.family == family]
    near = [log for log in pool if _near(log, sent)]
    for log in near:
        stamp = _find(log, needles, sent)
        if stamp is not None:
            return Finding("delivered", log.path, stamp)
    if family is None:
        return Finding("unconfirmed", None)
    # A delivered receipt is positive transport evidence, so a missing text is
    # a disagreement between sources, never confirmed loss. Pending and failed
    # records were never submitted either, so only an uncertain attempt is lost.
    if record.get("state") == "uncertain" and any(_covers(log, sent) for log in near):
        return Finding("lost", None)
    return Finding("unconfirmed", None)


def reconcile(records: list[dict[str, Any]], logs: list[SessionLog]) -> dict[str, Any]:
    inferred = infer_families(records, logs)
    families: dict[str, str | None] = {}
    counts = {status: 0 for status in STATUSES}
    by_receiver: dict[str, dict[str, int]] = {}
    findings = []
    excluded = 0
    receipt_only = 0
    for record in records:
        if record.get("state") == "cancelled":
            excluded += 1
            continue
        target = str(record.get("target") or "")
        family = role_family(target) or inferred.get(target)
        families[target] = family
        finding = classify(record, family, logs)
        counts[finding.status] += 1
        if finding.status == "unconfirmed" and record.get("state") == "delivered":
            receipt_only += 1
        bucket = by_receiver.setdefault(target, {status: 0 for status in STATUSES})
        bucket[finding.status] += 1
        findings.append({
            "id": record.get("id") or Path(str(record.get("_path") or "")).stem,
            "sender": record.get("sender"),
            "target": target,
            "created_at": record.get("created_at"),
            "state": record.get("state"),
            "family": family,
            "status": finding.status,
            "evidence": finding.evidence,
            "evidence_at": finding.evidence_at.isoformat() if finding.evidence_at else None,
        })
    briefs = [item for item in findings if str(item.get("sender") or "").rsplit("-", 1)[-1] == "lead"]
    lost_briefs = sum(1 for item in briefs if item["status"] == "lost")
    return {
        "records": len(findings),
        "excluded": excluded,
        "receipt_only": receipt_only,
        "counts": counts,
        "by_receiver": by_receiver,
        "families": families,
        "inferred": inferred,
        "briefs": len(briefs),
        "lost_briefs": lost_briefs,
        "upper_bound": STATED_UPPER_BOUND,
        "findings": findings,
    }


def render_text(summary: dict[str, Any], since: str | None, until: str | None) -> str:
    window = f" {since or 'start'}..{until or 'now'}"
    lines = [f"hx reconcile{window}: {summary['records']} send records",
             "  delivered {delivered}  lost {lost}  unconfirmed {unconfirmed}".format(**summary["counts"])]
    for target in sorted(summary["by_receiver"]):
        row = summary["by_receiver"][target]
        lines.append(f"  {target:14} delivered {row['delivered']:4}  lost {row['lost']:4}  "
                     f"unconfirmed {row['unconfirmed']:4}  family {summary['families'].get(target) or '-'}")
    lines.append(f"  {summary['excluded']} cancelled records excluded (never sent); "
                 f"{summary['receipt_only']} delivered receipts missing from the transcript")
    lines.append(f"  upper bound {summary['upper_bound']} briefs with no matching reply; "
                 f"confirmed lost {summary['lost_briefs']} of {summary['briefs']} briefs "
                 f"({summary['counts']['lost']} of {summary['records']} send records)")
    return "\n".join(lines)


def command(since: str | None = None, until: str | None = None, as_json: bool = False) -> int:
    try:
        since_at = (dt.datetime.strptime(since, DATE_FORMAT).replace(tzinfo=dt.timezone.utc)
                    if since else None)
        until_at = (dt.datetime.strptime(until, DATE_FORMAT).replace(tzinfo=dt.timezone.utc)
                    + dt.timedelta(days=1) - dt.timedelta(microseconds=1) if until else None)
    except ValueError as exc:
        raise ValueError(f"dates must be YYYY-MM-DD: {exc}") from exc
    if since_at and until_at and since_at > until_at:
        raise ValueError("--since must not be after --until")
    records = load_records(paths.hx_dir(), since_at, until_at)
    logs = load_logs(default_roots(), since_at, until_at)
    summary = reconcile(records, logs)
    if as_json:
        print(json.dumps(summary, indent=2))
    else:
        print(render_text(summary, since, until))
    return 0
