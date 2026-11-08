# Difference analysis: context, verification, evals, delegation, review, safety and harness

Goal: compare the strongest gathered methods with what our shared setup actually does, then specify the changes that close the gaps. Playbook: **poteto-mode, Investigation**, using `how` inline as an assignee. Principles applied: **Prove It Works** (inspect mechanisms and execution evidence, not installed skill names), **Guard the Context Window** (bounded source slices and aggregate log extraction), and **Explain the Number** (separate requests, repeated requests, completed reviews and coverage). Throughput checkpoint: n/a, read-only investigation.

Scope: capabilities **1, 2, 3, 5, 6, 9, 11**. This document is the only file written for this assignment. The creator's blank-slate direction and shared-layer requirement are the decision rule, not a presumption that installed machinery works: `docs/greats/PROGRAM.md:3`, `:9`, `:23`.

## Overview and verdicts

| Capability | Verdict | Gap that determines the verdict |
|---|---|---|
| 1 Context management and handoff | **weaker** | Strong checkpoints and native recovery; early boundary decisions and verified continuation are not closed through the normal task lifecycle. |
| 2 Verification | **weaker** | Real runners and live checks exist; the reusable control skill plus maintained user-feature map is not established by the audit evidence. |
| 3 Evals for skills and prompts | **missing** | No observed shared-skill/prompt comparison-and-promotion loop; domain-specific memory evals do exist. |
| 5 Delegation and parallel agents | **weaker** | Bounded briefs, roles and ownership exist; permissions and persistent-context prerequisites are not reliably settled before dispatch. |
| 6 Review | **weaker** | Substantial pane-based review exists; durable, independent, contract-first verdicts tied to the accepted snapshot are not enforced. |
| 9 Safety | **weaker** | Real command/ownership checks and redaction exist; they do not remove the private-data/untrusted-input/egress combination at the execution boundary. |
| 11 Harness and environment design | **weaker** | Shared distribution and orchestration exist; provider adapters and lifecycle rules drift without an executable shared contract and behavior gate. |

**Count: 6 weaker, 1 missing, 0 implemented as they do it, 0 does not fit.** These are capability-level verdicts. Working submechanisms are credited below; “weaker” does not mean “absent.” No whole capability is rejected as a poor fit.

## Evidence rules and baseline corrections

1. **Read-time implementation and historical behavior are different evidence.** The earlier context report found the missing DeepSeek limit (`research/greats/context-management.md:64`). The current `policy.json:166` includes it at 200K, with native-versus-effective rationale at `:175`; it is no longer an outstanding missing entry. The controller remains explicit (`hx/__main__.py:128`) and completion does not invoke it (`hx/__main__.py:192`).
2. **A runner count is not task coverage.** The baseline records 339 Vitest, 296 `npm run check` and 56 E2E commands (`research/greats/baseline.md:17`); these are invocation counts, not unique tasks, successful runs, or user-feature coverage. The baseline itself cautions that zero Skill-tool calls can miss leaf files read directly (`:10`). Its “entire leaf layer is unused” heading must not become a finding of zero actual use.
3. **Review is materially undercounted by routing records.** The recount below identifies **45 actual shell dispatch messages requesting review or recheck, grouped into 40 rounds** after five duplicate/supersession reductions, for Jotarium and hx. This replaces the “about three routed reviews” proxy for review activity (`research/greats/baseline.md:26`). Requests are not completions. Dividing either 3 or 40 by 334 commits does not establish reviewed-commit coverage: reviews may cover multiple commits, design artifacts, or repeated fixes; queued requests may never complete.
4. **“Zero evals” needs a domain qualifier.** No shared pstack eval-artifact directory was present in the read-only inventory, consistent with the baseline's shared-skill claim (`research/greats/baseline.md:47`). But agentmem has frozen replay/adoption tooling and blinded package construction (`C:/Users/caspe/Documents/Projects/agent-memory/tools/evaluate_knowledge.py:1`, `:57`, `:604`; `tools/evaluate_set_r.py:1`, `:164`). Its checked-in decision report describes a sealed re-gate that failed safety and a disabled knowledge store (`C:/Users/caspe/Documents/Projects/agent-memory/docs/llmwiki-evaluation/WHY-NOT-LLMWIKI.md:19`, `:61`, `:75`). Those are existing domain evals and documented results, not a new run in this audit.
5. **Historical permission failures are scoped.** The baseline's 9 tool-denial failures among 21 routed outcomes are evidence for that routed sample, not a failure rate for all pane delegation (`research/greats/baseline.md:30`, `:32`). The current Codex team profile explicitly uses `danger-full-access` (`C:/Users/caspe/.codex/trusted-dev.config.toml:15`). Do not respond to the historical rate by disabling more protections.

## 1. Context management and handoff

### Their method

Combine pstack's small phases and fresh owners, Pocock's smart-zone boundaries and portable handoff, Addy's selective keep/cut/protect packing, and native harness token accounting. Keep raw exploration in isolated workers; bring concise evidence and artifact references back. At a boundary, preserve the objective, constraints, exact current failure, decisions and next action; compact or start fresh before the useful attention budget is consumed. Sources: [Lauren's pstack](lauren-tan.md#pstack--the-skill-plugin-itself), [Pocock's smart zone](matt-pocock.md#the-smart-zone--dumb-zone--context-window-economics), [portable handoff](matt-pocock.md#handoff--portable-context-as-a-file), [Addy's context engineering](addy-osmani.md#context-engineering), [native mechanisms](context-management.md#1-how-the-named-methods-work).

**Disagreements:** Pocock's roughly 150K is a heuristic, not a universal native limit. Addy does not prescribe one percentage across hosts. A portable handoff switches session/harness/owner; compaction reduces an existing session. Their outputs can share an artifact schema without making them the same operation. Sources: [smart zone](matt-pocock.md#the-smart-zone--dumb-zone--context-window-economics), [handoff](matt-pocock.md#handoff--portable-context-as-a-file), [context engineering](addy-osmani.md#context-engineering).

### Our method, exactly

- The shared adapter prescribes fresh owners at task boundaries and context-aware roles, while the feature playbook explicitly says to spawn a fresh owner rather than chain interruptions (`C:/Users/caspe/.agents/pstack/TEAM.md:21`, `:58`; `C:/Users/caspe/.agents/pstack/upstream/skills/poteto-mode/playbooks/feature.md:19`). Global instructions tell Claude, Codex and OpenCode to load this adapter (`C:/Users/caspe/.claude/CLAUDE.md:10`; `C:/Users/caspe/.codex/AGENTS.md:9`; `C:/Users/caspe/.config/opencode/AGENTS.md:10`). Those are instructions, not measured adherence.
- hx samples provider-specific session telemetry; OpenCode's denominator comes from policy (`hx/context.py:167`, `:217`). Worker boundary/pressure thresholds are 0.40/0.75, while the lead has a 400K effective budget and a 0.60 boundary (`policy.json:164`, `:176`). Current DeepSeek mapping is repaired; automatic resolution for a newly selected model is still not demonstrated by that lookup (`hx/context.py:217`).
- Checkpoints require authored sections, matching task revision and owned-file status/committed changes (`hx/checkpoint.py:466`, `:518`). The compaction executor requires lower fresh telemetry in the same session, then a nonce plus next-action acknowledgement (`hx/context.py:579`, `:592`). These are valuable implemented submechanisms.
- The ordinary completion path validates the checkpoint but stops after printing the task (`hx/__main__.py:192`). `hx-router` asks the lead to run a context decision at every boundary (`C:/Users/caspe/.codex/skills/hx-router/SKILL.md:48`). `hx say` warns and still sends; unknown/stale telemetry is tolerated (`hx/say.py:37`, `:54`, `:60`). PreCompact/SessionStart hooks restore existing state after compaction rather than create an early decision (`hooks/claude_precompact.py:1`; `hooks/claude_session_compact.py:132`).
- The measured project inventory contains 13 decisions and two refused compaction attempts, not zero calls. Native recovery occurred independently: seven Codex markers and an OpenCode reduction from 967,737 to 41,893 input-plus-cache tokens; these are not verified hx handoff successes (`research/greats/context-management.md:92`, `:124`, `:125`, `:140`). Devin's verifier needs provider-specific sequencing because the context footer disappears until another turn (`docs/audit/devin.md:65`; `hx/context.py:579`).

### Verdict: weaker

The gap is reliable early invocation and continuation, not the absence of compaction. Do not weaken worktree/session guards or count a lower token total as proof of preserved task meaning. The 89%-pressure example delivered another assignment after warning (`research/greats/context-management.md:112`).

### The change — M, shared hx lifecycle and provider adapters

Replace the lead's repeated “remember to decide” choreography with one boundary operation using the existing ledger and checkpoint: finish/validate → record provider/native/effective counters → decide → checkpoint/compact or fresh-owner handoff → verify continuation → admit the next assignment. Persist `keep`, `defer`, unknown telemetry and refusals as well as success. Keep lease ownership and worktree checks intact. Use an explicit readiness state so a refusal cannot silently become another ordinary brief. Keep implementation short-lived at the boundary; no always-running poller is required.

Adapt Devin's acknowledgement/telemetry order before enabling native compaction; make the lead's per-work-item loop guard distinct from a whole-session ceiling. Consolidate the duplicate threshold prose into a policy reference. Reuse checkpoint sections for portable handoff and reference source artifacts rather than copying them. Use a repo-owned artifact path under the existing team scope, rather than copying Pocock's OS-temp location literally (`C:/Users/caspe/.codex/skills/herdr-team/SKILL.md:36`).

### Proof

Repeat the context audit with an event denominator: eligible completed boundaries in the **same** session/task scope. Measure decisions/boundaries, unknown-limit rate, pressure→recovery latency, tokens before/after, refusal disposition and fresh-owner time to first correct action. Plant synthetic constraints/current-error/next-action facts and test their survival after native compaction and fresh handoff. Require every eligible boundary to have a recorded disposition; a deferred busy worker is not a failed compaction or a successful recovery. Compare native and effective percentages with their denominators displayed (`research/greats/context-management.md:131`, `:137`, `:138`).

## 2. Verification

### Their method

Lauren's control skill includes launch, health check, user-path driving, evidence and cleanup, backed by a feature map that says how to reach and prove each feature. Maintain it with a source pass plus a live pass. Combine this with Addy's failing reproduction and Simon's insistence on running and inspecting what was generated. Sources: [verification and feature map](lauren-tan.md#verification-skills--the-feature-map), [TDD](addy-osmani.md#test-driven-development), [test what it writes](simon-willison.md#test-what-it-writes-then-take-over).

**Disagreements:** a compiler is a strong cheap guardrail, but it does not replace a user journey. Browser QA does not replace invariant tests. Karpathy's training baselines are the corresponding proof for a training artifact, not a reason to require GPU metrics for this CLI. Sources: [types](matt-pocock.md#types-as-the-ai-guardrail), [DevTools](addy-osmani.md#chrome-devtools-mcp), [training recipe](andrej-karpathy.md#a-recipe-for-training-neural-networks).

### Our method, exactly

The upstream generator and maintenance procedures are already installed inputs: the generator prefers existing Playwright/PTY/HTTP harnesses, creates a feature map, and must execute its generated instructions (`C:/Users/caspe/.agents/pstack/upstream/skills/create-verification-skill/SKILL.md:17`, `:25`, `:34`, `:40`); maintenance requires a live pass over every feature (`.../maintain-verification-skill/SKILL.md:25`, `:33`). The team adapter maps Cursor paths/control terminology to project-local verification skills (`C:/Users/caspe/.agents/pstack/TEAM.md:63`).

Observed verification is stronger than “it compiles”: the baseline has real E2E commands (`research/greats/baseline.md:17`); the Q-181 review actually inspected screenshots and ran focused tests, then found an owned-once veil error (`[C6363]:7293`, source aliases below). However, the baseline reports no generated verification-skill artifacts (`research/greats/baseline.md:10`); this audit's read-only glob of `.claude/skills/*verify*` and `.codex/skills/*verify*` in hx found none. That is a scoped absence, not a scan of every project on the laptop.

hx's success classification is a run-protocol check, not acceptance: terminal completion can return no failure without running the feature's user contract (`hx/ocrun.py:831`, `:852`). Completion validates checkpoint text and files; its `tests` section is authored evidence, not an executed acceptance runner (`hx/__main__.py:194`; `hx/checkpoint.py:470`).

### Verdict: weaker

Real execution is implemented in practice. The gap is a reusable, reachable feature map and evidence tied to the task's actual acceptance surface, rather than relying on the lead to reinvent the recipe for each brief.

### The change — M, pstack onboarding plus repo template

Use the existing generator instead of creating another verification framework. A shared onboarding recipe emits the thin `.claude/skills/verify-<app>/` adapter plus a feature map and points Codex to it from `AGENTS.md`. Reuse each repo's own tests and launch scripts. Start with 3–5 important journeys, then expand from actual product routes. Replace repeated launch/seed/browser-slot prose in briefs with paths to the proven recipe. Store a small evidence reference in the existing task/checkpoint: snapshot, journey, command, outcome and artifact path. Unreachable prerequisites stay explicit; do not change the journey to get a pass.

### Proof

Give a fresh verifier a vague user symptom and the feature map; measure successful reach/reproduction, time to first correct action, and manual questions. Run launch→doctor→drive→assert side effect→cleanup; verify evidence survives. Track completed tasks with a real acceptance artifact, feature-map journey coverage and post-done defects. Compare these with the baseline's runner counts, while retaining their different units (`research/greats/baseline.md:17`, `:18`).

## 3. Evals for shared skills and prompts

### Their method

Use pstack's blinded variant comparison as the promotion loop: organic task, isolated project-shaped workspace, cases/rubric held from the candidate, separate blinded judge, transcript-derived behavior, repeated cases and a before/after decision. Add Pocock's edge cases and Karpathy's exact verifiers where available; use model judging for what cannot be checked mechanically. Sources: [Lauren's evals](lauren-tan.md#evals-as-unit-tests-for-skills--hill-climbing), [Pocock's behavior eval](matt-pocock.md#evals-check-behavior-not-execution--skeptic-converted), [Theo's council](theo-browne.md#turn-fuzzy-goals-into-evals-hill-climb-with-a-council), [Addy's pack](addy-osmani.md#the-agent-skills-pack), [Karpathy's verifiability](andrej-karpathy.md#agentic-engineering).

**Disagreements:** a model council is not an exact verifier, and an exact test does not judge taste. Use the cheapest valid verifier for each criterion; calibrate subjective judges and do not hill-climb one exposed example into overfitting. Sources: [Theo](theo-browne.md#turn-fuzzy-goals-into-evals-hill-climb-with-a-council), [Karpathy](andrej-karpathy.md#agentic-engineering).

### Our method, exactly

The pinned upstream eval playbook already specifies blinding, organic prompts, a separate model-family judge and transcript inspection (`C:/Users/caspe/.agents/pstack/upstream/skills/poteto-mode/playbooks/eval.md:5`, `:17`, `:20`, `:22`). Skill distribution builds and compares generated file trees; it has no behavior-evaluation step (`C:/Users/caspe/.agents/pstack/sync.py:87`, `:116`, `:168`). Its `--check` is not a safe read-only behavior probe: it creates and removes staging directories (`:124`, `:140`), so this assignment did not run it.

The baseline found no shared eval artifacts (`research/greats/baseline.md:47`); this audit confirmed that `C:/Users/caspe/.agents/pstack/evals/` is absent. That supports **no observed shared-skill promotion loop**, not “nobody has ever evaluated anything.” Agentmem's frozen fixtures, isolated baseline/reliability arms and blinded package builder are useful precedent, with documented limitations on what a replay can establish (`C:/Users/caspe/Documents/Projects/agent-memory/tools/evaluate_knowledge.py:57`, `:604`, `:707`; `tools/evaluate_set_r.py:164`).

### Verdict: missing

The missing subject is shared skill/prompt behavior and its adoption gate. Installing a second skill pack or counting `Skill` calls would not close it.

### The change — M, pstack promotion workflow

Reuse the existing eval playbook and agentmem's fixture/hash/provenance pattern; do not build a second orchestration engine. Add a small versioned case set for boundary recovery, truthful verification, review independence, denied-tool repair and skill routing. Compare the currently installed variant with one candidate in sanitized workspaces; hide version/model labels from the judge and rubric from candidates. Check known invariants deterministically, then judge remaining behavior. Keep sealed regression cases and repeated runs. Promote a shared revision only with a result artifact containing cases, raw evidence references, model/harness versions, spread and regressions. Remove self-reported “principles followed” as a scoring signal: the upstream eval explicitly forbids chain-eliciting candidate prompts (`.../playbooks/eval.md:9`, `:22`).

### Proof

First demonstrate that a deliberately broken variant loses on the intended behavior and that both variants actually did the task. Measure case pass rates, judge disagreement, repeated-run spread, cost and downstream failures; publish inconclusive results when noise dominates. Replay the same held-out cases after a sync. Establish a pre-change score before claiming improvement; the current baseline cannot provide it (`research/greats/baseline.md:47`).

## 5. Delegation and parallel agents

### Their method

Use a planner/owner to partition independent artifacts, bound the contract and give each worker an isolated workspace and fresh task context. Return concise evidence; review independently. Start with trusted local verification before multiplying workers. Simon's four useful parallel patterns and Lauren's trust ladder support this ordering. Sources: [parallel coding agents](simon-willison.md#parallel-coding-agents), [trust ladder](lauren-tan.md#the-trust-curve--trust-ladder-how-she-got-to-auto-merge), [Pocock's context isolation](matt-pocock.md#the-smart-zone--dumb-zone--context-window-economics), [Addy's harness](addy-osmani.md#agent-harness-engineering).

**Disagreements:** Lauren's auto-merge is the last trust stage; Simon emphasizes review capacity, and Karpathy keeps an autonomy slider. These do not require adopting auto-merge to adopt isolated workers and bounded contracts. Sources: [trust ladder](lauren-tan.md#the-trust-curve--trust-ladder-how-she-got-to-auto-merge), [parallel agents](simon-willison.md#parallel-coding-agents), [autonomy slider](andrej-karpathy.md#partial-autonomy-and-the-autonomy-slider).

### Our method, exactly

Role families, no re-delegation by assignees, own-team messaging, ≤10-line briefs and file/worktree ownership are explicit (`C:/Users/caspe/.agents/pstack/TEAM.md:21`, `:58`; `C:/Users/caspe/.codex/skills/herdr-team/SKILL.md:13`, `:24`, `:37`). Ledger checks reject conflicting file claims in the same worktree and validate claimed worktrees (`hx/ledger.py:431`, `:452`, `:498`). Session leases reject another holder or an unresolved compaction (`hx/ledger.py:698`). These are implemented controls, not just conventions.

The routing failure branch distinguishes tool/input problems from capability failure (`hx/route.py:788`). Nevertheless, 9/21 recorded routed outcomes failed on permission denials and two tasks repeated the same cause (`research/greats/baseline.md:32`, `:33`). Heavy pane traffic and the review inventory below prove that routing outcomes cover only one path. Persistent task chaining is directly visible: after a review the lead queues another before compaction (`[C6363]:5299`), while a later assignment explicitly requests a fresh OpenCode session (`[C6363]:8148`).

### Verdict: weaker

Parallel ownership is substantially implemented. The gap is preparing a worker that can execute its particular contract, then controlling persistent-context reuse and review load. “More agents” does not repair a denied prerequisite.

### The change — M, hx admission and pstack brief contract

Before an expensive dispatch, validate worktree/owned files, required commands and named network destinations against the selected provider's actual capability profile. Use non-mutating probes only where inspection cannot settle availability. Refuse or repair missing prerequisites before model work. Do not probe by executing a forbidden action or bypass an owner denial. Attach one concise capability result and artifact contract to the existing task. Default new work to a fresh owner; allow persistent reuse for a stated live-state benefit with a context-boundary disposition. Replace repeated same-cause retries and overlapping provider-denial prose with one maintained capability source.

### Proof

Repeat the routed-outcome measurement, separately from pane tasks: permission failures, useful completions, retries by cause, wait time and review time per accepted artifact. Add parent context growth per returned artifact and file-conflict counts. Replay the two documented denial cases against the admission check and require refusal before launch, without weakening the authorization boundary (`research/greats/baseline.md:32`, `:33`).

## 6. Review

### Their method

Use Addy's CLAIM→EXTRACT→DOUBT→RECONCILE→STOP cycle: the fresh reviewer receives artifact plus contract, not the author's claim/rationale, and findings are classified rather than accepted reflexively. Pair it with Lauren's small review units and conversion of repeated findings into structural checks. Simon and Karpathy preserve accountable understanding. Sources: [doubt-driven development](addy-osmani.md#doubt-driven-development), [atomic PRs](lauren-tan.md#atomic-prs-and-git-history-as-context), [Dune](lauren-tan.md#dune--constraint-as-code-architecture-for-agent-written-apps), [vibe engineering](simon-willison.md#vibe-engineering-later-agentic-engineering), [agentic engineering](andrej-karpathy.md#agentic-engineering).

**Disagreements:** review on main after earned auto-merge versus accountable pre-merge review; proof-only adversarial review versus normal judgment of maintainability/trade-offs. Keep separate outcomes for reproducible defects and reasoned design concerns. Sources: [trust ladder](lauren-tan.md#the-trust-curve--trust-ladder-how-she-got-to-auto-merge), [doubt-driven development](addy-osmani.md#doubt-driven-development).

### Our method, exactly

Cross-family review is explicitly the point of a panel (`C:/Users/caspe/.agents/pstack/TEAM.md:43`). `interrogate` admits findings only when a run proves the failure (`C:/Users/caspe/.codex/skills/interrogate/SKILL.md:10`, `:20`, `:24`). Shipping requires a non-author verifier and snapshot validity checks (`C:/Users/caspe/.agents/pstack/upstream/skills/poteto-mode/playbooks/shipping.md:5`, `:7`). The `correct` skill already directs structural treatment of repeated mistakes (`.../skills/correct/SKILL.md:15`). These are existing methods to activate, not missing skills to reinstall.

Practice includes **40 distinct requested rounds**, detailed below, and useful actual findings: Q-181's owned-once issue at `[C6363]:7293` produced a fix task at `:7301` and a completion report at `:7333`; Q-182's review found an old-save badge backfill defect even while 76 focused tests passed (`:7497`). The review gap is therefore not adequately described as “thin because only three tasks.”

There is still no acceptance gate in the inspected task close path requiring a matching independent verdict: a task starts with `review: None` (`hx/ledger.py:513`), `set_status` can close it (`:631`), and `task done` validates checkpoint/file state rather than reviewer identity or accepted snapshot (`hx/__main__.py:192`). A reviewer can be assigned a large mutable working-tree scope and then have the author change it concurrently; one actual addendum narrows a live review while the author rewrites UI (`[C6363]:6402`). Independence of model family does not itself prove a fresh context or stable artifact.

### Verdict: weaker

Review exists and finds defects. The missing guarantees are artifact/contract isolation, freshness, completed-verdict accounting and a demonstrated feedback ratchet. Completed review coverage is **unknown**, not single-digit percent.

### The change — M, shared review operation and acceptance record

Use one hx review record on the existing task: artifact/base/head or working-tree content digest, acceptance contract, reviewer session/family, evidence, findings dispositions and final verdict. Give a fresh reviewer the artifact and contract; keep the author's CLAIM separate. For nontrivial acceptance, require a completed independent verdict covering the accepted snapshot; a later substantive edit invalidates it. Keep low-impact routine work proportional rather than creating a creator-facing Review card for every change. Reuse `interrogate` for reproducible correctness findings and the existing review/shipping standards for other concerns. Replace ad hoc “ready to merge” text as the sole durable gate. Feed repeated accepted findings into the existing `correct` mechanism; delete duplicated prose once structure/checks enforce it.

### Proof

Count requested, started, completed, fix-needed and superseded rounds separately. Link verdicts to accepted snapshots before calculating coverage. Track author/reviewer family, fresh-session status, stale-verdict refusals, found/escaped defects and repeated finding classes converted to checks. Use the pane recount below as the request baseline; do not mistake it for a completed-review rate.

## 9. Safety

### Their method

Prefer Simon's architectural separation: do not combine private-data access, untrusted material and arbitrary external communication in one execution principal. Use scoped test credentials and isolated execution, then add Lauren's hard checks and Addy's security floor/scanning. Sources: [lethal trifecta](simon-willison.md#the-lethal-trifecta-for-ai-agents), [agentic loops](simon-willison.md#designing-agentic-loops), [Dune](lauren-tan.md#dune--constraint-as-code-architecture-for-agent-written-apps), [constraints](addy-osmani.md#constraint-driven-development).

**Disagreements:** scanning and prompts improve safety but do not provide Simon's separation guarantee. Theo's criticism of fragmented `.env` sharing is not permission to expose credentials. Sources: [trifecta](simon-willison.md#the-lethal-trifecta-for-ai-agents), [skeuomorphism test](theo-browne.md#skeuomorphism-test-familiar-tools-as-inherited-constraints).

### Our method, exactly

Global instructions and the team contract restrict scope and secret handling (`C:/Users/caspe/.codex/AGENTS.md:16`, `:22`; `C:/Users/caspe/.codex/skills/herdr-team/SKILL.md:36`). hx constrains message target/header identity (`hx/say.py:30`, `:79`) and ledger ownership (`hx/ledger.py:431`). Agentmem redacts before its model/write pipeline (`C:/Users/caspe/Documents/Projects/agent-memory/README.md:17`, `:21`; installed `C:/Users/caspe/.agents/memory/app/agentmem/pipeline.py:26`). These protect particular paths, not every shell/tool action.

The persistent OpenCode overlay allows arbitrary bash by default, with raw Herdr calls asking (`team/opencode-trusted-dev.jsonc:7`). Devin permits exec and explicitly documents that command-string denials are accident guardrails, not a sandbox (`team/devin-worker.config.json:25`, `:30`). Codex's trusted-dev profile uses `danger-full-access` (`C:/Users/caspe/.codex/trusted-dev.config.toml:15`); the launcher selects it at `team/Start-HerdrTeam.ps1:73`. It also appends persistent project trust at `:63`–`:70`, which conflicts in mechanism with the global preference for command-scoped alternatives (`C:/Users/caspe/.codex/AGENTS.md:16`). No actual exfiltration incident or successful adversarial safety test was established by this audit.

### Verdict: weaker

Instructions, command rules and redaction do not make filesystem/egress separation true. The historical denial failures are not evidence that broad unsandboxed access is the right long-term design.

### The change — L, shared launcher and capability boundary

Design two execution profiles: repo-only implementation/review with synthetic data and bounded network access, and a separately authorized broker for genuinely private or credential-bearing operations. Workers receive operation results, not credential material. Separate untrusted research acquisition from private-data tools. Preserve authorized local development and team messaging while restricting the actual process/filesystem/egress capability; command-string bans remain a secondary layer. Replace persistent project-trust mutation with an ephemeral/command-scoped mechanism where the host supports it. Remove redundant deny prose only after the hard boundary is demonstrated. This is an implementation-stage design/owner decision, not permission to change today's granted trusted profile in this read-only task.

### Proof

Use synthetic canaries in a disposable workspace and an authorized local sink to test the full trifecta: malicious fetched text, attempted out-of-scope read, attempted exfiltration. Verify filesystem/network denial at the execution boundary, including script-mediated calls, while ordinary build/test/team reports still work. Measure expected denials separately from accidental prerequisite failures. Test redaction and constraint weakening with synthetic fixtures; never use real credentials as probes. Adopt only after live provider-specific acceptance shows the boundary works.

## 11. Harness and environment design

### Their method

Treat the model-plus-harness as the product: version prompts, tools, context policy, hooks, execution scope, feedback and recovery together. Turn observed failures into the smallest enforceable rule; keep successful checks quiet and failures actionable; delete obsolete scaffolding. Sources: [Addy's harness](addy-osmani.md#agent-harness-engineering), [Theo's harness](theo-browne.md#the-harness-is-the-product-judgment-over-typing), [Karpathy's LLM OS](andrej-karpathy.md#llm-os-model-as-kernel-context-as-ram-tools-as-peripherals), [agentic loops](simon-willison.md#designing-agentic-loops).

**Disagreements:** OS metaphor, product strategy, and Dune's application architecture are compatible lenses, not interchangeable implementations. Do not transplant Cursor cloud-only commands onto Windows/Herdr literally; preserve the mechanism with provider-native execution. Sources: [pstack](lauren-tan.md#pstack--the-skill-plugin-itself), [harness](addy-osmani.md#agent-harness-engineering).

### Our method, exactly

Shared generation is real: upstream/local skills feed Claude, Codex and Devin, and agent wrappers feed Claude/OpenCode (`C:/Users/caspe/.agents/pstack/sync.py:19`, `:87`, `:106`, `:168`). The upstream pin is `180939f9e59cc9512e9330649175763e81fad16f`, 3 November (`C:/Users/caspe/.agents/pstack/upstream/UPSTREAM:1`). Six local skill directories were present in the read-only inventory: herdr-team, hx-router, memory, obsidian, qmd, remember. The team adapter defines provider terminology and roles (`C:/Users/caspe/.agents/pstack/TEAM.md:49`). Agentmem's installer has Claude/Codex lifecycle hooks (`C:/Users/caspe/Documents/Projects/agent-memory/install.py:84`, `:99`); hx separately has compaction restore hooks (`team/claude-settings.json:60`). Installation code is not proof every current hook fired successfully.

The adapters still contain contradictory behavior. The generated hx-router says OpenCode models each have their own allowance (`C:/Users/caspe/.codex/skills/hx-router/SKILL.md:11`), while live usage code models a shared pool (`hx/usage.py:245`). The team OpenCode launch overlay pins `qwen3.7-plus` (`team/opencode-trusted-dev.jsonc:4`), while policy's persistent default is DeepSeek (`policy.json:117`): this is configuration drift, not proof the current pane is using Qwen. The Devin prompt generator still instructs review text in notes (`hx/ocrun.py:1146`), conflicting with the global typed-review rule (`C:/Users/caspe/.codex/AGENTS.md:25`). The observed permission failures and explicit-only context trigger are further harness integration evidence (`research/greats/baseline.md:32`; `hx/__main__.py:192`).

### Verdict: weaker

The shared delivery mechanism is implemented. Coherent behavior across providers is not proved by copying identical files: lifecycle ordering, permissions, live-model resolution and accepted output contracts need executable tests.

### The change — M, pstack adapter source and hx doctor/template

Subtract stale duplicate rules first: derive shared-pool/model/permission facts from one versioned policy and emit short references in generated instructions. Correct contradictory review plumbing in its source rather than editing generated host copies. Extend a read-only doctor to report selected versus observed model, provider capabilities, required hooks/skills, context denominators and artifact availability; report unknown explicitly. Keep `sync` for distribution, with the capability-3 eval artifact as its behavioral promotion evidence. Reuse ledger/checkpoint/run results for observability instead of adding a second state service. Provide a repo template for launch, verify, review and recovery contracts. Do not add the entire Addy pack alongside pstack as an overlapping second router.

### Proof

Run the same small synthetic task/denial/review/compaction scenarios through each supported host adapter, then inspect artifacts and actual tool behavior. Measure configuration mismatch, unknown telemetry, hook restoration fidelity, startup context bytes, accidental denial rate and accepted-outcome rate with the model pinned. Change only the harness revision in a comparison. A file-tree sync check remains a distribution test, not the behavior verdict (`C:/Users/caspe/.agents/pstack/sync.py:116`).

## Highest-impact implementation order

| Rank | Shared change | Size | What it replaces | Acceptance signal |
|---|---|---|---|---|
| 1 | Boundary-driven context/recovery and next-assignment admission | M | Manual boundary reminders and warn-then-send as the only pressure response | Every eligible boundary has a durable disposition; synthetic task facts survive recovery. |
| 2 | Blinded shared-skill/prompt promotion eval | M | Untested prose adoption and self-reported chain-following | Broken variants lose; held-out before/after behavior improves beyond repeat-run noise. |
| 3 | Capability-scoped worker admission and execution profiles | L overall; M preflight slice | Same-cause denial retries, broad access plus command-string bans as the main boundary | Required tasks run; synthetic out-of-scope read/egress probes fail at the actual boundary. |
| 4 | Reusable control skill/feature map plus snapshot-bound review record | M per shared mechanism; S initial repo recipe | Repeated manual QA recipes and unbound “ready to merge” prose | Fresh verifier reaches the feature; independent verdict covers the accepted snapshot. |
| 5 | Generated instruction cleanup and provider doctor | M | Stale model/quota/review instructions copied across hosts | One declared policy; cross-provider scenarios show consistent intended behavior. |

Ranks describe proposed work, not changes performed in phase 3. “Their approach wins” here means adopting demonstrated mechanisms and verification, not copying a heuristic threshold, temp directory, cloud command or auto-merge stage without its prerequisites.

## Appendix: review recount and reproducibility

### Scope and method

Read-only scan of Claude JSONL tool-use records dated **2026-10-25 through 2026-11-08**, with 44 files containing in-window tool calls. The review finding uses the explicit line inventory below, not a regex-hit total. This recount does not attempt to reproduce the baseline's whole-file skill/runner invocation totals (`research/greats/baseline.md:7`).

For each Bash `tool_use`, remove heredoc payloads, tokenize shell text with Python `shlex.split`, and extract real `hx say <recipient> <message>` and `herdr agent prompt <recipient> <message>` token sequences. Inspect candidates manually. Exclude reports to the lead, review-screen implementation, art/review paths, correction implementation tasks, FYIs, cancelled unrelated work and this assignment. Restrict the final inventory to Jotarium/hx reviews; exclude website-only and trading-project reviews. Include rechecks of prior findings. Do not execute any extracted command. One shell block in the broad scan was unparseable, so the final count is a **documented lower bound**, not an exhaustive machine-wide total.

The 40 rows below account for **45 dispatch messages**. Five extra messages disappear when grouping: q130/q136 resend (1), phase-2 fix recheck resend (1), security brief reminder (1), and the zoom/achievement/delete-idea review queue's two superseding/correcting messages (2). A new fix recheck stays a new round. No queued request is assumed completed.

### Source aliases

- `[C6363]` = `C:/Users/caspe/.claude/projects/C--Users-caspe/6363b92e-c30b-4a81-a5d4-d382a2848927.jsonl`
- `[C9633]` = `C:/Users/caspe/.claude/projects/C--Users-caspe/9633d81c-2b0a-4528-bbf7-753635f6f109.jsonl`
- `[C1665]` = `C:/Users/caspe/.claude/projects/C--Users-caspe/1665c115-acff-4ad7-9d50-067c838a0a00.jsonl`
- `[C7fd7]` = `C:/Users/caspe/.claude/projects/C--Users-caspe/7fd72b39-ed51-4abf-be1c-cda15809f29b.jsonl`
- `[Cdab6]` = `C:/Users/caspe/.claude/projects/C--Users-caspe/dab6c5ce-9924-482a-9bcc-77d010b3131c.jsonl`
- `.../skills/` in pstack references above expands to `C:/Users/caspe/.agents/pstack/upstream/skills/` unless a generated `C:/Users/caspe/.codex/skills/` path is explicitly named.
- In the agentmem eval discussion, abbreviated `tools/` and `docs/` paths expand to `C:/Users/caspe/Documents/Projects/agent-memory/`.

### Distinct review/recheck request rounds

| # | Artifact / round | Recipient | Actual dispatch source line(s) |
|---|---|---|---|
| 1 | Quick-capture clientRef | Codex | `[C1665]:16456` |
| 2 | Q-130/Q-136 | Codex | `[C6363]:320`, `:334` (resend) |
| 3 | Garden UI | Codex | `[C6363]:931` |
| 4 | Jotarium rename phase 2 | Codex | `[C6363]:2210` |
| 5 | Phase-2 fix reproduction/recheck | Codex | `[C6363]:2310`, `:2539` (resend) |
| 6 | Q-160 local-auth/shutdown security | Codex | `[C6363]:3558`, `:3598` (brief reminder) |
| 7 | hx route/balancing | Codex | `[C6363]:4742` |
| 8 | Q-150/Q-166 UI | Codex | `[C6363]:4864` |
| 9 | Q-142 whole-branch merge readiness | Codex | `[C6363]:5131` |
| 10 | Q-142 merge-blocker recheck | Codex | `[C6363]:5299` |
| 11 | Q-174 round-2 gear | Devin | `[C6363]:6131` |
| 12 | Animated-companions serving/ship path | OpenCode | `[C6363]:6349` |
| 13 | Q-172 spoiler veil | OpenCode | `[C6363]:6704` |
| 14 | Q-179 blockers, with Q-180 addendum | Codex | `[C6363]:6722` (addendum `:6739` is not another request) |
| 15 | Q-177 gear collection | OpenCode | `[C6363]:6854` |
| 16 | Q-179 second-round recheck | Codex | `[C6363]:7024` |
| 17 | Q-181/Q-183 | OpenCode | `[C6363]:7120` |
| 18 | Q-182 balance | Codex | `[C6363]:7417` |
| 19 | Q-182 fix round | OpenCode | `[C6363]:7593` |
| 20 | Q-185 market | OpenCode | `[C6363]:8148` |
| 21 | Q-184 gear layers | Devin | `[C6363]:8305` |
| 22 | Q-163 backups/restore authorization | Devin | `[C6363]:8543` |
| 23 | Q-157/Q-186/Q-187 combined queue | OpenCode | `[C6363]:8489`, `:8713`, `:8720` (last two supersede/correct scope) |
| 24 | Q-188 follow-ups | OpenCode | `[C6363]:8877` |
| 25 | Q-165 release:rc | OpenCode | `[C6363]:9343` |
| 26 | Q-192 UI fix | Devin | `[C6363]:9465` |
| 27 | Planning baseline, pass 5 | Codex | `[C7fd7]:367` |
| 28 | Final planning baseline | Codex | `[C7fd7]:1514` |
| 29 | Three planning blockers, fix confirmation | Codex | `[C7fd7]:1567` |
| 30 | Q-123 | Codex | `[C9633]:597` |
| 31 | D-77 design | Codex | `[C9633]:929` |
| 32 | D-77 slice 1 | Codex | `[C9633]:1117` |
| 33 | D-77 fix/slice 2 | Codex | `[C9633]:1260` |
| 34 | D-77 final review | Codex | `[C9633]:1543` |
| 35 | Q-129/Q-130/Q-131 | Codex | `[C9633]:1669` |
| 36 | D-77 last fix check | Codex | `[C9633]:1709` |
| 37 | Q-129/Q-130/Q-131 fix and XP cap | Codex | `[C9633]:1975` |
| 38 | Q-129 final fix confirmation | Codex | `[C9633]:2045` |
| 39 | Two-branch launch review | Codex | `[C9633]:7305` |
| 40 | H1–H3 cross-family integration | Codex | `[Cdab6]:146` |

All 45 listed sends used `hx say`. The broad extraction also checked raw `herdr agent prompt`; no additional in-scope review request was established from that channel by this inventory. Reports arriving through pane prompts establish some completed reviews, such as `[C6363]:7293` and `:7497`, but a complete matched completion/merge denominator was not reconstructed. That is the measurement gap the proposed review record closes.
