| model | pass | median s | total $ | fails |
|---|---|---|---|---|
| mimo-v2.6-flash | 5/5 | 142 | 0.028 |  |
| qwen3.8-flash | 5/5 | 125 | 0.046 |  |
| glm-5.3-flash | 5/5 | 46 | 0.047 |  |
| minimax-m3 | 5/5 | 51 | 0.106 |  |
| qwen3.7-plus | 4/5 | 60 | 0.081 | tests |
| kimi-k2.7-code | 4/5 | 106 | 0.273 | tests |
| deepseek-v4.1-flash | 0/5 | 76 | 0.000 | bugfix, feature, debug, refactor, tests |

Run 2026-10-16 (stamp 20261016T012704Z), 7 models x 5 tasks, one attempt each, default variant.
Refactor results re-scored after fixing a false negative in `tasks/refactor/check.py` (it counted files that only import the shared helper as duplicates).
deepseek-v4.1-flash failed every run with `APIError: This Go model requires Global regions` (account privacy setting), not a capability failure.
qwen3.7-plus and kimi-k2.7-code `tests` failures are genuine: their tests did not catch either seeded mutant.

Decision: economy = glm-5.3-flash, default = minimax-m3 (also the persistent hx-opencode model), strong = kimi-k2.7-code.
Caveat: one attempt per cell and small tasks; the benchmark separates economy from broken, not default from strong.
