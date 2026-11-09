fix-then-ship

Goal. Preserve the old Herdr behavior while moving Python host operations behind a contract that U2 can implement for the Herdr app. Playbook. Poteto-mode Investigation, performed inline as the assigned cross-family reviewer of DeepSeek's implementation. Boundary Discipline guided the checks on external outcome normalization and unknown roster handling. Test Behavior, Not Implementation guided the checks on migrated assertions and the in-memory mutation probe.

throughput checkpoint: n/a, read-only investigation

## Findings, ranked by severity

1. **P1. `hx/devin.py:411`: an unresolved provider kind is discarded as if it consumes no Devin capacity -> return unknown live-session data when any roster entry could be an unresolved Devin, and refuse admission until resolved.**

   The accepted finding 7 explicitly requires unresolved possible Devin terminals to fail closed. `host.member` maps an absent or unrecognized program to `kind="unknown"`. `live_sessions` then filters it out, and `_read_live_sessions` returns a known empty list. `acquire_slot` only refuses `None`, so an unknown terminal can permit an extra account-wide run. This is a missing accepted safety requirement, rather than a newly introduced old-host regression.

   Read-only reproducer. Normalize `{"name":"dev-devin","pane_id":"p","agent_status":"idle"}`, patch `usage.live_members` to return that one Member, then call `devin._read_live_sessions()`. Observed `[]`. Patch `devin.slots_dir` to raise `RuntimeError("passed admission guard")` and call `acquire_slot(1, 60, {"id":"probe"})`. Observed that exception, proving admission advanced past its unknown-roster guard without any filesystem write. Unknown names and other teams need the same conservative treatment; a name must not be used to fabricate a known provider kind.

2. **P2. `hx/host.py:160`: checked sends discard the response and manufacture completion from the requested wait flag -> normalize the actual documented Herdr outcome into stable SendResult fields, and preserve uncertainty where the host supplies no evidence.**

   A zero-exit response `{"result":{}}` to `send_checked(..., wait=True)` produces `started=True, ended=True, reason=None`. The requested flag is not evidence that a turn ended. Conversely, a JSON timeout result is converted by `_command` into a RuntimeError, then becomes `started=False` with the entire diagnostic JSON as `reason`. The subprocess-timeout path instead returns `started=True, reason="timeout"` without evidence that the agent started. These cases do keep `ended=False` for recognized timeouts, but lose the promised distinction between observed start, completion, timeout, and other failures. `status` and `turn` are always fabricated defaults.

   Read-only subprocess stubs produced these exact results:

   ```text
   input {"result":{}}
   output {'started': True, 'status': 'unknown', 'turn': None, 'ended': True, 'reason': None}

   input {"result":{"type":"timeout","started":true,"ended":false,"reason":"timeout"}}
   output {'started': False, 'status': 'unknown', 'turn': None, 'ended': False,
           'reason': 'Herdr did not complete the request: <complete input JSON>'}
   ```

   These are synthetic boundary probes, not claims that the installed old CLI emits those additional fields. Preserve its documented successful waited-result behavior, reject malformed success envelopes, and translate its known timeout/error taxonomy structurally. If the old CLI cannot prove start, give the contract an explicit unknown start outcome rather than asserting one. Add cases for successful waited sends, JSON timeout, process timeout, blocked/stalled sends, malformed responses, and subprocess failure. The current contract test only covers process timeout.

3. **P2. `tests/test_devin_panes.py:292`: the migrated malformed-roster regression now validates its own fake instead of the production parser -> feed malformed JSON through a stubbed Herdr subprocess and exercise real HerdrHost normalization plus admission.**

   Previously the test stubbed the raw agent-list envelope and exercised production validation. Now its local `fake_members` checks the envelope itself and raises the expected error before the adapter runs. The test remains green if production roster validation disappears. The new host tests also never drive `HerdrHost.all_members` through a subprocess fixture.

   In-memory mutation proof. With `patch.object(host.HerdrHost, "all_members", return_value=[])`, run `pytest.main(["-q", "-p", "no:cacheprovider", "tests/test_host_contract.py", "tests/test_host_boundary.py", "tests/test_devin_panes.py::test_admission_refuses_malformed_agent_list"])`. Observed **20 passed in 1.23s**. This replacement deletes all real roster parsing and ignores every roster response, yet all of these purported boundary/contract checks pass. No source file was modified.

   A second coverage loss is at `tests/test_context.py:463`. The old `_herdr_read` stub ran through the same lock assertions as sends. The replacement read lambda no longer checks either lease or repo lock. Restore both assertions around the read seam as well as the send seam. Existing success, acknowledgement, telemetry, timeout lease retention, and session identity assertions otherwise remain present in the moved tests inspected.

## Answers to the brief

- The ordinary old-host commands retain their argument arrays and deadlines. Listing uses `herdr agent list`, suppressed stderr, and 30 seconds. Info uses checked `agent get` with UTF-8 replacement decoding and 30 seconds. Ordinary sends use un-captured `agent prompt` with 60 seconds. Reset uses the checked prompt with 60 seconds. Both compaction sends retain `--wait --until idle --until done --timeout 300000` and a 310-second process deadline. Reads retain `--source recent-unwrapped --lines N`, checked execution, replacement decoding, and 30 seconds. The `_command` error-type set matches the removed `_herdr` helper. The output-error text changed from Herdr to Host in usage diagnostics. Known-provider/session telemetry and doctor field reads were migrated consistently.
- `detect()` returns `HerdrHost` outside an hc terminal, even without `HERDR_ENV` or a pane marker. An hc terminal deliberately returns no host until U2. The three detection tests cover those choices.
- The Protocol declares own, all_members, team, info, send, send_checked, and read. Migrated Python callers use those declared operations. No caller reaches into `.command`, `._command`, or a host-specific getattr. The remaining unrelated getattr calls concern exception dispositions and ledger locks. The all-members path includes unnamed and other-team Members.
- Findings 5 and 8 are substantially addressed. Full-roster access and checked-send methods exist; `focused` survives as nullable metadata and stays in the usage projection. Finding 7's usage-availability half is addressed because a team member with `status="unknown"` makes `available=False`. Its unresolved-provider admission half remains open as finding 1.
- Both recognized timeout paths currently have `ended=False`, and context retains the unknown compaction reservation on that result. The general checked-result contract still needs finding 2. A completely missing roster produces `None`/an exception and refuses Devin admission; a partially unresolved roster does not.
- `tests/test_host_boundary.py` is a useful architectural string guard for the current flat `hx/*.py` package. It does not prove command flags, error mapping, real roster parsing, or behavior through the Host contract. `test_context_uses_only_declared_methods` exercises the helper rather than an entire Controller compaction. The existing Controller tests preserve most behavioral assertions but mock the helper. Add an end-to-end Controller test with the minimal Host and timeout outcomes before treating the contract suite as complete.
- Inventory section 9's launcher/workspace/layout operations remain in PowerShell and are unchanged. Config permission surfaces, hooks, and shared-state joins are unchanged. This review covers U1's Python extraction; it does not claim that the deferred U2/U4/U7 surfaces have already migrated.

## Verification and boundaries

`python -m pytest -q tests` completed with **670 passed in 58.76s**. Read-only Python probes used synthetic Members and patched subprocesses. The separate in-memory parser mutation run passed all 20 selected tests, as recorded above. Source comparison used the uncommitted diff against HEAD, plus the new host module and both new test files. Reviewed the plan Revision section/U1 row and prior findings 5, 7, and 8.

Only this report was intentionally written. No application changes, Git writes, live Herdr commands, hc mutations, credential reads, network requests, or delegation were performed. The assigned inline review and read-only fence supersede the optional comment-editing and separate-agent workflows in the general skills. The final authorized hc report delivery is the sole exception to the hc mutation restriction.
