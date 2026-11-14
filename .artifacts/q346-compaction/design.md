# Q-346 design: compaction admission

Goal: no ordinary work reaches an agent while a compaction is active or its outcome is unknown. The brief waits in the outbox and is delivered by `hx say --pending` after proof. Control messages and reports to the lead always arrive.

Model: `CompactionProposed.tla` replaces `AdmitWork` with `HoldUnverified`. The implementation adds the liveness edges the model does not prove: `reserved -> released` after proof no keys went out, and `unknown -> verified` after fresh same-session lower telemetry or an established session replacement.

## Candidates

| Axis | A: session operation plus delivery map | B: one agent operation record |
|---|---|---|
| Compaction owner | Existing `sessions[provider-session-id]` record | New `agents[name]` record |
| Send exclusion | New `deliveries[agent]` map | Nested in the same agent record |
| Migration | None | Dual-read `sessions`, migrate writers |
| Test churn | Low | High, every `sessions["sid"]` assertion changes |
| Lock scope | One short leases lock for check plus reserve | One short leases lock |
| Risk | Two maps can drift | Migration can miss a writer |

## Chosen shape

A, with the audit's operation phases and a strict recovery predicate.

- `sessions[provider-session-id]` stays the one compaction owner. New fields: `phase` in `reserved|requested|unknown`, `source` in `guarded|quick`, `reserved_at`, `requested_at`, `before_utilisation`, `before_sampled_at`.
- `deliveries[agent]` is the send exclusion. It is not compaction state. It is keyed by target agent so two senders cannot overlap and distinct targets stay independent.
- `hx say` classifies a message as control when it is for the lead, carries `--control`, or carries `--force`. Control skips both admission and the delivery reservation.
- Ordinary work checks the matching compaction operation and acquires `deliveries[agent]` in one short `leases.lock` critical section. No host I/O runs under that lock.
- `hx compact <agent> --quick` reserves the operation before keys, marks `requested` under the lock immediately before keys, then verifies same-session fresh lower telemetry outside the lock.
- `hx context recover <agent> [--session SID] [--token TOKEN]` releases only a matching operation. A `reserved` record releases after idle and fresh same-session telemetry. A `requested` or `compact-unknown` record releases after idle, fresh telemetry after the request, and lower utilisation. A non-empty different session releases the obsolete record.

## Invariants

1. No ordinary transport while a matching operation is `compacting` or `compact-unknown`.
2. At most one live delivery reservation per agent. A second sender waits briefly, then stays pending.
3. Compaction keys are sent only after the reservation exists and `requested` is durable.
4. A stale heartbeat or dead PID never releases a same-session unknown. Only fresh proof or session replacement does.
5. Every lease mutation preserves `deliveries`. `_recompute_leases` and task release must not drop it.
6. Control and lead reports never take the leases lock, so a false hold cannot silence the team.

## Test plan

- Early host wait: ordinary `hx say` is pending with no transport; lead report sends.
- Stale 103 percent: active operation holds ordinary work regardless of telemetry.
- Crash before keys: `reserved` releases after fresh same-session idle proof.
- Crash after keys: `compact-unknown` stays held while busy or stale; fresh lower releases.
- Session replacement: obsolete-session operation releases, the current operation stays.
- Two concurrent sends: one sends, one pending, retry delivers after release.
- Interrupted compaction: `KeyboardInterrupt` leaves `compact-unknown`, no delivery reservation.
- Recovery matching: wrong token, wrong agent and active `compacting` all keep.
- Lock boundary: transport runs with no leases or repo lock.

## Review dispositions

- Legacy `compacting` lease with no `phase` now follows the requested/unknown recovery path, so an upgrade does not leave a permanent hold.
- The delivery reservation TTL is 300 seconds, above the host send bound. A live sender does not lose its reservation during `_submission_evidence`.
- `host.SendNotAttempted` proves no keys were pressed, so both compaction paths remove the reservation. A hard kill between the durable request marker and the key call stays fail-closed until proof or session replacement.
- Admission resolves the target session with `context._session`, matching the key used by the compaction lease for Devin panes and Claude status files.
