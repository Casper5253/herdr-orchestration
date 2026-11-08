# Change list (Q-202)

Goal: turn the two difference analyses into one deduplicated, ranked implementation list. This is not code work; every change lands in a shared layer and is verified against real behavior.

Ranked by impact over size. `fault` marks a fix for a measured failure or measured absence, with evidence in the diffs. Top 5: **C4, C1, C3, C7, C11**.

## Dedupe notes

- Codex capability 3 and Devin capability 8 are the same promotion-eval change: **C3**.
- Codex capability 6's "feed repeated findings into correct" and Devin capability 12's `correct` step are the same ratchet: **C7**.
- Codex capability 1's threshold consolidation and Codex capability 11's single-policy source are the same policy cleanup: **C8**.
- Codex capability 1's handoff reuse and Devin capability 7's handoff mechanism are the same artifact: **C10** owns the skill and report habit; **C1** owns the lifecycle that uses it.
- Codex capability 5 and capability 9 split into preflight (**C4**) and execution profiles (**C5**); they are different changes.
- Codex capability 2 and capability 11 both mention a repo template: **C2** owns the verification-specific adapter; **C8** owns the broader launch/verify/review/recovery template.
- Devin capability 10's two edits are one report-contract change: **C11**.

---

## C4 — Admission preflight before dispatch

**Change.** Before an expensive dispatch, validate the worktree and owned files, the required commands and the named network destinations against the selected provider's actual capability profile. Use non-mutating probes only where inspection cannot settle availability. Refuse or repair missing prerequisites before model work. Attach one concise capability result and artifact contract to the existing task. Default new work to a fresh owner; allow persistent reuse only for a stated live-state benefit with a context-boundary disposition.

**Shared layer.** `hx` admission and the pstack brief contract.

**Size.** M. **Fault.** Yes. 9 of 21 routed outcomes failed (43%), and every failure is a tool-permission denial; `q196-setup` and `h3-devin-context` each retried the same cause.

**Replaces / deletes.** Replaces same-cause retries and overlapping provider-denial prose with one maintained capability source. Deletes duplicate prose once that source owns it.

**Depends on.** C8 for the durable capability source; the first slice can use the current provider profile.

**Proof.** Repeat the routed-outcome measurement separately from pane tasks: permission failures, useful completions, retries by cause, wait and review time per accepted artifact, parent context growth and file conflicts. Replay both documented denial cases and require a refusal before launch, without weakening the authorization boundary.

**Closes.** 5.

**Evidence.** [Codex §5](../../research/greats/diff-codex.md#5-delegation-and-parallel-agents); [baseline](../../research/greats/baseline.md).

---

## C1 — Boundary context and recovery lifecycle

**Change.** Replace the lead's repeated "remember to decide" choreography with one boundary operation on the existing ledger and checkpoint: finish/validate → record provider/native/effective counters → decide → checkpoint or compact or fresh-owner handoff → verify continuation → admit the next assignment. Persist `keep`, `defer`, unknown telemetry and refusals as well as success. Keep lease ownership and worktree checks. Add an explicit readiness state so a refusal cannot silently become another ordinary brief. Adapt Devin's acknowledgement/telemetry order before enabling native compaction; make the lead's per-work-item loop guard distinct from a whole-session ceiling.

**Shared layer.** `hx` lifecycle, pstack `herdr-team`/`hx-router`, and the provider adapters.

**Size.** M. **Fault.** Yes. The measured inventory has 13 decisions and 2 refused attempts; completion does not invoke the controller; `hx say` warns and still sends; the 89% pressure example delivered another assignment; the active model's effective limit was missing at audit time.

**Replaces / deletes.** Replaces manual boundary reminders and warn-then-send as the only pressure response. Deletes duplicate threshold prose once C8 owns the policy reference.

**Depends on.** None for the first slice; C8 aligns the policy reference; C10 supplies the portable handoff artifact.

**Proof.** Repeat the context audit with an event denominator: eligible completed boundaries in the same session/task scope. Measure decisions per boundary, unknown-limit rate, pressure-to-recovery latency, tokens before/after, refusal disposition and fresh-owner time to first correct action. Plant synthetic constraints, current-error and next-action facts and test their survival after native compaction and fresh handoff. Require every eligible boundary to have a recorded disposition.

**Closes.** 1.

**Evidence.** [Codex §1](../../research/greats/diff-codex.md#1-context-management-and-handoff); [context audit §2](../../research/greats/context-management.md#2-what-hx-actually-implements); [measured records](../../research/greats/context-management.md#3-measured-execution-records).

---

## C3 — Shared skill/prompt promotion eval

**Change.** Use the existing eval playbook and agentmem's fixture/hash/provenance pattern; do not build a second orchestration engine. Add a small versioned case set for boundary recovery, truthful verification, review independence, denied-tool repair and skill routing, plus three cases per edited skill (bug-fix, feature, review). Compare the installed variant with one candidate in sanitized workspaces; hide version and model labels from the judge and the rubric from candidates. Check known invariants deterministically, then judge the remaining behavior. Keep sealed regression cases and repeated runs. Promote a shared revision only with a result artifact containing cases, raw evidence references, model/harness versions, spread and regressions. Remove self-reported "principles followed" as a scoring signal.

**Shared layer.** `~/.agents/pstack/local/evals/`, `sync.py` as the gate, and the agentmem fixture pattern.

**Size.** M. **Fault.** Yes. There are zero shared skill/prompt eval artifacts and no promotion gate; the baseline's "zero evals" claim is qualified: agentmem has frozen domain evals, but the shared skill and prompt layer has none.

**Replaces / deletes.** Replaces untested prose adoption and self-reported chain-following. Adds one `evals/` directory and one check call inside the existing sync, not a new tool.

**Depends on.** None for the first case set; C8 consumes the result artifact as its behavioral promotion evidence.

**Proof.** First demonstrate that a deliberately broken variant loses on the intended behavior, and that both variants actually did the task. Measure case pass rates, judge disagreement, repeated-run spread, cost, downstream failures and held-out before/after behavior; publish inconclusive results when noise dominates. Replay the same held-out cases after a sync.

**Closes.** 3, 8.

**Evidence.** [Codex §3](../../research/greats/diff-codex.md#3-evals-for-shared-skills-and-prompts); [Devin §8](../../research/greats/diff-devin.md#8-skills-and-prompt-design); [baseline correction](../../research/greats/diff-codex.md#evidence-rules-and-baseline-corrections).

---

## C7 — `correct` as the standing review-to-hard-rule ratchet

**Change.** Adopt `correct` as the standing post-review step: each accepted review finding is routed to become a hard check (type, lint, or test) rather than another prose rule. Write that into `herdr-team`'s review protocol beside the findings format. Add the paved-path `AGENTS.md` section per repo so constraints live where agents read them.

**Shared layer.** pstack `correct`, `herdr-team` review protocol, and the repo `AGENTS.md` template.

**Size.** S. **Fault.** Yes. `correct` has 0 invocations in 14 days; repeated review comments are not converting into checks; `AGENTS.md` is two lines.

**Replaces / deletes.** Replaces review comments as the only enforcement. Deletes nothing; one line in a skill file plus a repo template block.

**Depends on.** C6 for accepted review findings; can start with the current review protocol.

**Proof.** Count hard checks added per accepted finding (now ~0), repeated-mistake rate in later reviews, and review comments per 100 lines. Track `correct` invocations and the share of findings that become type, lint or test checks.

**Closes.** 6, 12.

**Evidence.** [Devin §12](../../research/greats/diff-devin.md#12-code-shape-and-constraints); [Codex §6](../../research/greats/diff-codex.md#6-review).

---

## C11 — WHY line and discard accounting

**Change.** Add `WHY:` to the `herdr-team` report format: intent, risk, how verified. Allow a brief to close as `STATUS: discarded` with a reason line, and record discarded attempts in `routing.jsonl` so attempts-per-kept-change becomes measurable.

**Shared layer.** `herdr-team` report format and hx routing/ledger.

**Size.** S. **Fault.** Yes. Reports have 0 WHY lines; discards are not visible, so salvage and retry are indistinguishable from success paths; comprehension debt is unpaid.

**Replaces / deletes.** Replaces the NOTES catch-all and invisible discards. Deletes nothing.

**Depends on.** None; C6 later consumes the disposition data.

**Proof.** Count WHY presence in reports (now 0), discarded attempts in routing outcomes, attempts per kept change, and comprehension probes. Track one deliberately larger task per sprint routed through hx to test whether ambition expands.

**Closes.** 10.

**Evidence.** [Devin §10](../../research/greats/diff-devin.md#10-ambition-autonomy-and-taste).

---

## C2 — Reusable control skill and feature map

**Change.** Use the existing `create-verification-skill` generator instead of creating another verification framework. A shared onboarding recipe emits the thin `.claude/skills/verify-<app>/` adapter plus a feature map and points Codex to it from `AGENTS.md`. Reuse each repo's own tests and launch scripts. Start with 3–5 important journeys, then expand from actual product routes. Replace repeated launch/seed/browser-slot prose in briefs with paths to the proven recipe. Store a small evidence reference in the existing task/checkpoint: snapshot, journey, command, outcome and artifact path. Unreachable prerequisites stay explicit; do not change the journey to get a pass.

**Shared layer.** pstack verification-skill generator, repo `.claude/skills/verify-<app>/`, and the repo `AGENTS.md` pointer.

**Size.** M.

**Replaces / deletes.** Replaces repeated manual QA recipes and launch/seed/browser prose. Deletes none; reuses the existing generator and repo scripts.

**Depends on.** None; C8 may carry the broader repo template.

**Proof.** Give a fresh verifier a vague user symptom and the feature map; measure successful reach/reproduction, time to first correct action, and manual questions. Run launch → doctor → drive → assert side effect → cleanup; verify evidence survives. Track completed tasks with a real acceptance artifact, feature-map journey coverage and post-done defects. Keep runner counts separate from coverage.

**Closes.** 2.

**Evidence.** [Codex §2](../../research/greats/diff-codex.md#2-verification); [baseline](../../research/greats/baseline.md).

---

## C10 — Portable handoff and memory note

**Change.** Port Matt's handoff mechanism into `~/.agents/pstack/local/handoff/SKILL.md` and unify it with the hx checkpoint: the checkpoint is the handoff artifact; use a repo-owned or workspace path under the existing team scope rather than copying Pocock's OS-temp location literally, include suggested skills, reference source artifacts by path or URL instead of copying them, redact secrets, and cover the four triggers (harness swap, repo move, colleague send, side fork). Add one line in `herdr-team` beside the report format: decisions get a `mem note` before the report is sent.

**Shared layer.** pstack local handoff skill, `herdr-team`, and agentmem.

**Size.** S.

**Replaces / deletes.** Replaces no existing handoff habit; deletes nothing. Uses agentmem and the existing checkpoint path, not a second store.

**Depends on.** C1 for the checkpoint/handoff substrate.

**Proof.** Re-measure vault writes per week (now ~5), handoff/checkpoint files per task (now ~0.07), and post-compact task-survival from session logs. Test a fresh agent's questions and time to first correct action after a handoff.

**Closes.** 7.

**Evidence.** [Devin §7](../../research/greats/diff-devin.md#7-memory-and-knowledge); [context audit §2](../../research/greats/context-management.md#2-what-hx-actually-implements).

---

## C6 — Review operation and acceptance record

**Change.** Use one hx review record on the existing task: artifact/base/head or working-tree content digest, acceptance contract, reviewer session and family, evidence, findings dispositions and final verdict. Give a fresh reviewer the artifact and contract; keep the author's CLAIM separate. For nontrivial acceptance, require a completed independent verdict covering the accepted snapshot; a later substantive edit invalidates it. Keep low-impact work proportional rather than creating a creator-facing Review card for every change. Reuse `interrogate` for reproducible correctness findings and the existing review/shipping standards for other concerns. Replace ad hoc "ready to merge" text as the sole durable gate. Feed repeated accepted findings into C7.

**Shared layer.** hx ledger/task, `herdr-team` review protocol, and pstack shipping.

**Size.** M.

**Replaces / deletes.** Replaces ad hoc "ready to merge" text as the only gate. Deletes duplicated review prose once structure and checks enforce it.

**Depends on.** C7 for repeated findings; C1 for fresh owners; C2 for verification evidence.

**Proof.** Count requested, started, completed, fix-needed and superseded rounds separately. Link verdicts to accepted snapshots before calculating coverage. Track author/reviewer family, fresh-session status, stale-verdict refusals, found and escaped defects, and repeated finding classes converted to checks. Use the 40-round recount as the request baseline; do not mistake it for a completed-review rate.

**Closes.** 6.

**Evidence.** [Codex §6](../../research/greats/diff-codex.md#6-review); [40-round recount](../../research/greats/diff-codex.md#appendix-review-recount-and-reproducibility).

---

## C9 — Project workflow skill for I-6

**Change.** Add `~/.agents/pstack/local/project-workflow/SKILL.md`: idea → optional prototype spike → `spec.md` → `plan.md` → task list → work + revision → docs note. Artifacts live under `.artifacts/<task>/`; briefs reference them by path. Reuse the existing briefs, ledger and playbooks. Keep the optional prototype-first path for the Felix/Theo case and the spec gate for the Addy case.

**Shared layer.** pstack local workflow skill, the repo `.artifacts/<task>/` convention, and the brief template.

**Size.** M.

**Replaces / deletes.** Replaces brief-only planning and chat-only amendments. Deletes nothing; it is one file and one convention, not a new pipeline.

**Depends on.** C10 for handoff and C6 for review; the first slice can use the existing briefs and ledger.

**Proof.** Count briefs that link a spec/plan artifact (now ~0), mid-flight amendment messages per task (now no trail), review rounds in `.hx/ledger.json`, task artifacts and ADR count. Check that optional prototype spikes are visible and that the spec gate is not skipped silently.

**Closes.** 4.

**Evidence.** [Devin §4](../../research/greats/diff-devin.md#4-planning-and-the-project-workflow-i-6); [baseline](../../research/greats/baseline.md).

---

## C8 — Single policy source and hx doctor

**Change.** Subtract stale duplicate rules first: derive shared-pool, model and permission facts from one versioned policy and emit short references in generated instructions. Correct contradictory review plumbing in its source, not in generated host copies. Extend a read-only doctor to report selected versus observed model, provider capabilities, required hooks and skills, context denominators and artifact availability; report unknown explicitly. Keep `sync` for distribution, with C3's eval artifact as its behavioral promotion evidence. Reuse ledger/checkpoint/run results for observability instead of adding a second state service. Provide a repo template for launch, verify, review and recovery contracts. Do not add the entire Addy pack alongside pstack as an overlapping second router.

**Shared layer.** pstack sync/source, hx doctor, and the team/repo templates.

**Size.** M. **Fault.** Yes. The adapters drift: the launch overlay pins Qwen while policy defaults to DeepSeek; the Devin prompt still puts review text in notes; the generated hx-router models per-model allowance while live usage models a shared pool.

**Replaces / deletes.** Replaces stale model/quota/review instructions and duplicate threshold prose. Deletes stale generated copies once the source is correct.

**Depends on.** C3 for the behavioral gate; C1 and C5 for the context and permission scopes.

**Proof.** Run the same small synthetic task/denial/review/compaction scenarios through each supported host adapter, then inspect artifacts and actual tool behavior. Measure configuration mismatch, unknown telemetry, hook restoration fidelity, startup context bytes, accidental denial rate and accepted-outcome rate with the model pinned. Change only the harness revision in a comparison. A file-tree sync check remains a distribution test, not the behavior verdict.

**Closes.** 11.

**Evidence.** [Codex §11](../../research/greats/diff-codex.md#11-harness-and-environment-design).

---

## C5 — Execution profiles and capability boundary

**Change.** Design two execution profiles: repo-only implementation/review with synthetic data and bounded network access, and a separately authorized broker for genuinely private or credential-bearing operations. Workers receive operation results, not credential material. Separate untrusted research acquisition from private-data tools. Preserve authorized local development and team messaging while restricting the actual process/filesystem/egress capability; command-string bans remain a secondary layer. Replace persistent project-trust mutation with an ephemeral or command-scoped mechanism where the host supports it. Remove redundant deny prose only after the hard boundary is demonstrated.

**Shared layer.** the shared launcher/team config and provider profiles.

**Size.** L.

**Replaces / deletes.** Replaces broad access plus command-string bans as the main boundary, and persistent project-trust mutation. Deletes redundant deny prose only after the boundary is live.

**Depends on.** C4 preflight and C8 policy source.

**Proof.** Use synthetic canaries in a disposable workspace and an authorized local sink to test the full trifecta: malicious fetched text, attempted out-of-scope read, attempted exfiltration. Verify filesystem/network denial at the execution boundary, including script-mediated calls, while ordinary build/test/team reports still work. Measure expected denials separately from accidental prerequisite failures. Test redaction and constraint weakening with synthetic fixtures; never use real credentials as probes. Adopt only after live provider-specific acceptance.

**Closes.** 9.

**Evidence.** [Codex §9](../../research/greats/diff-codex.md#9-safety).

---

## C12 — Metric-loop `program-md` template and pilot

**Change.** Add a `program-md` template in `~/.agents/pstack/local/` that generalizes autoresearch to any metric-driven experiment loop: setup, allowed edit surface, fixed budget, keep/discard metric and stop condition. Pilot it on the trading algorithm's backtests, the closest real surface. Reuse nanoGPT, nanochat, llm.c and micrograd as references, not imports.

**Shared layer.** pstack local template and one pilot directory.

**Size.** M.

**Replaces / deletes.** Replaces nothing; no loop exists. Deletes nothing.

**Depends on.** None.

**Proof.** Measure experiments per hour unattended, keep/discard/crash counts, `results.tsv` or ledger rows, and whether the loop ran overnight without human intervention. Add the pilot's own metric and cost.

**Closes.** 13.

**Evidence.** [Devin §13](../../research/greats/diff-devin.md#13-model-training-and-research-loops).

