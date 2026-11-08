# hca-r2: every place the team setup depends on the old `herdr` CLI

Goal (brief `.hx/briefs/hca-r2.md`): the Herdr app on herdr-core (`hc`) becomes the primary
host. Our tooling speaks only to the standalone `herdr` CLI. This is the complete
inventory of couplings the adapter must cover, with file:line, the data each site needs
back, and how it is tested today.

Three process-level surfaces exist; everything else is a consumer, a config file, or prose:

- `hx.usage._herdr_agent_list` runs `herdr agent list` and is the only exec site for the
  list operation (`herdr_agents` validates `result.agents`).
- `hx.context._herdr` runs `herdr agent <args>` for `get`/`prompt`; `hx.context._herdr_read`
  runs `herdr agent read` (plain text, not JSON). `hx.say.say` runs
  `herdr agent prompt` directly, bypassing `_herdr`.
- PowerShell side: `team/Start-HerdrTeam.ps1`'s `Invoke-Herdr` runs `herdr @args` and
  `ConvertFrom-Json` — it assumes every call returns JSON.

`herdr agent list`/`get` JSON shape (from `.hx/briefs/herdr-agent-list.sample.json` and
`herdr-agent-get-devin.sample.json`): envelope `{id, result: {agents: [...]}}` /
`{id, result: {agent: {...}, type: "agent_info"}}`. Agent fields the code reads:
`name`, `pane_id`, `agent` (kind: claude|codex|opencode|devin), `agent_status`
(idle|done|working|blocked|error; READY = {idle,done} at `hx/usage.py:23`),
`agent_session.value` (provider session id), `cwd`, `focused`.
Fields present but unused: `interactive_ready`, `terminal_title(_stripped)`, `tab_id`,
`workspace_id`, `terminal_id`, `revision`, `completion_seq`, `state_change_seq`,
`agent_session.kind`, `agent_session.source`, envelope `id` and `result.type`
(except the error taxonomy check at `hx/context.py:109`).

## 1. Process call sites (code that executes `herdr`)

| Site | What it does | herdr command / env | Data it needs back | How tested |
|---|---|---|---|---|
| `hx/usage.py:541-547` (`_herdr_agent_list`) | Lists all live agents | `herdr agent list` (check_output, timeout 30, stderr devnull) | `result.agents[]` | `tests/test_usage.py:265-348` stubs `_herdr_agent_list` with the `{"result":{"agents":[...]}}` envelope |
| `hx/usage.py:550-555` (`herdr_agents`) | Validates list is a dict list | (wraps above) | `result.agents` | same; `test_devin.py:22`, `test_ocrun_devin.py:19` stub `herdr_agents` |
| `hx/usage.py:588-601` (`own_agent_name`) | Finds own name: agent whose `pane_id` equals `HERDR_PANE_ID` | env `HERDR_PANE_ID` + `herdr agent list` | `pane_id`, `name` | `tests/test_team_prefix.py:9-55`, `test_say*.py` set `HERDR_PANE_ID` + synthetic agent lists |
| `hx/usage.py:573-585` (`team_prefix`) | Resolves `<team>-` prefix | env `HX_HERDR_PREFIX` (ours), else `own_agent_name`, else `policy.team_prefix` | `name` | `test_team_prefix.py`, `test_cli.py:107` |
| `hx/usage.py:604-652` (`agents_usage`) | Team availability block of `hx usage` | `herdr agent list` via `herdr_agents()` | `name`, `agent_status` (blocked/error ⇒ unavailable), `cwd`, `focused`, `agent_session.value` | `test_usage.py:265-300` |
| `hx/usage.py:655-681` (`usage_snapshot`) | Feeds the same agent list to devin block + agents block | `herdr agent list` | whole agent objects | `test_usage.py:303-326`, `test_devin_panes.py` |
| `hx/context.py:103-111` (`_herdr`) | Shared exec + envelope error check | `herdr agent <args>` (get, prompt) | `result`, `error` key, `result.type` ∉ {timeout, agent_prompt_stalled, agent_blocked, error} | `test_context.py:445-467` replaces `_herdr`; asserts it runs outside ledger locks |
| `hx/context.py:114-115` (`agent_info`) | One agent's full record | `herdr agent get <name>` | `result.agent` (all fields above) | stubbed in `test_cli.py:110-117`, `test_doctor.py:17,212`, `test_boundary.py:22`, `test_context*.py`, `test_lead_context.py:35`, `test_say.py:24` |
| `hx/context.py:150-156` (`_herdr_read`) | Reads a pane's screen text | `herdr agent read <name> --source recent-unwrapped --lines N` | plain text (NOT JSON) | stubbed `test_context.py:49,467`; comments at `test_context.py:48,287,319,373,466` |
| `hx/context.py:272` | Devin telemetry: parses the pane footer `Context: Nk / Mk (P%)` | `_herdr_read(name, lines=6)` | last-line footer text | `test_context_devin.py` (footer parse, `pane_id` identity at 71-72, 216) |
| `hx/context.py:395` | Sends a worker's native reset (`/new`, `/clear`) | `herdr agent prompt <name> <cmd>` | error-free completion | `test_context_session_reset.py` (env + `agent_info` stubs) |
| `hx/context.py:748` | Sends `/compact`, waits for idle/done | `herdr agent prompt <name> /compact --wait --until idle --until done --timeout 300000` | `result.type` (wait outcome) | `test_context.py:42-45` fake returns `{"result":{"type":"agent_prompted"}}` |
| `hx/context.py:762-765` | Re-brief message + reads reply for ack token | `herdr agent prompt … --wait --until idle --until done --timeout 300000`, then `_herdr_read` | `result.type`; pane text | same `test_context.py` fixture |
| `hx/say.py:93` | Sends a team message (the only unattended send path) | `herdr agent prompt <name> <text>` (fire-and-forget, exit code only) | exit code | `test_say.py:31-45` stubs `subprocess.run` and asserts literal argv `["herdr","agent","prompt",…]` |
| `hx/say.py:83-85` | Refuses to run outside a team pane | env `HERDR_PANE_ID` via `own_agent_name` | `name` | `test_say.py` env fixtures |
| `hx/say.py:41-44,75` | Pressure hold and Devin-kind check before sending | `herdr agent get` via `agent_info` | `agent` (kind), `agent_session.value` | `test_say*.py` agent_info stubs |
| `hx/ocrun.py:1434` (also 8, 1650) | `visible` run mode default | env `HERDR_ENV == "1"` | — | `test_ocrun_visible.py:254-270`; `conftest.py:22` scrubs it |
| `hx/doctor.py:106-110` | Live opencode model: reads pane's session id, then its db | `herdr agent get <prefix>opencode` via `agent_info` | `agent`, `agent_session.value` | `test_doctor.py:17,212` stubs `agent_info` |
| `hx/devin.py:399-411,431-437` | `live_sessions`: Devin-kind panes, any name | `herdr agent list` via `herdr_agents()` | `agent`=="devin", `name`, `agent_status`, `cwd` | `test_devin.py`, `test_devin_panes.py` (synthetic lists) |
| `hx/devin.py:504` (`acquire_slot`) → `_read_live_sessions` | Counts live Devin panes against `max_concurrent` | `herdr agent list` | same | `test_ocrun_devin.py:19-20` stubs `herdr_agents` |
| `hx/devin.py:622-640` (`usage`) | Devin pool status incl. live pane count | `herdr agent list` | same | `test_devin_panes.py` |
| `hx/route.py:1035` → `hx_usage.usage_snapshot` | Router reads the full snapshot | `herdr agent list` (indirect) | `devin.live_sessions[].name/status`, `agents.prefix` (route.py:368-376) | `test_devin_panes.py`, route tests via synthetic usage dicts |
| `hx/capability.py:125` | Pane config path `<state>/<prefix>devin/config.json` | `team_prefix` → possibly `herdr agent list` | `name` | `test_admission.py` profile tests |
| `hx/review.py:50` | Maps owner name → provider family | `team_prefix` → possibly `herdr agent list` | `name` | review tests (prefix stubbed/default) |
| `hooks/claude_statusline.py:88` | Writes `herdr_pane_id` into `claude-status-*.json` | env `HERDR_PANE_ID` | — | `test_hooks.py:338-341` |
| `team/Start-HerdrTeam.ps1:11` | Must run inside a pane | env `HERDR_ENV == '1'` | — | `test_team_launcher.py:31` |
| `team/Start-HerdrTeam.ps1:16` (`Invoke-Herdr`) | Wrapper: `& herdr @args`, `ConvertFrom-Json`, throws on nonzero exit | all calls below | JSON stdout | `test_team_launcher.py:35-37` stubs `function herdr` returning canned JSON |
| `team/Start-HerdrTeam.ps1:55` | Renames an existing pane to `<team>-claude` | `herdr agent rename <pane> <name>` | (result ignored) | same stub |
| `team/Start-HerdrTeam.ps1:57-58` | Creates the team workspace | `herdr workspace create --cwd <p> --label <l> --focus` | `result.root_pane.pane_id` | same stub (`{"result":{"root_pane":{"pane_id":…}}}`) |
| `team/Start-HerdrTeam.ps1:65,67,90` | Splits panes right/down, sets `--env OPENCODE_CONFIG=…` | `herdr pane split <pane> --direction <d> --cwd <p> [--env K=V] --no-focus` | `result.pane.pane_id` | same stub (`{"result":{"pane":{"pane_id":…}}}`) |
| `team/Start-HerdrTeam.ps1:71,82,83,91` | Starts one agent per pane with kind-specific args | `herdr agent start <name> --kind <claude|codex|opencode|devin> --pane <id> --timeout 120000 -- <args>` | readiness; `agent_not_ready` first-run signal | same stub |
| `team/Start-HerdrTeam.ps1:93` | Prints final roster | `herdr agent list` | display | — |
| `install.ps1:24-25` | Rewrites the Codex identity line in `~/.codex/trusted-dev.config.toml` | text names `herdr agent list`, `pane_id`, `HERDR_PANE_ID` | — | — |

## 2. Env-var couplings

- `HERDR_ENV` — pane detection: `hx/ocrun.py:1434`, `team/Start-HerdrTeam.ps1:11`,
  `~/.agents/skills/herdr-team/SKILL.md:8` (activation gate), `~/.agents/pstack/TEAM.md:49`,
  `tests/conftest.py:22`, `tests/test_ocrun_visible.py`, `tests/test_team_launcher.py:31`.
- `HERDR_PANE_ID` — own identity + status-file join key: `hx/usage.py:590`,
  `hooks/claude_statusline.py:88`, `install.ps1:25`, `herdr-team/SKILL.md:8`; test fixtures
  in `conftest.py:25`, `test_say.py` (12 sites), `test_say_devin.py:23`,
  `test_team_prefix.py`, `test_context_session_reset.py:20`, `test_hooks.py:338`.
- `HX_HERDR_PREFIX` — our own override (not a herdr var, but part of the identity
  contract): `hx/usage.py:579`, `tests/conftest.py:26`, `tests/test_team_prefix.py:27`.

## 3. Config / permission couplings (no exec, but name the binary and subcommands)

| File | Lines | Coupling |
|---|---|---|
| `team/herdr-team.rules` | whole file (Codex prefix_rules) | allow: `herdr agent list|get|read|wait`, `herdr pane list|read|current|layout|get`, `herdr workspace|tab list`, `herdr status`, `herdr --skill`; prompt: `agent prompt`, `agent send-keys|start|rename|focus|attach`, `pane run|send-text|send-keys|close|split|move|input`, `workspace|tab|server|worktree create|close|stop`. Installed to `~/.codex/rules/` |
| `team/claude-settings.json` | 4-19, 24-31 allow; 34-49 ask; 51-52 deny | Same allow/prompt matrix for Claude permissions incl. per-member `herdr agent prompt hx-*/dev-*` allows; deny self-prompt `herdr agent prompt dev-claude:*`; also statusLine/hook commands at 55-77 |
| `team/opencode-trusted-dev.jsonc` | 10-19 | `herdr *` ask with narrow allows (list/get/read/wait, pane list/read, status, --skill). 21-32 already carry `hc`/`herdr-app` rules; 39-40, 49-50 reference `.config/herdr-app` and `AppData/Local/herdr-core` dirs |
| `team/devin-pane.config.json` | 61 | `Exec(herdr)` deny |
| `team/devin-worker.config.json` | 59 | `Exec(herdr)` deny |
| `policy.json` | 230, 236, 242 | `Exec(herdr)` deny in three named profiles (adapter needs `Exec(hc)` twins) |
| `team/install-files.json` | all | Manifest installing the above into `~/.herdr-team/` and `~/.codex/rules/` |
| `team/README.md` | 10, launch lines | Documents `herdr agent prompt` as the message contract |

## 4. Shared skills (read-only sweep; repo `skills/` is the pstack-sync source)

| File | Lines | Coupling |
|---|---|---|
| `~/.agents/skills/herdr-team/SKILL.md` (repo copy `skills/herdr-team/SKILL.md`) | 8, 17-20 | Activation `HERDR_ENV=1` + `herdr agent list`; identity = `pane_id == $HERDR_PANE_ID`; commands `herdr agent list|get|read --source recent-unwrapped`, `herdr --skill`, `hx say` (raw `herdr agent prompt` asks the user) |
| `~/.agents/skills/hx-router/SKILL.md` (repo copy identical) | 19, 31 | `persistent` dispatch = herdr-team brief to a pane; `Start-HerdrTeam.ps1 … -Devin`; "put the herdr syntax it needs in the brief"; `hx context decide` reads the Devin footer |
| `~/.agents/skills/kickoff/SKILL.md` | 40-44, 56, 58 | Launch/reset recipe: `Start-HerdrTeam.ps1`, `herdr agent list`, `herdr agent prompt dev-codex /clear`, `dev-opencode /new`; Devin restart = `herdr pane split` + `herdr pane close` + `herdr agent start … --kind devin` (same recipe for opencode) |
| `~/.agents/pstack/TEAM.md` | 49, 62 | Herdr-session detection `HERDR_ENV=1` + `herdr agent list`; transcripts = `herdr agent read dev-<name> --source recent-unwrapped` |
| `evals/skills/cases/herdr-team.json`, `evals/skills/run.py` | — | Eval cases for the skill text; content coupling only |

## 5. Shared filesystem state (indirect coupling — survives a CLI swap but binds layout)

- `~/.herdr-team/state/` is the shared state dir: `hx/ledger.py:66` (leases.json, `HX_LEASES_PATH` override), `hx/usage.py:49,56` (`HX_CLAUDE_STATUS`, `HX_STATE_DIR`), `hx/context.py:55,136,264,323` (resets, claude-status, guard under leases dir), `hx/devin.py:82` (devin-slots, live-sessions), `hooks/claude_statusline.py:22-26`.
- `herdr_pane_id` is the join key between statusline records and `agent.pane_id`:
  written at `hooks/claude_statusline.py:88`, read at `hx/context.py:143`.
- `<state>/<team>-devin/config.json` mirrors the herdr agent name: written by
  `Start-HerdrTeam.ps1:87-89`, read by `hx/capability.py:128` (`f"{prefix}devin"`),
  `hx/devin.py:330` (`state_dir()/name`), `hx/doctor.py:73` — note doctor hardcodes
  `dev-devin` while capability derives the prefix; flag for the adapter.
- `hx/ledger.py:333,350` docstrings encode a concurrency contract: herdr calls must run
  outside the shared lease lock (enforced by `test_context.py:446-467`).
- `hx/handoff.py:147` emits "Suggested skills: poteto-mode, herdr-team" — name only.

## 6. Transitive / indirect couplings

- `herdr-app/mods/hx-usage/main.js:7` — `import { exec, theme } from "herdr"` is the NEW
  app's mod SDK (not the old CLI), but it execs `hx usage` (25, 38) and
  `hx context decide <name>` (45), inheriting whatever host hx targets.
- `hx/route.py:368-376` — `_idle_devin_pane` reads `live_sessions[].name/status` from the
  snapshot: a second-order shape dependency on `agent.name`/`agent_status`.
- `hx/ocrun.py:1350` → `devin.acquire_slot` → `herdr agent list`;
  `hx/ocrun.py:1514` → `capability.prompt_findings` → `worker_profile` → `team_prefix`
  (possible `herdr agent list`).
- `hx/doctor.py:32` — `REQUIRED_SKILLS` names `herdr-team` (skill presence check, name coupling).
- `scripts/preflight-corpus.py:24,28` — references fixture `herdr-app-setup.md` and the
  `herdr-triage` repo name only; no coupling. `hx/opencode_go.py`, `hx/paths.py`,
  `hx/profile.py`, `hx/checkpoint.py`, `hx/handoff.py` (beyond line 147): no coupling.
- `docs/`, `research/`, `.artifacts/`, `.hx/briefs`+`runs` — prose/historical only.

## 7. Tests that mock or call herdr (each needs an hc twin or a host seam)

| Test file | Mechanism |
|---|---|
| `tests/conftest.py:19-26` | Scrubs `HERDR_ENV`, `HERDR_PANE_ID`, `HX_HERDR_PREFIX` so tests never hit the live server |
| `tests/test_usage.py:265-348` | Stubs `usage._herdr_agent_list`; feeds `{"result":{"agents":[…]}}` envelopes |
| `tests/test_say.py`, `test_say_devin.py`, `test_team_prefix.py` | Agent-list envelopes + `HERDR_PANE_ID`; `test_say.py:43-44,59` asserts literal `["herdr","agent","prompt",…]` argv |
| `tests/test_context.py` | Stubs `context.agent_info`, `context._herdr` (returns `{"result":{"type":"agent_prompted"}}`), `_herdr_read`; 446-467 asserts herdr runs outside ledger locks |
| `tests/test_context_devin.py`, `test_context_session_reset.py`, `test_boundary.py`, `test_lead_context.py`, `test_cli.py`, `test_doctor.py` | Stub `agent_info` / `telemetry` with herdr field names (`pane_id`, `agent_session`, `agent_status`, `cwd`) |
| `tests/test_team_launcher.py:31-37` | PowerShell `function herdr` stub returning workspace-create/split envelopes |
| `tests/test_ocrun_visible.py:254-270` | `HERDR_ENV` toggles |
| `tests/test_hooks.py:338-341` | `HERDR_PANE_ID` → `herdr_pane_id` record field |
| `tests/test_devin.py:22`, `test_ocrun_devin.py:19` | Stub `hx.usage.herdr_agents` |
| `tests/test_devin_panes.py` | Synthetic `herdr agent list` agent lists for usage/route |
| `tests/test_admission.py:180-181,433` (and fixtures `c4-admission.md:16`, `herdr-app-setup.md:20`, `greats-diff.md:11`) | Asserts `denied_exec` semantics on `herdr agent prompt`/`herdr agent list` command strings |
| `tests/test_ledger.py`, `test_checkpoint.py` | `.herdr-team/state` paths via `HX_LEASES_PATH` only |
| `tests/test_opencode_overlay.py:12-20` | Asserts overlay covers `.config/herdr-app` + `AppData/Local/herdr-core` (already new-host aware) |

## 8. Direct JSON-shape reach-ins (where a host abstraction must cut)

1. `hx/usage.py:552` — `_herdr_agent_list().get("result", {}).get("agents")`: envelope.
2. `hx/context.py:109` — `data.get("error")` + `result.type` error taxonomy
   `{timeout, agent_prompt_stalled, agent_blocked, error}`.
3. `hx/context.py:115` — `["result"]["agent"]` envelope.
4. `hx/context.py:123`, `hx/doctor.py:107`, `hx/usage.py:647` —
   `(agent.get("agent_session") or {}).get("value")` nested session object; and
   `hx/context.py:304` *constructs* `{"agent_session": {"value": sid}}` to mimic it.
5. `hx/usage.py:643-648`, `hx/devin.py:408` — field projections (name/status/cwd/focused/agent_session) duplicated at two sites.
6. `hx/route.py:368-376` — derived snapshot fields `agents.prefix`, `devin.live_sessions[].name/status`.
7. `team/Start-HerdrTeam.ps1:58,65,67,90` — `.result.root_pane.pane_id`, `.result.pane.pane_id` (PowerShell-side envelope).
8. `hx/context.py:143` + `hooks/claude_statusline.py:88` — `herdr_pane_id` record key in shared status files.
9. Every test fixture in §7 mirrors the envelope; an adapter that changes the shape must update them all.

## 9. Operations table

| Operation | Call sites (exec sites / consumers) | Current herdr command | Fields used |
|---|---|---|---|
| Own identity | 3 env reads (usage.py:590, statusline.py:88, install.ps1:25) + 1 list consumer (own_agent_name); HERDR_ENV gates at ocrun.py:1434, Start-HerdrTeam.ps1:11 | env `HERDR_PANE_ID`, `HERDR_ENV`; `herdr agent list` | `pane_id`, `name` |
| List team agents | 1 exec (usage.py:543) / 7 consumers (usage.py:570,594,617,665; devin.py:435,625; route.py:1035, ocrun.py:1350 indirect) | `herdr agent list` | `name`, `pane_id`, `agent`, `agent_status`, `cwd`, `focused`, `agent_session.value` |
| Agent info | 1 exec (context.py:105 via `get`) / 13 callers (context.py:384,434,551,579,724,743,751,763,775; say.py:41,75; doctor.py:106) | `herdr agent get <name>` | full agent object; `agent`, `agent_status`, `agent_session.value`, `pane_id`, `cwd` |
| Send message | 4 exec (say.py:93; context.py:395,748,762) + 2 emitted commands (context.py:349,540) + skill/README prose | `herdr agent prompt <name> <text> [--wait --until idle --until done --timeout 300000]` | `result.type` wait/error taxonomy |
| Read screen | 1 exec (context.py:152) / 2 callers (272,764) + TEAM.md:62, herdr-team SKILL:19 | `herdr agent read <name> --source recent-unwrapped --lines N` | plain text pane |
| Start agent | 4 exec (Start-HerdrTeam.ps1:71,82,83,91) + kickoff SKILL:44 | `herdr agent start <name> --kind <k> --pane <id> --timeout 120000 -- <args>` | readiness / `agent_not_ready` |
| Rename agent | 1 (Start-HerdrTeam.ps1:55) | `herdr agent rename <pane> <name>` | — |
| Workspace create | 1 (Start-HerdrTeam.ps1:57) | `herdr workspace create --cwd <p> --label <l> --focus` | `result.root_pane.pane_id` |
| Pane layout | 3 exec (Start-HerdrTeam.ps1:65,67,90) + pane close in kickoff SKILL:44 only | `herdr pane split <p> --direction <d> --cwd <p> [--env K=V] --no-focus`; `herdr pane close` | `result.pane.pane_id` |
| Permission surfaces | config only: herdr-team.rules (whole), claude-settings.json:4-52, opencode-trusted-dev.jsonc:10-19, `Exec(herdr)` ×4 (devin-pane, devin-worker, policy.json:230/236/242) | names the binary + every subcommand agents may type | — |
| Shared state | 8+ sites in §5 | `~/.herdr-team/state/` layout | `herdr_pane_id` key; `<team>-devin/config.json` naming |

## 10. Completeness proof

Sweep commands run repo-wide (bare `herdr`, because `_herdr_agent_list`,
`"herdr"` in quotes and `herdr-app`/`herdr-core` evade the narrower patterns):

```
rg -ln "herdr" --hidden -g '!*.pyc' -g '!__pycache__' -g '!.git' .
rg -n --no-heading "herdr |HERDR_|agent_info|pane_id" hx/ team/ hooks/ scripts/ install.ps1 tests/ herdr-app/
rg -n "HERDR_[A-Z_]+" -o hx/ team/ hooks/ tests/ install.ps1
rg -n "herdr_agents\(|team_agents\(|own_agent_name\(|team_prefix\(|usage_snapshot\(|agent_info\(|_herdr_read\(|_herdr\(|live_sessions\(|agents_usage\(|session_telemetry\(" hx/
```

Result of the file-list grep (every file containing "herdr", grouped):

- In scope with real couplings: `hx/{context,devin,doctor,handoff,ledger,say,usage,ocrun}.py`,
  `team/{Start-HerdrTeam.ps1,claude-settings.json,devin-pane.config.json,devin-worker.config.json,
  herdr-team.rules,install-files.json,opencode-trusted-dev.jsonc,README.md}`,
  `hooks/claude_statusline.py`, `install.ps1`, `policy.json`, `skills/{herdr-team,hx-router}/SKILL.md`,
  `herdr-app/mods/hx-usage/main.js`, `herdr-app/{package.json,INSTALL.md}`,
  18 `tests/` files (table in §7) + 4 `tests/fixtures/briefs/*.md`.
- Out of scope, prose/historical only: `docs/**`, `research/**`, `.artifacts/**`,
  `.hx/**` (briefs, runs, ledger, sample JSONs — includes the two `herdr-agent-*.sample.json`
  used for the shape in §0), `evals/skills/**`, `scripts/preflight-corpus.py` (filename strings),
  `README.md`, `.gitignore`, `e2e/` (no hits).
- Zero-hit files inside the named scope: `hx/{__main__,capability,checkpoint,handoff,
  opencode_go,paths,profile,review,route}.py` for the four brief patterns — but
  `route.py`, `capability.py`, `review.py`, `ocrun.py` still couple transitively via
  `team_prefix`/`usage_snapshot`/`acquire_slot` (§6); `scripts/preflight-corpus.py`,
  `hooks/claude_precompact.py`, `hooks/claude_session_compact.py`,
  `team/questlog-mcp-opencode.cmd`, `e2e/` clean.

Caveat: the brief's literal grep set (`herdr `, `HERDR_`, `agent_info`, `pane_id`) misses
`_herdr_agent_list` stubs in `test_usage.py`/`test_devin.py`/`test_ocrun_devin.py` and
quoted `"herdr"` strings; the bare-`herdr` sweep above is the complete one.
