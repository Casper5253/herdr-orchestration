# Brief: phase 3, the difference analysis (Q-199, Q-202)

Use poteto-mode, Investigation playbook. Open your report with the goal, the playbook and the principles you applied.

Read C:\Users\caspe\Documents\Projects\herdr-orchestration\docs\greats\PROGRAM.md first, including the creator's verbatim direction. Their rule: assume nothing is implemented, compare exactly how they do it with exactly how we do it, and prefer their approach unless the evidence shows it does not fit.

## Inputs (all in herdr-orchestration/research/greats/)

- capabilities.md: 13 capabilities, each person's method linked to its item file.
- The person files, for the exact mechanisms.
- baseline.md: our measured behaviour over 14 days. One caveat: its review count only sees routed hx tasks and misses reviews sent through the team panes (hx say, herdr agent prompt), so recount review from the session logs.
- context-management.md: the measured root causes for rare compaction.
- Our setup, to read as it actually is: ~/.agents/pstack/ (TEAM.md, sync.py, the skills), ~/.claude/CLAUDE.md, the Codex and OpenCode global instructions, the hx repo (hx/, policy.json, docs/), agentmem (C:\Users\caspe\Documents\Projects\agent-memory or wherever the `mem` CLI lives), and the herdr-team skill.

## Rules

- Write only research/greats/diff-<your-slug>.md in herdr-orchestration. No code changes, no git writes, no deletes.
- Every claim about our setup cites a file:line or a measured number. Every claim about theirs links to the item.

## For each capability you own (listed in the message that sent you here)

1. **Their method:** the strongest version, merging the people who agree. Name the disagreements.
2. **Our method, exactly:** what the files say, and what the logs show actually happens.
3. **Verdict:** `implemented as they do it` / `weaker` (name the gap) / `missing` / `does not fit` (with the evidence why).
4. **The change:** the concrete change that closes the gap, in a shared layer (pstack, the global instructions, hx, agentmem, a repo template). Give its size (S/M/L) and what it would replace or delete. Subtract before you add.
5. **Proof:** how we would measure that the change worked, ideally the same measurement as the baseline.

## Report

Via `hx say claude`, under 8 lines: your file path, the verdict count, and your top 3 changes by impact.
