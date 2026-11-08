# Brief: two hx fixes, measured OpenCode Go usage (Q-205) and the DeepSeek context limit

Use poteto-mode, Feature playbook. Open your report with the goal, the playbook and the principles you applied.

## Rules

- Repo: C:\Users\caspe\Documents\Projects\herdr-orchestration, on a new branch `git checkout -b fix/usage-context` (untracked files may stay). No commits or pushes; the lead owns git.
- Never print, log, copy or store a token or credential. Read OpenCode's stored login only in memory, and only to call OpenCode's own servers.

## 1. The DeepSeek context limit (from your own finding)

The default worker model `deepseek-v4.1-flash` is missing from `context_limits` (policy.json:164), so `hx context decide` returns unknown for it. Add the correct limit, citing your source for the number (OpenCode's model metadata or the provider docs), plus a test that the default worker gets a real utilisation.

## 2. Measured OpenCode Go usage (Q-205)

The creator, 8 Nov, pointing at the OpenCode console: https://opencode.ai/console/wrk_01M1KFTWRKHDMAYZ41XAGHT27Q/go "for go usage". Today `hx usage` estimates OpenCode from opencode.db spend against the plan cap, tagged estimated.

1. Find whether the console's Go usage figure comes from an API that the logged-in OpenCode CLI can call with its stored login. Look at OpenCode's public source (sst/opencode on GitHub) and any `opencode` command that shows plan usage. Do not scrape the HTML console with a browser login.
2. If such an API exists: read it in hx, tag the result measured, keep the local estimate as the fallback when the call fails, and time it out quickly. Add tests with a recorded, token-free response.
3. If it does not exist, change nothing and report what you found.

## Verify

`python -m pytest` in the repo passes. Run `hx usage` and `hx context decide dev-opencode`, and show the relevant fields (no secrets).

## Report

Via `hx say claude`, under 8 lines: both results, the test result, and the files you changed.
