# Q-422

Goal: retain uncertain reset holds without positive proof and serialize control sends against new compaction key streams.
Playbook: poteto-mode Bug fix. Roles run inline under the assignee contract.
Principles: Fix Root Causes puts control admission at the shared leases boundary. Model the Domain reuses the expiring holder-owned delivery record. Test Behavior Not Implementation checks emitted slash commands rather than requiring absence of a reservation. Prove It Works requires failing and passing executions with fake host I/O.

## Checklist

- [x] 1. Reproduce it yourself on the matching surface via the control skill (Non-negotiables), even when a debug or instrumentation protocol says to ask the user to reproduce. Fake host boundary required by brief, no real terminals.
- [x] 2. Binary-search the cause. Unknown live ID counted as changed; control bypass skipped the delivery reservation entirely.
- [x] 3. Plan the fix. Reuse the delivery lease with a control flag on admission and the shared rule. Extend held-session telemetry sampling to resets. No new persistent shape.
- [x] 4. Verify on the same surface. Original probes pass and CLI dispatch exercised with scratch leases and fake host I/O. Final full suite passed.
- [x] 5. Stage the commits so the failing repro lands before the fix in git history. Skip staging and commits, prohibited by brief. Fail-first execution retained below.
- [x] 6. Run Opening a PR. Skip, prohibited Git writes and no PR request.

## Ground and design

Ledger owns the shared per-pane admission rule under its leases lock. Say reserves before host I/O and releases in finally after evidence. Compaction calls the same rule before keys. Reset recovery currently treats an empty live session ID as replacement, while compaction uses idle plus absent held telemetry. Control currently bypasses reservation altogether.

Candidate A adds a second control-reservation map and teaches compaction to consult it. Rejected because it duplicates lifecycle and cleanup. Candidate B uses existing deliveries, adds a keyword-only control flag, and bypasses session-operation holds while still checking an in-flight delivery. Selected. Control delivery retains the existing 300-second abandonment bound and holder-matched cleanup.

Caller sketch: say -> Ledger.admit_delivery(..., control=message_control) -> key_stream_reason(..., control=control). All accepted sends reserve a holder; host sends outside the lock; finally releases only that holder. Reset recover samples held reset telemetry before locking and keeps unknown IDs unless idle with absent held telemetry. Known same session requires idle and fresh post-request telemetry.

## Fail-first evidence

Removed both strict xfail marks. Removed the control probe assertion requiring no reservation, which encoded the bug and conflicts with the brief. Added successful compaction after the control send finishes.

`python -m pytest tests/test_q346_recheck2.py -q`

`2 failed, 7 passed in 1.40s`

Both failures are the named defects: reset recovery returned recovered instead of keep; concurrent quick compaction returned compacted instead of keep.

## Expanded verification

`python -m pytest tests/test_q346_recheck2.py tests/test_context_session_reset.py tests/test_compaction_admission.py tests/test_key_stream_exclusion.py -q`

`68 passed in 24.55s`

`python .artifacts/q422/cli_check.py`

`CLI scratch acceptance passed: unknown reset kept, replacement released, control send excludes compaction, later compaction succeeds.`

First full run: `1 failed, 1214 passed in 252.87s`. The old failed-reset test expected release on idle with pre-request telemetry. Updated to assert keep first, then supply fresh same-session post-request telemetry and assert release. This is the new positive-proof requirement, not a production workaround. Focused run above includes the corrected test.

Final acceptance: `python -m pytest tests -q` reported `1215 passed in 211.55s (0:03:31)`.
