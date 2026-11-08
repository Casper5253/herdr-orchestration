# Context management: mechanisms, measured behavior, and gaps

Goal: explain why smart compaction is rarely seen, then compare the actual mechanisms with the named practitioners and harnesses. Playbook: poteto-mode Investigation with a root-cause mindset. Fix Root Causes separates absent triggers, unavailable telemetry, and guard refusals. Prove It Works requires execution records rather than treating a feature description as acceptance. Guard the Context Window favors bounded evidence summaries over pasted transcripts. This is gather, measurement, and difference analysis only. Sources: `docs/greats/PROGRAM.md:3`, `.hx/briefs/greats-context.md:1`, `C:/Users/caspe/.claude/skills/principle-fix-root-causes/SKILL.md:8`, `C:/Users/caspe/.claude/skills/principle-prove-it-works/SKILL.md:8`, `C:/Users/caspe/.claude/skills/principle-guard-the-context-window/SKILL.md:10`.

Throughput checkpoint: n/a, read-only investigation. Investigation and synthesis ran inline under the assignee contract; no delegation, native compaction, clear, configuration change, or live decision command was performed. Source: `.hx/briefs/greats-context.md:7` and `C:/Users/caspe/.codex/skills/herdr-team/SKILL.md:16`.

## Finding

**The early hx controller is incomplete as an operational loop. Native compaction does work, often much later.** There is no automatic decision call on task completion, the normal message path only warns, and the current default OpenCode model cannot produce hx utilization because its effective limit is missing. Actual requests also encounter task/ownership/telemetry guards. Those are separate failures to reach recovery, not evidence that every provider's compactor is broken. Sources: `hx/__main__.py:192`, `hx/say.py:37`, `hx/context.py:209`, `policy.json:117`, `policy.json:164`, and the execution inventory below.

The strongest current example is the active DeepSeek session. It reached 967,737 input-plus-cache tokens and then auto-compacted; its first subsequent ordinary request used 41,893. Thus native recovery occurred, while hx's intended earlier pressure management did not have usable telemetry for this model. Sources: OpenCode row snapshot in Appendix A; `hx/context.py:214`; `policy.json:164`.

## 1. How the named methods work

### pstack / Lauren Tan

The local upstream pin is `180939f9e59cc9512e9330649175763e81fad16f`, dated 2026-11-03. Its source is `cursor/plugins/pstack`; the requested `github.com/poteto` profile was checked, but the implementation source is the plugin repository. Source: `C:/Users/caspe/.agents/pstack/upstream/UPSTREAM:1` and [the pinned plugin](https://github.com/cursor/plugins/tree/180939f9e59cc9512e9330649175763e81fad16f/pstack).

The guard principle is a prompt-level rule: bound phases, files and turns, and account for mechanism cost. Poteto-mode additionally says bulk reads belong in subagent contexts, with summaries returned to the main thread. New work normally gets a fresh agent; reuse is reserved for expensive live state. Briefs carry consolidated scope and file pointers. This is context prevention and isolation, not a numeric auto-compaction controller. Neither cited rule supplies a token threshold or a keep/drop algorithm. Sources: [guard principle](https://raw.githubusercontent.com/cursor/plugins/main/pstack/skills/principle-guard-the-context-window/SKILL.md), [pinned poteto-mode, lines 65–92](https://raw.githubusercontent.com/cursor/plugins/180939f9e59cc9512e9330649175763e81fad16f/pstack/skills/poteto-mode/SKILL.md).

### Claude Code

Claude Code performs automatic compaction inside the harness; `/compact` can supply a focus, `/clear` removes unrelated conversational baggage, and subagents isolate verbose work. Current official documentation lists about 967K as the default for native-1M models, with model-specific exceptions. `/autocompact`, launch settings and an environment override can request an earlier token boundary. This is current documentation, not proof of the installed session's historical setting; our recorded auto events also occurred below 967K. Sources: [context window](https://code.claude.com/docs/en/context-window), [model configuration](https://code.claude.com/docs/en/model-config#default-auto-compact-thresholds), and Claude event records in section 3.

Compaction replaces conversation history with a summary. Project instructions are reloaded, compact-matched SessionStart hooks run, and recent files can be re-read. Path-scoped material in message history can be summarized away. Auto memory's startup index is limited to the first 200 lines or 25KB; detailed topic files are loaded separately. Sources: [what survives compaction](https://code.claude.com/docs/en/context-window), [memory loading](https://code.claude.com/docs/en/memory).

Subagents have their own context and can return a condensed result, so their raw reads need not fill the parent. This is a different control from compacting the parent after it is already large. Source: [Claude subagents](https://code.claude.com/docs/en/sub-agents).

### Codex CLI

Codex exposes automatic history compaction through `model_auto_compact_token_limit`; the default comes from the model, and current configuration also distinguishes total active context from growth after a carried prefix. `/compact` is the manual entry point. Do not substitute hx's 75% policy for Codex's native model threshold. Sources: [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference), [developer commands](https://learn.chatgpt.com/docs/developer-commands).

The current `ModelInfo::auto_compact_token_limit` implementation caps its model-level limit at 90% of the resolved raw model window, choosing a lower configured model limit when present. Usable window size is calculated separately with headroom; the percentages in our 475K-window logs therefore should not be equated directly with that raw-window clamp. This is a source-level mechanism, not a reconstruction of each historical launch setting. Source: [OpenAI model metadata implementation](https://raw.githubusercontent.com/openai/codex/main/codex-rs/protocol/src/openai_models.rs).

OpenAI's native compaction returns a smaller conversation window containing an opaque encrypted compaction item. That retained state is not a readable, independently auditable checkpoint. Resume continues an existing saved session; it is not itself a promise to discard irrelevant history. `codex resume --last` is scoped to the current directory unless `--all` is supplied. Sources: [compaction API guide](https://developers.openai.com/api/docs/guides/compaction), [resume](https://learn.chatgpt.com/docs/developer-commands#codex-resume).

### OpenCode, pinned to the observed v1.18.27

The live session reports version 1.18.27. Its matching source computes overflow from actual token accounting and model limits, with output headroom; disabling automatic compaction disables that test. This differs from hx's effective cost/attention budget. Sources: Appendix A; [v1.18.27 overflow code](https://raw.githubusercontent.com/anomalyco/opencode/v1.18.27/packages/opencode/src/session/overflow.ts).

That version separately prunes older completed tool outputs after protecting 40K tokens of tool output, only applying a prune when more than 20K would be removed; it protects recent turns and skill output. Summary compaction preserves a recent tail, with a default budget bounded between 2K and 15K, and reconstructs earlier work through the summary. These are native selection rules; hx delegates to them instead of implementing a summarizer. Source: [v1.18.27 compaction implementation](https://raw.githubusercontent.com/anomalyco/opencode/v1.18.27/packages/opencode/src/session/compaction.ts).

Current OpenCode documentation additionally describes a 15% free-space buffer and experimental background summaries. Those newer config names and behavior must not be assumed to exist in the observed v1.18.27 runtime. Source: [current compaction documentation](https://opencode.ai/docs/compaction/), compared with the pinned overflow source above.

### Karpathy and Simon Willison

Karpathy's framing concerns selecting task instructions, tools, retrieved material, state/history and compression for the next step. It does not specify an executable token-trigger policy. The original tweet could not be fetched directly because it returned 403; Willison's contemporary post preserves its attribution and framing. This is an attribution limitation, not a verified Karpathy implementation. Sources: [Willison's context-engineering post](https://simonwillison.net/2025/Jun/27/context-engineering/), [original tweet](https://twitter.com/karpathy/status/1937902205765607626).

Willison's follow-up points to distinct techniques: selective retrieval, selecting tools, isolated contexts, pruning, summarization and offloading. Those tactics address relevance and contamination as well as token capacity. They do not imply a universal percentage threshold. The detailed mechanisms belong to Drew Breunig's linked primary article, rather than being original algorithms by Willison. Sources: [Willison's link post](https://simonwillison.net/2025/Jun/29/how-to-fix-your-context/), [Breunig's primary article](https://www.dbreunig.com/2025/06/26/how-to-fix-your-context.html).

### Matt Pocock

Pocock distinguishes a handoff artifact from an in-context compaction summary. A new session needs explicit decisions and rationale because it cannot recover the predecessor's intent by asking it. His concrete boundary is a ticket or role transition: write the handoff, clear, and start from that artifact. His implement-spec workflow assigns tickets to separate subagents/worktrees. No numeric compaction threshold is specified in these sources. Sources: [handoff](https://www.aihero.dev/ai-coding-dictionary/handoff), [implement-spec](https://www.aihero.dev/skills-implement-spec).

### Addy Osmani

His context-engineering skill organizes persistent rules, feature specs, relevant files, current errors and transient history separately. It recommends compacting well before the host limit without prescribing one cross-host percentage. It cuts obsolete attempts, verbose outputs and superseded drafts first, while retaining the goal, hard constraints, active failure and current file. Conclusions are summarized before detail is dropped. Source: [context-engineering skill, particularly lines 16–30 and 169–209](https://raw.githubusercontent.com/addyosmani/agent-skills/main/skills/context-engineering/SKILL.md).

## 2. What hx actually implements

| Mechanism | Exact behavior | Evidence |
|---|---|---|
| Worker thresholds | Boundary 0.40; pressure 0.75. Pressure can allow an open task, but the worker must be idle/done. | `policy.json:175`; `hx/context.py:434`; `hx/context.py:447` |
| Effective limits | Claude lead 400K; several older OpenCode model IDs 200K. DeepSeek, the persistent default, has no entry. | `policy.json:117`; `policy.json:164` |
| Lead threshold | 0.60 of the effective 400K budget, or 240K tokens. The native percentage display is not that budget. | `hx/context.py:236`; `hx/context.py:258`; `hx/context.py:393` |
| Trigger | `hx context decide` is an explicit CLI call. Task completion writes and validates a checkpoint, then prints the task. It does not invoke the controller. | `hx/__main__.py:128`; `hx/__main__.py:192` |
| Message pressure | `hx say` samples the target, skips the lead, prints a warning on fresh high usage, and still sends. Unknown telemetry is silently tolerated. | `hx/say.py:37` |
| Preconditions | Current task ownership, known/fresh session telemetry, idle pane, acceptable task/pane worktrees, leases and guards. Multiple unfinished tasks refuse selection. | `hx/context.py:306`; `hx/context.py:340`; `hx/context.py:422` |
| Cooldown / loop guard | Defaults are 20 minutes and two reservations per task; a failed attempt also blocks that task's retry. No explicit overrides for these appear in the inspected context policy. | `hx/context.py:388`; `hx/context.py:461`; `policy.json:175` |
| Worker execution | Reserve session, validate checkpoint and authored sections, recheck pane/task, send native `/compact`, demand fresh lower usage in the same session, then demand a nonce plus next-action acknowledgement. | `hx/context.py:501`; `hx/context.py:541`; `hx/context.py:579` |
| Unknown outcome | Preserve a `compact-unknown` lease after a submitted, unverified attempt. Recovery does not erase the no-retry task guard. | `hx/context.py:599`; `hx/context.py:604` |
| Lead execution | Return instructions for checkpoint, delayed background prompt and turn exit. `hx compact` does not perform this self-compaction. Confirmation separately records the lead guard. | `hx/context.py:397`; `hx/context.py:407`; `C:/Users/caspe/.codex/skills/hx-router/SKILL.md:52` |
| Checkpoint fidelity | Completion and later validation compare owned-file status/committed changes and require authored sections. This is stronger than an unchecked prose summary. | `hx/checkpoint.py:466`; `hx/checkpoint.py:518`; `hx/context.py:549` |
| Hooks | Claude PreCompact supplies guardrails and existing valid checkpoints; SessionStart(compact) restores active task state. These react to compaction; neither creates an early trigger. | `team/claude-settings.json:60`; `hooks/claude_precompact.py:1`; `hooks/claude_session_compact.py:130` |

The documented boundary rule is therefore an instruction to the lead, not an enforced task lifecycle event. `hx-router` explicitly asks for a decision at every boundary, but the completion handler does not make that call. This is the operational gap behind “we have context management” being weaker than “it reliably happens.” Sources: `C:/Users/caspe/.codex/skills/hx-router/SKILL.md:48`; `hx/__main__.py:192`.

There is also a structural lead-specific ceiling. The lead always uses the guard task key `lead`; the default two-per-task cap therefore becomes two confirmed self-compactions per provider session, rather than per changing work item. The retained guard for session `2ae4a6ff-a32a-4c42-bde7-0472d1fd1188` already has count two. That session would fail the loop guard on another early decision under this code. This is a code-and-state inference, not an observed refusal in the 13-call project inventory. Sources: `hx/context.py:386`, `hx/context.py:415`, `C:/Users/caspe/.herdr-team/state/compact-guard.json:67`.

### Devin's special case is a verification-order limitation

The code defaults `devin.compact_command` to absent/null and emits `clear-needed`, not an executable compaction decision. The prior live audit says Devin did print a compaction completion at 70K/262K, but its context footer disappeared until another turn. hx asks for fresh lower telemetry *before* issuing the acknowledgement turn, so the generic verifier cannot accept that sequence. This is documented evidence of a native capability with an incompatible verification order, not proof that Devin has no compaction capability. Sources: `hx/context.py:301`; `hx/context.py:469`; `hx/context.py:579`; `docs/audit/devin.md:65`.

The fallback requires checkpoint, `/clear`, and a fresh brief; it is a multi-step instruction rather than an automatic executor. Source: `hx/context.py:263` and `hx/context.py:503`.

## 3. Measured execution records

### Scope and counting rules

The project-specific call inventory uses four lead transcript files associated with the two requested projects. Their names are abbreviated below solely to keep citations readable; the full source paths and exact line references are in the source index. Commands were parsed as shell tokens, not counted by searching documentation text. `--help`, text written into briefs, and commands from the separate agent-memory project were excluded. Sources: [^C6363] [^C9633] [^Cb695] [^Cdab6].

This yields **13 decision invocations and two explicit compaction attempts** in the inventory below. Eight decision outputs capture `keep`, one captures `clear-needed`, and four omit the action. One of those four prints a worktree-ownership guard reason. Do not manufacture the missing actions. The two compaction attempts both capture `keep`, so neither reached a native send. Sources: the 15 rows below; `hx/context.py:505`.

| Project / transcript command line | Captured outcome | Output evidence |
|---|---|---|
| herdr / b695:700 | `keep`, unknown/stale telemetry, empty session ID | b695:701 [^B701] |
| herdr / dab6:133 | `keep`, unknown/stale Devin telemetry | dab6:134 [^D134] |
| herdr / dab6:344 | `keep`, edits outside task ownership, utilization 0.2672 | dab6:346 [^D346] |
| herdr / dab6:387 | `keep`; JSON truncated before the reason | dab6:388 [^D388] |
| Jotarium / 9633:1130 | `keep`, multiple unfinished Devin tasks, utilization 0.8359 | 9633:1131 [^N1131] |
| Jotarium / 9633:1664 | `keep`, utilization printed as 0.68; reason omitted | 9633:1665 [^N1665] |
| Jotarium / 9633:1766 | Action omitted; 0.89 printed, pressure warning followed by another brief | 9633:1767 [^N1767] |
| Jotarium / 9633:1985 | Action omitted; 0.56 printed | 9633:1986 [^N1986] |
| Jotarium / 9633:2090 | Action omitted; 0.70 printed | 9633:2091 [^N2091] |
| Jotarium / 6363:2502 | Action omitted; worktree-ownership guard reason printed | 6363:2503 [^C2503] |
| Jotarium / 6363:4905 | `clear-needed`, no registered hx task, utilization 0.8817 | 6363:4906 [^C4906] |
| Jotarium / 6363:8001, Codex | `keep`, utilization 0.18; reason omitted | 6363:8003 [^C8003] |
| Jotarium / 6363:8001, Devin | `keep`, utilization 0.5878; reason omitted | 6363:8003 [^C8003] |
| Jotarium / 6363:5361, `hx compact` | `keep`, requested task was not current/most recent | 6363:5363 [^C5363] |
| Jotarium / 6363:7541, `hx compact` | `keep`, unfinished task; utilization only 0.1160 | 6363:7542 [^C7542] |

The 89% Devin example is particularly informative: a pressure warning was visible, but the same tool result records delivery of the next assignment. A warning alone does not form a closed recovery loop. Source: 9633:1767 [^N1767]; `hx/say.py:54`.

The two refused Codex requests were below the 40% boundary threshold anyway, at roughly 15.8% and 11.6%. Fixing their task selection would not have made compaction due. They demonstrate workflow mismatch, not an unsafe refusal or proof that guards blocked a necessary compaction. The high-pressure Devin samples are the stronger evidence of blocked or uncompleted early recovery. Sources: [^C5363] [^C7542] [^C4906] [^N1131]; `policy.json:176`.

The two `.hx/routing.jsonl` files contain 84 and 35 records respectively. Their kinds are routing decisions, outcomes, ocrun records and older untyped rows; none is a context-decision audit record. That is **not** proof of zero context calls. The controller prints CLI results and updates guard/lease state; it does not append decision events there. Sources: `.hx/routing.jsonl:1`, `.hx/routing.jsonl:84`, `C:/Users/caspe/Documents/Projects/todo-rpg/.hx/routing.jsonl:1`, `C:/Users/caspe/Documents/Projects/todo-rpg/.hx/routing.jsonl:35`, `hx/route.py:978`, `hx/__main__.py:133`, `hx/context.py:422`.

### How high each family got, and what actually compacted

| Agent / evidence scope | Highest observed context | Completed native events in that scope | What this establishes |
|---|---|---|---|
| Claude lead, current mixed-work session 6363 | 953,388 pre-compaction tokens; highest assistant input/cache sample 953,164 | Two: manual 953,388 → 39,004; auto 550,309 → 36,591 | Native compaction is present, but these events are far above hx's 240K lead boundary. [^C4627] [^C9577] |
| Claude lead, earlier Jotarium session 9633 | 637,458 pre-compaction tokens | Two automatic boundaries, 637,458 → 28,177 and 594,125 → 29,241 | More native recovery, not an hx checkpoint-ack success. [^N2791] [^N6009] |
| Codex, 11 retained project/worktree rollouts starting Oct 31 through Nov 8 | 449,700 input tokens / 475,000 window, about 94.7% | Seven `compacted` records across three rollouts | The native safety net operates; this is not evidence of seven hx-managed compactions. [^Kpeak] [^Kcompact1] [^Kcompact2] [^Kcompact3] |
| OpenCode, current session tied to Jotarium work, directory `Projects` | 967,737 input plus cache-read tokens | One auto summary with `finish=stop`, no error; next normal input 41,893 | A successful native reduction near a million tokens while hx lacks this model's limit. Appendix A. |
| OpenCode, older herdr root session `ses_ebdc0e37bffe2HRdUnfnfQ8GiW` | 310,130 on GLM; 230,102 on Kimi | Two matched completed summaries: one automatic and one manual | Earlier native/manual recovery also exists. These are provider records, not automatically hx-verified successes. Appendix A. |
| Devin pane, Jotarium historical samples | 89%, rounded in captured output; another exact utilization is 0.8816793893 | No verified hx compaction in the listed calls. Clear fallback required. | Real pressure was reached. The exported run totals are cumulative prompt usage, not context high-water marks. [^N1767] [^C4906]; `.hx/runs/hx-20261108-044545/export.json:3350` |

The seven Codex markers are at rollout lines 1178; 873 and 2370; and 1489, 3102, 4463 and 6482. They are completion records, not recall-quality evaluations. Source paths: [^Kcompact1] [^Kcompact2] [^Kcompact3].

At the fresh 05:04:42 UTC status snapshot, the lead had 272,431 current input/cache tokens. The native display said 27%, while hx's 400K denominator makes that 68.1%, already above its 60% threshold. This discrepancy explains why the visible native percentage can look comfortable while hx policy would prefer a boundary. It does not prove a decision ran at that time. Sources: `C:/Users/caspe/.herdr-team/state/claude-status-6363b92e-c30b-4a81-a5d4-d382a2848927.json:3`, `hx/context.py:246`, `policy.json:165`.

The shared guard snapshot has seven session entries and 11 recorded reservation/confirmation counts; its newest `last_at` is 2026-10-24. This is retained state, not a complete invocation log or a count of successful compactions. A guard can record a failed attempt, and the lead confirmation path differs from worker execution. Sources: `C:/Users/caspe/.herdr-team/state/compact-guard.json:1`, `hx/context.py:407`, `hx/context.py:529`, `hx/context.py:485`.

### What the record cannot establish

- A machine-wide exact decision frequency cannot be reconstructed from the routing files, because context decisions are not logged there and some historical output was deliberately truncated. The 13/2 inventory is an observed project-session count, not an all-time total. Sources: routing discussion and transcript inventory above; `hx/__main__.py:133`.
- The 144 currently closed tasks in the two ledgers are not a valid denominator for those calls: ledger history and retained transcript scope differ. No boundary-coverage percentage is claimed. Sources: `.hx/ledger.json:1`; `C:/Users/caspe/Documents/Projects/todo-rpg/.hx/ledger.json:1` and the scoped inventory above.
- The current `.hx` Devin exports expose cumulative token totals, not enough instantaneous context telemetry to infer a peak. Earlier footer samples provide the measured pressure evidence instead. Source: `.hx/runs/hx-20261108-044545/export.json:3350`; [^N1767].
- A small post-compaction token count does not prove the correct constraints or next action survived. The native markers above have not undergone the hx nonce/next-action acceptance test in this audit. Source: `hx/context.py:592` compared with the native event fields cited above.

## 4. Difference analysis

| External mechanism | Our verified counterpart | Verdict and gap |
|---|---|---|
| Harness invokes compaction from its own context accounting. | hx has an explicit decision command and warning; completion does not call it. | **Weaker operationally.** Deterministic thresholds exist, but an agent must remember to run the workflow. `hx/__main__.py:128`, `hx/__main__.py:192`, `hx/say.py:37`. |
| Native model-aware overflow accounting. | OpenCode hx telemetry looks up a hand-maintained model ID table. Default DeepSeek is absent. | **Missing for the active model.** The earlier controller cannot evaluate its threshold. `hx/context.py:214`, `policy.json:117`, `policy.json:164`; pinned OpenCode overflow source above. |
| Pocock's ticket boundary and fresh explicit handoff; pstack's fresh-agent default. | Persistent panes accumulate multiple briefs; task prerequisites can be absent, unfinished or mismatched. | **Weaker boundary discipline.** Those exact guard outcomes appear in the inventory. [^C4906] [^C5363] [^C7542]; Pocock and pstack sources above. |
| Addy's deliberate keep/drop choices and protection of current task state. | Authored checkpoint sections plus a verified next-action acknowledgement. | **Substantially implemented at the worker protocol level**, but not reached in the two observed attempts. `hx/context.py:549`, `hx/context.py:592`; [^C5363] [^C7542]. |
| Native completion events and post-compaction state. | Shared guards hold some outcome state; CLI decisions have no append-only context audit stream. | **Missing operational visibility.** Call coverage, refusals and before/after effects cannot be calculated from `.hx/routing.jsonl`. `hx/context.py:485`, `hx/route.py:978`. |
| Claude reloads instructions/memory and can isolate bulk reads. | Claude hooks restore active task checkpoints; installed pstack also directs context isolation. | **Mechanisms present, effectiveness unmeasured.** This audit does not claim that all startup material is bounded or every hook restored the right project. `hooks/claude_session_compact.py:130`; `C:/Users/caspe/.claude/skills/poteto-mode/SKILL.md:74`; official Claude sources above. |
| A provider-aware verifier accepts the real completion sequence. | Devin drops the footer before hx's verification stage. | **Provider mismatch.** Preserve verification, but adapt its ordering before enabling native Devin compaction. `docs/audit/devin.md:65`; `hx/context.py:579`. |

### Top three gaps, ranked

1. **No enforced early decision loop, with no durable coverage record.** Boundary checking is an instruction in a skill; ordinary dispatch merely warns. Make each eligible boundary produce a persisted decision and ensure that a high-pressure refusal leads to a recorded checkpoint/repair/defer action, rather than simply another brief. This is a proposed shared-layer change, not implemented work. Evidence: `C:/Users/caspe/.codex/skills/hx-router/SKILL.md:48`, `hx/__main__.py:192`, `hx/say.py:37`, [^N1767].
2. **Telemetry does not cover the selected model or expose the same budget as the UI.** Resolve native and effective limits explicitly for every dispatched model, fail visibly on an unmapped model, and show the denominator with the percentage. DeepSeek's missing entry is a concrete fault; the 27%-versus-68% lead snapshot is a separate presentation mismatch. Evidence: `policy.json:117`, `policy.json:164`, `hx/context.py:217`, `hx/context.py:258`, fresh status snapshot above.
3. **Recovery prerequisites and provider verification are not carried through the normal workflow.** Stale or missing task ownership blocks otherwise useful recovery, and Devin's telemetry ordering requires a different acceptance sequence. Keep the safety guards; do not bypass worktree checks or enable an unverified command merely to make compaction visible. Evidence: [^C4906] [^C5363] [^C7542] [^D346]; `docs/audit/devin.md:69`; `hx/context.py:340`.

The evidence does **not** support “no one ever calls it” or “all compaction is broken.” It supports infrequent, manually orchestrated early checking, visible guard refusals, one concrete default-model telemetry hole, and functioning native recovery outside that early workflow. Sources: the complete call inventory, native event table, and `hx/context.py:214`.

## Appendix A. Read-only OpenCode row snapshot

Captured on 2026-11-08 around 05:00–05:04 UTC from `C:/Users/caspe/.local/share/opencode/opencode.db`, opened with SQLite URI `mode=ro`. SQLite has no text line numbers, so record keys and the actual query are provided instead of inventing line references. This block is the measurement evidence for the corresponding rows above. The schema and hx's own accounting query are corroborated by `hx/context.py:209`.

```text
session.id = ses_e470971bbffecwoJ3hRwT9Ii51
session.directory = C:/Users/caspe/Documents/Projects
session.version = 1.18.27
peak message.id = msg_1b981089c001uXmy7VD8PoAWrk
peak input + cache.read = 967737
compaction part.id = prt_1b9812529001fsX7pJ6yn1HC9H
part.time_created = 2026-11-08T04:53:31.049Z
part.type = compaction; auto = true; overflow = false
summary message.id = msg_1b98125b8001WjJHoOy4ogL2rK
summary = true; finish = stop; error = absent
first ordinary nonzero-input message after = msg_1b981da0e001sm1Y2KyUCg4kAw
first ordinary post-compaction input + cache.read = 41893

older session.id = ses_ebdc0e37bffe2HRdUnfnfQ8GiW
directory = C:/Users/caspe/Documents/Projects/herdr-orchestration
GLM peak message.id = msg_142b1a090001DX7L5Lk16ol3JW; input + cache.read = 310130
Kimi peak message.id = msg_1425d895a001FuV1yDxjKBFkWp; input + cache.read = 230102
auto compaction part.id = prt_1425dde750018tSkx3fGZpKmXs
auto summary message.id = msg_1425dde9d001FLbf7wNg5EU1Tx; finish = stop; no error
manual compaction part.id = prt_142b5d8d2001oWM87bOTJteDfa
manual summary message.id = msg_142b5d90b001K7t7DYxNgZNU0q; finish = stop; no error
```

The missing-limit failure was also executed directly through the existing read-only telemetry function, without invoking a controller decision or changing a lease. At 05:12 UTC it returned the following result even though the source record was fresh. Sources: `hx/usage.py:120`, `hx/context.py:167`, `hx/context.py:217`.

```python
# Run from herdr-orchestration with python -B to avoid bytecode writes.
from hx import context, usage
policy = usage.load_policy()
agent = {"agent": "opencode", "agent_session": {
    "value": "ses_e470971bbffecwoJ3hRwT9Ii51"}}
print(policy.get("context_limits", {}).get("deepseek-v4.1-flash"))
print(context.telemetry(agent, policy))
```

```text
None
{'session_id': 'ses_e470971bbffecwoJ3hRwT9Ii51',
 'utilisation': None, 'sampled_at': '2026-11-08T05:12:11.834000+00:00'}
```

The fresh Claude status observation is preserved here because the status file is overwritten in place. Source: `C:/Users/caspe/.herdr-team/state/claude-status-6363b92e-c30b-4a81-a5d4-d382a2848927.json:3`, read at the timestamp shown.

```text
timestamp = 2026-11-08T05:04:42.883861+00:00
sum(current_usage.input_tokens, cache_creation_input_tokens, cache_read_input_tokens) = 272431
context_window.used_percentage = 27
policy context_limits.claude-lead = 400000
computed hx utilization = 0.6810775
```

Reproduction uses `session` for directory/version, `message` for assistant usage and summary completion, and `part` for actual compaction markers. A truthy generic `summary` object is not enough: count only `summary is True`. Cumulative session token counters are not instantaneous context. Source: [v1.18.27 completed-summary predicate](https://raw.githubusercontent.com/anomalyco/opencode/v1.18.27/packages/opencode/src/session/compaction.ts), `hx/context.py:214`.

```sql
SELECT id, directory, version FROM session WHERE id = ?;
SELECT id, time_created, data FROM message WHERE session_id = ? ORDER BY time_created;
SELECT id, message_id, time_created, data FROM part WHERE session_id = ? ORDER BY time_created;
```

## Source index for execution records

These transcripts were read for structured tool calls, result metadata, usage and compaction boundaries. No credential files or private financial records were needed. Home-root Claude sessions span work beyond one repository; only the explicitly identified project command sites enter the 13/2 inventory. Source scope is therefore narrower than all calls ever issued by these panes. Sources: transcript locations below and `hx/paths.py:21`.

[^C6363]: `C:/Users/caspe/.claude/projects/C--Users-caspe/6363b92e-c30b-4a81-a5d4-d382a2848927.jsonl:2502` through the cited command/result sites.
[^C9633]: `C:/Users/caspe/.claude/projects/C--Users-caspe/9633d81c-2b0a-4528-bbf7-753635f6f109.jsonl:1130` through the cited command/result sites.
[^Cb695]: `C:/Users/caspe/.claude/projects/C--Users-caspe/b695d635-94c5-45b5-8306-15c85ee22c6f.jsonl:700`.
[^Cdab6]: `C:/Users/caspe/.claude/projects/C--Users-caspe/dab6c5ce-9924-482a-9bcc-77d010b3131c.jsonl:133` and later cited sites.
[^B701]: `C:/Users/caspe/.claude/projects/C--Users-caspe/b695d635-94c5-45b5-8306-15c85ee22c6f.jsonl:701`.
[^D134]: `C:/Users/caspe/.claude/projects/C--Users-caspe/dab6c5ce-9924-482a-9bcc-77d010b3131c.jsonl:134`.
[^D346]: `C:/Users/caspe/.claude/projects/C--Users-caspe/dab6c5ce-9924-482a-9bcc-77d010b3131c.jsonl:346`.
[^D388]: `C:/Users/caspe/.claude/projects/C--Users-caspe/dab6c5ce-9924-482a-9bcc-77d010b3131c.jsonl:388`.
[^N1131]: `C:/Users/caspe/.claude/projects/C--Users-caspe/9633d81c-2b0a-4528-bbf7-753635f6f109.jsonl:1131`.
[^N1665]: `C:/Users/caspe/.claude/projects/C--Users-caspe/9633d81c-2b0a-4528-bbf7-753635f6f109.jsonl:1665`.
[^N1767]: `C:/Users/caspe/.claude/projects/C--Users-caspe/9633d81c-2b0a-4528-bbf7-753635f6f109.jsonl:1767`.
[^N1986]: `C:/Users/caspe/.claude/projects/C--Users-caspe/9633d81c-2b0a-4528-bbf7-753635f6f109.jsonl:1986`.
[^N2091]: `C:/Users/caspe/.claude/projects/C--Users-caspe/9633d81c-2b0a-4528-bbf7-753635f6f109.jsonl:2091`.
[^C2503]: `C:/Users/caspe/.claude/projects/C--Users-caspe/6363b92e-c30b-4a81-a5d4-d382a2848927.jsonl:2503`.
[^C4906]: `C:/Users/caspe/.claude/projects/C--Users-caspe/6363b92e-c30b-4a81-a5d4-d382a2848927.jsonl:4906`.
[^C8003]: `C:/Users/caspe/.claude/projects/C--Users-caspe/6363b92e-c30b-4a81-a5d4-d382a2848927.jsonl:8003`.
[^C5363]: `C:/Users/caspe/.claude/projects/C--Users-caspe/6363b92e-c30b-4a81-a5d4-d382a2848927.jsonl:5363`.
[^C7542]: `C:/Users/caspe/.claude/projects/C--Users-caspe/6363b92e-c30b-4a81-a5d4-d382a2848927.jsonl:7542`.
[^C4627]: `C:/Users/caspe/.claude/projects/C--Users-caspe/6363b92e-c30b-4a81-a5d4-d382a2848927.jsonl:4611` and `:4627`.
[^C9577]: `C:/Users/caspe/.claude/projects/C--Users-caspe/6363b92e-c30b-4a81-a5d4-d382a2848927.jsonl:9577`.
[^N2791]: `C:/Users/caspe/.claude/projects/C--Users-caspe/9633d81c-2b0a-4528-bbf7-753635f6f109.jsonl:2791`.
[^N6009]: `C:/Users/caspe/.claude/projects/C--Users-caspe/9633d81c-2b0a-4528-bbf7-753635f6f109.jsonl:6009`.
[^Kpeak]: `C:/Users/caspe/.codex/sessions/2026/11/05/rollout-2026-11-05T16-56-26-01a1aaec-762e-7cb0-9f3c-f1b86569c98a.jsonl:4461`.
[^Kcompact1]: `C:/Users/caspe/.codex/sessions/2026/11/01/rollout-2026-11-01T14-13-39-01a195bd-fe28-7330-8a69-894523de635a.jsonl:1178`.
[^Kcompact2]: `C:/Users/caspe/.codex/sessions/2026/11/02/rollout-2026-11-02T14-21-47-01a19aeb-ce1f-7332-b65b-78ada3ec06e1.jsonl:873` and `:2370`.
[^Kcompact3]: `C:/Users/caspe/.codex/sessions/2026/11/05/rollout-2026-11-05T16-56-26-01a1aaec-762e-7cb0-9f3c-f1b86569c98a.jsonl:1489`, `:3102`, `:4463`, `:6482`.
