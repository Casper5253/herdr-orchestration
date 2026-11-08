# Capability matrix (phase 3 prep)

Goal: turn the phase 1 gather into capabilities, show each person's method against each capability, name agreement and disagreement, and state what would have to be measured in our own setup for an honest phase 3 comparison. This is a read-only preparation document. It does not judge our implementation and it changes nothing outside `research/greats/`.

Inputs: `lauren-tan.md`, `matt-pocock.md`, `theo-browne.md`, `andrej-karpathy.md`, `felix-rieseberg.md`, `simon-willison.md`, `addy-osmani.md`, and `context-management.md`. Method links use `file.md#heading`; the heading text is the source of the anchor.

Capability count: **13**. Strongest cross-person agreement: **verification**, **context management and handoff**, and **evals**; in each, the disagreement is about mechanism, not the goal.

## Capability summary

| # | Capability | People with a method | Agreement |
|---|---|---|---|
| 1 | Context management and handoff | Lauren, Matt, Addy, Simon, Karpathy, plus native harness evidence | Strong on scarcity and explicit handoff; split on thresholds |
| 2 | Verification (proving work runs) | Lauren, Matt, Simon, Addy, Karpathy | Strong on proving the real artifact; split on surface |
| 3 | Evals for skills and prompts | Lauren, Matt, Theo, Addy, Simon, Karpathy | Strong on scored behavior; split on judge vs exact test |
| 4 | Planning and the project workflow (I-6) | Addy, Lauren, Theo, Simon, Felix, Karpathy | Strong on plan before code; split on spec weight vs prototype |
| 5 | Delegation and parallel agents | Lauren, Simon, Matt, Theo, Karpathy, Addy | Strong on staged trust and isolation; split on auto-merge |
| 6 | Review (code and design) | Addy, Lauren, Simon, Karpathy, Theo | Strong on review as the bottleneck; split on human vs auto-merge |
| 7 | Memory and knowledge | Matt, Karpathy, Addy, Lauren, Simon | Strong on durable artifacts; split on handoff vs wiki vs git |
| 8 | Skills and prompt design | Lauren, Matt, Addy, Simon, Karpathy, Felix, Theo | Strong on small, versioned, verified interfaces; split on pack size |
| 9 | Safety (untrusted input, secrets) | Simon, Addy, Lauren, Theo | Strong on least privilege; split on hard vs soft enforcement |
| 10 | Ambition, autonomy and taste | Felix, Karpathy, Theo, Addy, Simon, Lauren | Strong on judgment as the scarce input; split on autonomy |
| 11 | Harness and environment design | Addy, Karpathy, Theo, Lauren, Simon | Strong on model plus harness; split on the unit of design |
| 12 | Code shape and constraints | Lauren, Matt, Addy | Strong on constraining the design space; split on hard vs soft |
| 13 | Model training and research loops | Karpathy | Single source |

---

## 1. Context management and handoff

**Agreement.** Lauren, Matt, Addy, Simon and Karpathy all treat context as a scarce, degrading resource. They agree on the direction: keep the active context small, make the boundary explicit, and prefer a fresh session or isolated subagent over carrying stale history. The native harness evidence agrees on the mechanism at a high level: Claude Code, Codex CLI and OpenCode all compact or prune from their own accounting, and all can isolate work in subagents.

**Disagreement.** Matt gives a concrete rough threshold (about 150,000 tokens where the dumb zone begins); Addy says to compact well before the host limit but refuses one cross-host percentage; pstack gives a prompt-level rule, not a token controller; Karpathy and Willison describe selection tactics, not an executable token trigger. Matt's `/handoff` is explicitly for portability, not compression; Addy's and the native harnesses' compaction is for capacity. Those are different jobs and the gathered material does not treat them as interchangeable.

**Methods.**
- **Lauren Tan:** pstack's guard principle bounds phases, files and turns and routes bulk reads to subagents; new work normally gets a fresh agent, and reuse is reserved for expensive live state. ([pstack](lauren-tan.md#pstack--the-skill-plugin-itself))
- **Matt Pocock:** the smart-zone / dumb-zone model; when the boundary is reached, clear, compact, hand off, or redesign the task, and use subagents to keep the parent in the smart zone. ([smart zone / dumb zone](matt-pocock.md#the-smart-zone--dumb-zone--context-window-economics))
- **Matt Pocock:** `/handoff` writes a portable handoff document to the OS temp directory, includes suggested skills, references existing artifacts by path or URL, and redacts sensitive information; it is for portability, not compression. ([handoff](matt-pocock.md#handoff--portable-context-as-a-file))
- **Addy Osmani:** the context hierarchy (rules, specs, source, errors, history), the context budget, and the keep/cut/protect rules; compact before the host's limit, compress before dropping, and put task-critical material last. ([context engineering](addy-osmani.md#context-engineering))
- **Addy Osmani:** the agent harness handles compaction, tool-call offloading, skills with progressive disclosure, and full context resets with a structured brief. ([agent harness](addy-osmani.md#agent-harness-engineering))
- **Simon Willison:** context is king; start a new chat to reset, dump in existing code to seed, provide full examples, and prefer tools where you can see exactly what is in the context. ([context is king](simon-willison.md#context-is-king))
- **Simon Willison:** `llm` is the CLI and library for prompts, templates, logs, embeddings, schemas and tools. ([llm](simon-willison.md#the-llm-cli-and-library))
- **Simon Willison:** `files-to-prompt` concatenates a directory into one labelled prompt with Claude XML or Markdown output. ([files-to-prompt](simon-willison.md#files-to-prompt))
- **Simon Willison:** `strip-tags` removes HTML and keeps selected semantic tags for a cleaner prompt. ([strip-tags](simon-willison.md#strip-tags))
- **Simon Willison:** `symbex` extracts the code for named Python symbols and can pipe it into `llm`. ([symbex](simon-willison.md#symbex))
- **Andrej Karpathy:** Software 3.0 makes the context window the lever over the model, and the LLM OS makes the context window the RAM while files and retrieval are storage. ([Software 3.0](andrej-karpathy.md#software-30-prompts-context-and-tools-are-the-program), [LLM OS](andrej-karpathy.md#llm-os-model-as-kernel-context-as-ram-tools-as-peripherals))
- **Karpathy and Willison (audit framing):** context engineering is selection, tool choice and compression; the audit treats their material as tactics, not an executable token policy. ([audit: Karpathy and Simon Willison](context-management.md#karpathy-and-simon-willison))
- **Native harness evidence:** Claude Code auto-compacts, supports `/compact` with a focus, `/clear`, and subagents; Codex exposes `model_auto_compact_token_limit` and `/compact`; OpenCode v1.18.27 computes overflow from actual tokens, prunes older tool outputs after protecting 40K, and summarizes with a recent tail. ([Claude Code](context-management.md#claude-code), [Codex CLI](context-management.md#codex-cli), [OpenCode](context-management.md#opencode-pinned-to-the-observed-v11827))
- **Measured baseline:** the context audit found 13 `hx context decide` calls and two compaction attempts, both `keep`; native recovery did happen, including an OpenCode auto-summary at 967,737 input-plus-cache tokens and a next ordinary request of 41,893. ([what hx implements](context-management.md#2-what-hx-actually-implements), [measured records](context-management.md#3-measured-execution-records))

**What to measure in our setup.**
- Every turn's actual input plus cache read plus output, against the same denominator the model UI uses, with the model ID and resolved effective limit recorded. Unknown-limit failures must be visible.
- Every compaction event: auto or manual, trigger source, before and after tokens, timestamp, task boundary, whether a checkpoint or handoff existed, and what survived (goal, constraints, current error, next action) verified by a nonce or an explicit next-action acknowledgement.
- Boundary coverage: task completions and other eligible boundaries divided by actual `hx context decide` calls; `hx say` pressure warnings that still sent; guard, lease and cooldown outcomes; unfinished or stale task refusals. The audit shows routing logs do not carry context-decision events, so a durable decision log is needed.
- Handoff quality: a fresh agent's time to first correct action, questions it had to ask, and duplicate or missing artifacts; compare handoff, compaction and a fresh subagent on the same task.
- Subagent isolation: raw reads and tool output returned to the parent versus kept in the child, parent growth per delegated task, and task success.
- Native versus hx intent: native thresholds from Claude status files, Codex rollouts and OpenCode's database versus hx's 0.40/0.75 and lead 0.60 policy; the 27%-versus-68% lead display mismatch is a concrete measurement to repeat.

---

## 2. Verification (proving work runs)

**Agreement.** Lauren, Matt, Simon, Addy and Karpathy all reject "it compiles" or "the agent says it works" as proof. They agree that the check must run against the real artifact: the app, the test suite, the compiler, the browser or the training metric. The disagreement is about the surface, not the principle.

**Disagreement.** Lauren invests in an app-control skill and a feature map so the agent drives the product like a user; Matt relies on types and compile errors as the cheap guardrail; Simon emphasizes manual QA, running the code and taking over when needed; Addy uses TDD plus Chrome DevTools; Karpathy uses training metrics, baselines and overfit-one-batch tests. A green unit suite is not the same evidence as a driven UI, and a typecheck is not the same evidence as a reproduced user bug.

**Methods.**
- **Lauren Tan:** verification skills drive the real surface (CDP for Electron/web, simctl for iOS) and ship a feature map that maps user-visible features to reachable UI paths and code locations; `maintain-verification-skill` keeps the map from drifting. ([verification skills and feature map](lauren-tan.md#verification-skills--the-feature-map))
- **Lauren Tan:** benny reuses the same control skills in an automation to reproduce Slack reports against the app and report back. ([benny](lauren-tan.md#benny--the-slack-triaging-automation-pack))
- **Matt Pocock:** types make illegal states unrepresentable and give the agent a self-correcting compile signal without a human; the recommendation is more abstractions plus harsh lint rules, not fewer. ([types as the AI guardrail](matt-pocock.md#types-as-the-ai-guardrail))
- **Simon Willison:** test what the model writes; if you have not seen it run, it is not a working system; use tools that run code in a loop and be ready to take over. ([test what it writes](simon-willison.md#test-what-it-writes-then-take-over))
- **Addy Osmani:** TDD's red-green-refactor and the Prove-It pattern for bugs; tests are proof, and a test that passes immediately proves nothing. ([test-driven development](addy-osmani.md#test-driven-development))
- **Addy Osmani:** Chrome DevTools MCP gives the agent DOM, console, network, performance traces and screenshots, closing the gap between static review and live browser behavior. ([Chrome DevTools MCP](addy-osmani.md#chrome-devtools-mcp))
- **Andrej Karpathy:** the training recipe verifies loss at init, an input-independent baseline, overfit one batch, decreasing training loss, and a full evaluation rather than a proxy. ([A Recipe](andrej-karpathy.md#a-recipe-for-training-neural-networks))
- **Andrej Karpathy:** agentic engineering requires tests and evaluation loops, not blind acceptance of generated code. ([agentic engineering](andrej-karpathy.md#agentic-engineering))

**What to measure in our setup.**
- Per completed task: the exact command, log, screenshot, trace or browser session that proves the behavior; tasks marked done with no real run are the failure count.
- Test evidence: changed-line coverage, full-suite result, mutation or assertion quality where available, skipped or deleted tests, and time from failure to reproduction.
- Feature-map coverage: user-visible features with a reachable path, bug reports reproduced automatically versus by hand, and feature-map drift after changes.
- Browser evidence: console errors, failed requests, DOM state, performance trace, screenshot before and after; false-green rate where the page looks right but the behavior is wrong.
- Type and lint evidence: compile errors caught before review, suppressions added, and defects that escaped both tests and types.
- Model-training evidence where applicable: baseline loss, loss at init, overfit-one-batch result, validation metric and seed/commit reproducibility.

## 3. Evals for skills and prompts

**Agreement.** Lauren, Matt, Theo, Addy, Simon and Karpathy all treat an eval as a scored loop over behavior, not a one-off prompt review. They agree on the shape: define cases, run the skill or prompt, judge the output, and iterate until the score is good enough. They also agree that generation and evaluation should be separated, ideally with a fresh context or a different judge.

**Disagreement.** Lauren and Addy use scored cases and a judge against a skill or prompt; Matt's useful version is a judge agent scoring real behavior with edge cases; Theo uses a multi-model council and hill-climbs a fuzzy target; Karpathy's strongest version is a verifiable reward or a training metric, not a rubric judge. A council, a judge model and an exact test are different levels of trust and cost, and the gathered material does not settle which should be the default.

**Methods.**
- **Lauren Tan:** the `eval` playbook tests whether a skill or prompt change actually changed behavior, blinded; scored cases run in a loop until the score saturates. ([evals as unit tests for skills](lauren-tan.md#evals-as-unit-tests-for-skills--hill-climbing))
- **Matt Pocock:** a test agent runs tasks in a real workflow, a judge agent scores the output, and the score controls retries or path changes; his test is edge cases, and his verdict is "checking behavior, not just execution." ([evals](matt-pocock.md#evals-check-behavior-not-execution--skeptic-converted))
- **Theo Browne:** turn a fuzzy goal into a measurable eval, generate candidates in parallel, let a council of models critique and score them, keep the best, and repeat. ([fuzzy goals to evals](theo-browne.md#turn-fuzzy-goals-into-evals-hill-climb-with-a-council))
- **Addy Osmani:** the agent-skills pack ships 25 eval case files and a runner in `evals/`; each skill is designed to be verified, not just read. ([Agent Skills pack](addy-osmani.md#the-agent-skills-pack))
- **Simon Willison:** designing an agentic loop starts with clear success criteria and automated tests; the loop is a brute-force search over tool calls, and the success signal is the design. ([designing agentic loops](simon-willison.md#designing-agentic-loops))
- **Andrej Karpathy:** verifiability is the boundary of what models and RL can automate; coding and math improve fast because the reward is automatic, while domains without a verifier need their own evals, fine-tuning or environment. ([agentic engineering](andrej-karpathy.md#agentic-engineering))
- **Andrej Karpathy:** the training recipe uses trusted metrics, baselines and ablations as the eval harness. ([A Recipe](andrej-karpathy.md#a-recipe-for-training-neural-networks))

**What to measure in our setup.**
- Eval inventory: number of cases per skill or prompt, the judge prompt, the rubric, the score scale, and whether the run was blind or fresh-context.
- Before and after: pass rate, score distribution, regressions, saturation, retries, and the size of the change that produced the movement.
- Judge quality: agreement with a second judge or a human, position and verbosity bias, noise across repeated runs, and cost per case.
- Predictive value: correlation between eval score and downstream task success, review findings or escaped defects.
- Skill routing: trigger accuracy, false triggers, duplicate skills, and the rate at which the right skill is invoked without being named.
- Tool-description and UI evals: number of candidates, council votes, metric movement, and whether the winner survives a real task.

---

## 4. Planning and the project workflow (I-6)

**Agreement.** Addy, Lauren, Theo, Simon, Felix and Karpathy all agree that the work should start with a clear target and be split into small units with a verification step, and that documentation and version control carry the context between units. Lauren's atomic PRs, Addy's spec-to-plan-to-tasks chain, Simon's plan-first agentic loops, Theo's goal-level prompts and Felix's business-impact framing are the same move at different altitudes.

**Disagreement.** Addy makes a written spec a gate and says stop after the spec; Felix says a weekend prototype can bypass the PRD and buy-in entirely; Theo says prompt granularity and delegation can be enough and a bespoke software factory is not a prerequisite. That is a real split between specification-first and prototype-first. Lauren's "multiple PRs per task" also cuts against a single giant deliverable, while Addy's task breakdown expects a planned sequence.

**Methods.**
- **Addy Osmani:** the full workflow: describe the idea, let the model ask questions, write `spec.md`, generate a project plan, implement one task at a time, load context, test, review, commit often, use worktrees, and keep `CLAUDE.md`/`GEMINI.md` rules. ([workflow](addy-osmani.md#my-llm-coding-workflow-into-2026))
- **Addy Osmani:** spec-driven development with SPECIFY, PLAN, TASKS and IMPLEMENT; a capability map when one request spans independent capabilities; six spec areas; stop after the spec; save `tasks/plan.md` and `tasks/todo.md`. ([spec-driven development](addy-osmani.md#spec-driven-development))
- **Addy Osmani:** incremental implementation in thin vertical slices with simplicity, scope discipline, feature flags, safe defaults and rollback-friendly changes. ([incremental implementation](addy-osmani.md#incremental-implementation))
- **Addy Osmani:** the agent-skills pack adds planning, documentation and ADR skills alongside the build loop, so the workflow is not just code generation. ([Agent Skills pack](addy-osmani.md#the-agent-skills-pack))
- **Addy Osmani:** Beyond Vibe Coding frames the spectrum from prototype to production and the 70% problem that the last 30% must solve. ([Beyond Vibe Coding](addy-osmani.md#beyond-vibe-coding-book-and-guide))
- **Lauren Tan:** atomic PRs and git history as context; each PR should describe one small piece so it can be reviewed and reverted, with an observed range of roughly 50 to 1,000 lines. ([atomic PRs](lauren-tan.md#atomic-prs-and-git-history-as-context))
- **Lauren Tan:** pstack's playbook table routes a task to a named process before work starts, and the playbook's steps become the todo list. ([pstack](lauren-tan.md#pstack--the-skill-plugin-itself))
- **Theo Browne:** model-era workflow from steps to goals to orchestration; larger assignments reveal capabilities that small tickets never exercise. ([workflow](theo-browne.md#model-era-model-of-workflow-steps--goals--orchestration))
- **Theo Browne:** a recurring service can shrink to a Markdown instruction file plus cron: inspect four repos, prioritize, update an HTML page, upload to S3 and return the URL. ([scheduled service](theo-browne.md#scheduled-service--a-markdown-file-gbrain-tier))
- **Theo Browne:** rejecting work without guilt; code is cheap, so discard a bad branch and regenerate rather than salvage a sunk cost. ([rejecting work](theo-browne.md#rejecting-work-without-guilt--throw-away-a-branch-try-again))
- **Simon Willison:** exploratory mode then authoritarian production mode; research options first, then specify the function, tests and behavior. ([two modes](simon-willison.md#two-modes-exploratory-and-authoritarian))
- **Simon Willison:** vibe engineering lists the workflow prerequisites: automated tests, planning, documentation, version control, automation, code review, manual QA, research, preview deploys and estimation. ([vibe engineering](simon-willison.md#vibe-engineering-later-agentic-engineering))
- **Felix Rieseberg:** pick business impact over backlog, pick a radical idea, build it over a weekend, focus on one idea, and lean into the fresh perspective. ([hunger beats tenure](felix-rieseberg.md#hunger-beats-tenure-business-impact-over-backlog))
- **Andrej Karpathy:** agentic engineering is the discipline of designing the spec, supervising the plan, inspecting diffs and preserving quality while agents write code. ([agentic engineering](andrej-karpathy.md#agentic-engineering))

**What to measure in our setup.**
- For I-6, the artifacts: `spec.md`, `plan.md`, task list, ADRs and handoffs; when they exist, how often they change, and whether code starts before approval.
- Task size and shape: files per task, lines per PR, commits per task, tasks per plan, and the rate of scope change or rework.
- Cycle time: idea to spec, spec to first code, first code to verified task, and task to merge; compare planned versus actual.
- Revision: review rounds per task, rejected attempts, branches/worktrees discarded, and changes that had to be re-planned rather than patched.
- "See first, then judge": number of decisions made from a prototype, demo or measurement versus a debate; time spent before the first observable result.
- Documentation and handoff: onboarding time for a fresh agent, questions asked, stale docs, and whether ADRs and specs are linked from PRs.

---

## 5. Delegation and parallel agents

**Agreement.** Lauren, Simon, Matt, Theo, Karpathy and Addy all agree that delegation is not free: it needs isolation, bounded tasks, a clear return artifact and review. They also agree that subagents are the main tool for protecting the parent context, and that trust should be staged rather than switched on all at once.

**Disagreement.** Lauren reaches auto-merge after a trust curve; Simon treats review as the bottleneck and limits parallel work to research, small maintenance and carefully specified changes; Theo moves from steps to goals to orchestration as models improve; Karpathy keeps a human on an autonomy slider and warns about partial autonomy; Addy's harness splits planner, generator and evaluator. The disagreement is how much autonomy to grant and whether auto-merge is a valid end state.

**Methods.**
- **Lauren Tan:** the trust curve: stay in the loop, encode standards into skills and verify they are applied, move to cloud agents, add automations, then auto-merge and review on main. ([trust curve](lauren-tan.md#the-trust-curve--trust-ladder-how-she-got-to-auto-merge))
- **Lauren Tan:** benny is a dormant automation pack that triages Slack reports, reproduces them with the same control skills and reports back; pstack calls this "fearless parallelism". ([benny](lauren-tan.md#benny--the-slack-triaging-automation-pack), [pstack](lauren-tan.md#pstack--the-skill-plugin-itself))
- **Simon Willison:** parallel agents work for research and proofs of concept, codebase questions, small maintenance and carefully specified work; review is the natural bottleneck. ([parallel coding agents](simon-willison.md#parallel-coding-agents))
- **Matt Pocock:** subagents keep the parent in the smart zone; the dream is the same work for fewer tokens, and subagents are the elegant way to get it. ([smart zone / dumb zone](matt-pocock.md#the-smart-zone--dumb-zone--context-window-economics))
- **Theo Browne:** the orchestration stage: decompose work, spawn model workers, verify results; larger assignments are what exercise the capability. ([workflow](theo-browne.md#model-era-model-of-workflow-steps--goals--orchestration))
- **Theo Browne:** a council of models can critique and score candidates for a fuzzy goal; this is delegation as a judging panel, not a single worker. ([council](theo-browne.md#turn-fuzzy-goals-into-evals-hill-climb-with-a-council))
- **Andrej Karpathy:** partial autonomy and the autonomy slider: Cursor's Tab, Cmd+K, Cmd+L and Cmd+I ladder, with the human choosing how much autonomy to give up. ([autonomy slider](andrej-karpathy.md#partial-autonomy-and-the-autonomy-slider))
- **Addy Osmani:** the harness is where subagent spawning, handoffs, model routing, planner/generator/evaluator splits and worktree isolation live. ([agent harness](addy-osmani.md#agent-harness-engineering))
- **Addy Osmani:** orchestrating agents feels like management; delegation, review and redirection are the new work. ([80% problem](addy-osmani.md#the-80-problem-and-comprehension-debt))

**What to measure in our setup.**
- Number of agents per task, ownership, worktree or branch, duration, tokens per agent and parent context growth.
- Dispatch and return latency; subagent summary size versus raw reads; duplicate work; task success and rework.
- Human review load per delegated task; rejected work rate; conflicts; auto-merge rate and post-merge incidents.
- Guard outcomes: unfinished tasks, stale ownership, lease/cooldown refusals, and worktree checks; these are the safety cost of delegation.
- Trust stage per task and the human intervention rate per 100 agent actions.
- Parallel attempts per task, kept versus discarded branches, and cost per kept change.

## 6. Review (code and design)

**Agreement.** Addy, Lauren, Simon, Karpathy and Theo all agree that review is the bottleneck when code generation is cheap, and that the reviewer must be able to understand the change, not just see green. They agree on small diffs, explicit reasoning and a human or fresh-context check at the point where the work is accepted.

**Disagreement.** Lauren is willing to auto-merge after a trust curve and review on main; Simon, Karpathy and Addy keep a human accountable before merge; Addy's doubt-driven review is an adversarial fresh-context pass, while a normal code review is a verdict on a finished artifact. Lauren's meta-rule is to turn every repeated review comment into a hard rule; Addy's constraints skill does the same for the quality bar. The split is between review as a gate and review as a ratchet that removes future review.

**Methods.**
- **Addy Osmani:** doubt-driven development: CLAIM, EXTRACT, DOUBT, RECONCILE, STOP; a fresh-context reviewer with an adversarial prompt receives ARTIFACT and CONTRACT, not the CLAIM; findings are classified as contract misread, actionable, trade-off or noise. ([doubt-driven development](addy-osmani.md#doubt-driven-development))
- **Addy Osmani:** the 80% problem names comprehension debt and the verification bottleneck; review must protect understanding, not just pass tests. ([80% problem](addy-osmani.md#the-80-problem-and-comprehension-debt))
- **Addy Osmani:** the 70% problem explains why the last 30% needs human review: edge cases, security, accessibility, performance and maintainability. ([70% problem](addy-osmani.md#the-70-problem))
- **Addy Osmani:** the agent-skills pack includes code-review, code-simplification, security and performance review skills with five-axis review and change sizing. ([Agent Skills pack](addy-osmani.md#the-agent-skills-pack))
- **Lauren Tan:** Dune's meta-rule: every time a human flags something in review, turn it into a hard rule, a lint rule, a CI failure or an elimination of the pattern; the worst place is "code review land". ([Dune](lauren-tan.md#dune--constraint-as-code-architecture-for-agent-written-apps))
- **Lauren Tan:** atomic PRs keep the review unit small and the git history readable; this also makes reverts cheap. ([atomic PRs](lauren-tan.md#atomic-prs-and-git-history-as-context))
- **Simon Willison:** vibe engineering depends on a code review culture; AI-written code needs extra scrutiny, and review is a large part of the work. ([vibe engineering](simon-willison.md#vibe-engineering-later-agentic-engineering))
- **Andrej Karpathy:** the agentic engineer inspects diffs, supervises plans and preserves quality; the MenuGen payment bug is the example of a plausible change that a human must reject on system-design grounds. ([agentic engineering](andrej-karpathy.md#agentic-engineering))
- **Theo Browne:** rejecting work without guilt; the reviewer's job is to pick the good attempt, not salvage a bad one. ([rejecting work](theo-browne.md#rejecting-work-without-guilt--throw-away-a-branch-try-again))

**What to measure in our setup.**
- Diff and PR size, review time, comments per PR, severity labels, actionable versus noise, and time to merge.
- Defects found in review versus escaped defects; post-merge incidents; revert rate.
- Fresh-context review: findings accepted, rejected and classified; cycles to stop; whether the reviewer had the contract.
- Comprehension: can the reviewer explain the change and its trade-offs, and how long does that take?
- Review feedback converted into a lint, test, CI check or constraint; repeated comment rate.
- Auto-merge rate and incident rate; human sign-off at architectural decision points.

---

## 7. Memory and knowledge

**Agreement.** Matt, Karpathy, Addy, Lauren and Simon all agree that chat history is not a durable memory. The durable form is an explicit artifact: a handoff, a rules file, a spec or ADR, a knowledge base, or git history. They agree that a fresh agent should be able to resume from the artifact without asking the previous agent.

**Disagreement.** Matt's handoff is for portability and deliberately not compression; Karpathy's knowledge base compiles raw sources into a maintained wiki; Addy's rules and specs are loaded by the harness; Lauren uses git history and atomic PRs as context. These are different stores with different update rules, and the gathered material does not say one replaces the others.

**Methods.**
- **Matt Pocock:** `/handoff` writes a portable document to the OS temp directory, includes suggested skills, references existing artifacts by path or URL, and redacts sensitive information. ([handoff](matt-pocock.md#handoff--portable-context-as-a-file))
- **Matt Pocock:** Cursor rules split workspace rules (versioned, shareable, project-specific) from global rules (personal style), with short examples and code snippets. ([Cursor rules](matt-pocock.md#cursor-rules--workspace-vs-global-with-real-rule-text))
- **Andrej Karpathy:** an LLM knowledge base compiles raw sources into a persistent Markdown wiki with summaries, entity pages, concept pages, contradictions, cross-links and logs. ([LLM knowledge bases](andrej-karpathy.md#llm-knowledge-bases))
- **Andrej Karpathy:** the LLM OS treats files and retrieval as storage and the context window as RAM, which makes the file store part of the memory architecture. ([LLM OS](andrej-karpathy.md#llm-os-model-as-kernel-context-as-ram-tools-as-peripherals))
- **Addy Osmani:** context engineering separates persistent rules, feature specs, source files, errors and history; the spec and rules are the durable parts. ([context engineering](addy-osmani.md#context-engineering))
- **Addy Osmani:** the agent harness reloads `AGENTS.md` memory files and uses full context resets with a structured brief; the pack also has documentation-and-ADRs. ([agent harness](addy-osmani.md#agent-harness-engineering), [Agent Skills pack](addy-osmani.md#the-agent-skills-pack))
- **Lauren Tan:** git history is context; each atomic PR describes a small piece and makes the history usable for review and reversion. ([atomic PRs](lauren-tan.md#atomic-prs-and-git-history-as-context))
- **Simon Willison:** context is king; code, docs and prior replies are context, and a clean reset plus a good seed is often better than carrying a stale thread. ([context is king](simon-willison.md#context-is-king))

**What to measure in our setup.**
- Handoff quality: fresh-agent time to first correct action, questions asked, missing or duplicated artifacts, and whether the handoff referenced the source instead of copying it.
- Rules memory: size, age, conflicts, stale entries and actual invocation; which rules changed behavior.
- Knowledge base: number of entries, retrieval hits, stale or contradictory pages, update latency and source links.
- Git memory: commit and PR message quality, ability to recover context from history, and actual bisect or revert use.
- Post-compaction survival: active task, constraints, current error, next action and approvals after a reset.
- Documentation and ADR coverage; link integrity; time for a new agent to orient.

---

## 8. Skills and prompt design

**Agreement.** Lauren, Matt, Addy, Simon, Karpathy, Felix and Theo all treat the prompt, rules and skills as a designed interface to the model. They agree on small, composable, versioned, example-rich units with a clear trigger and a verification step. They also agree that a skill or prompt is code-shaped: it can regress, so it needs tests or evals.

**Disagreement.** Matt explicitly prefers many tiny skills over heavyweight frameworks; Addy ships a 25-skill pack with lifecycle commands; Lauren has a router that selects a playbook and leaf skills; Felix's contribution is a single depth prompt; Karpathy's `program.md` is a minimal skill for an autonomous loop. The split is how much structure belongs in the interface: one prompt, a small composable library, or a full lifecycle pack.

**Methods.**
- **Lauren Tan:** pstack: a routing skill selects a playbook, copies its steps into a todo list and loads leaf skills and principles on demand; it is a Cursor plugin with skills, agents, automations and a Claude Code adapter. ([pstack](lauren-tan.md#pstack--the-skill-plugin-itself))
- **Matt Pocock:** `mattpocock/skills` is a small, composable skills library with `skills/<category>/<name>/SKILL.md`, companion docs and per-harness adapters; user-invoked skills orchestrate, model-invoked skills are called by the agent. ([skills library](matt-pocock.md#mattpocockskills--a-personal-composable-skills-library))
- **Matt Pocock:** "the system prompt is the LLM's UI": skills, rules, `AGENTS.md` and briefs are the product surface and deserve product-grade design. ([system prompt](matt-pocock.md#the-system-prompt-is-the-llms-ui))
- **Matt Pocock:** Cursor rules are short, carry code examples and teach nuance the model gets wrong rather than restating style. ([Cursor rules](matt-pocock.md#cursor-rules--workspace-vs-global-with-real-rule-text))
- **Addy Osmani:** the Agent Skills pack: 25 skills, 9 lifecycle commands, 4 personas, 7 reference checklists, hooks and evals; every skill has frontmatter, process, anti-rationalization, red flags and verification. ([Agent Skills pack](addy-osmani.md#the-agent-skills-pack))
- **Addy Osmani:** the agent harness includes system prompts, `AGENTS.md`, skill files and subagent prompts, and keeps `AGENTS.md` short and earned. ([agent harness](addy-osmani.md#agent-harness-engineering))
- **Simon Willison:** Agentic Engineering Patterns: a guide of chapter-shaped patterns for coding agents, including how agents work under the hood and subagents. ([Agentic Engineering Patterns](simon-willison.md#agentic-engineering-patterns))
- **Andrej Karpathy:** `program.md` is a minimal skill for the autonomous research agent: setup, experiment rules, result format, loop and stop condition. ([autoresearch](andrej-karpathy.md#autoresearch-programmd-as-the-research-org))
- **Andrej Karpathy:** Software 3.0 treats the prompt and context as the program; the prompt is not a hint but the interface to the interpreter. ([Software 3.0](andrej-karpathy.md#software-30-prompts-context-and-tools-are-the-program))
- **Felix Rieseberg:** the depth prompt rewrites a request to ask what a top professional would do, what raw material they would start from, which tools they would use and what outside judge would verify the result. ([depth prompt](felix-rieseberg.md#the-depth-prompt-make-the-request-deeper-not-bigger))
- **Theo Browne:** the harness is the product; the interface over models, prompts and tools is the durable surface, and judgment is the human input. ([harness is the product](theo-browne.md#the-harness-is-the-product-judgment-over-typing))

**What to measure in our setup.**
- Skill inventory: count, size, dependencies, overlap, stale entries and trigger accuracy.
- Prompt and skill changes: version, diff, eval before/after, regression rate and the behavior that actually changed.
- Interface quality: a new agent can find and apply the right skill without being told; token cost; red flags and verification are present.
- Rule compliance: actual sessions where the rule was followed or ignored; repeated violations; rules converted into checks.
- `program.md`-style skills: allowed edit surface, stop condition, metric and human edit surface.
- Depth prompt: outcome versus output, expert methods used, raw inputs, outside judge and whether the result changed.

## 9. Safety (untrusted input, secrets)

**Agreement.** Simon, Addy, Lauren and Theo all agree on least privilege and on keeping secrets out of the agent's reachable context. They agree that untrusted content can carry instructions and that a tool that can read private data and communicate externally creates an exfiltration path.

**Disagreement.** Simon's strongest claim is that guardrails are not enough and the lethal combination must be avoided; Lauren's answer is hard architectural constraints and CI bans; Addy's is a security skill plus a `CONSTRAINTS.md` floor around secrets and suppressions; Theo's `.env` question is about inherited workflow constraints, not permission to commit secrets. The split is prevention by architecture versus prevention by policy and scanning.

**Methods.**
- **Simon Willison:** the lethal trifecta: private data, untrusted content and external communication. If an agent has all three, an attacker can trick it into exfiltration; guardrails claiming 95% are not enough, so avoid the combination. ([lethal trifecta](simon-willison.md#the-lethal-trifecta-for-ai-agents))
- **Simon Willison:** designing agentic loops includes YOLO-mode risks, sandbox or another computer, shell tools, `AGENTS.md` examples and tightly scoped credentials with budget limits. ([agentic loops](simon-willison.md#designing-agentic-loops))
- **Addy Osmani:** the Agent Skills pack includes `security-and-hardening` with OWASP, auth, secrets, dependency auditing and boundary rules. ([Agent Skills pack](addy-osmani.md#the-agent-skills-pack))
- **Addy Osmani:** constraint-driven development puts secrets, suppression comments and unimplemented stubs in a floor that does not get weakened; security scanning and dependency checks are enforced dimensions. ([constraint-driven development](addy-osmani.md#constraint-driven-development))
- **Addy Osmani:** the 70% problem includes security and data privacy as part of the last 30% that AI does not close on its own. ([70% problem](addy-osmani.md#the-70-problem))
- **Lauren Tan:** Dune's hard layer: CI bans, import boundaries between `electron-main` and `electron-renderer`, lint rules and compiler diagnostics, with soft rules only as a second layer. ([Dune](lauren-tan.md#dune--constraint-as-code-architecture-for-agent-written-apps))
- **Theo Browne:** the `.env` question is a workflow smell, not a reason to commit secrets; the point is that config sharing is fragmented and inherited. ([skeuomorphism test](theo-browne.md#skeuomorphism-test-familiar-tools-as-inherited-constraints))
- **Simon Willison:** `llm-cmd` turns a request into a shell command, displays it for editing, executes on Enter, and warns that it is dangerous; the human stays in the dangerous step. ([llm-cmd](simon-willison.md#llm-cmd))

**What to measure in our setup.**
- Tool permission matrix: filesystem, network, shell, secrets and external communication; which agent has which, and the approval mode.
- Lethal-trifecta audit per tool and per agent: private data, untrusted content, external communication; red-team prompt-injection and exfiltration attempts.
- Sandbox evidence: container or VM, network allowlist, credential scope and budget cap; YOLO-mode denials.
- Secrets: secret scanning, `.env` handling, redaction, and whether secrets ever entered the model context or logs.
- Security findings: dependency vulnerabilities, auth and input validation, escaped incidents, and review findings.
- Constraint integrity: suppressions added, tests skipped, thresholds lowered, exceptions opened, and whether the bar was weakened to pass.

---

## 10. Ambition, autonomy and taste

**Agreement.** Felix, Karpathy, Theo, Addy, Simon and Lauren all agree that code generation is becoming cheap and the scarce input is judgment: what to build, how much autonomy to grant, which attempt to keep and what quality bar to defend. They agree that ambition should rise with model capability, and that taste and understanding cannot be fully delegated.

**Disagreement.** Karpathy's vibe coding is explicitly for throwaway or low-stakes work, while his agentic engineering is for production; Simon draws the same line; Addy's 70% and 80% problems say the last 30% needs human expertise; Lauren is willing to auto-merge after a trust curve; Theo rejects work without guilt and optimizes for picking; Felix pushes for much larger ambition. The disagreement is how much human review remains at the end and whether auto-merge is acceptable.

**Methods.**
- **Felix Rieseberg:** ambition as Speed, Altitude, Depth and Range; raise the goal, use the expert's instruments, set unreasonable targets, challenge assumptions, use an outside judge, replace nouns with verbs and assemble a crew. ([ambition](felix-rieseberg.md#ambition-as-four-types-speed-altitude-depth-and-range))
- **Felix Rieseberg:** hunger beats tenure: business impact over backlog, a radical idea, a weekend prototype, one idea and the fresh perspective. ([hunger](felix-rieseberg.md#hunger-beats-tenure-business-impact-over-backlog))
- **Felix Rieseberg:** hiring by portfolio, not coding puzzles; scan for prototypes that show taste, empathy and principled thinking. ([hiring](felix-rieseberg.md#hiring-by-portfolio-not-coding-puzzles))
- **Felix Rieseberg:** public projects as worked examples of small, complete, polished artifacts. ([public projects](felix-rieseberg.md#public-projects-as-worked-examples))
- **Andrej Karpathy:** vibe coding raises the floor; agentic engineering preserves the quality bar and raises the ceiling. ([vibe coding](andrej-karpathy.md#vibe-coding), [agentic engineering](andrej-karpathy.md#agentic-engineering))
- **Andrej Karpathy:** partial autonomy and the autonomy slider: build augmentations, not just agents, and let the human choose how much autonomy to give up. ([autonomy slider](andrej-karpathy.md#partial-autonomy-and-the-autonomy-slider))
- **Andrej Karpathy:** "you can outsource your thinking, but you can't outsource your understanding"; taste, judgment and system design remain human. ([agentic engineering](andrej-karpathy.md#agentic-engineering))
- **Theo Browne:** rejecting work without guilt; code is disposable, and the reviewer's job is to pick, not salvage. ([rejecting work](theo-browne.md#rejecting-work-without-guilt--throw-away-a-branch-try-again))
- **Theo Browne:** breadth as product strategy; small teams cover more area and let users build the depth. ([breadth](theo-browne.md#breadth-as-product-strategy-let-users-build-the-depth))
- **Theo Browne:** the harness is the product and judgment over typing; the human picks between generated alternatives. ([harness is the product](theo-browne.md#the-harness-is-the-product-judgment-over-typing))
- **Addy Osmani:** the 70% problem and 80% problem: the last 30% and comprehension debt are the human's work; AI amplifies existing expertise. ([70% problem](addy-osmani.md#the-70-problem), [80% problem](addy-osmani.md#the-80-problem-and-comprehension-debt))
- **Addy Osmani:** Beyond Vibe Coding frames the spectrum from rapid prototyping to production AI-assisted engineering. ([Beyond Vibe Coding](addy-osmani.md#beyond-vibe-coding-book-and-guide))
- **Simon Willison:** vibe coding is not all AI-assisted programming; the golden rule is not committing code you cannot explain, and vibe engineering is the accountable end. ([vibe coding](simon-willison.md#vibe-coding-is-not-all-ai-assisted-programming), [vibe engineering](simon-willison.md#vibe-engineering-later-agentic-engineering))
- **Simon Willison:** `simonw/tools` is a public collection of 77 HTML+JavaScript apps and 6 Python apps built by prompting; it is a corpus for learning what models can and cannot do. ([simonw/tools](simon-willison.md#simonwtools-a-collection-of-vibe-coded-tools))
- **Lauren Tan:** the trust curve and "throughput without quality is not a goal"; trust is earned in stages, and auto-merge is the last stage. ([trust curve](lauren-tan.md#the-trust-curve--trust-ladder-how-she-got-to-auto-merge))

**What to measure in our setup.**
- Task ambition: would this task have been attempted before the model could do it; outcome versus output; product breadth.
- Autonomy setting per task, human interventions per 100 actions, error cost and trust stage; auto-merge incidents.
- Rejected work: attempts per kept change, branches/worktrees discarded, time to discard and cost per kept change.
- Taste and comprehension probes: explain the design, spot the bug, choose between candidates, defend a trade-off.
- 70%/80% measures: prototype-to-production time, escaped defects, comprehension debt, review time and rubber-stamp rate.
- Learning: manual coding alternation, new skills acquired, and whether the human can still write the code they review.
- Agent-native product surfaces: docs, APIs, CLIs, MCP, structured logs and headless setup; can an agent complete the task through them?

---

## 11. Harness and environment design

**Agreement.** Addy, Karpathy, Theo, Lauren and Simon all agree that the model is only one input and the harness around it dominates behavior. They agree on the main components: context injection, tools, permissions, a loop, state and feedback. They also agree that the harness is where context, safety and verification meet.

**Disagreement.** Addy treats the harness as an engineering artifact with hooks, sandboxes, subagents and observability; Theo treats it as the product; Karpathy treats it as an operating system with a kernel, RAM and peripherals; Lauren treats it as an architecture with hard constraints; Simon treats it as a designed loop of tools and credentials. These are compatible lenses with different units of design, not a factual disagreement.

**Methods.**
- **Addy Osmani:** agent harness engineering: model plus prompts, tools, context policies, hooks, sandboxes, subagents, feedback loops, recovery paths and observability; every mistake becomes a rule. ([agent harness](addy-osmani.md#agent-harness-engineering))
- **Andrej Karpathy:** the LLM OS: model as kernel, context as RAM, files as storage, tools as peripherals, agents as processes. ([LLM OS](andrej-karpathy.md#llm-os-model-as-kernel-context-as-ram-tools-as-peripherals))
- **Andrej Karpathy:** Software 3.0 makes the prompt, context, tools, memory and instructions the program; agent-native infrastructure makes docs, CLIs, APIs and MCP legible to agents. ([Software 3.0](andrej-karpathy.md#software-30-prompts-context-and-tools-are-the-program), [agent-native](andrej-karpathy.md#agent-native-infrastructure-build-for-the-agent-not-just-the-human))
- **Theo Browne:** the harness is the product; aggregation, routing and orchestration across models and subscriptions are the durable surface. ([harness is the product](theo-browne.md#the-harness-is-the-product-judgment-over-typing))
- **Lauren Tan:** Dune is an agent-friendly app architecture: feature directories, hard CI and import boundaries, soft rules only as a second layer, and every review comment becomes a hard rule. ([Dune](lauren-tan.md#dune--constraint-as-code-architecture-for-agent-written-apps))
- **Simon Willison:** designing agentic loops: define the goal, choose tools, isolate execution, scope credentials and give the loop a verification signal. ([agentic loops](simon-willison.md#designing-agentic-loops))

**What to measure in our setup.**
- Harness inventory and version: system prompts, `AGENTS.md`, tools, MCP, hooks, sandbox, subagents, compaction, observability.
- Tool behavior: success and error rates, prompt-injection exposure, approval count, denied actions and sandbox/network policy.
- Context mechanics: tokens injected at startup, compaction/offload, skill disclosure, parent growth per subagent and cost per task.
- Hook and feedback behavior: what fires, what it blocks, what it injects, and whether failures are verbose while success is silent.
- Harness changes: same model in two harnesses, behavior diff, regression, latency and token cost.
- Agent-native surfaces: docs/API/CLI/MCP coverage, headless setup and whether an agent can act without a human clicking.

---

## 12. Code shape and constraints

**Agreement.** Lauren, Matt and Addy all agree that prose rules are not enough and the design space should be constrained by structure: types, lint, architecture, CI and small units. They agree that the shortest path should be the correct path, and that repeated review comments should become a hard check.

**Disagreement.** Matt argues for more abstractions and harsh lint rules to shrink the design space and save tokens; Lauren argues for hard bans, import boundaries and "designed for the dumbest agent", with soft rules as a second layer; Addy argues for a written `CONSTRAINTS.md` and quality gates. The split is how much abstraction versus simplicity, and how much enforcement is hard CI versus written policy.

**Methods.**
- **Lauren Tan:** Dune: feature co-location, `useEffect` and code comments banned, `electron-main`/`electron-renderer` import boundaries enforced in CI, lints for every observed bad pattern, compiler diagnostics, and a meta-rule to convert review findings into hard rules. ([Dune](lauren-tan.md#dune--constraint-as-code-architecture-for-agent-written-apps))
- **Lauren Tan:** atomic PRs as the unit of change and revert. ([atomic PRs](lauren-tan.md#atomic-prs-and-git-history-as-context))
- **Matt Pocock:** types as the AI guardrail; make illegal states unrepresentable, use more abstractions plus harsh lint rules, and turn repeated tasks into custom scripts. ([types](matt-pocock.md#types-as-the-ai-guardrail))
- **Matt Pocock:** Cursor rules that teach nuance with short code examples, split into workspace and global rules. ([Cursor rules](matt-pocock.md#cursor-rules--workspace-vs-global-with-real-rule-text))
- **Addy Osmani:** constraint-driven development: `CONSTRAINTS.md` with a floor, numbered dimensions, commands, lifecycle placement, exceptions and ratchets; do not weaken the bar to pass. ([constraint-driven development](addy-osmani.md#constraint-driven-development))
- **Addy Osmani:** the Agent Skills pack adds code-review, code-simplification, security and performance review skills with change sizing and quality gates. ([Agent Skills pack](addy-osmani.md#the-agent-skills-pack))
- **Addy Osmani:** the 70% problem says modularity, edge cases, types, error handling and architecture are the human's contribution; the 80% problem names abstraction bloat and architectural hygiene. ([70% problem](addy-osmani.md#the-70-problem), [80% problem](addy-osmani.md#the-80-problem-and-comprehension-debt))

**What to measure in our setup.**
- Type errors, lint errors, CI failures, suppressions, skipped tests and architecture-boundary violations.
- Code size, module size, abstraction count, duplication, complexity and PR size.
- Review comments per 100 lines and the rate converted to a lint, test, CI check or constraint.
- Defects and incidents by module; change failure rate; revert rate; lead time.
- Constraint integrity: threshold changes, exceptions, expiries and suppressions; whether a check was weakened to pass.
- Dune-style coverage: feature co-location, import boundaries, bans, lint rules and refactor PR count.
- Public-artifact craft: tests, packaging, release, UX and documentation in the projects we reuse.

---

## 13. Model training and research loops

**Agreement.** Only Karpathy presents this capability in the gathered material. There is no cross-person agreement to report.

**Disagreement.** No person-to-person disagreement. The material contrasts model training with agent workflows; that is a difference of capability, not a disagreement between people.

**Methods.**
- **Andrej Karpathy:** Software 2.0: the dataset and the weights are the program; training compiles the dataset into the network. ([Software 2.0](andrej-karpathy.md#software-20-the-dataset-and-the-weights-are-the-program))
- **Andrej Karpathy:** A Recipe for Training Neural Networks: become one with the data, build an end-to-end skeleton and dumb baselines, overfit, regularize, tune and squeeze. ([A Recipe](andrej-karpathy.md#a-recipe-for-training-neural-networks))
- **Andrej Karpathy:** nanoGPT: a roughly 300-line training loop and a roughly 300-line model, with config files, Shakespeare and GPT-2 examples. ([nanoGPT](andrej-karpathy.md#nanogpt-the-small-readable-gpt-training-loop))
- **Andrej Karpathy:** nanochat: a minimal full-stack ChatGPT harness with tokenization, pretraining, finetuning, eval, inference and a template CLI, controlled by one `--depth` dial. ([nanochat](andrej-karpathy.md#nanochat-the-minimal-full-stack-chatgpt-harness))
- **Andrej Karpathy:** llm.c: GPT-2/3 training in raw C/CUDA with a PyTorch reference test and a roughly 1,000-line CPU implementation. ([llm.c](andrej-karpathy.md#llmc-gpt-training-in-raw-c-and-cuda))
- **Andrej Karpathy:** micrograd and microGPT: a tiny autograd engine and a single-file GPT that contains the algorithmic essence. ([micrograd / microGPT](andrej-karpathy.md#micrograd-and-microgpt-the-algorithm-in-one-small-file))
- **Andrej Karpathy:** autoresearch: give an agent a real training setup; it edits `train.py`, runs a fixed 5-minute experiment, keeps or discards based on `val_bpb`, and repeats overnight; `program.md` is the research org. ([autoresearch](andrej-karpathy.md#autoresearch-programmd-as-the-research-org))

**What to measure in our setup.**
- Training runs: validation metric, fixed time budget, GPU, tokens, cost, seed and commit; baseline, loss at init, overfit-one-batch and full eval.
- Experiment loop: experiments per hour, keep/discard/crash counts, `results.tsv`, branch state and whether the agent edited only the allowed file.
- Reproducibility: environment, data, tokenizer and eval harness; can a second run match the result?
- Code size and dependencies: one dial, no giant config, no hidden framework; the shortest readable implementation.
- Model quality: validation metric, CORE or task eval, and the cost to reach the target.
- Agent autonomy: human interventions per 100 experiments, timeout handling, crashes, and whether the loop actually continued unattended.

---

## Reusable artifacts (not a capability)

These are the public-code lists from the seven person files. They are implementation inputs, not separate capabilities. `context-management.md` has no public-code list; its source index is measurement evidence.

- [Lauren Tan public code](lauren-tan.md#public-code-to-reuse): pstack plugin, poteto-mode router, leaf skills, benny pack, Claude Code adapter.
- [Matt Pocock public code](matt-pocock.md#public-code-to-reuse): `mattpocock/skills`, handoff skill and docs, repo layout, Cursor rules.
- [Theo Browne public code](theo-browne.md#public-code-to-reuse): T3 Chat/T3 Code, Lakebed, the Markdown-plus-cron pattern, eval/hill-climb method.
- [Andrej Karpathy public code](andrej-karpathy.md#public-code-to-reuse): nanochat, autoresearch, nanoGPT, llm.c, micrograd, microGPT, vibe-coded apps.
- [Felix Rieseberg public code](felix-rieseberg.md#public-code-to-reuse): Electron, Fiddle, windows95, macintosh.js, clippy, claude-coach, relic, language-model-builder.
- [Simon Willison public code](simon-willison.md#public-code-to-reuse): `llm`, `files-to-prompt`, `strip-tags`, `symbex`, `llm-cmd`, `simonw/tools`, model plugins.
- [Addy Osmani public code](addy-osmani.md#public-code-to-reuse): `agent-skills`, `AGENTS.md`/`CLAUDE.md`, evals, hooks, personas, references, Chrome DevTools MCP.



