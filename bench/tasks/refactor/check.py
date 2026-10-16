"""Hidden acceptance check for the refactor task.

Verifies behaviour is unchanged AND the duplication is gone (only one
module in the directory should own the quoted-splitting body).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SAMPLES = [
    "a,b,c",
    '"hello, world",2,3',
    '\ufeff"q","uoted"\r\n',
    "trailing,quoted",  # not a quoted row
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse_line_bodies(text: str):
    """Yield the source of every top-level `def parse_line` body."""
    out = []
    for m in re.finditer(r"^def\s+parse_line\s*\(", text, re.MULTILINE):
        start = m.start()
        out.append(text[start:start + 400])
    return out


def main() -> int:
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    sys.path.insert(0, str(repo.resolve()))

    # Force a fresh import each time.
    for mod in ("importer", "exporter", "parser"):
        sys.modules.pop(mod, None)

    import importer  # type: ignore  # noqa: E402
    import exporter  # type: ignore  # noqa: E402

    # 1. Behaviour unchanged.
    expected = [
        ["a", "b", "c"],
        ["hello, world", "2", "3"],
        ["q", "uoted"],
        ["trailing", "quoted"],
    ]
    for line, want in zip(SAMPLES, expected):
        got_imp = importer.parse_line(line)
        got_exp = exporter.parse_line(line)
        if got_imp != want:
            return _fail(f"importer.parse_line({line!r}) -> {got_imp}, want {want}")
        if got_exp != want:
            return _fail(f"exporter.parse_line({line!r}) -> {got_exp}, want {want}")

    text = "a,b\n\nc,d\n"
    if importer.parse_text(text) != [["a", "b"], ["c", "d"]]:
        return _fail("importer.parse_text changed behaviour")
    if exporter.parse_log(text) != [["a", "b"], ["c", "d"]]:
        return _fail("exporter.parse_log changed behaviour")

    # 2. Duplication gone: at most one top-level `def parse_line` body
    #    among importer.py and exporter.py. The other module should
    #    import it.
    py_files = [p for p in repo.iterdir() if p.suffix == ".py" and p.name != "check.py"]
    bodies = {p.name: _parse_line_bodies(_read(p)) for p in py_files}
    local_parse_line = {
        name: bs for name, bs in bodies.items() if name in ("importer.py", "exporter.py")
    }
    if len(local_parse_line) != 2:
        return _fail(f"expected importer.py and exporter.py to still exist, got {list(local_parse_line)}")
    own_body_count = sum(1 for bs in local_parse_line.values() if bs)
    if own_body_count > 1:
        return _fail(
            f"duplication remains: {own_body_count} modules still define parse_line "
            f"({[n for n,bs in local_parse_line.items() if bs]})"
        )
    if own_body_count < 1:
        # One of them must re-export it (def parse_line = ... or import ... as parse_line).
        re_exported = False
        for fname in ("importer.py", "exporter.py"):
            text = _read(repo / fname)
            if re.search(r"^\s*(from\s+\S+\s+import\s+.*parse_line|import\s+.*parse_line|parse_line\s*=)", text, re.MULTILINE):
                re_exported = True
        if not re_exported:
            return _fail("parse_line is defined in neither module and not re-exported")

    # 3. The quoted-splitting logic should exist in exactly one file.
    split_files = []
    for p in py_files:
        t = _read(p)
        if 'in_q' in t or 'split_quoted' in t:
            split_files.append(p.name)
    if len(split_files) > 1:
        return _fail(f"quoted-splitting logic is duplicated in {split_files}")

    # 4. A small smoke run via the CLI helper should be reachable (no syntax errors).
    proc = subprocess.run(
        [sys.executable, "-c", "import importer, exporter, parser; print('ok')"],
        cwd=str(repo), capture_output=True, text=True,
    )
    if proc.returncode != 0 or "ok" not in proc.stdout:
        return _fail(f"imports broke: {proc.stderr.strip() or proc.stdout.strip()}")

    return 0


def _fail(msg: str) -> int:
    print(f"FAIL: {msg}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
