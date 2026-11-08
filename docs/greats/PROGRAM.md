# Program: learn from the greats, blank slate (Q-199, ideas I-104 and I-6)

## The creator's direction (8 Nov 2026, verbatim)

"Assume nothing these people present as ideas is implemented. Research EXACTLY how they do what they do for all stated solutions/skills. Determine if that is how we do it. If so, then flag it as implemented. If not, determine the gap. Understand that these people are much much smarter and better at this stuff than me so i would almost implicitly prioritize their approach over my current one. Point is, Do everything. Start blank slate, gather all materials, concepts, theory and publicly available code needed to do implement all these skills, then do a difference analysis, then do the implementation. For example, yes we have context management. It works poorly. i barely see smart compaction happen. That means its faulty. So saying we have it, while its not strong enough, is not the full picture. Thats why i want to invest in this and make this a big research implementation refactoring approach. ALSO: tie this in with the project idea I-6."

I-6: "Create a dedicated project workflow approach. Include things like: Planning phase (before anything is created), Documentation, Work incl revision. Clear guidelines in line with my way of working (see first then judge instead of stuck in should we shouldnt we mode forever). basically tailor my way of working to ME".

Standing rule: every change lands in a shared layer and reaches all projects (pstack, the global agent instructions, hx, agentmem, repo templates). Jotarium is one example, never the target.

## People

Lauren Tan (poteto, pstack), Matt Pocock, Theo (t3.gg), Andrej Karpathy, Felix Rieseberg, Simon Willison, Addy Osmani. Add others only when the creator's list clearly implies them.

## Phases

1. **Gather (blank slate).** For each person, every solution or skill they present: what it is, exactly how it works (mechanism, files, prompts, config, code), and primary sources (their repos, posts, talks, threads). No verdicts on our setup yet. Output: `research/greats/<person>.md`.
2. **Measure ours.** For each capability that phase 1 surfaces (context management and compaction, verification, evals, memory, review, planning, delegation, the project workflow), measure how our setup actually behaves from logs and runs, not from what the docs claim. Context management goes first: the creator sees smart compaction rarely happen.
3. **Difference analysis.** One row per capability: their method, our method with evidence, and the verdict (implemented as they do it / weaker, with the gap / missing). Their approach wins by default unless the evidence shows it does not fit. Output: one creator-facing HTML page.
4. **Implement.** Close the gaps in the shared layers, in ranked order, each change verified. The project workflow (I-6) is one of the deliverables: planning, documentation, and work including revision, built from the gathered methods and fitted to "see first, then judge".

## Rules for every worker

- poteto-mode. Cite a real URL or file for every claim; never invent a source, quote or repo.
- Research files are for machines (markdown). Anything the creator reads is HTML.
- Phases 1 to 3 change nothing outside `research/greats/` and `docs/greats/`.
