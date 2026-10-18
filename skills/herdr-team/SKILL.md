---
name: herdr-team
description: Collaboration contract for a trusted Herdr dev team (agents named <team>-<role>, for example dev-claude, dev-codex, dev-luna, dev-haiku; any team name and any role). Use when a message starts with [team], or when you delegate to, review for, or report to another teammate through Herdr.
---

# Herdr dev team

Active only when `HERDR_ENV=1` and `herdr agent list` shows team agents named `<team>-<role>`. Roles and models are free: `dev-claude`, `dev-codex`, `dev-luna` or `dev-haiku` are all teammates. The lead is `<team>-claude` unless `policy.json` sets another `lead_role`. Your name is the `name` of the entry whose `pane_id` equals `$HERDR_PANE_ID`; its prefix is your team. Below, `dev-` stands for your team prefix: only address teammates with your own prefix.

## Roles
- dev-claude is normally the **lead**; otherwise the agent the user talks to leads that task. It scopes the work, assigns it, integrates the results and reports to the user.
- Everyone else is an **implementer**, **specialist** or **reviewer**. Do exactly the brief, no more.
- One owner per task. Never re-delegate a task you were given; report back instead.

## Commands
Run each as one plain command. No pipes, `;`, `&&`, variables or subshells, because Codex rules only match plain commands. Put the message in single quotes with no apostrophes inside it.
- Discover: `herdr agent list`
- Delegate or report: `hx say <role> '[team from dev-<you>] ...'` with any teammate's role (the part after the team prefix, for example `claude`, `codex`, `luna`) or full name. It only reaches live agents on your own team, refuses a header that names someone else, and is the one send command that needs no approval; a raw `herdr agent prompt` asks the user.
- Check: `herdr agent get dev-<name>` / `herdr agent read dev-<name> --source recent-unwrapped --lines 60`
- Syntax help: `herdr --skill`

Don't block on long work with `--wait`. Send the brief, carry on or end your turn, and the assignee reports back with a prompt. If a send or wait times out, run `get`/`read` first. Never resend blindly. If a target is `blocked`, it is waiting for the user: never send keys, tell the lead.

## Brief (lead to assignee, ≤10 lines)
`[team from dev-X] TASK: … FILES: <files or worktree you own> ACCEPT: <test command> SKILLS: <optional pstack skill or poteto-mode playbook> REPORT TO: dev-X`

## Report (assignee to lead, one prompt, ≤6 lines)
`[team report from dev-Y] STATUS: done|blocked. CHANGED: files. TESTS: command → result. NOTES: …`
Reviewers report findings as `file:line: problem`, or `no findings`. Never paste diffs, files or transcripts: the reader runs `git diff` / `git log -p` itself.
A report is never a new task. The lead decides on corrections; at most 2 correction rounds per task, then the lead escalates to the user.

## pstack
Every agent has the pstack skills. The lead may name one in `SKILLS:` (e.g. `poteto-mode bug-fix`, `interrogate`, `blast-radius`); the assignee loads and follows it. pstack roles map to team members through `~/.agents/pstack/TEAM.md`: the lead routes cross-agent roles with briefs, and an assignee runs every role inside itself and never re-delegates.

## Scope
- Work only inside the lead's project repo.
- Parallel implementers get disjoint files, or separate worktrees (`git worktree add ../<repo>-<task> -b <task>`).
- No approval needed: editing project code, running tests/linters/builds, refactors, read-only review, local commits on a task branch.
- Escalate to the user through the lead, and stop until answered: anything outside the repo, credentials or `.env`, network or external services, installs, push/deploy, deleting data or history (`reset --hard`, force-push, bulk `rm`), paid services.

## Routing, usage and context (hx)
The lead routes, checks quota and manages context with the `hx` CLI; follow the `hx-router` skill. Workers: when a brief names a task id, write its checkpoint before reporting done (`python -m hx checkpoint write <id> ...`), and after a `/compact` resume from the checkpoint the lead names.
