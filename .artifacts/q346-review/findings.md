# Q-346 cross-family review

Goal: no ordinary brief reaches an agent while its compaction is active or unknown.
Playbook: poteto-mode Investigation (code review). Principles applied: prove every
claim by running it (failing probes in `probes/`), check the edges the brief names —
races, crash recovery, bypasses, old state — and follow the leases lock ordering.

The core mechanism holds up: compaction check and `deliveries` reservation share one
short leases lock, so `hx say` vs `hx compact` serializes cleanly in either order;
`deliveries` then blocks compaction while keys fly. Marker-before-keys, `SendNotAttempted`
release, `compact-unknown` on interrupt, and legacy no-`phase` leases are all correct.
Bypass is classification-based (resolved lead role, `control` record flag, `--force`),
not text-based: `with_header` already binds the sender name, so an ordinary brief
cannot pass as a control or lead message by its text. Old `sessions` entries without
`phase`/`session_id` load and recover (`test_legacy_compacting_lease_without_phase_is_recoverable`).

## Findings

- **P2 — `hx/context.py:1044`: `compact_quick` ignores non-compaction session holds.**
  Its `reserve` checks only `compaction_operations` (token entries in compaction
  states) and `delivery_hold`. A `reset:<name>` hold (`resetting` or
  `reset-unknown`; the entries carry no `token`) is invisible, so `hx compact
  --quick` types `/compact` into a pane whose reset is in flight or unverified.
  The guarded path refuses the same state via `_has_blocking_session_lease`
  (`hx/context.py:269`), which the quick path never calls.
  Probe: `test_quick_compaction_must_refuse_while_reset_unverified[resetting]` and
  `[reset-unknown]` — `/compact` was typed both times.

- **P2 — `hx/context.py:480`: `hx reset` ignores compaction ops and delivery holds.**
  `reset`'s `reserve` checks only `sessions["reset:<name>"]`. A `compacting` /
  `compact-unknown` op on `sessions[sid]` — including the up-to-300 s verify
  window `compact_quick` holds open — and a `deliveries[name]` reservation are
  both invisible, so `/new` or `/clear` keys can land mid-compaction or mid-paste
  of an `hx say` delivery on the same pane. The reset-side check was already
  missing for the guarded path (pre-existing), but the new op type widens the
  window and the new `deliveries` primitive is not consulted.
  Probes: `test_reset_must_refuse_during_active_compaction` (`/new` typed while a
  `compacting` op was live) and `test_reset_must_refuse_during_delivery_in_flight`
  (`/new` typed while `deliveries` was held).

- **P3 — `hx/ledger.py:119`: a `deliveries` entry without a parseable timestamp
  never expires.** `delivery_hold` returns the entry when `heartbeat_at` /
  `acquired_at` is missing or corrupt, and `hx context recover`
  (`hx/context.py:1129`) only touches `sessions`, so nothing releases the slot:
  sends and compactions on that target block forever. Only reachable through
  malformed or foreign state — this code always writes both stamps — but it
  violates the "never block a worker forever" requirement the brief names.
  Probe: `test_delivery_without_timestamp_must_not_block_forever`.

- **P3 — `hx/ledger.py:430`: `heartbeat_at` is written once and never renewed.**
  The 300 s TTL bounds total send-plus-evidence duration, not liveness. A send
  that exceeds it (chained host timeouts on `send_checked` + evidence reads)
  goes stale while still holding keys, and a second delivery is admitted to the
  same pane — interleaved input. Typical worst case stays well under 300 s, so
  the window is thin; a real heartbeat renewal in `_deliver` would close it.

- **P3 — `hx/context.py:1150`: obsolete ops are unreleasable while the live
  session id is unknown.** The obsolete-session release needs a truthy
  `live_sid`; when `_session(agent)` is `""` the candidate falls into the
  proof branches, which can never pass for a session that no longer exists.
  Self-corrects once the host reports a sid again; a `--session` targeted
  release still cannot help.
  Probe: `test_recover_releases_obsolete_op_when_live_session_unknown`.

## Checked, no finding

- Two `hx say` to one target: second waits `DELIVERY_WAIT_SECONDS` then stays
  `pending`; holder-matched release; retried by `--pending`. Covered by the
  author's tests and the lock-sharing argument.
- Brief between request marker and keys: `compacting` state exists from
  `reserve` onward in both paths; `say` blocks on state regardless of `phase`.
- Crash matrix: pre-marker crash deletes the lease (no keys provable);
  `SendNotAttempted` releases; any other `BaseException` after `submitted`
  leaves `compact-unknown`, releasable by `hx context recover` on fresh lower
  telemetry or session replacement. Fail-closed as designed; the
  keys-never-landed-but-`requested` window can hold work until telemetry drops
  — the author's stated open decision, not a defect.
- Ordinary brief cannot pass as `--control`/lead report by text: bypass is
  `force`, the `control` record flag, or resolved lead role.
- Old state: missing `deliveries` map normalized; legacy `compacting` /
  `compact-unknown` leases without `phase` block sends and release through
  `recover`'s `phase in (None, "requested")` branch.

## Probes

`probes/test_q346_probes.py` — 6 tests, all fail on this worktree (each asserts
the required behavior). `python -m pytest -q tests` -> 1135 passed in 386.78 s.

## Verdict

fix — the admission core is sound, but `hx reset` and `hx compact --quick` do not
mutually exclude, so two key-streams can still reach one pane.
