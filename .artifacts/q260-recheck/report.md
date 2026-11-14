Goal: decide whether the round-1 fix makes watcher-owned compaction safe enough to merge. The watcher now sends /compact to real workers itself, so a bug can interrupt a busy worker, compact the lead, compact twice, lose a brief, or deliver early. This recheck used the poteto-mode Investigation playbook (code review). Principles applied: Prove It Works (every claim got an executable probe against the real Controller, ledger locks and guard file, not a read-through), Make Operations Idempotent (the two-watcher and restart claims were probed as deterministic interleavings), Model the Domain (each lease state was traced through key_stream_reason, delivery_hold and the recover path).

Verdict: merge.

The review covers the uncommitted Q-260 diff in this worktree after fix round 1. All review writes are inside .artifacts/q260-recheck/. Application files were left unchanged.

## Findings

No findings against the five recheck claims. Two LOW observations that do not violate the claims:

- LOW `hx/context.py:1273-1278`: `_write_watch_checkpoint` uses a fixed `<name>.md.tmp` name and target per worker. Two watchers racing that write can collide on the tmp file (interleaved content or a FileNotFoundError from the loser's os.replace). Consequence is a `keep`/`error` recovery line and possibly a garbled informational file, never keys sent to a wrong or busy pane. A unique tmp name would close it.
- LOW `hx/context.py:1314`: the watcher checkpoint is written before `compact_quick`'s guards run, so a refused compaction still leaves a `watcher-recovery` checkpoint behind. Informational only, inside gitignored `.hx/watch-checkpoints/`.

## Round-1 findings, reverified

F1 (delayed second watcher compacts twice): fixed. `reserve_with_repo` (hx/context.py:1133) now rechecks ownership, the watch loop guard, the interval guard and the post-compaction sample cutoff inside `mutate_leases_with_repo`, and writes the attempt count and last_at durably before keys. Probe `test_delayed_second_watcher_cannot_compact_twice` pauses watcher B before its locked reservation, runs A to completion, and observes one send.

F2 (worker becoming busy before send): fixed. compact_quick rechecks same-session idle immediately after mark_requested and again before _send_slash (hx/context.py:1183). Probe `test_worker_becoming_busy_between_mark_and_send_receives_no_keys` flips the fake to working when the requested phase lands; no keys.

F3 (watcher checkpoint overwrote a task checkpoint): fixed. Watch checkpoints live in `.hx/watch-checkpoints/`, disjoint from `.hx/checkpoints/`. Probe `test_same_named_task_checkpoint_bytes_preserved` uses the sharpest collision, task id `hx-codex` equal to the worker name, and the bytes survive.

F4 (pre-compaction sample drives the next decision): fixed. Both decide paths and the locked reservation reject samples strictly older than the proof timestamp recorded as `last_compacted_sample` (hx/context.py:587, 795, 823, 1149). Probe `test_the_proof_sample_itself_may_drive_a_decision` confirms the cutoff is exactly `sampled < proof`: the proof sample and later are admitted, anything older is deferred.

## The five claims, probed

1. Busy, lead, mid-delivery, guarded path. Busy and lead send nothing; an in-flight delivery hold at decide time and one injected between decide and the locked reservation both send nothing. The reservation reuses the Q-346/Q-422 machinery: mutate_leases_with_repo, a token'd session lease in state compacting, and key_stream_reason admission. Probe `test_compaction_lease_excludes_delivery_key_streams` called the real `admit_delivery` inside the send and got `blocked: compaction requested`.
2. Once-only. Restart with a genuinely fresh post-proof sample still keeps on the interval guard; a second watcher delayed past the first's completion keeps on interval and stale-sample rechecks inside the lock; the attempt count is durable on disk before keys (probe asserts count == 1 at send time).
3. Checkpoint. Same-named task checkpoint bytes preserved; `list_tasks()` is identical before and after; no `.hx/checkpoints/` entry is created; the file itself says `task: none registered`.
4. Same-session proof and unknown handling. Undropped telemetry, a drop observed on a different session id, and a low sample that predates the request all end `compact-unknown` with the lease held and `tasks.watch.failed` durable. After an operator `recover` releases the hold, the failed guard still refuses a retry; exactly one send total.
5. Pending delivery and staleness. Driving the real `watch()` + `Controller`, a verified `compacted` result calls `retry_pending_for` for that worker and emits a delivery line; a `compact-unknown` result calls nothing and emits no delivery key. A sample older than the proof is deferred.

## Verification

Probe command:

```text
python -m pytest .artifacts/q260-recheck/test_recheck.py -q --basetemp=.artifacts/q260-recheck/probe-temp3 -o cache_dir=.artifacts/q260-recheck/probe-cache
```

Result: 20 passed in 3.06s. One earlier probe iteration failed on its own fixture (a future sampled_at cannot be _fresh); corrected to a genuinely fresh post-proof sample.

Requested suite, run inside the write boundary:

```text
python -m pytest tests -q --basetemp=.artifacts/q260-recheck/suite-temp -o cache_dir=.artifacts/q260-recheck/pytest-cache
```

Result: 1229 passed, 4 failed in 281.70s. The four are the same basetemp artifact round 1 documented: two tests expect the repo's `.hx/pytest-temp/run-<pid>` convention and two expect the Git ceiling to contain the temp root, while the boundary forced basetemp under `.artifacts/`. `run_isolation.py` repeats both affected modules with the conftest root pointed inside this artifact directory: 9 passed in 0.20s. This is not a claim of one all-green full-suite run; it is a claim that the only failures are isolation-test configuration effects.

`git diff --check` passes. `git status` shows no change outside `.artifacts/`. No real terminal was driven; no Git writes; ports and token files untouched.
