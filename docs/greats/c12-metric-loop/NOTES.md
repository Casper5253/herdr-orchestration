# C12 metric loop — pilot notes

Template: `program.md.tmpl` in this directory. Pilot harness:
`scripts/preflight-corpus.py` (run from the worktree root).

## The pilot

- **Metric:** `wrong` = false-positive findings on the 5 clean fixture
  briefs (`tests/fixtures/briefs/`) + missed findings on 4 catch lines, for
  `capability.prompt_findings('devin', ...)`. Lower is better.
- **Corpus:** fixed — the 5 fixture briefs plus 4 instruction lines naming
  denied commands (`git clone`, `git checkout -b`, `gh api`, and a
  "running" form the round-2 scanner misses).
- **Log:** `attempts.tsv` in this directory — one row per attempt, reverts
  included.

## Result

`wrong` went **1 → 0** in 5 iterations. Baseline missed one catch case:
"finish by running git checkout -b feat/y" — bare prose, no backticks, and
`running` is not in the scanner's run-phrase verbs. Iteration 1 widened the
run phrase to `run|runs|running|execute|executes|invoke|invokes|start|starts|try|tries`
and fixed the miss with no new false positives. Iterations 2–5 were
reverted: `and`/`or` triggers flagged a brief ("`hc` or `herdr agent
list`"), `by` and extra skip words tied (a tie is a revert), and dropping
`with` re-broke the documented `Start with git checkout` catch.

## For the lead — what to install

- **The kept change** for `hx/capability.py`: widen `_RUN_PHRASE_RE` to the
  verb set above (one regex, `hx/capability.py`). Verified by the pilot log.
- **Into the shared skills** — the template's home is the poteto-mode
  hillclimb playbook: add `program.md.tmpl` as the loop's program file and
  one line under the playbook's "run a loop" step: "copy
  `program.md.tmpl`, fill metric/command/baseline/budget, one change per
  iteration, log every attempt to attempts.tsv". That is the whole
  mechanism — the pilot shows 6 lines of harness were enough.

## Open notes

- The corpus lives in tests/, so the pilot can be re-run after any scanner
  change: `python scripts/preflight-corpus.py`.
- A second natural pilot target (not run): startup context bytes of the
  generated skills — metric = bytes of SKILL.md frontmatter+body loaded at
  session start.
