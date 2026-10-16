# OpenCode fact audit

Generated from the installed `opencode` CLI and public docs. Every fact is tagged `[measured: <command>]` for values observed directly, `[documented: <url>]` for values taken from docs, or `[unknown]` where the fact could not be verified.

## Installed version

- Version: `1.18.27` [measured: `opencode -v`]

## OpenCode Go model IDs available on this machine

`opencode models` returned the following IDs under the `opencode-go/` provider on this system [measured: `opencode models`]:

- `opencode-go/cat-jam-free`
- `opencode-go/claude-haiku-5-5`
- `opencode-go/deepseek-v4-pro`
- `opencode-go/deepseek-v4.1-flash`
- `opencode-go/gemini-4-argon-contributor`
- `opencode-go/glm-5.2`
- `opencode-go/glm-5.3`
- `opencode-go/glm-5.3-flash`
- `opencode-go/gpt-5.6-luna`
- `opencode-go/gpt-6-luna`
- `opencode-go/grok-4.7`
- `opencode-go/hy3`
- `opencode-go/hy4-preview`
- `opencode-go/kimi-k2.7-code`
- `opencode-go/kimi-k3`
- `opencode-go/longcat-2.0`
- `opencode-go/longcat-2.5-preview-free`
- `opencode-go/mimo-v2.5`
- `opencode-go/mimo-v2.5-pro`
- `opencode-go/mimo-v2.6-flash`
- `opencode-go/mimo-v2.6-pro`
- `opencode-go/minimax-m3`
- `opencode-go/muse-spark-1.2-contributor`
- `opencode-go/muse-spark-1.3-contributor`
- `opencode-go/qwen3.7-plus`
- `opencode-go/qwen3.8-flash`
- `opencode-go/qwen3.8-max`
- `opencode-go/space-bunny`
- `opencode-go/step-5-preview`

## Plans and usage-limit structure

- OpenCode Go has two monthly plans: **Go** at `$10/month` and **Go Plus** at `$40/month` [documented: https://opencode.ai/docs/go/]
- Both plans access the same model catalog; the difference is the per-model monthly dollar limit [documented: https://opencode.ai/docs/go/]
- Usage limits are **per-model monthly dollar amounts**, not a single shared pool across models [documented: https://opencode.ai/docs/go/]
- Each model has three rolling allowance levels: **5-hour = 20%**, **weekly = 50%**, and **monthly = 100%** of that model's monthly limit [documented: https://opencode.ai/docs/go/]
- Token prices are the same for Go and Go Plus [documented: https://opencode.ai/docs/go/]
- Prices are per **1M tokens** [documented: https://opencode.ai/docs/go/]

## Per-model pricing and monthly limits

The table below uses the model IDs measured on this machine and the pricing/limit tables from the public OpenCode Go docs [documented: https://opencode.ai/docs/go/]. Where a model has tiered pricing, the cheaper tier applies up to the listed token threshold.

| Model ID | Input | Output | Cached Read | Cached Write | Go monthly limit | Go Plus monthly limit |
|---|---|---|---|---|---|---|
| `opencode-go/cat-jam-free` | Free | Free | Free | — | Unlimited (limited time) | Unlimited (limited time) |
| `opencode-go/claude-haiku-5-5` (≤100K) | $0.10 | $0.50 | $0.01 | $0.125 | $15 | $60 |
| `opencode-go/claude-haiku-5-5` (>100K) | $0.50 | $2.50 | $0.05 | $0.625 | $15 | $60 |
| `opencode-go/deepseek-v4-pro` (Off-Peak) | $0.66 | $1.98 | $0.022 | — | $15 | $60 |
| `opencode-go/deepseek-v4-pro` (Peak) | $1.32 | $3.96 | $0.044 | — | $15 | $60 |
| `opencode-go/deepseek-v4.1-flash` (Off-Peak) | $0.15 | $0.60 | $0.003 | — | $80 | $160 |
| `opencode-go/deepseek-v4.1-flash` (Peak) | $0.30 | $1.20 | $0.006 | — | $80 | $160 |
| `opencode-go/gemini-4-argon-contributor` | $4.00 | $20.00 | $0.20 | — | $60 (limited-time promo) | $240 (limited-time promo) |
| `opencode-go/glm-5.2` | $1.40 | $4.40 | $0.26 | — | $60 | $180 |
| `opencode-go/glm-5.3` | $1.40 | $4.40 | $0.26 | — | $15 | $120 |
| `opencode-go/glm-5.3-flash` | $0.15 | $0.50 | $0.03 | — | $60 | $180 |
| `opencode-go/gpt-5.6-luna` (≤272K) | $0.20 | $1.20 | $0.02 | $0.25 | $15 | $60 |
| `opencode-go/gpt-5.6-luna` (>272K) | $0.40 | $1.80 | $0.04 | $0.50 | $15 | $60 |
| `opencode-go/gpt-6-luna` (≤272K) | $0.10 | $0.50 | $0.01 | $0.125 | $15 | $60 |
| `opencode-go/gpt-6-luna` (>272K) | $0.20 | $0.75 | $0.02 | $0.25 | $15 | $60 |
| `opencode-go/grok-4.7` (≤200K) | $2.00 | $6.00 | $0.50 | — | $15 | $60 |
| `opencode-go/grok-4.7` (>200K) | $4.00 | $12.00 | $1.00 | — | $15 | $60 |
| `opencode-go/hy3` | $0.14 | $0.58 | $0.035 | — | $60 | $240 |
| `opencode-go/hy4-preview` | $0.834 | $2.501 | $0.042 | — | $30 | $120 |
| `opencode-go/kimi-k2.7-code` | $0.95 | $4.00 | $0.19 | — | $60 | $180 |
| `opencode-go/kimi-k3` | $3.00 | $15.00 | $0.30 | — | $15 | $60 |
| `opencode-go/longcat-2.0` | $0.30 | $1.20 | $0.006 | — | $60 | $240 |
| `opencode-go/longcat-2.5-preview-free` | Free | Free | Free | — | Unlimited (limited time) | Unlimited (limited time) |
| `opencode-go/mimo-v2.5` | $0.14 | $0.28 | $0.0028 | — | $60 | $120 |
| `opencode-go/mimo-v2.5-pro` | $0.435 | $0.87 | $0.003625 | — | $15 | $60 |
| `opencode-go/mimo-v2.6-flash` | $0.14 | $0.28 | $0.0028 | — | $60 | $120 |
| `opencode-go/mimo-v2.6-pro` | $0.435 | $0.87 | $0.003625 | — | $15 | $60 |
| `opencode-go/minimax-m3` | $0.30 | $1.20 | $0.06 | — | $60 | $180 |
| `opencode-go/muse-spark-1.2-contributor` | $0.10 | $0.20 | $0.002 | — | $60 | $120 |
| `opencode-go/muse-spark-1.3-contributor` | $0.10 | $0.20 | $0.002 | — | $60 | $120 |
| `opencode-go/qwen3.7-plus` (≤256K) | $0.40 | $1.60 | $0.04 | $0.50 | $60 | $180 |
| `opencode-go/qwen3.7-plus` (>256K) | $1.20 | $4.80 | $0.12 | $1.50 | $60 | $180 |
| `opencode-go/qwen3.8-flash` | $0.15 | $0.47 | $0.016 | $0.20 | $30 | $90 |
| `opencode-go/qwen3.8-max` | $2.00 | $6.00 | $0.25 | $2.50 | $15 | $60 |
| `opencode-go/space-bunny` | $0.15 | $0.60 | $0.03 | — | $30 | $120 |
| `opencode-go/step-5-preview` | $1.00 | $2.70 | $0.10 | — | $60 (limited-time promo) | $240 (limited-time promo) |

## Selecting a model and reasoning variant

- **Per run**: `opencode run -m <provider>/<model> --variant <variant> "..."` [measured: `opencode run --help`]
- **Per run / root launch**: `opencode -m <provider>/<model>` also accepts `--model` [measured: `opencode --help`]
- `--variant` is described as "provider-specific reasoning effort, e.g., high, max, minimal" [measured: `opencode run --help`]
- **In the TUI**: the `/models` command opens the model picker and a `variant_cycle` keybind cycles variants for the active session [documented: https://opencode.ai/docs/models/]
- **Config defaults**:
  - Top-level `model` and `small_model` keys set the default model [documented: https://opencode.ai/docs/config/]
  - Per-agent `model` and `options` override the global default for that agent [documented: https://opencode.ai/docs/config/]
  - Per-command `model` selects a model for custom commands [documented: https://opencode.ai/docs/config/]
  - Per-provider `provider.<id>.models.<id>.options` and `provider.<id>.models.<id>.variants` configure reasoning effort/variants [documented: https://opencode.ai/docs/models/]
- A model already selected for a session takes precedence over the configured default [documented: https://opencode.ai/docs/models/]
- Model selector format is `provider/model` with an optional `#variant` suffix; variants are applied last after provider/model settings [documented: https://opencode.ai/v2/docs/models/]

## Native compaction (auto and manual)

### OpenCode v1 (installed: 1.18.27)

- Config keys under `compaction` in `opencode.json` [documented: https://opencode.ai/docs/config/]:
  - `auto` — automatically compact when context is full (default `true`)
  - `prune` — remove old tool outputs to save tokens (default `false`)
  - `reserved` — token buffer left free for the compaction call
- Manual compaction in the TUI: `/compact` (alias `/summarize`) [documented: https://forums.basehub.com/anomalyco/opencode/20]
- Environment variable `OPENCODE_DISABLE_AUTOCOMPACT=true` disables automatic compaction [documented: https://github.com/anomalyco/opencode/blob/v1.18.21/packages/web/src/content/docs/cli.mdx]
- The compaction agent can be customized under `agent.compaction.model` and `agent.compaction.options` [documented/inferred from source: https://github.com/anomalyco/opencode/issues/46164]

### OpenCode v2 (documented, not installed here)

- Config keys under `compaction` [documented: https://opencode.ai/v2/docs/compaction]:
  - `auto` — enable automatic compaction near the context limit (default `true`)
  - `keep.tokens` — approximate recent conversation kept beside the summary (default `15000`)
  - `buffer` — tokens to keep free below the model limit (default 10% of limit)
- Native provider compaction: set `providers.<id>.settings.compaction.type: "native"` or per-model `settings.compaction.type`; `native` uses the provider's compaction endpoint, `summary` uses OpenCode's own [documented: https://opencode.ai/v2/docs/compaction]
- Manual compaction: `opencode api post /api/session/{sessionID}/compact --data '{}'` and `/compact` in the TUI [documented: https://opencode.ai/v2/docs/compaction]

## Usage and quota telemetry

- `opencode stats` reports: session count, message count, days active, total cost, average cost/day, average/median tokens per session, input/output/cache-read/cache-write token totals, and a tool-usage histogram [measured: `opencode stats`]
- `opencode export [sessionID] --sanitize` returns a JSON object with `info` (id, title, model, cost, tokens, times) and a `messages` array [measured: `opencode export <sessionID> --sanitize`]
- No CLI command was found that reports remaining per-model quota or current limit usage against OpenCode Go limits [unknown]

## Session export/import

- `opencode export [sessionID]` exports session data as JSON [measured: `opencode export --help`]
- `--sanitize` redacts sensitive transcript and file data [measured: `opencode export --help`]
- `opencode import <file>` imports session data from a JSON file or URL [measured: `opencode --help`]
- `opencode session list` lists sessions and `opencode session delete <sessionID>` deletes one [measured: `opencode session --help`]
