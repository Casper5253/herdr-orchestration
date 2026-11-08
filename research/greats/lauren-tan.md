# Lauren Tan (poteto)

Principal engineer at Cursor (previously SpaceX/xAI, Netflix, Meta), React Compiler core team, creator of the `pstack` plugin. Ships ~1,000 PRs/month with agents that auto-merge. Source: https://github.com/cursor/plugins/tree/main/pstack (README), https://cho.sh/7D77B5 (transcript of her pstack talk).

## pstack — the skill plugin itself

**What.** A Cursor plugin (`cursor/plugins`, `pstack/` directory) that packages her working method as skills: `.cursor-plugin/`, `agents/`, `assets/`, `automations/benny/`, `docs/`, `skills/`, `plugin.json`. Installed via `/add-plugin pstack` (Cursor) or `/plugin install pstack@cursor-plugins` (Claude Code; commands prefixed `/pstack:`). Her framing: "throughput without quality is not a goal… pstack helps you write less, but higher quality code" and "fearless parallelism" — trust one agent deeply, then multiply.

**How exactly.** The entry point `poteto-mode` (`skills/poteto-mode/SKILL.md`) is a routing skill: it classifies the task against a playbook table (investigation, bug fix, perf issue, hillclimb, runtime forensics, trace forensics, feature, refactoring, prototype, visual parity, authoring-a-skill, eval, babysit, shipping, autonomous run, orchestrate, autopilot-full, autopilot-stack, session pickup, pause safely, multi-phase plan, worktree cleanup, opening a PR), copies that playbook's steps verbatim into a todo list, and routes each step to a leaf skill (`how`, `why`, `recall`, `ketchup`, `blast-radius`, `architect`, `arena`, `swarm`, `interrogate`, `unslop`, `no-comments`, `technical-writing`, `tdd`, `create-verification-skill`, `maintain-verification-skill`, `deslop`, `benchmark-checklist`, plus ~40 `principle-*` leaf skills like `principle-model-the-domain`, `principle-guard-the-context-window`, `principle-make-operations-idempotent`). Principles load on demand only when a rule triggers — "read the leaf skill in full for any principle you apply". Keeping the mode persistent across turns is an opt-in Cursor feature (option+enter → custom mode). Pairs with `/loop` for long runs.

**Sources.** https://github.com/cursor/plugins/tree/main/pstack · https://github.com/cursor/plugins/blob/main/pstack/README.md · https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/SKILL.md

## Verification skills + the feature map

**What.** Per-application "control" skills that let the agent *run the product the way a user does* — drive a CLI, click through an Electron app via Chrome DevTools Protocol, boot an iOS simulator — instead of the human copy-pasting screenshots. Her first one at Cursor was "Control Glass" for the Agents Window. `create-verification-skill` generates one for any app.

**How exactly.** The generated skill drives real surfaces (CDP for Electron/web, Apple simctl utilities for iOS) and ships a **`feature map`** file: a directory the skill builds by exploring the code, mapping user-visible feature names ("left sidebar", "PR tab") to how the agent reaches them in the UI and where the code lives. Motivation from the transcript: the control skill alone was "basically completely useless" — the agent could launch the app but didn't know how to get to any feature, so vague bug reports and screenshots went nowhere. With the feature map, a Slack-channel report or screenshot maps to a reachable feature. `maintain-verification-skill` keeps the map honest: "your verify skill's feature map has drifted from the app" — a source wave plus one live pass.

**Theory.** "The most important skill… is verification… the ability for an agent to actually run the code… however your application is exposed to your users, it can do the same thing and run it for real." Without it "you are the verifier… you're constantly just in the loop and being a bottleneck." It doesn't guarantee *good* code but it guarantees *correct* code.

**Sources.** https://cho.sh/7D77B5 (~0:00–22:00 segments) · https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/eval.md · repo `skills/` entries for `create-verification-skill` / `maintain-verification-skill` (https://github.com/cursor/plugins/tree/main/pstack/skills)

## Evals as "unit tests for skills" + hill-climbing

**What.** An `eval` playbook inside pstack for testing whether a skill or prompt change actually changed agent behavior — deliberately *blinded*.

**How exactly.** From the README skill table: `eval` — "test how a skill or prompt change affects agent behavior, blinded." From the transcript: she built the Control Glass skill CLI by iterating against an eval — "you can use like slash loop in Cursor and you can say okay keep looping on this eval right until everything is 10 out of 10" — i.e., define scored cases, run the skill against them, judge each run, loop until the score saturates. Denis's recap she confirmed: build eval/verification locally first where you can watch the agent act.

**Theory.** Skills are code-shaped prose; a prompt edit can silently regress them. Verifiability is the property that lets you improve anything — same argument Karpathy makes.

**Sources.** https://cho.sh/7D77B5 (~22:00) · https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/eval.md

## The trust curve / trust ladder (how she got to auto-merge)

**What.** A staged journey: heavily-in-loop single agent → trusted local verification → cloud agents → automations → auto-merging PRs, reviewed on main.

**How exactly.** Stage it: (1) stay local and watch the agent act on the real app; (2) encode your own engineering standards into skills and *verify the agent applies them* — "when you are able to encode all of that into skills and you can verify that your agent is actually doing them that allows you to really kind of ascend this curve"; (3) move to cloud agents once trust exists — "don't try to jump from… in-loop… to I'm going to spawn a hundred or thousands of cloud agents right now… you're just going to waste a lot of tokens"; (4) automations pick up signals on their own (Benny); (5) auto-merge — "I woke up today and there were like 20 PRs landed and I just reviewed them on main." No shortcut: "this is really about your personal level of trust in agents."

**Sources.** https://cho.sh/7D77B5 (opening through ~28:00)

## benny — the Slack-triaging automation pack

**What.** A dormant automation pack shipped inside pstack at `automations/benny/` that triages Slack issue reports, reproduces them against the app, and reports back.

**How exactly.** From the README's automations section: "benny triages slack issue reports, then …" — the pack is copied into your repository at `.cursor/automations/benny/`, enables pstack there for shared skills, keeps user configuration outside the copied pack, and runs on Cursor automations (Cursor-only feature). In the transcript she describes the value loop: Benny "opens up a cloud… runs cursor in its own computer and it uses the same control skills to interact with the application and try to reproduce the bug" — e.g. it reproduced a report, found it already fixed on main, and told her to just ship another build. The reuse of the *same* control/verification skills by the automation is the mechanism: what a human-verifying agent can do locally, a fleet agent can do unattended.

**Sources.** https://github.com/cursor/plugins/blob/main/pstack/automations/benny · https://github.com/cursor/plugins/blob/main/pstack/automations/benny/FOR_AGENTS.md · https://cho.sh/7D77B5 (~24:00)

## "Dune" — constraint-as-code architecture for agent-written apps

**What.** An internal framework/mental model for Grokbot: "like Next.js for Electron apps… designed for agents to write." Not open source — "a collection of ideas and principles" she suggests screenshotting and having your agent replicate.

**How exactly.** Layered enforcement, hard before soft:
1. **Codebase conventions** — features are nouns of the framework (feature, entry point, transcript card); "a feature is all in a single directory… all co-located in one place" so agents work encapsulated ~80% of the time; "designed… for the dumbest agent."
2. **Static analysis / CI (hard red)** — literal bans: `useEffect` banned outright; *code comments banned* ("99% of the time agents just write code comments that… describe some historical thing that is totally irrelevant"); an `electron-main/` vs `electron-renderer/` directory split with an **import-boundary CI check** on the dependency graph so heavy work can't leak onto the renderer's 16 ms frame budget; lints for every bad pattern observed; compiler diagnostics.
3. **Soft layers** — rules, skills, style guide, and Bugbot (Cursor's CI code-review bot): "your agents can still forget… it's very, very soft… if you only have rules and Bugbot and skills… it's only a matter of time before your codebase looks like complete trash."
4. **The meta-rule**: every time a human has to flag something in review, "consider that as a code smell… how do I turn this into a hard rule, a lint rule, a CI failure, or categorically eliminate this problem entirely." Worst place to be is "code review land" where humans enforce invariants.

**Theory.** "The shortest path is the best path" — agents take shortcuts, so make the shortcut the correct route. Greenfield vibe-coded apps get "organic architecture" optimized for shortcuts and spiral; strict scaffolding early is what lets agents absorb "a ton of constraints… it's actually very annoying to write code in Grokbot, but agents absorb all of that annoyance." She spent ~600 PRs refactoring Grokbot into this before auto-merge was safe. Also: strict toolchains (Rust's borrow checker) raise the floor.

**Sources.** https://cho.sh/7D77B5 (~28:50–46:00) · https://x.com/poteto/status/2118017093677494706 (slides on agent-friendly architecture)

## Atomic PRs and git history as context

**What.** Small, single-purpose PRs, multiple per task rather than one giant diff.

**How exactly.** "I do encourage my agents to split up their work into multiple PRs… I like the idea that the git history is a very rich source of context… each PR [should] atomically describe what that small piece of thing is doing, which also makes it easier for me to revert changes… it's not in this 40,000 line PR where who knows what landed in there." No hard size cap; observed range ~50–1,000 lines.

**Sources.** https://cho.sh/7D77B5 (~35:20–37:13)

## Public code to reuse

- `cursor/plugins/pstack/` — the whole plugin: skills, agents, playbooks, principles, benny pack. https://github.com/cursor/plugins/tree/main/pstack
- `pstack/skills/poteto-mode/SKILL.md` — the router skill; playbook table + trigger rules. https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/SKILL.md
- `pstack/skills/` — leaf skills incl. `create-verification-skill`, `maintain-verification-skill`, `eval` playbook, principles. https://github.com/cursor/plugins/tree/main/pstack/skills
- `pstack/automations/benny/` + `FOR_AGENTS.md` — automation pack template. https://github.com/cursor/plugins/tree/main/pstack/automations/benny
- Claude Code adapter generated copy at `harness/pstack/` in the same repo (README references it). https://github.com/cursor/plugins/tree/main/harness/pstack
- Dune architecture: not public; described only in the talk transcript. https://cho.sh/7D77B5
