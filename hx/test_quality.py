"""hx.test_quality: the shared test quality gate (Q-1189).

Scans the pytest files of the repo it runs in. Fails on a test with no
assertion, only truthiness or not-None assertions, or a patch of the module
the test file is named after. Exceptions live in
``test-quality-allowlist.json`` with a required reason; a stale entry
fails the check.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

from hx import paths

ALLOWLIST = "test-quality-allowlist.json"
RULE_TEXT = {
    "no-assertion": "No assertion in the test or in a helper it calls. Add a real behavioural assertion or an allowlist entry in test-quality-allowlist.json with a one-line reason.",
    "truthiness-only": "Only truthiness or not-None assertions. Assert the real value, effect or boundary or add an allowlist entry in test-quality-allowlist.json with a one-line reason.",
    "patches-subject": "The test patches the module the file is named after. Test the real module with its dependencies patched or add an allowlist entry in test-quality-allowlist.json with a one-line reason.",
}
WEAK_SELF_ASSERTS = {"assertTrue", "assertFalse", "assertIsNotNone"}
STRONG_CALLS = {"isinstance", "issubclass"}
PYTEST_RAISES = {
    "pytest.raises",
    "pytest.warns",
    "pytest.deprecated_call",
    "raises",
    "warns",
    "deprecated_call",
}
SKIP_DIRS = {"__pycache__", ".git", ".venv", "node_modules", ".artifacts"}


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if isinstance(item, str)]
    return []


def _pytest_paths(root: Path) -> list[str]:
    found: list[str] = []
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib

            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            value = (
                data.get("tool", {})
                .get("pytest", {})
                .get("ini_options", {})
                .get("testpaths")
            )
            found.extend(_as_list(value))
        except (OSError, ValueError, ImportError):
            pass
    pytest_ini = root / "pytest.ini"
    if pytest_ini.is_file():
        try:
            for line in pytest_ini.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("testpaths"):
                    _, _, value = stripped.partition("=")
                    found.extend(value.replace(",", " ").split())
        except OSError:
            pass
    return found or ["tests"]


def _skip(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def test_files(root: Path) -> list[str]:
    found: set[str] = set()
    for entry in _pytest_paths(root):
        candidate = (root / entry).resolve()
        if candidate.is_dir():
            for path in list(candidate.rglob("test_*.py")) + list(candidate.rglob("*_test.py")):
                if not _skip(path):
                    found.add(path.relative_to(root).as_posix())
        elif candidate.is_file() and candidate.name.endswith(".py"):
            found.add(candidate.relative_to(root).as_posix())
    return sorted(found)


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _called_last_name(call: ast.Call) -> str:
    return _call_name(call.func).rsplit(".", 1)[-1]


def _is_pytest_raises(call: ast.Call) -> bool:
    name = _call_name(call.func)
    return name in PYTEST_RAISES or name.endswith(".raises") or name.endswith(".warns")


def _is_self_assert(call: ast.Call) -> bool:
    name = _call_name(call.func)
    return name.startswith("self.assert") or name.startswith("cls.assert")


def _weak_self_assert(call: ast.Call) -> bool:
    return _called_last_name(call) in WEAK_SELF_ASSERTS


def _weak_expr(node: ast.AST) -> bool:
    if isinstance(node, (ast.Name, ast.Attribute, ast.Subscript, ast.Constant)):
        return True
    if isinstance(node, ast.Call):
        return _called_last_name(node) not in STRONG_CALLS
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _weak_expr(node.operand)
    if isinstance(node, ast.BoolOp):
        return all(_weak_expr(value) for value in node.values)
    if isinstance(node, ast.Compare):
        return all(
            _weak_compare(op, comparator)
            for op, comparator in zip(node.ops, node.comparators)
        )
    if isinstance(node, ast.Await):
        return _weak_expr(node.value)
    return False


def _weak_compare(op: ast.cmpop, comparator: ast.AST) -> bool:
    if isinstance(comparator, ast.Constant):
        if comparator.value is None:
            return isinstance(op, ast.IsNot)
        if isinstance(comparator.value, bool):
            return isinstance(op, (ast.Is, ast.IsNot, ast.Eq, ast.NotEq))
    return False


def _assertion_weakness(fn: ast.AST) -> list[bool]:
    weaknesses: list[bool] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            weaknesses.append(_weak_expr(node.test))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if isinstance(item.context_expr, ast.Call) and _is_pytest_raises(item.context_expr):
                    weaknesses.append(False)
        elif isinstance(node, ast.Call):
            if _is_self_assert(node):
                weaknesses.append(_weak_self_assert(node))
            elif _is_pytest_raises(node):
                weaknesses.append(False)
    return weaknesses


def _asserting_helpers(tree: ast.Module) -> set[str]:
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    asserting = {fn.name for fn in functions if _assertion_weakness(fn)}
    changed = True
    while changed:
        changed = False
        for fn in functions:
            if fn.name in asserting:
                continue
            if any(
                _called_last_name(call) in asserting
                for call in ast.walk(fn)
                if isinstance(call, ast.Call)
            ):
                asserting.add(fn.name)
                changed = True
    return asserting


def _test_functions(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    found: list[tuple[str, ast.AST]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            found.append((node.name, node))
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test_"):
                    found.append((f"{node.name}::{child.name}", child))
    return found


def _subject_stem(file: str) -> str:
    name = Path(file).name
    if name.endswith(".py"):
        name = name[:-3]
    if name.startswith("test_"):
        return name[5:]
    if name.endswith("_test"):
        return name[:-5]
    return name


def _patch_targets(fn: ast.AST) -> list[str]:
    targets: list[str] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = _call_name(node.func)
        last = name.rsplit(".", 1)[-1]
        if last == "setattr" and not name.endswith("monkeypatch.setattr"):
            continue
        if last not in {"patch", "setattr"}:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            targets.append(first.value)
    return targets


def analyze_source(file: str, text: str) -> list[dict[str, Any]]:
    tree = ast.parse(text, filename=file)
    helpers = _asserting_helpers(tree)
    stem = _subject_stem(file)
    tests: list[dict[str, Any]] = []
    for name, fn in _test_functions(tree):
        weaknesses = _assertion_weakness(fn)
        helper_call = any(
            _called_last_name(call) in helpers and _called_last_name(call) != fn.name
            for call in ast.walk(fn)
            if isinstance(call, ast.Call)
        )
        rules: list[str] = []
        if not weaknesses and not helper_call:
            rules.append("no-assertion")
        elif not helper_call and weaknesses and all(weaknesses):
            rules.append("truthiness-only")
        if any(target.split(".")[-1] == stem for target in _patch_targets(fn)):
            rules.append("patches-subject")
        tests.append({"file": file, "line": fn.lineno, "name": name, "rules": rules})
    return tests


def load_allowlist(root: Path) -> tuple[list[dict[str, str]], list[str]]:
    path = root / ALLOWLIST
    if not path.is_file():
        return [], []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], [f"{ALLOWLIST}: {exc}"]
    if not isinstance(raw, list):
        return [], [f"{ALLOWLIST}: must hold a JSON array"]
    entries: list[dict[str, str]] = []
    problems: list[str] = []
    for entry in raw:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("file"), str)
            or not isinstance(entry.get("test"), str)
        ):
            problems.append(f"{ALLOWLIST}: an entry needs a file and a test name")
            continue
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            problems.append(f'{ALLOWLIST}: {entry["file"]} "{entry["test"]}" has no reason')
            continue
        entries.append(
            {
                "file": entry["file"].replace("\\", "/"),
                "test": entry["test"],
                "reason": reason.strip(),
            }
        )
    return entries, problems


def check(
    root: Path,
    files: list[str] | None = None,
    allowlist: list[dict[str, str]] | None = None,
    stale_scope: str = "all",
) -> dict[str, Any]:
    selected = test_files(root) if files is None else [file.replace("\\", "/") for file in files]
    allowlist = allowlist or []
    tests: list[dict[str, Any]] = []
    scan_problems: list[str] = []
    for file in selected:
        try:
            text = (root / file).read_text(encoding="utf-8-sig")
            tests.extend(analyze_source(file, text))
        except (OSError, SyntaxError) as exc:
            scan_problems.append(f"{file}: {exc}")
    live = {f'{test["file"]}\0{test["name"]}' for test in tests}
    scanned = set(selected)
    allowed: set[str] = set()
    stale: list[dict[str, str]] = []
    for entry in allowlist:
        if stale_scope == "files" and entry["file"] not in scanned:
            continue
        key = f'{entry["file"]}\0{entry["test"]}'
        if key in live:
            allowed.add(key)
        else:
            stale.append(entry)
    failures = [
        test
        for test in tests
        if test["rules"] and f'{test["file"]}\0{test["name"]}' not in allowed
    ]
    return {
        "files": len(selected),
        "tests": len(tests),
        "failures": failures,
        "stale": stale,
        "allowlisted": len(allowed),
        "problems": scan_problems,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hx test-quality",
        description="Shared test quality gate for the pytest files of this repo.",
    )
    parser.parse_args(argv)
    root = paths.project_root()
    entries, problems = load_allowlist(root)
    result = check(root, allowlist=entries)
    problems = list(problems) + list(result["problems"])
    for entry in result["stale"]:
        problems.append(
            f'stale allowlist entry: {entry["file"]}: "{entry["test"]}" matches no test. Remove it.'
        )
    failures = result["failures"]
    total = len(failures) + len(problems)
    if total:
        print(f"test quality: {total} problem(s)", file=sys.stderr)
        for failure in failures:
            rules = ", ".join(failure["rules"])
            print(
                f'  FAIL {failure["file"]}:{failure["line"]} "{failure["name"]}" [{rules}]',
                file=sys.stderr,
            )
            for rule in failure["rules"]:
                print(f"    {RULE_TEXT[rule]}", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            f'test quality: {result["files"]} test files, {result["tests"]} tests, '
            f'{result["allowlisted"]} allowlisted, {total} problems',
            file=sys.stderr,
        )
        return 1
    print(
        f'test quality: {result["files"]} test files, {result["tests"]} tests, '
        f'{result["allowlisted"]} allowlisted, 0 problems'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
