---
name: project-workflow
description: The creator's workflow for a new project or a large new piece of work. Plan first, show something early, build in verifiable units, document at the boundary, revise on feedback. Trigger on "new project", a new idea moving to work, or any effort that spans more than a few briefs.
---

# Project workflow

Every project, not just this one. Five phases: plan, see, build, document,
revise. Artifacts live under `.artifacts/<task>/` in the project repo;
briefs reference them by path instead of repeating them. Jotarium tracks the
project item with one child item per phase.

The standing rule: **see first, then judge.** When the next question could be
answered by showing something, show it. A question goes to the creator only
when no prototype can answer it — never because a decision feels
uncomfortable.

## 1. Plan — before anything is created

Open `.artifacts/<task>/spec.md` and write, in this order:

- **Goal** — one sentence: what exists after this that does not exist now.
- **For whom** — the creator, the team, or a named other user.
- **Smallest version that proves it** — the cut that would make the creator
  say "yes, that". Not the smallest easy thing; the smallest convincing one.
- **Risks** — the two or three ways this most likely fails, each with a sign
  to watch for.
- **Ambitious version** — written once, then parked: ask what the goal beyond
  the goal is (Felix's altitude move — "keep our apps crash-free", not "fix
  this crash"), and write the version that would serve it. The parked version
  is a reference for scoping, not a commitment.

Create or update the Jotarium project item and `plan_idea` it if the work
came from an idea. The spec is the gate: no build starts until it exists.

## 2. See — show before debate

Before any long discussion of approach, build the cheapest thing that makes
the shape visible:

- a throwaway prototype, a mock, a sketch, or a five-line output sample;
- keep it disposable — it answers "is this the right shape", nothing more;
- put it where the creator can look at it and say what to keep.

Two outcomes only: the prototype is promoted into the plan, or it is deleted
and the spec changes. Either is success — a rejected sketch costs an hour, a
rejected architecture costs a week.

## 3. Build — verifiable units

Write `.artifacts/<task>/plan.md`: the ordered task list where each task is
one verifiable unit (a test that passes, a screen that works, a measured
number). Then work the list:

- route each task to a poteto-mode playbook and run through `hx` — briefs
  name the spec/plan paths, not their contents;
- verify each unit the way the brief says before moving on;
- keep units small enough that a wrong one is obvious inside a day;
- amendments go into spec.md or plan.md as edits, never as chat-only
  messages — the artifact is the record.

## 4. Document — at the boundary, in the right store

When a unit lands or a decision is made:

- **In the repo:** README for what the project is and how to run it,
  `AGENTS.md` for the paved path (build, test, verify commands), and a short
  decision entry in `.artifacts/<task>/` when a choice will be questioned
  later. Reference, don't copy — link the artifact, don't restate it.
- **In agentmem:** project-level knowledge that outlives the repo — why a
  direction was rejected, preferences the creator stated, cross-project
  lessons. `mem note` before the report, per the herdr-team rule.

## 5. Revise — feedback becomes checks

- Review nontrivial work cross-family (`hx review open/verdict`); a verdict
  names its snapshot and goes stale when the artifact moves.
- Creator feedback lands as typed review in Jotarium (check/decide/do) —
  Needs you is for real decisions only, not for showing progress.
- A correction that will happen again becomes a check through the `correct`
  skill — architecture first, then types, then lint, then test, docs last.
- When the shape itself is wrong, go back to phase 2 and show the new shape
  instead of arguing the old one.

## What this does not do

- It does not replace poteto-mode playbooks — it routes work to them.
- It does not replace kickoff, Jotarium, or hx — it names when to use them.
- It does not add meetings, status updates, or approval gates. The spec gate
  and the verifiable-unit list are the only checkpoints.
- Questions still go to the creator — for real decisions, resource asks, or
  taste calls a prototype cannot settle. Never for "should we or shouldn't
  we" when a sketch would answer it.
