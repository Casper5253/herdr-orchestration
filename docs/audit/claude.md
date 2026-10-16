# Claude Code fact audit

Generated from the installed `claude` CLI and public docs. Every fact is tagged `[measured: <command>]` for values observed directly, `[documented: <url>]` for values taken from docs, or `[unknown]` where the fact could not be verified.

## Installed version

- Version: `2.1.301 (Claude Code)` [measured: `claude -v`]

## Effort controls

### Per-session effort

- Launch flag: `--effort <level>` sets the effort level for the current session [measured: `claude --help`]
- Environment variable: `CLAUDE_CODE_EFFORT_LEVEL=<level>` takes precedence over launch flags and saved settings [documented: https://code.claude.com/docs/en/model-config]
- Interactive command: `/effort` opens a slider; `/effort <level>` sets it directly; `/effort auto` clears the saved level for the active model [documented: https://code.claude.com/docs/en/model-config]
- `/model` picker: left/right arrows adjust the effort slider for the current session [documented: https://code.claude.com/docs/en/model-config]
- `max` is session-only unless set via `CLAUDE_CODE_EFFORT_LEVEL`; it cannot be persisted accidentally [documented: https://code.claude.com/docs/en/model-config]

### Per-subagent effort

- Subagent markdown frontmatter: `effort: <level>` overrides the session effort while that subagent is active, but not the `CLAUDE_CODE_EFFORT_LEVEL` environment variable [documented: https://code.claude.com/docs/en/sub-agents]
- Per-invocation parameter: when spawning a non-fork subagent, an `effort` parameter can be passed for that run and persists when the subagent is resumed [documented: https://code.claude.com/docs/en/sub-agents]
- Subagents inherit the main conversation's extended-thinking configuration as of v2.1.198 [documented: https://code.claude.com/docs/en/sub-agents]

### Settings keys

- `effortLevel` — default effort level for models without a saved level of their own [documented: https://code.claude.com/docs/en/settings-reference]
- `maxEffortLevel` — cap applied to every model; the lowest cap across settings sources wins [documented: https://code.claude.com/docs/en/settings-reference]
- `modelSettings.<model>.effortLevel` — saved per-model effort level [documented: https://code.claude.com/docs/en/settings-reference]
- `modelSettings.<model>.maxEffortLevel` — per-model effort cap [documented: https://code.claude.com/docs/en/settings-reference]
- `modelSettings.<model>.autoCompactWindow` — per-model auto-compact window (`100000`–`1000000` or `"auto"`) [documented: https://code.claude.com/docs/en/settings-reference]
- `ultracode` — session setting that orchestrates dynamic workflows at the current effort level; `/effort ultracode` turns it on at `xhigh` [documented: https://code.claude.com/docs/en/model-config]

### Effort resolution precedence

From highest to lowest as documented [documented: https://code.claude.com/docs/en/model-config]:

1. Explicit choice: `CLAUDE_CODE_EFFORT_LEVEL`, `--effort`, or `/effort` (non-interactive `/effort` has narrower effect)
2. Saved settings: `modelSettings.<model>.effortLevel` or `effortLevel`
3. Model default effort level

After that, `maxEffortLevel` and organization caps clamp the level down if needed [documented: https://code.claude.com/docs/en/model-config].

## Hooks for compaction

- `PreCompact` fires before context compaction. Its matcher filters on `manual` or `auto`. It can block compaction by exiting with code 2 or returning `{"decision": "block"}`. stdout is appended as custom compact instructions [documented: https://code.claude.com/docs/en/hooks]
- `PostCompact` fires after context compaction completes. Its matcher also filters on `manual` or `auto` [documented: https://code.claude.com/docs/en/hooks]
- `SessionStart` fires when a session begins or resumes, including after a compaction. Its matcher filters on `source` values: `startup`, `resume`, `clear`, `compact`. A `SessionStart` hook with `"matcher": "compact"` is the documented way to re-inject critical context after compaction [documented: https://code.claude.com/docs/en/hooks-guide]
- `InstructionsLoaded` can also fire with `"load_reason": "compact"` [documented: https://code.claude.com/docs/en/hooks]

## Context usage exposure

- The status line receives a JSON payload on `stdin` that includes a `context_window` object [documented: https://code.claude.com/docs/en/statusline]
- `context_window` fields include: `context_window_size`, `used_percentage`, `remaining_percentage`, `total_input_tokens`, `total_output_tokens`, and `current_usage` (with `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`) [documented: https://code.claude.com/docs/en/statusline]
- `used_percentage` is pre-calculated from input-only tokens and does not include output tokens [documented: https://code.claude.com/docs/en/statusline]
- `exceeds_200k_tokens` is a fixed boolean threshold independent of actual context window size [documented: https://code.claude.com/docs/en/statusline]
- `rate_limits` fields (`five_hour`, `seven_day`, `spend_limit`) are included when available [documented: https://code.claude.com/docs/en/statusline]
- The interactive `/context` command also displays current context usage [documented/known]

## Headless `claude -p` / `--print` options

Core headless mode flag [measured: `claude --help`]:

- `-p, --print` — print response and exit (useful for pipes). In non-interactive mode the workspace trust dialog is skipped and invalid settings files are silently ignored.

Output and input control [measured: `claude --help`]:

- `--output-format <text|json|stream-json>` — only works with `--print`
- `--input-format <text|stream-json>` — only works with `--print`
- `--include-partial-messages` — include partial message chunks as they arrive (only with `--print` and `--output-format=stream-json`)
- `--forward-subagent-text` — forward subagent text/thinking blocks as assistant/user messages (only with `--print` and `--output-format=stream-json`)
- `--include-hook-events` — include all hook lifecycle events in the output stream (only with `--output-format=stream-json`)

Headless-relevant budget, permissions, and session control [measured: `claude --help`]:

- `--max-budget-usd <amount>` — maximum dollar amount to spend on API calls (only with `--print`)
- `--no-session-persistence` — sessions are not saved to disk and cannot be resumed (only with `--print`)
- `--permission-mode <mode>` — `acceptEdits`, `auto`, `bypassPermissions`, `manual`, `dontAsk`, `plan`
- `--permission-prompts <host|none>` — who answers permission prompts in `--print` mode
- `--allowedTools, --allowed-tools <tools...>` / `--disallowedTools, --disallowed-tools <tools...>` — tool allow/deny lists
- `--tools <tools...>` — specify the built-in tool list, `""` to disable all, or `"default"`
- `--restricted` — removes built-in command-running tools and WebFetch unless named, ignores user/project/local settings

Prompt and model control [measured: `claude --help`]:

- `--model <model>` — model alias (e.g., `fable`, `opus`, `sonnet`) or full model name
- `--effort <level>` — effort level for the current session
- `--autocompact <auto|tokens>` — auto-compact window size
- `--fallback-model <model>` — comma-separated fallback models
- `--system-prompt <prompt>` / `--append-system-prompt <prompt>` / `--system-prompt-snapshot <on|off>`
- `--agents <json-or-file>` — custom agent definitions
- `--settings <file-or-json>` — extra settings file or JSON string
- `--json-schema <schema>` — validate structured output

Other headless/CI-relevant flags [measured: `claude --help`]:

- `--bare` — minimal mode: skip hooks, LSP, plugin sync, attribution, auto-memory, background prefetches, keychain reads, CLAUDE.md auto-discovery
- `--safe-mode` — disable customizations (CLAUDE.md, skills, plugins, hooks, MCP, commands, agents, themes, keybindings)
- `--file <specs...>` — download file resources at startup
- `--cloud [description|session_id|url]` — create or attach to a cloud session
- `--bg, --background` — start session in background and return immediately
- `--worktree [name]` / `--fork-session` / `--session-id <uuid>`
- `--verbose` — override verbose setting

## Unknowns

- No installed command was found that reports real-time remaining Claude Code API quota or rate-limit percentages from the CLI; the status line `/context` expose is the primary mechanism observed in docs [unknown]
