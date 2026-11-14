# Q-422

Goal: retain unknown reset outcomes without positive proof, and prevent a new quick compaction from typing while a control send is in flight. Playbook: poteto-mode Bug fix. Fix Root Causes moved every send through shared admission. Model the Domain reused holder-owned expiring delivery records. Test Behavior Not Implementation replaced the probe assertion that encoded the missing reservation with observable compaction exclusion and later success. Prove It Works drove failing probes, passing regressions and real CLI dispatch with scratch state and fake terminal I/O.

## Changes

- `hx/context.py` samples held reset-session telemetry before taking the leases lock. Unknown live session IDs keep the reset hold unless the pane is idle and held telemetry is wholly absent. Known replacement releases the obsolete hold. An idle same-session release requires fresh post-request telemetry.
- `hx/ledger.py` admits control messages through the same short delivery reservation. Its shared rule lets control bypass session-operation holds but still serializes all deliveries. Existing expiry, holder-matched release and host-I/O-outside-lock rules remain in use.
- `hx/say.py` reserves and releases control, forced, lead-report and ordinary deliveries through the shared path, including pending retries.
- `tests/test_q346_recheck2.py` removes both strict xfail marks. Adds stale/partial/absent telemetry and known replacement coverage, control/force/lead contention and retry cases, failure cleanup, and successful compaction after the send finishes.
- `tests/test_context_session_reset.py` now requires positive post-request evidence before recovering an idle unchanged session. The former assertion allowed recovery solely from idle status and old telemetry.

## Evidence

- Before production edits, `python -m pytest tests/test_q346_recheck2.py -q` reported `2 failed, 7 passed in 1.40s`. Both named regressions failed with their actual unwanted outcomes.
- After initial fix, the same file and adjacent admission/exclusion suites reported `37 passed in 2.48s`.
- Expanded focused validation reported `68 passed in 24.55s` for `python -m pytest tests/test_q346_recheck2.py tests/test_context_session_reset.py tests/test_compaction_admission.py tests/test_key_stream_exclusion.py -q`.
- `python .artifacts/q422/cli_check.py` passed. It invokes the real CLI dispatcher for reset recovery, say and quick compaction, checks exit codes, output, slash effects and scratch leases. No terminal commands reach a real host.
- First full suite reported `1 failed, 1214 passed in 252.87s`, identifying only the old reset expectation corrected above.
- Final acceptance `python -m pytest tests -q` reported `1215 passed in 211.55s (0:03:31)`. Both formerly strict-xfail cases are enabled and passing.
- `git diff --check` passed.

## Boundary

Only Git write was the required new worktree and branch creation. No commits, staging, real-terminal I/O, port 4180 access or token access. Cross-family review is left to the lead under the no-redelegation brief. Control retains its authorized recovery bypass of existing session-operation holds; its delivery reservation prevents new compaction/reset streams from entering while the send is in flight.
