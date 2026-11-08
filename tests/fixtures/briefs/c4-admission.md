# Brief: C4, admission preflight before dispatch (Q-203, first slice)

Use poteto-mode, Feature playbook. Open your report with the goal, the playbook and the principles you applied.

## Workspace and rules

- Worktree: C:\Users\caspe\Documents\Projects\herdr-orchestration-worktrees\devin-hx, branch feat/c4-admission (already checked out). No git writes, no deletes. Do not run gh or git clone.
- Don't change hx/context.py, hx/compact.py or hx/say.py: Codex is changing the context lifecycle (C1) in parallel.

## Goal

Read docs/greats/changes.md section C4 and research/greats/diff-codex.md §5. The measured fault: 43% of routed worker runs failed, and every failure was a tool-permission denial. A denied command ends a Devin run, and the same cause was retried (q196-setup three times today: gh and git clone denied).

## The first slice

1. **A capability profile per worker** (devin, opencode, codex): which commands it is denied, from its live config where hx can read it (the Devin pane config ~/.herdr-team/state/dev-devin/config.json, and the shipped template as fallback), and whether it may write outside its cwd.
2. **A preflight in `hx ocrun`**, before launch: it scans the brief for commands the worker is denied (for example `gh`, `git clone`, `git checkout`, `herdr-app check`) and for write paths outside the worker's cwd. If it finds one, it refuses with exit code 2 and a clear message naming each problem and its fix (the lead runs that command first, or the work goes to a worker that may). An explicit `--allow-preflight-risk` flag overrides it.
3. **Retries by cause:** `hx route --attempt N --last-failure tool` with a previous failure of the same denied command refuses the same route and says why.
4. **Tests:** replay both documented denial cases (the q196-setup brief with `git clone`, and a brief asking for `git checkout`). Both must be refused before launch.

Keep it small and data-driven: one profile table, one preflight function. Subtract any duplicate denial prose this replaces.

## Verify

`python -m pytest tests -q` passes (run tests/ only; the repo root has known unrelated collection errors).

## Report

Via `hx say claude` (if denied, end your final message with it), under 8 lines: files, test result, and what the preflight catches.
