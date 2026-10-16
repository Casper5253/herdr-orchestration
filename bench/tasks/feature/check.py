"""Hidden acceptance check for the feature task.

Exercises the new tag filter end-to-end via the store + CLI. Exits 0 on
pass; prints a one-line reason on failure.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]).resolve()))

from store import NoteStore  # type: ignore  # noqa: E402


def fail(msg: str) -> int:
    print(f"FAIL: {msg}")
    return 1


def main() -> int:
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()

    # 1. Store API: by_tag exists and filters case-insensitively.
    s = NoteStore()
    s.add("alpha", "x", tags=["Work", "Urgent"])
    s.add("beta", "y", tags=["personal"])
    s.add("gamma", "z", tags=["work"])

    if not hasattr(s, "by_tag"):
        return fail("NoteStore is missing by_tag()")
    work = s.by_tag("work")
    if {n.title for n in work} != {"alpha", "gamma"}:
        return fail(f"by_tag('work') returned {[n.title for n in work]}")
    if s.by_tag("missing") != []:
        return fail("by_tag should return [] for unknown tag")

    # 2. Models: tags is still a list[str] on every note.
    n = s.add("delta", "w", tags=["a", "b"])
    if n.tags != ["a", "b"]:
        return fail(f"Note.tags not stored verbatim, got {n.tags!r}")

    # 3. CLI: `add --tags a,b` stores tags; `tag a` filters. Drive the
    #    CLI in a single Python process with a shared store so the add
    #    is visible to the tag call. We do this by patching the store
    #    module before importing cli.
    driver = (
        "import io, sys, contextlib\n"
        "sys.path.insert(0, r'" + str(repo.resolve()) + "')\n"
        "import store as _store\n"
        "s = _store.NoteStore()\n"
        "_store.NoteStore = lambda: s\n"
        "import cli as _cli\n"
        "def run(argv):\n"
        "    buf = io.StringIO()\n"
        "    with contextlib.redirect_stdout(buf):\n"
        "        rc = _cli.main(argv)\n"
        "    return rc, buf.getvalue()\n"
        "rc1, out1 = run(['add', 'cli-note', 'body', '--tags', 'alpha,beta'])\n"
        "assert rc1 == 0, ('add rc=%r out=%r' % (rc1, out1))\n"
        "rc2, out2 = run(['tag', 'alpha'])\n"
        "assert rc2 == 0, ('tag rc=%r out=%r' % (rc2, out2))\n"
        "assert 'cli-note' in out2, ('tag output missing note: %r' % out2)\n"
        "rc3, out3 = run(['list'])\n"
        "assert rc3 == 0, ('list rc=%r out=%r' % (rc3, out3))\n"
        "print('CLI_OK')\n"
    )
    cli_proc = subprocess.run(
        [sys.executable, "-c", driver],
        cwd=str(repo), capture_output=True, text=True,
    )
    if cli_proc.returncode != 0 or "CLI_OK" not in cli_proc.stdout:
        return fail(
            "CLI driver failed: "
            f"rc={cli_proc.returncode} stdout={cli_proc.stdout!r} stderr={cli_proc.stderr!r}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
