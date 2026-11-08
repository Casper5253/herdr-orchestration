# After evidence — learn-from-the-greats program

Gathered 2026-11-08 for Q-208. Read-only: the only file written is this one. Sources are the commands and files named under each section. "Too early" means the mechanism shipped but the real record has no post-change observations yet. The one exception is the 299% incident, which is a live observation.

## C1 — Boundary context and recovery lifecycle

**Before.** "The measured inventory has 13 decisions and 2 refused attempts; completion does not invoke the controller; `hx say` warns and still sends; the 89% pressure example delivered another assignment; the active model's effective limit was missing at audit time" (docs/greats/changes.md:45). Live incident, quoted from .hx/briefs/c1-stale-sample.md: "dev-opencode's session was at 299% of its 200k budget, and `hx say opencode ...` was held (correct)" and "Seconds later, `hx say opencode ...` still printed 'Held: dev-opencode context utilisation is 2.99 (299%)'" after a session reset.

**After.** Commits 1cfded8 (boundary operation, pressure hold) and 991fce6 (one session-resolution path for `say` and `decide`). `hx task done` and `hx boundary` now record a disposition every time; `hx/say.py` holds a worker at or above pressure and prints the recovery command. The 299% regression is pinned in tests/test_context_session_reset.py:54 and :72 (`utilisation == 2.99`, `'299%'` in stderr). Today: 85 tests pass across `tests/test_boundary.py`, `tests/test_context.py`, `tests/test_say.py`. Live journal `.hx/context-boundaries.jsonl` does not exist, so 0 real boundary dispositions are recorded yet.

**Notice.** When a worker is over its context budget, hx now holds the next brief and tells you the recovery command instead of silently delivering it.

**Status.** Shipped; live boundary evidence too early (0 journal entries).

## C2 — Reusable control skill and feature map

**Before.** "Zero invocations of ... `create-verification-skill` ... and every `principle-*` leaf ... the eval and verification-skill generators produce files, and none were produced" (research/greats/baseline.md:10). "Briefs repeat manual QA recipes" (docs/greats/changes.md:121).

**After.** No commit, branch or file. `git branch --all` has no C2 branch; `rg "create-verification-skill"` finds no generated adapter; no `verify-<app>` skill exists in this repo; no repo `AGENTS.md` pointer exists at the root.

**Notice.** Nothing changes yet.

**Status.** Not started.

## C3 — Shared skill/prompt promotion eval

**Before.** "Zero `eval` invocations; no `~/.agents/pstack/evals/` artifacts; no skill change was ever tested before adoption in the window" (research/greats/baseline.md:47). "There are zero shared skill/prompt eval artifacts and no promotion gate" (docs/greats/changes.md:65).

**After.** Commits 87da228 and 6e4850c. `evals/skills/` holds 11 frozen cases across 3 skills and a runner. evals/skills/fixtures/proof-unchanged.json: before 3/3, after 3/3, `verdict: promote`. evals/skills/fixtures/proof-broken.json: before 3/3, after 2/3, `verdict: hold`, with P2-investigation-route at 0/3 for the broken copy. evals/skills/fixtures/gate-identical.json: every skill `promote`. Runner errors now exit 1, not 0 (6e4850c); `python -m pytest evals/skills -q` gives 3 passed. Codex's review at .hx/reports/codex-review-hx-87da228.md caught the 0/0 promote hole before anything used the gate.

**Notice.** A skill edit that breaks a checked behavior is now held back; it can no longer reach all agents on the strength of its prose.

**Status.** Shipped.

## C4 — Admission preflight before dispatch

**Before.** "9 of 21 routed outcomes failed (43%), and every failure is a tool-permission denial; `q196-setup` and `h3-devin-context` each retried the same cause" (docs/greats/changes.md:25; research/greats/baseline.md:32-33).

**After.** Commit c2cf4a3, merged at 13ec0c3 (2026-11-08 14:14 +0800). The preflight validates the worktree, owned files and required commands against the provider profile before launch. Today's `.hx/routing.jsonl`: 23 decisions, 15 outcomes, 7 ok and 8 failed (53%); all 8 failures are `kind: tool`. **0 outcomes exist after the 06:14Z merge**, and the newest `.hx/runs` entry is 04:58Z, so the post-merge routed-failure rate is unobserved. Codex's review found two P1 holes in hx/capability.py:597 and :610 (a "No tests" phrase suppressing a real denial, and a prohibition suppressing an unrelated instruction); those fixes are on fix/c4-c5.

**Notice.** A brief naming a command the worker is not allowed to run should now be refused before a model is paid to discover it.

**Status.** Shipped; effectiveness too early (0 post-merge outcomes); 2 review findings open.

## C5 — Execution profiles and capability boundary

**Before.** "Broad access plus command-string bans as the main boundary" (docs/greats/changes.md:227). Codex §9.

**After.** Commits 945a111 and fdc3b9d. `hx profile show implement --worker devin` prints: model `swe-2-high`, `permission_mode: accept-edits`, `subagents_enabled: false`, 6 allow rules, 65 deny rules, and all seven foreign config sources false except `agents_standard: true`. Codex's review found four P1 holes (hx/profile.py:54 and :66, hx/ocrun.py:1500 and :1462); those fixes are on fix/c4-c5.

**Notice.** Each worker run now gets a named profile with a concrete deny list instead of permissions assembled from prose.

**Status.** Shipped; 4 review findings open on fix/c4-c5.

## C6 — Review operation and acceptance record

**Before.** "~3 routed review tasks visible vs 334 commits in todo-rpg" (research/greats/baseline.md:26). "ad hoc 'ready to merge' text as the only durable gate" (docs/greats/changes.md:167).

**After.** Commits 6695dd8 and 6e4850c. `hx review open/verdict/status/resolve` record the snapshot digest, author and reviewer family, findings and dispositions. 6e4850c makes an edit to an untracked file change the digest, so the verdict goes stale. Codex's review caught exactly that name-only hashing gap before use (.hx/reports/codex-review-hx-87da228.md, C6 P1). Today: 19 tests pass across `tests/test_review.py`, `tests/test_doctor.py` and `tests/test_handoff.py`. Ledger check: 11 tasks, 0 with a review; `hx review status devin-tests` prints `none — no review opened`.

**Notice.** A review verdict now names the exact snapshot it covered and the reviewer's family, and turns stale when the code changes.

**Status.** Shipped; live review volume too early (0 of 11 tasks).

## C7 — `correct` as the standing review-to-hard-rule ratchet

**Before.** "`correct` has 0 invocations in 14 days; repeated review comments are not converting into checks; `AGENTS.md` is two lines" (docs/greats/changes.md:85).

**After.** No commit, branch or file. `correct` appears nowhere in `~/.agents/skills/herdr-team/SKILL.md` or `skills/herdr-team/SKILL.md`; no root `AGENTS.md` exists in this repo; no `correct` invocation is recorded.

**Notice.** Nothing changes yet.

**Status.** Not started.

## C8 — Single policy source and hx doctor

**Before.** "The adapters drift: the launch overlay pins Qwen while policy defaults to DeepSeek; the Devin prompt still puts review text in notes; the generated hx-router models per-model allowance while live usage models a shared pool" (docs/greats/changes.md:205).

**After.** Commit 6cb613e added `hx doctor`; 52f23b5 fixed the drifts it found. The doctor found four: the OpenCode overlay pinned `opencode-go/qwen3.7-plus` while policy selected `deepseek-v4.1-flash`; the Devin preamble still described the old notes-based review; `gpt-6.1-sol` was missing from `context_limits`; and `swe-2-medium/high/max` were missing from `context_limits`. Today `hx doctor` prints: `claude model:unknown selected=sonnet observed=None`, then `codex model:ok`, `opencode model:ok`, `devin model:ok`, and `no drift` (`--json`: `drift: false`). The unknown Claude model is reported explicitly rather than hidden.

**Notice.** One command now shows whether each agent's model, skills, deny list and context budget match policy, and names the unknown instead of guessing.

**Status.** Shipped.

## C9 — Project workflow skill for I-6

**Before.** "Count briefs that link a spec/plan artifact (now ~0), mid-flight amendment messages per task (now no trail)" (docs/greats/changes.md:191).

**After.** Commit fe0e1f9. The skill lives at `~/.agents/skills/project-workflow/SKILL.md` (5,293 bytes) with the idea → optional prototype → spec → plan → tasks → work → docs chain and `.artifacts/<task>/` convention; `~/.agents/skills/kickoff/SKILL.md` names it. No project has produced a spec/plan artifact yet, and no brief links one.

**Notice.** New projects start with a written spec, plan and artifact folder instead of a long chat that gets amended.

**Status.** Shipped; usage too early (0 artifacts).

## C10 — Portable handoff and memory note

**Before.** "handoff/checkpoint files per task (now ~0.07)", "vault writes per week (now ~5)" (docs/greats/changes.md:151). The window shows one `handoff-2026-10-31.md` (research/greats/baseline.md:43).

**After.** Commit 6f44dd4 plus 6e4850c. `hx handoff write <agent> --task <id>` composes `.hx/handoffs/<task>-<n>.md` from the checkpoint, ledger and newest run outcome, with a hard 4,096-byte cap and redaction of `Authorization: Bearer ...`, `*_API_KEY`, `*_TOKEN`, secrets and passwords. Today `.hx/handoffs` does not exist (0 new handoffs); the only artifact is the older `.hx/handoff-2026-10-31.md` (4,272 bytes). The redaction and cap are pinned in `tests/test_handoff.py`.

**Notice.** A fresh owner gets one short file naming the next action instead of re-reading the previous session.

**Status.** Shipped; live use too early (0 new handoffs).

## C11 — WHY line and discard accounting

**Before.** "Reports have 0 WHY lines; discards are not visible" (docs/greats/changes.md:105).

**After.** The installed shared copy `~/.agents/skills/herdr-team/SKILL.md:28` now reads `STATUS: done|blocked|discarded. WHY: intent, risk, how it was verified.` and line 29 explains WHY and `discarded`. Kickoff repeats the format. The repo back-port `skills/herdr-team/SKILL.md` still has the old `STATUS: done|blocked. CHANGED: ... TESTS: ... NOTES:` line and no WHY; a compare shows the installed copy is ahead.

**Notice.** Every report leads with why the change exists, what could break and how it was verified; a dropped approach is visible as `discarded`.

**Status.** Shipped in the shared source; the repo copy still carries the old format (drift).

## C12 — Metric-loop `program-md` template and pilot

**Before.** "no loop exists" (docs/greats/changes.md:247); "no project currently trains a model, so the autoresearch loop shape is the gap" (research/greats/diff-devin.md §13).

**After.** Commits f9c52ad and 9a42af6. Template: `docs/greats/c12-metric-loop/program.md.tmpl` (54 lines: setup, allowed edit surface, budget, keep/discard metric, stop condition). Pilot log `docs/greats/c12-metric-loop/attempts.tsv`: `wrong` went 1 → 0 in 5 iterations; 4 iterations were reverted (and/or triggers, `by`, dropping `with`, extra skip words), 1 was kept (the run-verb set). The kept change is live: commit 9a42af6 widens the preflight run-phrase regex.

**Notice.** A metric loop now has one property file and a keep/discard log, so improvement attempts leave a record instead of disappearing.

**Status.** Shipped (template and pilot); no production loop is running yet.

## Measured OpenCode usage

**Before.** The default model's context utilisation was `null`; the fail-first test reproduced `utilisation: null` for 100,000 input-plus-cache tokens (.hx/reports/q205-opencode-measured.md, Verification).

**After.** `hx usage` now reads `GET https://opencode.ai/zen/go/v1/usage`. Today's snapshot: `tag: measured`, source that endpoint, windows `5h 0.01`, `7d 0.11`, `30d 0.05`, `available: true`; local estimate `5h $2.55/$12 (21.2%)`, `7d $4.71/$30 (15.7%)`, `30d $4.71/$60 (7.9%)`; newest OpenCode message 6 seconds old.

**Notice.** The quota display shows the provider's own percentages, and routing uses the same measured pool instead of a guess.

**Status.** Shipped.

## Kickoff skill

**Before.** The standard session procedure was the older local skill `~/.agents/pstack-local-backup-20261108/kickoff/SKILL.md` (5,103 bytes); starting a session still depended on remembering the sequence.

**After.** `~/.agents/skills/kickoff/SKILL.md` (7,562 bytes, installed 2026-11-08 15:28) defines `/kickoff`: load poteto-mode, herdr-team, hx-router and memory, then pick work from Jotarium and run with routing, review and Jotarium upkeep. It is copied to every agent by `~/.agents/sync.py`.

**Notice.** Typing `/kickoff` starts a session the same way every time, instead of reconstructing the routine from memory.

**Status.** Shipped.

## Skill move to `~/.agents/skills`

**Before.** Our own shared skills lived under `~/.agents/pstack/local/` (that directory no longer exists; `Test-Path` false). A backup of the seven old skills sits at `~/.agents/pstack-local-backup-20261108/`.

**After.** Eight skills live in `~/.agents/skills/`: herdr-team 4,966 B, hx-router 11,307 B, kickoff 7,562 B, memory 3,877 B, obsidian 1,365 B, project-workflow 5,293 B, qmd 11,182 B, remember 2,292 B. `~/.agents/sync.py` (9,850 B) lists `~/.agents/skills/` as a source (lines 4-9) and generates the copies: 65 skill directories in `~/.claude/skills/`, plus the Codex and Devin mirrors. `~/.agents/pstack/sync.py` is now a 245-byte shim to the real sync.

**Notice.** One source folder feeds Claude, OpenCode, Codex and Devin, so a skill is edited once.

**Status.** Done.

## Reruns today

- Routed-failure rate since C4 merged: **too early**. `.hx/routing.jsonl` has 15 outcomes total (7 ok, 8 failed, all tool denials); the newest is 04:58Z and C4 merged at 06:14Z, so 0 outcomes fall after the merge.
- Drift count now: **0**. `hx doctor` ends `no drift`; Claude's model is reported `unknown` (selected `sonnet`, observed `None`).
- Tests: `python -m pytest tests -q` → 603 passed (2026-11-08, at 6e4850c). Focused: boundary/context/say → 85 passed; review/doctor/handoff → 19 passed; `python -m pytest evals/skills -q` → 3 passed.
- Review records: **too early**. 11 ledger tasks, 0 with a review record.
- Handoff artifacts: **too early**. 0 files in `.hx/handoffs`; 1 older handoff from 2026-10-31.
- Boundary dispositions: **too early**. `.hx/context-boundaries.jsonl` absent, so 0 recorded.
- Skill usage and ambition measures from the change list (leaf-skill invocations, discard counts, attempts per kept change, spec-linked briefs): **too early**; these need a fresh 14-day window.

## Today's incidents, in one place

- **Held DeepSeek brief at 299%.** The lead observed a hold at `2.99` (299%) of the 200k budget, then a stale hold after `/new`; reproduced in `.hx/briefs/c1-stale-sample.md` and pinned in `tests/test_context_session_reset.py`.
- **Four drifts found by the doctor.** OpenCode overlay model, Devin review preamble, Codex context budget, and the swe-2 context budgets; fixed in 52f23b5, and `hx doctor` now reports `no drift`.
- **Codex review before use.** `.hx/reports/codex-review-hx-87da228.md` found P1 gaps in C5 (4), C4 (2), C6 (1), C10 (1) and the sync gate (1). C6, C10 and the gate were fixed before the evidence file; C4 and C5 fixes are on fix/c4-c5.
