# Q-346 recheck

Goal: no ordinary brief reaches an agent while a compaction is active or its
outcome is unknown, and no two key streams reach one pane.

Playbook: poteto-mode Investigation (code review). Principles applied: Prove It
Works (failing probes in `probes/`), Test Behavior Not Implementation (each
probe drives the public controller or `hx say`), Fix Root Causes (trace each
gap to the admission decision), and Attack the Premise (the claim that every
key-sending path uses one rule).

## Findings

- **P2 `hx/say.py:770`.** `--force` is not persisted as control. The outbox
  record stores `control=bool(control)` (`hx/say.py:307`), while `force` is
  passed only to the first `_deliver` (`hx/say.py:775`). A forced message that
  goes pending (blocked destination or `SendNotAttempted`) is retried by
  `--pending` with `control=None`; `message_control` is then false and
  `_admit_work` holds it on an active compaction. This breaks the design's
  "control messages always arrive" and the explicit `--force` bypass.
  Probes: `test_forced_message_is_stored_as_control`,
  `test_forced_pending_retry_bypasses_compaction` (both fail).
  Fix: persist `control=bool(control or force)` in `_write_outbox`, or store
  the force flag and honor it on retry.

- **P2 `hx/context.py:1180`.** The new unknown-live-sid recover branch releases
  a held operation when `held_sample` is not fresh after the request. A live
  but idle session also has stale telemetry, so quiet is not proof the session
  is dead. The fix1 brief requires "without ever releasing a live one."
  Probe: `test_recover_keeps_live_held_session_when_live_sid_unknown` fails: a
  `compact-unknown` operation on `ses-old` with a stale `sampled_at` is
  released while the held session still has a valid sample.
  Fix: release only when the held session has no telemetry at all (not merely
  stale), or when the live sid is known and different, or when a provider
  session file/process proves the held session is gone. Otherwise keep.

- **P3 `hx/context.py:841`.** The guarded `hx compact` reserve does not call
  `key_stream_reason`. It duplicates the key-stream checks through
  `delivery_hold` (`hx/context.py:843`) and `_has_blocking_session_lease`
  (`hx/context.py:848`). The behavior is equivalent for today's states, but
  the shared one-rule invariant is not enforced by construction; a new state
  added to `key_stream_reason` will be missed by `hx compact`.
  Probe: `test_guarded_compact_consults_the_shared_key_stream_rule` patches
  `ledger.key_stream_reason` to block and observes `/compact` typed anyway.
  Fix: call `key_stream_reason` in the reserve, and keep the task-hold check.

- **P3 `hx/context.py:656` / `hx/context.py:699`.** The lead self-compact path
  is a key-sending path that does not consult `key_stream_reason` or
  `delivery_hold`. `_decide_lead` schedules a background
  `Start-Sleep 60; hc keys ... /compact; hc keys ... enter` while a delivery is
  in flight. The lead is a control path, but the self-compact is a compaction,
  not a report; a lead report can interleave.
  Probe: `test_lead_self_compact_refuses_while_a_key_stream_is_active` seeds a
  delivery hold and still gets `self-compact`.
  Fix: check the shared rule in `_decide_lead` and make the background command
  a guarded `hx compact --quick` path, or declare the lead intentionally
  exempt. This is lead-only, so the lead should decide.

## Checked, no finding

- `hx say` ordinary, `hx reset`, and `hx compact --quick` all call
  `key_stream_reason` inside the leases lock. The new tests and the focused
  suite pass.
- The guarded `hx compact` has equivalent behavior today through
  `delivery_hold` plus `_has_blocking_session_lease`; the P3 is structural,
  not a current behavioral gap.
- Control and lead reports, and the first `--force` send, intentionally skip
  admission. Classification is by resolved role or explicit flag, never by
  message text. The P2 is the retry durability, not the bypass itself.
- Question 3: the 300 s delivery TTL is above the real `hx say` send bound.
  `HcHost.send_checked` and `HerdrHost.send_checked` use a 60 s process
  timeout when not waiting; `_submission_evidence` adds at most about 10 s plus
  0.75 s plus bounded reads. The `context.compact` acknowledgement send uses
  `wait=True` with a 300 s timeout, but it holds a session lease, not a
  `deliveries` entry; a `hx say` to the same pane is blocked by the compaction
  operation. A >300 s ordinary send is not reachable with the current hosts, so
  no extra cap is needed. A `say`-level cap would be defense in depth, not a
  requirement.
- Stamp-less delivery entries expire; `_recompute_leases` preserves
  `deliveries`; two concurrent sends wait then stay pending; the crash matrix
  and legacy no-phase recovery pass; session replacement releases only the
  obsolete operation; reset and quick compaction exclude each other.

## Probe results

`python -m pytest .artifacts/q346-recheck/probes/test_q346_recheck.py -q`
-> 5 failed, 1 passed. The five failures are the four findings above (two
force probes, the live-session recover probe, the guarded-compact probe, and
the lead self-compact probe). The pass is the TTL guard
`test_delivery_ttl_covers_the_real_send_bound`.

`python -m pytest -q tests/test_key_stream_exclusion.py
tests/test_compaction_admission.py tests/test_context.py tests/test_say.py
tests/test_say_expiry.py tests/test_lead_context.py` -> 175 passed.

`python -m pytest .artifacts/q346-review/probes/test_q346_probes.py -q`
-> 6 passed (Devin's probes).

The lead's full-suite run is 1141 passed, per the brief; the full suite was
not rerun here.

## Verdict

fix
