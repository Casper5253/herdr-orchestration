# Q-346 fix round 2

Goal: no ordinary brief during active or unknown compaction, one key stream per pane, and durable control delivery including force.
Playbook: poteto-mode Bug fix, with all roles inline under the assignee contract.
Principles applied: Fix Root Causes persists control before delivery and fixes recovery evidence at admission. Model the Domain reuses the control field and lease state machine. Test Behavior Not Implementation drives delivery, recovery and CLI outcomes. Prove It Works reruns the failing probes and the exact acceptance command.

Status: blocked on full-suite acceptance. All four requested findings are fixed. The remaining two failures are in the deliberately buggy benchmark fixture, outside this brief. No unrelated code or test-discovery changes were made.

## Changes in this round

- hx/say.py persists control=true for force so pending retries retain their admission bypass.
- hx/context.py retains unknown-live-session holds whenever held-session telemetry exists, including stale and partial telemetry. An idle pane can still release when held-session telemetry is wholly absent, as requested by the recheck.
- Guarded compaction calls key_stream_reason under the reservation lock and keeps task-hold validation.
- Lead decisions consult key_stream_reason under the leases lock before writing recovery state. Delayed compaction now runs hx compact --quick, which reacquires admission immediately before keys. Removed the instruction to issue an unguarded extra Enter.
- tests/test_compaction_recheck.py brings all six supplied probes into the suite and adds eight cases for delayed CLI admission and stale or partial telemetry. tests/test_lead_context.py expects the guarded command. A comment in tests/test_key_stream_exclusion.py now describes absent telemetry accurately.

## Evidence

Before production changes, both supplied probes and promoted suite regressions produced:

    5 failed, 1 passed

After production changes:

    python -m pytest .artifacts/q346-recheck/probes -q --basetemp=.artifacts/q346-fix2/probe-temp
    6 passed in 0.25s

    python -m pytest tests/test_compaction_recheck.py tests/test_key_stream_exclusion.py tests/test_compaction_admission.py tests/test_context.py tests/test_say.py tests/test_say_expiry.py tests/test_lead_context.py -q
    189 passed in 8.32s

    python -m pytest -q
    2 failed, 1161 passed in 200.62s (0:03:20)

Full-command failures:

- bench/tasks/bugfix/repo/test_dates.py:32, test_list_dates_includes_endpoints.
- bench/tasks/bugfix/repo/test_dates.py:41, test_iter_yields_all_days.

The benchmark fixture documents its intentional failure. Its DateRange iterator uses cur < self.end while the exercise tests require the inclusive endpoint. These files were not changed. All other collected tests passed.

The delayed-command tests parse the returned background command and execute its actual hx CLI arguments with scratch leases and fake key sends. They cover success plus delivery, compacting, compact-unknown and reset-unknown holds introduced after scheduling. They verify the operation is requested before keys and removed after successful proof. No real terminal keys or background process were launched. This does not claim a live-terminal integration test.

No Git writes were performed. Earlier uncommitted work remains. No cross-family review was delegated under the assignee contract. The lead owns the final acceptance decision for the unrelated benchmark failures.
