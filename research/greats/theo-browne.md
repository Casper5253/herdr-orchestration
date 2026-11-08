# Theo Browne (t3.gg)

Founder of T3 Chat and T3 Code; creator of the T3 stack; runs many parallel agents and builds for agents as customers, not just tools. Source: https://ai.engineer/speakers/theo-browne · https://www.youtube.com/watch?v=-g1yKRo5XtY ("How I code with AI right now")

## Model-era model of workflow: steps → goals → orchestration

**What.** His organizing frame for how the work itself changes per model generation: Sonnet 3.5 = reliable tool calls (multistep coding useful day to day); Opus 4.5 = sustained long-running work (state the goal instead of directing each step); Mythos = orchestration (decompose work, spawn model workers, verify results).

**How exactly.** The operational claim is about prompt granularity, not tooling: "prompting for that delegation and verification can be enough; a bespoke software factory is not a prerequisite." The capability only shows on *larger* assignments — "larger assignments reveal capabilities that familiar tickets may never exercise" — most Jira tickets from his old job were already within Opus's reach, so orchestrating models shows nothing extra at ticket scale. Consequence: when the model improves faster than your habits, "the response is to expand the scope of what they attempt."

**Theory.** Capability is exercised by ambition of the task, not by cleverness of the prompt. He retracted his own earlier "capability wall" prediction on stage.

**Sources.** https://ai.engineer/talks/xUnRQ9vLXxo-everything-we-knew-about-software-has-changed (§01, transcript + slides) · https://www.youtube.com/watch?v=xUnRQ9vLXxo

## Scheduled service → a Markdown file ("GBrain tier")

**What.** Replacing a bespoke recurring service with a written goal the agent runtime executes on a schedule — his most concrete reusable pattern.

**How exactly.** He had a service that triaged PRs, arranged AI reviews and prioritized his work. He replaced it with written instructions (verbatim from the talk write-up):
1. Visit four GitHub repositories and inspect their open pull requests.
2. Determine the current status of the work and help prioritize it.
3. Update a static HTML file with the resulting overview.
4. Upload that file to S3 and return its URL.

"Markdown supplies the task description; the agent runtime supplies interpretation, tools, and execution. The file is not itself an execution engine." A daily 9 AM cron run produces his overview around 9:15–9:20. The design point: no maintained application logic — repo inspection, prioritization, report generation and publication are *described as a goal*, so a recurring task can shrink to an instruction file invoked by cron.

**Sources.** https://ai.engineer/talks/xUnRQ9vLXxo-everything-we-knew-about-software-has-changed (§06)

## Rejecting work without guilt — throw away a branch, try again

**What.** Because code is cheap now, discarding a bad attempt and regenerating is a normal move, not a sunk-cost failure.

**How exactly.** In his day-to-day workflow video he describes settling into a Cursor-based flow covering "planning, execution, how I do reviews, how I use work trees, how I've kind of changed how I use git on a fundamental level" — parallel attempts in worktrees, keeping the good one. The talk's "rejecting work without guilt" chapter is the same principle at team scale: an agent's output costs tokens, not evenings, so the reviewer's job is picking, not salvaging. His explicit caveat: "this is not a vibe coding tutorial… this is how to use these tools as an existing productive software engineer."

**Sources.** https://www.youtube.com/watch?v=-g1yKRo5XtY · https://rosetta.to/u/t3dotgg/how-i-code-with-ai-right-now (transcript mirror) · https://ai.engineer/talks/xUnRQ9vLXxo-everything-we-knew-about-software-has-changed (§04)

## Turn fuzzy goals into evals; hill-climb with a council

**What.** His method for improving things that can't be unit-tested — e.g. a tool-description or a UI — by writing an eval and iterating a multi-model council against it.

**How exactly.** Two concrete artifacts: the tool-description eval (his X thread on evaluating and hill-climbing tool descriptions) and the council UI hill-climb (multiple models critique/score candidate designs against the stated goal; iterate until the metric moves). Pattern: name the goal measurably, generate candidates in parallel, let a council judge them, keep the best, repeat — a manual arena+eval loop rather than a framework.

**Sources.** https://x.com/theo/status/2114955695434993702 (the eval and hill climb thread) · https://finance.biggo.com/podcast/8b11242133423fc5 (council UI hill-climb episode)

## Skeuomorphism test: familiar tools as inherited constraints

**What.** A diagnostic for workflow cargo-culting: the iOS 6 compass looked like a physical compass to earn trust; once accepted, the interface could optimize for the task instead.

**How exactly.** Apply it as a question — "does this convention serve the work, or survive because we stopped questioning how our tools divide it up?" His examples: terminal/Vim attachment as programmer-image rather than suitability (natural-language interaction belongs elsewhere); the `.env` file question — why does config sharing need a separate system when the project travels through git (the point is the fragmented workflow, not permission to commit secrets); language identity (junior: "I write JavaScript"; senior: sneering at it) as a hiring-scarcity artifact.

**Sources.** https://ai.engineer/talks/xUnRQ9vLXxo-everything-we-knew-about-software-has-changed (§02–03)

## Breadth as product strategy; let users build the depth

**What.** Strategic consequence of cheap code: small teams can now cover horizontal *breadth* (range of areas) that used to need thousands of engineers, and should design the product so users supply the missing *depth* (features per area).

**How exactly.** "With enough prompting and effort, a database platform can be added to a product in a day or two" — his caveat is explicit: not RDS-grade reliability, but enough surface for users to start trying. Then the depth comes from extensibility — his example is Slack's bot APIs letting people build agent workflows the vendor never anticipated. Named targets at the close: Slack, AWS, Salesforce — "test that assumption with broader products that users can extend." Related: his fullstack cloud Lakebed (announced at the talk; docs now live).

**Sources.** https://ai.engineer/talks/xUnRQ9vLXxo-everything-we-knew-about-software-has-changed (§07–09) · https://ai.engineer/speakers/theo-browne · Lakebed docs (linked from the talk's updates)

## The harness is the product; judgment over typing

**What.** T3 Chat/T3 Code are built on the thesis that the harness — aggregation, routing, orchestration across models and subscriptions — is the durable product, while the human's job moves to judgment as code gets disposable.

**How exactly.** One interface over many models (T3 Chat aggregates models/subscriptions); the engineer's leverage is taste in goal-setting and picking between generated alternatives — consistent with his "not a vibe coding tutorial" framing: read the plan, demand understanding, keep the pick human.

**Sources.** https://ai.engineer/talks/xUnRQ9vLXxo-everything-we-knew-about-software-has-changed · https://www.youtube.com/watch?v=-g1yKRo5XtY

## Public code to reuse

- T3 Chat / T3 Code — products, not libraries; the agent-harness UX is the reference. https://ai.engineer/speakers/theo-browne
- Lakebed — fullstack cloud (TypeScript capsules, database, auth, runtime); docs linked from the talk page.
- The Markdown-instructions + cron pattern needs no code: the four-step instruction file in §06 is the reusable artifact.
- Tool-description eval and council hill-climb: described in the X thread https://x.com/theo/status/2114955695434993702 and https://finance.biggo.com/podcast/8b11242133423fc5 — no published harness code found; the method itself is the take-away.
