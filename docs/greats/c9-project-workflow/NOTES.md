# C9 project-workflow — draft notes

Draft of `project-workflow/SKILL.md` for the lead to review and install at
`~/.agents/pstack/local/project-workflow/SKILL.md`. Written against the
c9-workflow brief; nothing outside `docs/greats/c9-project-workflow/` was
touched.

## Sources

- `docs/greats/changes.md` §C9: the artifact chain — idea → optional
  prototype spike → `spec.md` → `plan.md` → task list → work + revision →
  docs note; `.artifacts/<task>/`; briefs reference by path.
- `research/greats/diff-devin.md` §4: the disagreement — Addy's spec gate
  vs Felix/Theo's prototype-first. The draft keeps both: spec gates the
  build, and phase 2 keeps a cheap see-first path before any long debate.
  Our side already has typed Jotarium review encoding "see first, then
  judge" — the draft makes it the standing rule.
- `research/greats/felix-rieseberg.md`: the ambition tutorial (idea I-5) —
  altitude ("the goal beyond the goal") becomes the one-shot ambitious
  version in the spec, written once and parked rather than debated.
- `research/greats/matt-pocock.md`: reference-not-copy for artifacts, and
  the four handoff triggers (C10 already ships `hx handoff`).
- `research/greats/context-management.md` §4: our gap — no per-task
  artifact chain, amendments as chat messages, no ADR habit. Phases 3 and 4
  target exactly those.
- Creator's idea I-6 verbatim (the brief): planning phase before anything,
  documentation, work incl. revision, "see first then judge instead of
  stuck in should we shouldnt we mode forever", every project not just one.

## Choices

- **Five phases, not the six-node chain.** Plan absorbs idea+spec; see is
  the optional prototype as a first-class phase rather than a parenthetical
  because it is the creator's signature move. The `.artifacts/<task>/` file
  names match the C9 spec (`spec.md`, `plan.md`) so later tooling can count
  briefs that link them.
- **The standing rule is written as behavior, not slogan.** "A question
  goes to the creator only when no prototype can answer it" is the
  operational form of "see first then judge" and matches Needs-you-only-
  for-real-decisions in CLAUDE.md's Jotarium section.
- **Pointers, not repetition.** poteto-mode, kickoff, Jotarium, `hx`,
  `hx review`, `hx handoff`, `correct` are named, not restated — the
  change doc says reuse, and repeated instructions drift (C8 measured it).
- **~115 lines** of steps; the "what this does not do" section carries the
  anti-overreach rules instead of burying them in prose.

## Open for the lead

- Whether `spec.md`'s ambitious-version block stays parked-only or gets a
  promotion path into `plan.md` when the smallest version lands.
- Whether `hx doctor` or a lint should later count briefs that link a
  spec/plan artifact — the C9 proof metric needs a counter eventually.
