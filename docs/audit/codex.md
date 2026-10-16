# Codex CLI audit

Audit date: 2026-10-16, approximately 09:06-09:08 +08:00. Auditor: `hx-codex`.

Tags describe the evidence, not a guarantee for another installation or account.

- **measured** means observed command output or local data during this audit. Help output proves the advertised interface, not successful execution of every operation.
- **documented** means stated by the linked official OpenAI documentation, fetched during this audit. Interactive features were not exercised in the active team session.
- **unknown** means not established by these checks.

## Installation and available models

- **measured** `codex --version` returned `codex-cli 0.162.1`. `Get-Command codex` resolved to `C:\Users\caspe\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe`.
- **measured** Help/version commands completed despite warnings about access denied while cleaning stale arg0 directories and creating PATH aliases under `~/.codex/tmp/arg0`. This audit made no PATH or installation changes.
- **measured** `~/.codex/models_cache.json` reported `client_version = 0.162.1` and `fetched_at = 2026-10-16T01:06:06.107733900Z`. Its model records contained the following IDs and reasoning levels.

| Evidence | ID | Visibility | Default effort | Advertised effort levels |
| --- | --- | --- | --- | --- |
| measured, cached catalog | `gpt-6.1-sol` | `list` | `low` | `low`, `medium`, `high`, `xhigh`, `max`, `ultra` |
| measured, cached catalog | `gpt-6-astra` | `list` | `low` | `low`, `medium`, `high`, `xhigh`, `max`, `ultra` |
| measured, cached catalog | `gpt-6-sol` | `list` | `medium` | `low`, `medium`, `high`, `xhigh`, `max`, `ultra` |
| measured, cached catalog | `gpt-6-luna` | `list` | `medium` | `low`, `medium`, `high`, `xhigh`, `max` |
| measured, cached catalog | `gpt-5.6-sol` | `list` | `low` | `low`, `medium`, `high`, `xhigh`, `max`, `ultra` |
| measured, cached catalog | `gpt-5.6-terra` | `list` | `medium` | `low`, `medium`, `high`, `xhigh`, `max`, `ultra` |
| measured, cached catalog | `gpt-5.6-luna` | `list` | `medium` | `low`, `medium`, `high`, `xhigh`, `max` |
| measured, cached catalog | `gpt-reserve` | `hide` | `medium` | `low`, `medium`, `high`, `xhigh`, `max` |
| measured, cached catalog | `codex-auto-review` | `hide` | `medium` | `low`, `medium`, `high`, `xhigh`, `max` |

- **measured** The active session's `turn_context` recorded `model = gpt-6.1-sol` and `effort = medium`. Its completed usage events provide current-session evidence beyond a cached model listing.
- **unknown** Successful requests to every other catalog ID were not tested. Hidden entries are recorded as hidden, not presented as normal picker choices. Bare `sol`, `terra`, and `luna` aliases were not tested.
- **measured** `codex debug models --help` advertises raw JSON catalog output. Its `--bundled` option skips refresh and dumps the catalog shipped with the binary. The refresh command itself was not run.

## Model and reasoning selection

| Evidence | Scope | Interface |
| --- | --- | --- |
| measured, installed help | New interactive invocation | `codex -m gpt-6.1-sol -c model_reasoning_effort=high` |
| measured, installed help plus documented config key | One non-interactive invocation | `codex exec -m gpt-6.1-sol -c model_reasoning_effort=high "<prompt>"` |
| measured, installed help | Config override mechanism | `-c key=value` overrides the loaded config value. Dotted paths are accepted. Values parse as TOML, falling back to a string when TOML parsing fails. |
| measured, local config | Current user defaults | `~/.codex/config.toml` currently contains `model = "gpt-6.1-sol"` and `model_reasoning_effort = "medium"`. |
| documented | Mid-session model | `/model` chooses the active model. `/status` verifies the selection. [Developer commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli). |
| documented | Mid-session reasoning | `/reasoning` chooses reasoning effort for the current chat. [Developer commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli). |

- **documented** `model_reasoning_effort` accepts the effort advertised by the chosen model. Available values depend on the model and client. [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
- **unknown** The launch examples above were checked against help and configuration documentation, not executed as additional paid model turns. Persistence of interactive picker changes to future sessions was not tested.

## Native compaction

- **documented** `/compact` summarizes earlier chat context to free space while retaining key details. [Developer commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli).
- **documented** `model_auto_compact_token_limit` sets the token threshold for automatic history compaction. If unset, model defaults apply. [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
- **documented** `model_auto_compact_token_limit_scope` accepts `total` or `body_after_prefix`. The default `total` counts the full active context. `body_after_prefix` counts growth after the carried compaction-window prefix. `model_context_window` specifies the model's available context tokens. [Configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
- **measured** Neither `model_auto_compact_token_limit` nor `model_context_window` was present as a top-level assignment in the user config inspected by this audit. The active session's usage event reported `model_context_window = 475000`. The cached `gpt-6.1-sol` record reported `context_window = 500000`. These are distinct observed fields, not a measured compaction threshold.
- **measured** `codex --help` and `codex exec --help` expose no `compact` subcommand. No manual compaction was triggered in this session.
- **documented** Non-interactive manual compaction exists through the app-server protocol. `thread/compact/start` accepts a `threadId` and returns `{}` immediately. Completion is reported through notifications, including the `contextCompaction` item lifecycle. A return value alone is not completion evidence. [Codex app-server](https://learn.chatgpt.com/docs/app-server#trigger-thread-compaction).

```json
{"method":"thread/compact/start","id":25,"params":{"threadId":"<thread-id>"}}
```

- **unknown** App-server compaction was not called during this audit. Whether passing the literal string `/compact` as a `codex exec` prompt invokes native compaction was not established. Do not treat that as an automation interface. The effective automatic threshold and unattended auto-compaction behavior on this installed build were not exercised.

## Usage, quotas, and session logs

- **documented** `/status` displays the chat ID, context usage, and rate limits. `/usage daily`, `/usage weekly`, and `/usage cumulative` display account token activity. [Developer commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli).
- **documented** App-server offers `account/rateLimits/read` and `account/rateLimits/updated`. `usedPercent` describes window usage, `windowDurationMins` describes window length, and `resetsAt` is Unix seconds. `rateLimitsByLimitId`, when present, supplies multiple buckets. [Codex app-server](https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt).
- **measured** This project's current log exists at `C:\Users\caspe\.codex\sessions\2026\10\16\rollout-2026-10-16T09-06-12-01a1423e-c361-7641-b58e-c4b9cb1760ee.jsonl`. Its `session_meta` records this repository as `cwd`, `cli_version = 0.162.1`, `originator = codex-tui`, and `source = cli`.
- **measured** At the 01:07:09.454Z sample, the log contained an `event_msg` whose `payload.type` was `token_count`. `payload.info` contained `total_token_usage`, `last_token_usage`, and `model_context_window`. Both usage objects contained input, cached input, cache-write input, output, reasoning output, and total token fields.
- **measured** The same event contained `payload.rate_limits`, including `limit_id`, `primary`, `secondary`, `credits`, `plan_type`, and `rate_limit_reached_type`. The primary window reported `used_percent = 1.0`, `window_minutes = 300`, and `resets_at = 1792130450`. The secondary window reported `used_percent = 0.0`, `window_minutes = 10080`, and `resets_at = 1792717250`. These are timestamped observations, not current remaining quota promises.
- **measured** This inspected log had nested `rate_limits` inside `token_count` events. No separate `event_msg` with `payload.type = rate_limits` was observed. Separate `token_usage_record` top-level records were also present.
- **unknown** Universal event availability, freshness, schema stability, and account-wide token-to-quota conversion were not established. A consumer must tolerate absent or null quota fields. Token totals alone do not establish remaining quota.
- **measured** `codex exec --help` advertises `--json` for stdout JSONL events and `--ephemeral` to avoid session-file persistence. The audit did not establish that exec stdout has the same event schema as persisted rollout logs.

## Resume and fork

| Evidence | Operation | Installed interface |
| --- | --- | --- |
| measured, help | Resume interactively | `codex resume [SESSION_ID] [PROMPT]`; no ID opens a picker; `--last` continues the most recent; `--all` disables cwd filtering. IDs or names are accepted. `--include-non-interactive` includes exec sessions in picker and last selection. |
| measured, help | Resume non-interactively | `codex exec resume [SESSION_ID] [PROMPT]`, or `codex exec resume --last "<prompt>"`. `--all`, `-m`, `-c`, and `--json` are advertised. |
| measured, help | Fork interactively | `codex fork [SESSION_ID] [PROMPT]`; no ID opens a picker; `--last` forks the latest; `--all` disables cwd filtering. |
| measured, help | Fork non-interactively | `codex exec fork <SESSION_ID> [PROMPT]`. An ID or thread name is required. `-m`, `-c`, and `--json` are advertised. |
| documented | In-session controls | `/resume` selects a saved chat. `/fork` creates a new chat with a fresh ID while preserving the original transcript. [Developer commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli). |

- **unknown** No resume or fork was executed against a live session. Full runtime behavior and model override behavior on resumed or forked threads remain untested.

## Herdr rule compatibility

- **measured** `HERDR_PANE_ID = w7:p2`. `herdr agent get hx-codex` identifies that pane as `hx-codex` in this repository.
- **measured** `C:\Users\caspe\.codex\rules\herdr-team.rules` explicitly allows the plain prefix `herdr agent prompt` for `dev-claude`, `dev-opencode`, `hx-claude`, and `hx-opencode`. No rule in that file blocks a plain prompt to either hx teammate.

```python
prefix_rule(pattern=["herdr", "agent", "prompt", ["dev-claude", "dev-opencode", "hx-claude", "hx-opencode"]], decision="allow")
```

- **measured** The file separately marks agent keyboard injection and lifecycle commands, raw pane mutation, and workspace lifecycle mutations as `prompt`. Its comments say only plain commands match and caution against pipes, command separators, environment expressions, and PowerShell wrappers.
- **unknown** This file alone does not prove that every effective approval policy or runtime condition permits delivery. Team reports still require a successful Herdr submission result.

## Audit boundaries

- **measured** Checks used installed CLI help, selected non-secret config fields, the cached catalog, this project's current session metadata and usage events, and the Herdr rules. Only this Markdown artifact was authored by the auditor. No model probe, resume, fork, compaction, installation, or global configuration mutation was performed.
- **documented** URLs above were opened during the audit. The legacy `developers.openai.com/codex/` documentation URLs redirected to the cited `learn.chatgpt.com` pages.
- **unknown** Documentation may describe behavior newer than the installed executable. Documented features without a runtime check retain that distinction.
