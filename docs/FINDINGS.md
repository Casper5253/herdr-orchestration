# Findings log

Evidence gathered while building hx, newest last. Each entry: what, evidence, consequence.

1. **OpenCode Go limits are per model** (5h 20%, week 50%, month 100% of the model cap). docs/audit/opencode.md. Router rotates models.
2. **Kimi K2.7 is the heavy pool**: $17.10 over 584 calls in 7 days (57% of the Go weekly cap); ~138K cached tokens re-sent per call. opencode.db. Persistent sessions on Kimi are expensive; moved to escalation only.
3. **`/compact` via `herdr agent prompt` works for Codex** ("Context compacted · 1m 04s", same session id 01a1423e…). Must be sent from PowerShell or Python: Git Bash (MSYS) rewrites `/compact` to `C:/Program Files/Git/compact`.
4. **Headless `opencode run` auto-rejects permission asks** (e.g. external_directory) and still exits 0. Workers must stay inside the repo; `ocrun` cannot trust exit codes.
5. **deepseek-v4.1-flash is unusable on this account**: `APIError: This Go model requires Global regions` (workspace privacy setting). Excluded; enabling it is a user privacy decision.
6. **Headless run with an API error still exits 0**; the only signal is a `{"type":"error"}` event in `--format json` output.
7. **minimax-m3 can exhaust its 32K reasoning budget with zero output** (`finish=length`, `tokens.reasoning=32000`, `output=0`) on a multi-file correction after reading ~40K of context. It has no variants to lower thinking. Twice in a row on the router correction; fine on the bench, the bench build and the first router build. Consequence: `ocrun` treats `finish=length` with zero output as a capability failure and escalates; minimax-m3 is not the persistent default.
8. **Benchmark check false negative**: refactor check counted files importing `split_quoted` as duplicates. Fixed; scores re-derived. Lesson: verify graders against a correct solution from a different author before trusting a ranking.
