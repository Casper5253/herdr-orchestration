# Shared skill evals

C3 first slice: frozen, blinded promotion evals for the three skills the team uses most, `poteto-mode`, `herdr-team` and `hx-router`.

The measured gap: more than 40 pstack skills were never invoked in 14 days, and no shared skill or prompt change was ever tested before it went to all agents. This harness makes a skill change earn its promotion. It borrows agentmem's approach: frozen case hashes, an isolated workspace, neutral copies, a hidden side label, repeated deterministic checks, and an explicit before/after verdict.

## Files

- `run.py` - the runner and the only command the sync gate needs.
- `cases/<skill>.json` - the frozen cases for one skill.
- `cases/FROZEN.sha256` - SHA-256 of every case file; the runner refuses a changed case until it is re-frozen deliberately.
- `fixtures/poteto-mode-broken.md` - a copy of the installed skill with the final-report goal sentence removed and the Investigation route inverted to write code. Used only for the proof run.
- `fixtures/proof-unchanged.json`, `fixtures/proof-broken.json`, `fixtures/gate-identical.json` - result artifacts from the two proof runs and a full identical-side gate run.

## Run

Evaluate one skill by file:

```powershell
python evals/skills/run.py --skill poteto-mode --before "$HOME\.claude\skills\poteto-mode\SKILL.md" --after "<candidate>\poteto-mode\SKILL.md"
```

Evaluate every covered skill by directory:

```powershell
python evals/skills/run.py --skill all --before "$HOME\.claude\skills" --after "<staged-skills-dir>"
```

The runner invokes `opencode run` with `opencode-go/deepseek-v4.1-flash` by default. Override with `--model` or `--timeout`. Use `--case <id>` (repeatable) to debug one case. It copies the skill and case into a fresh temporary directory under neutral names, randomizes which side runs first, and never puts "before" or "after" in the model prompt.

Each case runs `--repeat` times per side (default 3) and a side passes the case only on a majority. Add `--out result.json` to write an artifact with the model, repeat count, per-case votes and the verdict.

Exit codes:

- `0` - promote: the candidate passes at least as many cases as the installed skill.
- `2` - hold: the candidate passes fewer cases.
- `1` - error: a case file is not frozen, a skill path is missing, or a model run failed.

## The sync gate command

The one command the sync gate calls:

```powershell
python evals/skills/run.py --skill all --before "<installed-skills-dir>" --after "<staged-skills-dir>"
```

The gate must treat exit `2` as a failed promotion and must not run `--freeze`. The lead wires this call into `sync.py` after this slice lands.

## Add or change a case

1. Edit `cases/<skill>.json`.
2. Add or change one case with an `id`, `prompt` and `checks`.
3. Re-freeze deliberately: `python evals/skills/run.py --freeze`.
4. Run the skill against itself (both sides pass equally) and against a deliberate break (the break must hold).

Checks:

- `must` - regexes that must match anywhere.
- `must_not` - regexes that must not match anywhere.
- `must_in_opening` - regexes that must match in the first `opening_lines` non-empty lines (default 2).
- `max_nonempty_lines` - optional output length cap.

The checks are deliberately literal: they test the observable behavior the skill is supposed to add, not whether the model mentions the skill by name. Keep prompts organic enough that a user could type them.

The first slice uses deterministic grading only. A future case that needs judgement should use a blinded old-vs-new comparison with a separate judge that is not told which side is which; do not add a self-reported "principles followed" check.

## Case set

- `poteto-mode` (3): final report opens with the goal, Investigation routing stays read-only, Bug fix starts with a reproduction.
- `herdr-team` (4): brief format with worktree and no-git, worker report format, blocked target, no re-delegation.
- `hx-router` (4): direct trivial work, input failure versus capability escalation, `hx context decide` at a boundary, `hx usage` before delegation.

## Proof

Unchanged skill, both sides identical:

```powershell
python evals/skills/run.py --skill poteto-mode --before "$HOME\.claude\skills\poteto-mode\SKILL.md" --after "$HOME\.claude\skills\poteto-mode\SKILL.md"
```

Result: `before 3/3; after 3/3`, verdict `promote`.

Deliberately broken copy:

```powershell
python evals/skills/run.py --skill poteto-mode --before "$HOME\.claude\skills\poteto-mode\SKILL.md" --after evals/skills/fixtures/poteto-mode-broken.md
```

Result: `before 3/3; after 2/3`, verdict `hold`, with `P2-investigation-route` failing 0/3 against the candidate.

Both runs write JSON artifacts with per-case votes. A full identical-side run over all three skills (`--skill all`) exits `0` and returns `promote` for every skill.
