# Difference analysis — dev-devin's six capabilities (Q-199 phase 3)

Method: poteto-mode, Investigation playbook. Principles: Attack the Premise (assume nothing implemented; the baseline numbers are the ground truth), Fix Root Causes, Guard the Context Window. "Ours" cites files or measured numbers from `research/greats/baseline.md` (14-day window, 2026-10-25→11-08); "theirs" cites `research/greats/capabilities.md` sections and the person files.

Verdict scale: `implemented as they do it` / `weaker` (gap named) / `missing` / `does not fit` (evidence why).

---

## 4. Planning and the project workflow (I-6)

**Their method (merged).** A written artifact chain: spec → plan → tasks → thin vertical slices → verify → commit often (Addy's SPECIFY/PLAN/TASKS/IMPLEMENT with `tasks/plan.md` + `tasks/todo.md`, spec as a gate). Lauren routes every task to a named playbook before work starts; PRs are atomic. Simon's two modes (explore, then authoritarian). Karpathy: agentic engineering = design the spec, supervise the plan, inspect diffs. **Disagreement:** Addy gates on a written spec; Felix and Theo say a weekend prototype or goal-level prompting can bypass it — spec-first vs prototype-first.

**Our method, exactly.** Plan-first is real: ~165 written briefs in the window (baseline §5), each carrying goal/playbook/principles; poteto-mode is installed and routes tasks to named playbooks (their mechanism, verbatim — `~/.claude/skills/poteto-mode/SKILL.md`); Jotarium is the tracker (idea → `plan_idea` → items → typed review → done, per `~/.claude/CLAUDE.md` Jotarium section); typed reviews (check/decide/do) encode "see first, then judge" — the strongest fit anywhere on this page. Gaps: no per-task artifact chain — `find` for `spec.md`/`plan.md`/ADR/`CONSTRAINTS.md` in both repos returns **one** `.artifacts/wp17b/plan.md`; brief amendments travel as chat messages, not versioned artifacts (two mid-flight corrections in my own session); no ADR habit anywhere.

**Verdict: weaker.** The bones exist (briefs, playbooks, typed review, tracker); the I-6 deliverable — a durable per-task spec/plan/revision chain plus a named prototype-first path — does not.

**The change (M).** A `project-workflow` skill in `~/.agents/pstack/local/project-workflow/SKILL.md`: idea → (optional prototype spike, the Felix/Theo path) → `spec.md` → `plan.md` → task list → work + revision → docs note; artifacts under `.artifacts/<task>/`, briefs reference them by path (Matt's "reference, don't copy"). Replaces: brief-only planning and chat-only amendments. Subtract-first: reuses the existing briefs/ledger/playbooks — it is one file and one convention, not a new pipeline.

**Proof.** Same measurement as baseline §5: count briefs that link a spec/plan artifact (now ~0), mid-flight amendment messages per task (now: no trail), and review rounds in `.hx/ledger.json`.

## 7. Memory and knowledge

**Their method (merged).** Durable artifacts, not chat history: Matt's `/handoff` (portable doc, OS temp dir, suggested skills, references-not-copies, redaction — for exactly four triggers), Karpathy's compiled knowledge base (a maintained wiki: entity pages, concept pages, contradictions, logs) and files-as-storage LLM-OS, Addy's persistent rules+specs, Lauren's git history, Simon's clean-reset-plus-good-seed. **Disagreement:** the stores differ (handoff vs wiki vs rules vs git) — the material doesn't rank them.

**Our method, exactly.** agentmem exists: `mem` CLI in `~/Documents/Projects/agent-memory` (agentmem/, tools/, docs/), vault `~/Documents/Notes/FDE Knowledge`, sessions auto-captured, `CLAUDE.md` Memory section mandates `mem search` on resume/decisions. hx checkpoints exist (`.hx/checkpoints/`, one `handoff-2026-10-31.md`; `herdr-team` SKILL.md:42 makes checkpoint-writes part of reporting when a brief names a task id). `context-management.md` measured that native compaction *does* work (DeepSeek hit 967k tokens → auto-compacted → next request 41.9k) but the hx early-pressure controller is incomplete: no automatic decision on task completion (`hx/__main__.py:192`), warn-only message path (`hx/say.py:37`), missing model limit → no telemetry (`policy.json:164`). Baseline: `memory` invoked 2×, `obsidian` 4×, vault commits 10 in 14 days — the store exists, the habit doesn't.

**Verdict: weaker.** Store and rules exist; usage is near zero, no handoff-artifact habit (the four Matt triggers are unhandled: harness swap, repo move, colleague send, side-fork), and the vault is a capture sink — nothing compiles it into Karpathy-style maintained pages.

**The change (S).** Port Matt's handoff mechanism into `~/.agents/pstack/local/handoff/SKILL.md` (his SKILL.md is 16 lines, MIT) and unify it with the hx checkpoint — the checkpoint *is* the handoff artifact: temp/workspace file, suggested skills, references-not-copies, redact secrets. Plus one line in `herdr-team` (next to its line-28 report format): decisions get a `mem note` before the report is sent. Replaces: nothing; deletes: none. Subtract-first: uses agentmem and the existing checkpoint path — no second store.

**Proof.** Baseline re-measurement: vault writes/week (now ~5), handoff/checkpoint files per task (now ~0.07), post-compact task-survival rate from session logs.

## 8. Skills and prompt design

**Their method (merged).** Small composable skills with invocation discipline: Matt's user-vs-model-invoked split + a `docs/` page per skill; Addy's pack format (frontmatter, process, anti-rationalization, red flags, verification) and short earned `AGENTS.md`; Karpathy's `program.md` — a minimal agent-loop file (setup, experiment rules, result format, loop, stop condition) and Software 3.0's prompt-as-program; Felix's depth prompt (= pstack `/sharpen`, already upstream); Theo's harness-is-product.

**Our method, exactly.** The inventory is the strongest version available — pstack pinned (`upstream/UPSTREAM:1`, pin dated 2026-11-03) and synced to all three agent families via `~/.agents/pstack/sync.py`; `TEAM.md` maps every role line to an agent family; `CLAUDE.md` "Adding a skill" makes `local/` the write path. Baseline: **75 invocations in 14 days, 78% in three skills** (poteto-mode 30, hx-router 17, herdr-team 11); the entire leaf layer — eval, interrogate, architect, arena, swarm, all ~40 principles, create-verification-skill, tdd — at zero; subagent sessions invoked zero skills. So most work runs with no skill active at all, and no skill change was ever eval-tested.

**Verdict: weaker.** Inventory: theirs. Trigger fidelity and regression discipline: absent. A skill system nobody invokes is the "we have context management, it works poorly" pattern again.

**The change (M).** A skill-eval harness in `~/.agents/pstack/local/` — three cases per edited skill (bug-fix, feature, review), a deterministic check where possible plus a fresh-model judge, run as a gate step in `sync.py` before it writes the copies. This is the Q-193 page's #1 adopt item landing in the layer that already owns skill promotion. Replaces: nothing — it gates a step that today has no gate. Subtract-first: one `evals/` dir + one check call inside the existing script, not a new tool.

**Proof.** Eval runs recorded per skill change (now 0); invocation distribution re-measured from session logs next window — target: leaf skills fire on the tasks they exist for, measurable as >0 non-router invocations.

## 10. Ambition, autonomy and taste

**Their method (merged).** Judgment is the scarce input: Felix's four ambitions (speed/altitude/depth/range) and hunger-beats-tenure; Karpathy's autonomy slider and "outsource thinking, never understanding"; Theo's pick-don't-salvage and breadth-as-strategy; Addy's 70%/80% problems and comprehension debt; Simon's golden rule (never commit code you can't explain); Lauren's trust curve. **Disagreement:** how much human review survives at the end — Lauren auto-merges, everyone else keeps a gate. For this creator ("see first, then judge"), the gate is correct — Lauren's own ladder says earn it in stages.

**Our method, exactly.** The autonomy slider is real and well-placed: lead routes by strength (`TEAM.md` role lines), workers run inside tool-permission sandboxes (the slider, literally — baseline §4: every routed failure is a denied tool), the human gate is Jotarium typed reviews, no auto-merge (workers have no git). Briefs ask for outcomes. Gaps: **comprehension debt is unpaid** — `herdr-team` SKILL.md:28's report format is STATUS/CHANGED/TESTS/NOTES, no WHY line; tasks stay ticket-scale (Theo's "larger assignments reveal capabilities" — no brief in the window attempted breadth); **reject-without-guilt is unpracticed** — retries salvage (q196-setup retried the same denied webfetch twice), nothing tracks discarded attempts; the `interrogate`/`arena` skills exist for contested calls — zero invocations.

**Verdict: weaker.** Structure protects judgment already (that part is `implemented as they do it`); the taste-preserving *practices* — WHY-lines, discard accounting, ambition expansion — are missing.

**The change (S+S).** (1) Add `WHY:` to the `herdr-team` report format — intent, risk, how verified; one line, lands in every report on every project. (2) Discard accounting: briefs may close as `STATUS: discarded` with a reason line, so attempts-per-kept-change becomes measurable in `routing.jsonl`. Replaces: the NOTES catch-all; deletes nothing.

**Proof.** WHY present in reports (count from session text, now 0); discarded attempts visible in routing outcomes (now invisible — salvage and retry are indistinguishable from success paths); one deliberately-larger task per sprint routed through `hx` to test Theo's expansion rule.

## 12. Code shape and constraints

**Their method (merged).** Prose rules are not enough — constrain the design space structurally, hard before soft: Lauren's Dune (feature co-location, hard bans on `useEffect` and code comments, `electron-main`/`electron-renderer` import-boundary CI, a lint per observed bad pattern, and the meta-rule: **every repeated review comment becomes a hard check**); Matt's more-abstractions-plus-harsh-lint; Addy's written `CONSTRAINTS.md` with ratchets. **Disagreement:** Matt pushes more abstraction, Lauren pushes dumbest-agent simplicity, Addy pushes a written floor — enforcement mechanism, not goal, is where they differ.

**Our method, exactly.** Strong generic base: strict TypeScript, zod at boundaries, branded types, eslint, `npm run check` as the gate, append-only DB triggers (genuinely hard enforcement), review fields validated in code. But: `AGENTS.md` is two lines; no import-boundary or feature-shape checks; comments are everywhere (unbanned); no `CONSTRAINTS.md`; and **no review→hard-rule conversion habit** — the pstack `correct` skill exists for exactly this ("make each repeated mistake impossible: architecture → types → lint → test") and baseline shows **0 invocations in 14 days**.

**Verdict: weaker.** The hard-layer tools are all installed; the meta-rule — the thing that makes Lauren's system compound — has never run.

**The change (S).** Adopt `correct` as the standing post-review step: each accepted review finding is routed to `correct` to become a hard check (type, lint, or test), written into `herdr-team`'s review protocol beside its line-29 findings format. Plus the paved-path `AGENTS.md` section per repo (Q-193 item 2) so constraints live where agents read them. Replaces: review-comments-as-only-enforcement. Subtract-first: zero new tools — one line in a skill file plus a repo template block.

**Proof.** Hard checks added per review finding (now ~0); repeated-mistake rate in later reviews; review comments per 100 lines trending down — all countable from git history and review artifacts.

## 13. Model training and research loops

**Their method.** Karpathy only, no disagreement: Software 2.0 (dataset+weights are the program), the Recipe (become one with the data, end-to-end skeleton, dumb baseline, overfit, regularize, tune), nanoGPT/nanochat (minimal readable loops, one `--depth` dial), llm.c, micrograd, and **autoresearch**: an agent edits one file (`train.py`), runs a fixed-budget experiment (5 min), keeps or discards on `val_bpb`, repeats overnight — `program.md` is the research org.

**Our method, exactly.** Nothing. No shared layer has a training surface or an experiment loop. But the *shape* is general: agent + editable file + fixed-budget run + metric keep/discard + `program.md`. Candidate surfaces exist: `~/Documents/Projects/algo-trading` and `QuantLab` (Projects dir listing) for real experiments, and hx `routing.jsonl` outcomes for policy hill-climbing.

**Verdict: missing** (and partly `does not fit`): no project currently trains a model, so nanoGPT-style training isn't the gap — the autoresearch *loop shape* is. Their approach wins on the transferable part only.

**The change (M, pilot).** A `program-md` template in `~/.agents/pstack/local/` generalizing autoresearch to any metric-driven experiment loop — setup, allowed edit surface, fixed budget, keep/discard metric, stop condition — piloted on the trading algorithm's backtests (the closest real surface). Replaces: nothing exists to replace. Subtract-first: one SKILL.md template + one pilot directory; nanoGPT et al. are reused as references, not imported.

**Proof.** Their own measures: experiments/hour unattended, keep/discard/crash counts, `results.tsv` or ledger rows, and whether the loop ran overnight without human intervention (baseline's delegation metrics apply verbatim).

---

## Verdict count

| Verdict | Capabilities |
|---|---|
| implemented as they do it | 0 |
| weaker | 4, 7, 8, 10, 12 (five) |
| missing | 13 (one, transferable part only) |
| does not fit | none outright; 13's training-literal half |

**Top 3 changes by impact.** (1) Skill-eval harness gating `sync.py` (cap 8, M) — every skill on every project improves under test, and it unblocks the others by making skill changes safe to ship. (2) `correct` as the standing review→hard-rule step (cap 12, S) — the compounding meta-rule, zero new tooling. (3) WHY line + `STATUS: discarded` in `herdr-team` (cap 10, S) — pays comprehension debt and makes taste measurable, one line each.
