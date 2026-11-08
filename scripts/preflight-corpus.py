"""C12 pilot: one metric, one change per iteration, keep iff it beats the metric.

Metric: wrong-findings = findings on clean briefs (false positives) + catch
cases with no finding (misses), over a fixed corpus. Run:

    python scripts/preflight-corpus.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hx import capability  # noqa: E402

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures" / "briefs"
CLEAN = [
    "greats-gather-devin.md",
    "greats-diff.md",
    "c4-admission.md",
    "herdr-app-setup.md",
    "q205-opencode-measured.md",
]
CATCH = [
    "Vendor Triage for review: `git clone --depth 1 https://github.com/sgomez/herdr-triage herdr-app/vendor/herdr-triage`.",
    "Start with `git checkout -b feat/x main`, then continue.",
    "Then run `gh api repos/x/tags` to list the tags.",
    "Finish by running git checkout -b feat/y and starting the work.",
]

VARIANTS = [
    ("baseline", "current code", {}),
    (
        "it1-run-verbs",
        "run phrase covers running/executing/starting/trying",
        {"RUN_PHRASE_RE": re.compile(r"(?i)\b(?:run|runs|running|execute|executes|invoke|invokes|start|starts|try|tries)\s+((?:[\w`~./\\:-]+\s*)+)")},
    ),
    (
        "it2-and-or",
        "it1 + and/or as exec triggers",
        {"RUN_PHRASE_RE": None, "TRIGGERS_ADD": {"and", "or"}},
    ),
    (
        "it3-by",
        "it1 + 'by' as exec trigger",
        {"RUN_PHRASE_RE": None, "TRIGGERS_ADD": {"by"}},
    ),
    (
        "it4-no-with",
        "it1 minus the 'with' trigger",
        {"RUN_PHRASE_RE": None, "TRIGGERS_DEL": {"with"}},
    ),
    (
        "it5-skip-instead",
        "it1 + skip lines with instead/rather/skip",
        {"RUN_PHRASE_RE": None, "SKIP_RE": re.compile(r"\b(?:do\s+not|don'?t|never|must\s+not|denied|denial|deny|no|instead|rather|skip)\b", re.IGNORECASE)},
    ),
]


def score() -> tuple[int, int, int]:
    fps, misses = 0, 0
    for name in CLEAN:
        text = (FIXTURES / name).read_text(encoding="utf-8")
        fps += len(capability.prompt_findings("devin", text, Path.cwd()))
    for line in CATCH:
        if not capability.prompt_findings("devin", line, Path.cwd()):
            misses += 1
    return fps + misses, fps, misses


def apply(patch: dict) -> dict:
    saved = {}
    if patch.get("RUN_PHRASE_RE"):
        saved["RUN_PHRASE_RE"] = capability._RUN_PHRASE_RE
        capability._RUN_PHRASE_RE = patch["RUN_PHRASE_RE"]
    elif "RUN_PHRASE_RE" in patch:
        saved["RUN_PHRASE_RE"] = capability._RUN_PHRASE_RE
        capability._RUN_PHRASE_RE = re.compile(
            r"(?i)\b(?:run|runs|running|execute|executes|invoke|invokes|start|starts|try|tries)\s+((?:[\w`~./\\:-]+\s*)+)"
        )
    if "TRIGGERS_ADD" in patch or "TRIGGERS_DEL" in patch:
        saved["TRIGGERS"] = capability._EXEC_TRIGGERS
        capability._EXEC_TRIGGERS = (
            capability._EXEC_TRIGGERS | patch.get("TRIGGERS_ADD", set())
        ) - patch.get("TRIGGERS_DEL", set())
    if "SKIP_RE" in patch:
        saved["SKIP_RE"] = capability._SKIP_RE
        capability._SKIP_RE = patch["SKIP_RE"]
    return saved


def restore(saved: dict) -> None:
    for key, value in saved.items():
        setattr(capability, key if key != "TRIGGERS" else "_EXEC_TRIGGERS", value)


def main() -> int:
    rows = ["iter\tchange\tfps\tmisses\twrong\tkept"]
    kept_patch: dict = {}
    best = None
    for name, desc, patch in VARIANTS:
        saved = apply({**kept_patch, **patch})
        wrong, fps, misses = score()
        restore(saved)
        kept = "baseline" if name == "baseline" else ("yes" if best is None or wrong < best else "no")
        if kept == "yes":
            kept_patch.update(patch)
        best = wrong if best is None else min(best, wrong)
        rows.append(f"{name}\t{desc}\t{fps}\t{misses}\t{wrong}\t{kept}")
    saved = apply(kept_patch)
    final, fps, misses = score()
    restore(saved)
    rows.append(f"final\tkept set applied\t{fps}\t{misses}\t{final}\t-")
    print("\n".join(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
