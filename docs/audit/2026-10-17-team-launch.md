# Audit: launching a five-agent team (2026-10-17)

Found while starting a portfolio-site build with dev-claude (lead, Opus), dev-luna (Codex gpt-6-luna), dev-haiku (Claude Haiku) and dev-codex (Codex gpt-6.1-sol). OpenCode was out of quota. Each finding names its evidence.

## What blocked an autonomous start

### 1. The lead cannot spawn teammates under auto mode
The Claude auto-mode classifier denied both `herdr agent start dev-haiku --kind claude ... --permission-mode auto` and an edit to `~/.codex/config.toml`, with reason "Create Unsafe Agents". `team/claude-settings.json` also lists `herdr agent start` under `ask`. The user had to run each start by hand with `! <command>`. `herdr pane split` and `herdr agent rename` were allowed.

Fix: let the lead run one vetted entry point instead of raw `herdr agent start`. Extend `Start-HerdrTeam.ps1` (finding 2) and add one narrow allow rule for it to the lead's settings, for example `PowerShell(powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\caspe\.herdr-team\Start-HerdrTeam.ps1:*)`. An explicit allow rule is what the classifier's denial message points to. Keep `herdr agent start:*` itself in `ask`, so arbitrary agent arguments still need the user. The script fixes the arguments, so allowing it does not allow arbitrary agents.

### 2. The launcher only knows claude, codex and opencode
`hx say` accepts haiku and luna (`hx/say.py:14`, commit f7e04cb), but `Start-HerdrTeam.ps1` cannot start them, and has no way to choose a model, such as Sol for dev-codex. The lead had to copy the launcher's split and env logic by hand (the pinned Codex PATH, `-p trusted-dev`).

Fix: replace the fixed panes with a role table in the script, and a `-Workers` parameter:

| Role | Kind | Arguments |
|---|---|---|
| codex | codex | `-p trusted-dev -m gpt-6.1-sol -c model_reasoning_effort=high` |
| luna | codex | `-p trusted-dev -m gpt-6-luna -c model_reasoning_effort=medium` |
| haiku | claude | `--model haiku --permission-mode auto --settings <dir>\claude-settings.json` |
| opencode | opencode | (current) |

Usage would be `Start-HerdrTeam.ps1 <repo> -LeadPane $env:HERDR_PANE_ID -Workers codex,luna,haiku`. `-NoOpenCode` becomes a special case of `-Workers`.

### 3. The role list is written twice
`hx/say.py:14` (`ROLES`) and `hx/usage.py:466` (`TEAM_AGENT` regex) each list the roles. Adding a role means editing both, and the launcher would make a third copy. Derive the regex from `say.ROLES`, or move the table into `policy.json`, and have the launcher read the same table.

### 4. A new repo cannot be trusted under the trusted-dev profile
In a repo Codex had not seen before, "Trust and continue" failed:

    Failed to set trust ... config/batchWrite failed: Invalid configuration: invalid transport in `mcp_servers.codedb` (code -32600) {"config_write_error_code":"configValidationError"}

`~/.codex/trusted-dev.config.toml:43` declares `[mcp_servers.codedb.tools.codedb_symbol]` (and `codedb_outline`) without a `command`. The server's `command` lives only in `config.toml`. When Codex saves the trust decision, it seems to validate the profile layer by itself and reject it. This is inferred from the error; it was not confirmed in Codex source. `herdr agent start` then timed out at the prompt. Workaround used: add `[projects.'<lowercased repo path>'] trust_level = "trusted"` to `config.toml` by hand.

Fix (either one):
- Move the two `approval_mode` tables into `config.toml`, so the profile no longer has a half-defined server.
- Have the launcher add the trust entry before it starts Codex, since the user already chose to trust the repo by launching the team on it.

## Still to check this session
Skills and hx behaviour in real use: `hx route`, `hx task`, `hx checkpoint`, `hx context decide`, `hx compact` (protected compaction), the lead self-compaction path, `hx say` to haiku and luna. Results go below.
