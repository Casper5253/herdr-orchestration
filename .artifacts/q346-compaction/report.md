# Q-346 compaction admission

Goal: no ordinary brief reaches an agent while a compaction is active or its outcome is unknown. The brief waits in the outbox and is delivered by `hx say --pending` after proof. Reports to the lead and control messages still arrive.

Playbook: poteto-mode Feature. The architect skill ran two design candidates before code. Principles: Model the Domain, Boundary Discipline, Make Operations Idempotent, Test Behavior, Prove It Works, and Sequence Work into Verifiable Units.

## What changed

- `hx say` classifies a message as control when it targets the lead or carries `--control` or `--force`. Control skips admission and the delivery reservation.
- Ordinary work checks the target compaction operation and acquires `deliveries[agent]` in one short leases lock. Host I/O runs outside that lock.
- A blocked ordinary brief stays `pending` with a reason. `hx say --pending` retries it after recovery.
- `hx compact <agent> --quick` reserves a compaction operation before typing `/compact` and verifies the same session at lower fresh utilisation.
- `hx context recover <agent>` accepts `--session` and `--token` and releases only a matching operation. It requires fresh same-session proof or a confirmed session replacement.
- The existing task-bound `hx compact` now records `phase` and the pre-compaction utilisation, checks a live delivery reservation, and catches `BaseException` so an interrupt leaves `compact-unknown`.
- `deliveries` is preserved by `_recompute_leases` and task release.
- Tests use isolated leases through `tests/conftest.py`.

## Design choice

| Axis | A: session operation plus delivery map | B: one agent operation record |
|---|---|---|
| Compaction owner | Existing `sessions[provider-session-id]` | New `agents[name]` |
| Send exclusion | New `deliveries[agent]` | Nested in the agent record |
| Migration | None | Dual-read and migrate writers |
| Test churn | Low | High |
| Main risk | Two maps can drift | Migration can miss a writer |

Chose A. It keeps the audit's existing controller as the one compaction owner and adds only the send exclusion the controller needs. B was structurally cleaner but required a schema migration and touched every existing `sessions` test.

## Throughput checkpoint

- **Blocking first steps.** The lease schema and recovery predicate had to be settled before tests could pass. No parallel gate.
- **Independent workstreams.** Source and tests could split, but the test file exercises the same lease transitions. One owner was cheaper.
- **Shared mutable state.** `leases.json` holds `sessions` and `deliveries`. Every check and reservation uses one short shared lock. No host I/O under the lock.
- **Smallest safe decomposition.** One worktree and one owner. The change is one feature across four modules and one new test file.

## Acceptance replay

- Early host wait: ordinary `hx say` returns pending with no transport while the operation is `compacting`. The lead report still sends.
- Stale 103 percent telemetry: the operation holds ordinary work. `hx context recover` keeps the hold until a fresh lower sample.
- Crash before keys: a `reserved` operation releases after fresh same-session idle proof. `host.SendNotAttempted` also releases because no keys were pressed.
- Crash after keys: `KeyboardInterrupt` leaves `compact-unknown` with a token and no delivery reservation. Recovery keeps it while busy or stale and releases on fresh lower telemetry.
- Session replacement: an obsolete-session operation releases. A second operation for another agent remains.
- Two concurrent sends: one sends, the second waits briefly and stays pending. `--pending` delivers it after the first releases.
- Interrupted compaction: both quick and task-bound paths leave an unknown operation instead of an orphan `compacting` lease.
- Control: a report to the lead and an explicit `--control` message bypass the hold.

## Verification

- `python -m pytest -q tests` -> 1135 passed in 121.74s.
- `python -m pytest -q tests/test_compaction_admission.py tests/test_context.py tests/test_say.py tests/test_say_expiry.py tests/test_ledger.py` -> 163 passed in 10.78s.
- `git diff --check` clean.
- No Git writes. Tests and artifacts only.

## Review dispositions

- P1 legacy `compacting` lease with no `phase` was unrecoverable. Fixed: a missing phase follows the requested/unknown branch.
- P2 delivery heartbeat TTL was 120 seconds and the send path can exceed it. Fixed: 300 seconds.
- P2 a hard kill between the request marker and the key call stays fail-closed. Kept by design. `SendNotAttempted` proves no keys and releases. Session replacement is the other release.
- P3 admission used the raw host session id. Fixed: it uses `context._session(destination)`, the same identity the compaction lease uses for Devin panes and Claude status files.

## Open decision

The hard-kill window between the durable request marker and the key call has no automatic release. This is deliberate fail-closed behavior. If the lead wants a manual escape, add an explicit force path to `hx context recover`. The current release paths are fresh lower proof, a `reserved` proof, and session replacement.

## Artifacts

- `design.md`: candidate comparison and chosen shape.
- `review-findings.md`: reviewer findings and dispositions.
- `pytest-full.txt`: full suite output.
- `pytest-targeted.txt`: focused suite output.
