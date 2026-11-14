Goal: make watcher-owned compaction safe for workers and preserve their work. This review used the Investigation playbook. Prove It Works required executable probes against the actual controller. Make Operations Idempotent required a deterministic two-watcher interleaving and a retry after an unknown result.

Verdict: fix.

The review covers the uncommitted Q-260 diff in this worktree. Every role ran inline. Application files were left unchanged. All review writes are inside this directory.

## Findings

### HIGH F1. A delayed second watcher can compact the same session twice

`hx/context.py:1126`: `reserve_with_repo` checks task ownership and key-stream holds but does not recheck compaction guards or telemetry after acquiring the reservation lock. The interval and loop guards at lines 1091-1104 are checked earlier.

Two controllers can both pass those checks. Watcher A then reserves, sends `/compact`, verifies lower telemetry, and releases the lease. Watcher B resumes its reservation after A finishes. There is no active key stream left, so B reserves and sends another `/compact` using its captured high sample. The 20-minute interval guard does not stop it because B passed that check before A started.

Probe `test_two_watchers_cannot_compact_twice_after_the_first_finishes` uses two Controller objects sharing the real ledger and guard files. It pauses B immediately before its locked reservation and runs A to completion. The final assertion fails with two observed slash commands. This is a deterministic scheduling interleaving, not a timing-dependent stress test.

Recheck the interval, loop guard, and sample cutoff inside the locked reservation, as the registered-task reservation already does for its guards. Store the attempt guard durably before sending keys so a crash cannot skip its count.

### HIGH F2. A worker that becomes busy before sending still receives keys

`hx/context.py:1156`: watcher compaction writes the requested phase and then sends the earlier `current` member without refreshing its status or session. Checkpoint validation and lease I/O occur after the last idle check. The existing registered-task path refreshes the member immediately after `mark_requested` at line 947.

Probe `test_worker_that_becomes_busy_during_reservation_is_not_sent_keys` changes the fake worker to working when the real requested-phase write finishes. The actual watcher path calls `_send_slash` while the fake worker is working. Its no-keys assertion fails. `HcHost.send_slash` types command keys before its later composer checks, so those checks cannot prevent the initial interruption.

Use the existing guarded send sequence, including a final live idle and same-session check after checkpoint and lease work and immediately before sending.

### HIGH F3. The watcher overwrites a real task checkpoint

`hx/context.py:1213`: the watcher writes `.hx/checkpoints/watch-<agent>.md` using replacement. That is the same namespace as ordinary task checkpoints, whose task IDs are unrestricted here.

Probe `test_watcher_checkpoint_does_not_overwrite_a_real_task_checkpoint` registers the legitimate task `watch-hx-codex` owned by `hx-opencode`, writes its checkpoint through the real `checkpoint.write_checkpoint` API, and compacts the unregistered `hx-codex` worker. The task checkpoint is replaced with watcher text. The byte-preservation assertion fails. The affected task belongs to another worker, so the no-tasks-for-target check does not protect it.

Put watcher checkpoints in a separate namespace or directory that task checkpoints cannot occupy. Do not replace existing task-authored checkpoint data.

### MEDIUM F4. Pre-compaction telemetry can still drive the next decision

`hx/context.py:1060`, also `hx/context.py:855`: success stores the initial sample timestamp as `last_compacted_sample`. `_stale_after_compaction` at line 587 only rejects samples no newer than that initial timestamp.

A sample collected after the initial baseline but before the command is therefore accepted after successful compaction. Probe `test_a_sample_from_before_the_command_cannot_drive_the_next_decision` starts with a baseline ten seconds old, completes compaction, and then supplies a high sample collected one second before the command. `decide` returns `compact-needed` instead of holding for post-compaction telemetry. The expected held-decision assertion fails.

This also affects the shared registered-task decision path. Use the successful post-compaction proof timestamp as the cutoff, and allow only that proof or later samples. A cutoff at the pre-compaction baseline does not satisfy Q-418.

## How the change works

The watcher observes a worker transition, calls `Controller.decide`, and invokes either the registered-task `compact` path or the new `compact_watch` path. The latter writes a watcher checkpoint and enters `compact_quick` with a session lease. That lease excludes normal delivery key streams. Completion requires an idle worker, the same provider session, a sample newer than the request, and lower utilisation. Only a `compacted` result calls target-filtered pending delivery.

The lease exclusion and unknown-outcome handling work in the review controls. A command with no completion proof leaves `compact-unknown`, and a subsequent watcher attempt sends nothing. The lead and an initially busy worker send nothing. An in-flight delivery blocks compaction. These three control probes pass. The production watcher tests separately cover pending delivery after success and no delivery after an unknown result.

The remaining gaps concern state changes between preflight checks and reservation or send, a shared checkpoint filename, and the successful compaction timestamp cutoff.

## Verification

Review probe command:

```text
python -m pytest .artifacts/q260-review/test_review.py -q --basetemp=.artifacts/q260-review/probe-temp-final -o cache_dir=.artifacts/q260-review/probe-cache
```

Result: 4 failed, 3 passed in 0.77 seconds. Each failure is one finding above. See `probe-results.txt`. Expected nonzero exit for the reproduced defects.

The requested tests directory suite completed:

```text
python -m pytest tests -q --basetemp=.artifacts/q260-review/suite-temp -o cache_dir=.artifacts/q260-review/pytest-cache
```

Result: 1225 passed, 4 failed in 249.71 seconds. See `suite-results.txt`. All four failures are isolation-test configuration effects from moving pytest temporary output into the only permitted write directory. Two tests expect the repository's normal temporary-root or process-name convention. Two expect the Git ceiling to match that root, while the repository conftest sets it to `.hx/pytest-temp`.

To verify those failures without writing outside the boundary, `run_isolation.py` sets the conftest temporary-root constant in memory before collection, uses a process-specific `run-<pid>` directory under these artifacts, and aligns the Git ceiling. It changes no application or test source and runs both affected test modules unchanged. Result: 9 passed in 0.34 seconds, including all four previously failing tests. See `isolation-results.txt`. This is not a claim of a single all-green full-suite run.

`git diff --check` passes. No real terminal was driven. No Git writes were performed by the reviewer. Test fixtures created their own disposable repositories under this artifact directory as required by the suite.
