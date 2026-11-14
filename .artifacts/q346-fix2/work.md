# Q-346 correction round 2

Goal: keep ordinary work behind active or unknown compaction and serialize compaction keys while preserving control retries.
Playbook: poteto-mode Bug fix. All roles run inline under the assignee contract.
Principles: Fix Root Causes changes persisted control and admission decisions. Model the Domain reuses the existing lease state machine and control field. Test Behavior Not Implementation checks delivery and controller outcomes. Prove It Works reruns supplied probes and full acceptance.

1. Reproduce it yourself on the matching surface via the control skill (Non-negotiables), even when a debug or instrumentation protocol says to ask the user to reproduce. Done with the supplied test doubles, 5 failed, 1 passed.
2. Binary-search the cause. Done. The force bit is lost at outbox serialization, stale data releases a hold, guarded reserve duplicates admission, and lead recommendations bypass it.
3. Plan the fix. Keep outbox control durable, require absent telemetry before unknown-session release, use shared admission under lock, and schedule guarded quick compaction. Skip delegation under the assignee contract.
4. Verify on the same surface. Supplied probes pass, 6 passed in 0.25s. Focused acceptance passes, 189 passed in 8.32s. Added CLI coverage executes the recommended delayed command arguments against scratch leases and host doubles. Full root suite finished with 2 failed, 1161 passed in 200.62s. Both failures are the documented benchmark bug exercise in bench/tasks/bugfix/repo/test_dates.py.
5. Stage the commits so the failing repro lands before the fix in git history. Skip Git writes under the brief. Tests are added before the fix.
6. Run Opening a PR. Skip under the no-Git-write brief.

Architecture sketch: the existing outbox record carries control as a boolean; session operations retain their token, session and phase. No new types or signatures. Reuse key_stream_reason at reservation boundaries. Prefer invoking compact_quick over adding a second background reservation implementation because compact_quick already owns reservation and uncertain-outcome cleanup.
Throughput checkpoint: four root changes in hx/say.py and hx/context.py; preserve probes as suite tests, then focused and full acceptance. No real host sends or live state.
