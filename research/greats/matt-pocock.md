# Matt Pocock

TypeScript educator (Total TypeScript, AI Hero), ex-Vercel. Runs the `mattpocock/skills` repo (~317k stars, MIT) — "Skills for Real Engineers", his own `.agents` directory published. Source: https://github.com/mattpocock/skills · https://aihero.dev/skills

## mattpocock/skills — a personal, composable skills library

**What.** A public repo of the skills he runs daily: "My agent skills that I use every day to do real engineering — not vibe coding." Deliberately positioned against heavyweight frameworks: "Approaches like GSD, BMAD, and Spec-Kit try to help by owning the process. But while doing so, they take away your control and make bugs in the process hard to resolve. These skills are designed to be small, easy to adapt, and composable."

**How exactly.** Layout: `skills/<category>/<name>/SKILL.md` plus per-harness adapter files (e.g. `skills/productivity/handoff/agents/openai.yaml` carrying `policy.allow_implicit_invocation: false`), and a `docs/<category>/<name>.md` companion per skill that explains when to reach for it vs alternatives. Installation three ways: Claude Code plugin (`claude plugin install mattpocock-skills@claude-plugins-official`, auto-updates), Codex plugin (`codex plugin marketplace add mattpocock/skills`), Copilot plugin, or `npx skills add` — plugins update themselves; `skills.sh` copies editable files you update by hand. "Pick one per agent, because installing both gives you every skill twice." Skills split on one axis: **who can invoke them** — *user-invoked* (typed by you, e.g. `/grill-me`, `/handoff`; their job is to orchestrate) vs *model-invoked* (the agent calls the Skill tool itself).

**Sources.** https://github.com/mattpocock/skills · https://www.skills.sh/mattpocock/skills/handoff

## The smart zone / dumb zone — context-window economics

**What.** His central model of agent performance: attention degradation means only part of the context window is usable; spend tokens in the "smart zone" and treat the "dumb zone" boundary as a workflow signal.

**How exactly.** Mechanism as he explains it: every token added creates attention relationships to every other token (5 tokens → 10 relationships; 10,000 tokens → ~1M), so performance sinks gradually — hallucinations up, retrieval down, worse code. "A useful rule of thumb is around 150,000 tokens" for where the dumb zone begins (he revised this up from ~100k six months prior; others frame the smart zone as the first 40–50%). At the boundary: "clear the context, compact the context, hand off, or think on how you can work your workflow differently to design tasks that don't need that much context window." Consequence for orchestration: "The dream for any coding agent is to do the same amount of work for fewer tokens. That means more time in the smart zone… Subagents are so elegant a solution that you'll see them everywhere, forever."

**Sources.** https://www.youtube.com/watch?v=sOd7svdu_1I (What is the dumb zone?) · https://x.com/mattpocockuk/status/1976380948393373991 (fewer tokens / smart zone post, quoted via https://acairns.co.uk/notes/2026/02/19/subagents-and-the-smart-zone)

## `/handoff` — portable context as a file

**What.** A user-invoked skill that compacts the live conversation into a handoff markdown document for a *different* agent/session to pick up.

**How exactly.** Full SKILL.md (16 lines, quoted): "Write a handoff document summarising the current conversation so a fresh agent can continue the work. Save to the temporary directory of the user's OS (`$TMPDIR`, else `/tmp`; `%TEMP%` on Windows) — not the current workspace. Include a 'suggested skills' section… Do not duplicate content already captured in other artifacts (specs, plans, ADRs, issues, commits, diffs). Reference them by path or URL instead. Redact any sensitive information…" The `argument-hint` is "What will the next session be used for?" and args tailor the doc. The `docs/` page is precise about scope: it gives **portability, not compression** — only four triggers: swapping harness (Claude → Codex), moving directory/repo (prototype hand-off), sending work to a colleague, and **forking a side task** (you keep working; a second agent takes the fork). Everything else → `/compact` or a fresh subagent.

**Sources.** https://github.com/mattpocock/skills/blob/main/skills/productivity/handoff/SKILL.md · https://github.com/mattpocock/skills/blob/main/docs/productivity/handoff.md · https://www.youtube.com/watch?v=dtAJ2dOd3ko (his walkthrough)

## Cursor rules — workspace vs global, with real rule text

**What.** His `.cursor/rules` set for TypeScript AI development, published because community rule directories were "low-quality, underwritten, low on code examples."

**How exactly.** Two-tier split: **Workspace Rules** — versioned in git, shareable, project-specific; **Global Rules** — live in the IDE, personal style. Content focus: "TypeScript language features, documentation, structure (like Result types), teaching the AI specific nuances (like `noUncheckedIndexedAccess`), and practical habits, rather than specific frameworks." Example rule verbatim:

```
# Return Types
When declaring functions on the top-level of a module,
declare their return types. This will help future AI
assistants understand the function's purpose.
const myFunc = (): string => { return "hello"; };
```

(with a JSX-component exception). The pattern that matters: rules are short, carry *code examples*, and teach nuance the model gets wrong rather than restating style.

**Sources.** https://www.totaltypescript.com/cursor-rules-for-better-ai-development · https://www.totaltypescript.com/should-you-declare-return-types

## Types as the AI guardrail

**What.** The argument that TypeScript is *more* necessary in the AI era, not less — types constrain what agents can emit.

**How exactly.** Types make illegal states unrepresentable rather than prompt-policed; compile errors give the agent a self-correcting signal without a human in the loop. Related habit he recommends: *more* abstractions, not fewer — "we should use MORE abstractions in the AI age. You can use them (combined with harsh lint rules) to reduce the design space available to the agent and constrain them only to good decisions. Combined with the fact that high-leverage abstractions let you do more with less code — so, more token efficient. Plus, unwinding the damage from a bad abstraction is much cheaper with agents." And: turn repeated tasks into custom tooling — "agents delegating tasks to scripts they own and control. Means fewer tokens, fewer hallucinations."

**Sources.** https://www.totaltypescript.com/the-case-for-typescript-in-the-ai-coding-era · https://www.techtwitter.com/tweet/19c5ef12-c85b-4bbd-8753-bb846b9efd71 (abstractions post) · https://www.techtwitter.com/tweet/dc96eb25-a871-4800-bf2b-0f2743cf6e25 (CLI tooling post)

## Evals: "check behavior, not execution" — skeptic converted

**What.** His stance on evals was hostile until shown a working loop; the useful version is a judge agent scoring real behavior.

**How exactly.** From the Mastra live review: "'Evals are usually fluff. No one shows how to make them useful.'" Then shown a working setup — a test agent runs tasks inside a real workflow, a **judge agent scores output**, and the score controls retries or path changes. His test was edge cases; his verdict: "'You're checking behavior, not just execution.'" The system prompt framing he also uses: agents as interns needing supervision — "Managing AI interns with types."

**Sources.** https://mastra.ai/blog/matt-pocock-meets-mastra · https://www.youtube.com/embed/w-aNPblqg94 · https://www.youtube.com/embed/rmc0v4Fy5pI (embedded session videos)

## "The system prompt is the LLM's UI"

**What.** Framing skills/rules/prompts as a designed interface to the model, not a bag of hints.

**How exactly.** Skills, `AGENTS.md`/`CLAUDE.md`, rules files and briefs together form the product surface the model works against — so they deserve product-grade design: small, composable, example-rich, and versioned. This is why his repo favors many tiny skills over monolithic frameworks.

**Sources.** https://bsky.app/profile/did:plc:oeio7zuhrsvmlyhia7e44nk6 (his Bluesky profile; the post is linked from the Q-193 page) · https://github.com/mattpocock/skills

## Public code to reuse

- `mattpocock/skills` — whole library, MIT. https://github.com/mattpocock/skills
- `skills/productivity/handoff/SKILL.md` + `docs/productivity/handoff.md` + `agents/openai.yaml` — full handoff mechanism. https://github.com/mattpocock/skills/tree/main/skills/productivity/handoff
- Repo layout convention: `skills/<category>/<skill>/SKILL.md`, `docs/<category>/<skill>.md`, per-harness `agents/*.yaml` adapters.
- Cursor rules set: linked from https://www.totaltypescript.com/cursor-rules-for-better-ai-development
- AI Hero: https://aihero.dev/skills (course "AI coding for real engineers" — paywalled; repo is the free artifact).
