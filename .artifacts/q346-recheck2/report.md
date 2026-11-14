# Q-346 recheck after fix round 2

Goal: no ordinary brief reaches an agent while a compaction is active or its outcome is unknown, no two key streams reach one pane, and control messages and `--force` always arrive.

Playbook: poteto-mode Investigation (code review). Principles applied: Prove It Works (every remaining gap has a failing probe and every claimed fix was rerun), Test Behavior Not Implementation (probes drive `Controller`, `hx say` and the public `Ledger` methods, not private helpers), Attack the Premise (the premises "each fix is at the root" and "the shared rule sees every key stream" are tested directly), Boundary Discipline (admission belongs at the leases boundary; control bypass is the edge policy).

Verdict: fix. Findings 1 and 3 are fixed at the root, finding 2 is fixed only for compaction operations, and finding 4 is fixed only for durable holds. Three gaps remain, one of them reachable with a normal task release.

## The four findings

1. Forced/control durability: fixed. `hx/say.py:307` persists `control`, `hx/say.py:773` writes `control or force`, and `hx/say.py:636` reads the record on a retry. A forced message that goes pending because the destination is blocked (not just `SendNotAttempted`) retries as control and bypasses a live compaction; an ordinary pending message is still held. Both probes pass.

2. Unknown-live-sid recovery: fixed for compaction operations, not at the root. The compaction branch now releases only when the held session has no telemetry and the pane is idle (`hx/context.py:1180`, `:1186`), and stale, partial and same-session-idle samples keep. Codex's parametrized test and my same-session test pass. The reset branch at `hx/context.py:1210` still treats an unknown live sid as a different session and releases, so the shared root is not fixed. Finding 1 below.

3. Guarded compaction shared rule: fixed. `hx/context.py:844` calls `key_stream_reason` inside the typed reserve, and a real delivery hold or a real reset hold now stops the keys, not only a patched rule. Probes pass.

4. Lead self-compaction: fixed at decision time, not at the root. `hx/context.py:690` refuses on a durable hold and `hx/context.py:700` schedules `hx compact --quick`, which rechecks admission immediately before keys. But a report to the lead is control by resolved role, takes no delivery reservation (`hx/say.py:637`), and is invisible to the shared rule (`hx/ledger.py:157`), so the quick compaction still types `/compact` while a lead report is in flight. Finding 2 below.

## Findings

- P2 `hx/context.py:1210`: `recover` releases a `reset-unknown` hold whenever `live_sid != held_sid`, and an unknown live sid is the empty string, so a busy pane with no readable session id releases the hold. This is the same root as finding 2, "unknown is not proof"; the compaction branch was fixed, the reset branch was not. A reset whose keys may still be staged then lets an ordinary send or a compaction type into the same pane. Probe: `test_recover_keeps_reset_unknown_when_live_sid_is_unknown` fails. Fix: apply the compaction rule to the reset hold, keep when the live sid is unknown, and release only on a known different sid or on idle with positive proof.

- P2 `hx/say.py:637` and `hx/ledger.py:157`: control sends, including every report to the lead, skip admission and take no `deliveries` reservation (`hx/say.py:635`, `:637`), so `key_stream_reason` cannot see them. The lead's background `hx compact ... --quick` (`hx/context.py:700`) types `/compact` while a control send to the same pane is in flight. My finding 4 probe with a seeded delivery hold passes, but the named scenario, "a lead report can interleave", does not. Design invariant 6, "control and lead reports never take the leases lock", is in direct tension with "no two key streams reach one pane"; the lead owns that tradeoff. Probes: `test_lead_quick_compact_waits_for_an_in_flight_control_send` fails, and the same test shows the control send holds no `deliveries` entry. Fix options: control takes a short reservation but never waits on a compaction, or the lead self-compaction is declared exempt and control is serialized another way.

- P1 `hx/ledger.py:879`: `release_task_reservations` calls `leases.clear()` before evaluating the `files`/`sessions`/`deliveries` map passed to `_recompute_leases`, so the new `deliveries` line at `hx/ledger.py:883` always passes empty maps. `hx task release` therefore wipes every delivery reservation and every compaction operation, and `admit_delivery` then admits an ordinary send during a live compaction. This violates design invariant 5, "task release must not drop `deliveries`", and the first goal clause. It also drops other projects' file partitions, because `_recompute_leases` rebuilds from the emptied `files`. Probes: `test_task_release_preserves_deliveries_and_session_operations` and `test_task_release_does_not_open_admission_during_compaction` fail. Fix: snapshot the three maps before `clear()`, or delete only the released task's entries in place, then recompute.

## Checked, no finding

- Forced and explicit control persist across a pending retry, including a blocked destination; an ordinary pending message is still held by a live compaction.
- The guarded and quick reserves consult the shared rule under the leases lock; a real delivery hold and a real reset hold stop the keys; `hx reset` stops during an active compaction.
- The compaction recover branch keeps stale, partial and same-session-idle samples; it releases only on absent telemetry with an idle pane, or on a known different session.
- Two ordinary sends serialize through `deliveries`; the holder-matched release, the 300 s TTL bound, legacy no-phase leases, and the crash and `SendNotAttempted` matrix pass in the focused suite.
- Supplied round-1 probes pass 6 of 6.

## Verification

- Probes: `python -m pytest .artifacts/q346-recheck2/probes -q` -> 4 failed, 5 passed in 1.65 s. The four failures are the three findings above (the reset one, the control one, and the two task-release probes).
- Full suite: `python -m pytest -q` -> 2 failed, 1161 passed in 235.96 s. The two are the intentional `bench/tasks/bugfix/repo/test_dates.py` fixtures. Do not override `--basetemp`; the isolation tests assert the project's own per-pid temp root and fail if a custom basetemp is supplied.
- Focused: `python -m pytest tests/test_compaction_recheck.py tests/test_key_stream_exclusion.py tests/test_compaction_admission.py tests/test_context.py tests/test_say.py tests/test_say_expiry.py tests/test_lead_context.py -q` -> 189 passed in 7.27 s.
- Captured output: `.artifacts/q346-recheck2/pytest-probes.txt` and `.artifacts/q346-recheck2/pytest-full.txt`.

## Scope

Read-only apart from `.artifacts/q346-recheck2/`. No source edits, no git writes, no real terminals, no port 4180, no token files.
