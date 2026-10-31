# Devin CLI audit (2026-10-31)

Devin CLI 3000.11.234 on Windows 11, account tier Devin Pro (`devin auth status`). Every fact below was measured on this machine unless it cites a docs page.

## Price and promotion

- docs.devin.ai/desktop/models (self-serve tab): "SWE-2 is free for self-serve plans in Devin Desktop and Devin CLI through November 30, 2026; it does not apply to Devin cloud sessions." Afterwards list pricing applies ($3.00/1M input, $15.00/1M output, $0.30/1M cached input). No timezone is published.
- devin.ai/pricing: Pro ($20/month) lists "Free use of SWE-2". Quotas refresh daily and weekly; overage is "consumed at API pricing". No promo-specific rate limit or fair-use text.
- Voice mode does not receive the promotion. Devin Cloud sessions do not either.
- `devin models list --format json` on this account:

| model_uid | label | cost_tier |
|---|---|---|
| `swe-2-medium` | SWE-2 Medium | Free |
| `swe-2-high` | SWE-2 High | Free |
| `swe-2-max` | SWE-2 Max | Free |
| `swe-2-medium-lightning`, `swe-2-high-lightning`, `swe-2-max-lightning` | SWE-2 * Lightning | High cost ($1/1M in, $5/1M out) |

Reasoning effort is part of the model id. There is no separate effort flag (the TUI cycles it with Alt+T). The family alias `swe` floats to the latest version, so hx pins the full ids.

## Headless behaviour

- `devin -p --prompt-file <f> --model <id> --export <path>` runs one prompt and exits. stdout is the agent's text; stderr carries warnings and a first-run setup banner. There is no JSON output mode.
- **A rejected tool call ends the run with exit code 0.** stderr says "rejected a tool call that requires confirmation"; the export records the observation. Three rejection wordings were seen: "Tool execution was rejected by the user", "Permission denied for this tool.", "Permission to run the command `…` was denied. The user needs to approve command execution."
- `--permission-mode accept-edits` alone is not enough for real work: Devin edits files through `exec` (printf/redirects) as often as through `edit`. The worker config allows `exec` and denies destructive or outward commands.
- `permission_mode` in a `--config` file is ignored in print mode (export shows `Normal`), so hx passes the flag.
- Deny rules also catch compound commands: `echo before && git push origin main` was denied by `Exec(git push)`.
- Print mode cannot answer the workspace-trust prompt; `--respect-workspace-trust false` is required for scripted runs (documented for "scripts and CI").

## Deny-rule matching (probed 2026-10-31, one run per command)

| Command | Rule | Result |
|---|---|---|
| `git -C . push origin master` | `Exec(git push)` + `Exec(git -C)` | denied |
| `git -c user.name=x commit …` | `Exec(git commit)` + `Exec(git -c)` | denied |
| `Remove-Item -r junk` (PowerShell flavour) | `Exec(Remove-Item -r)` | denied |
| `curl https://example.com` | `Exec(curl)` | denied |
| `python -m hx usage` | `Exec(python -m hx)` | denied |
| `devin.exe --version` | `Exec(devin)` only | **allowed**: matching is on the literal word, so `devin.exe` needed its own rule |

`Exec(git -C)` was then removed again: Devin routinely starts with `git -C <repo> log`, and the denial ended a real run in its first step. So `git -C <dir> push` and `git -c … commit` are open again, which is residual risk. String rules are guardrails against accidents, not a sandbox: a worker that writes a script and runs it can step around any of them. The worker runs with the user's own git and network credentials, minus secret-named environment variables (hx scrubs `*TOKEN*`, `*SECRET*`, `*API_KEY*` and similar, except `QUESTLOG_TOKEN_OPENCODE`). The lead's diff review and the preamble's rules are the real controls. On Windows there is no Devin sandbox to close this.

## Config

- `--config <path>` **replaces** `%APPDATA%\devin\config.json` and Devin **writes setup state back into it** (org id, `setup_complete`). hx therefore copies `team/devin-worker.config.json` into each run directory.
- User-scope MCP servers (`%APPDATA%\devin\mcp_config.json`) still load under `--config`.
- By default Devin imports other tools' setup (`read_config_from`): on this machine it loaded `~/.claude/CLAUDE.md` as an always-on rule ("You are Claude … the lead"), the Claude skills, the questlog MCP and the SessionStart memory hook text. The worker config turns every import off except `agents_standard` (repo `AGENTS.md`).
- `subagents_enabled: false`: subagents run on `agent.subagent_model`, default `swe-1-6-fast`, a paid model.

## Export (ATIF-v1.7)

`--export` writes JSON with `session_id`, `agent.model_name` (label), `agent.extra.permission_mode`, `steps[]` and `final_metrics` (`total_prompt_tokens`, `total_completion_tokens`, `total_cached_tokens`). Every agent step has `model_name` and `extra.generation_model` (the id that served it), `tool_calls[]` and `observation.results[]` keyed by `source_call_id`. hx verifies every agent step's model against the pinned id.

## Questlog

Questlog is reachable through the user-scope MCP entry `questlog` → `team/questlog-mcp-opencode.cmd`. That launcher maps `QUESTLOG_TOKEN_OPENCODE` to `QUESTLOG_TOKEN` at start (owner directive, 2026-10-31: Devin uses OpenCode's token). No config file holds the token. A print-mode run listed 48 items through `mcp__questlog__list_items`.

## Not verified

- What a quota or rate-limit error looks like (exit code, stderr, export). hx classifies quota markers in output and records them, but no live sample exists.
- Server-side concurrency limits for local CLI runs. The pricing table's "Up to 10" concurrent sessions is for cloud sessions.
- Whether usage of free SWE-2 shows up on the account's quota page. Check app.devin.ai usage after a day of work.
- Whether a local `-p` run can start a cloud session on its own. Docs only describe `--cloud`, `/cloud` and `/handoff`; hx never passes `--cloud`, and the worker denies `Exec(devin)`.
