# Adversarial review of the herdr-core adapter

Verdict: revise the contract and lifecycle design before U2; U7 cannot meet its stated acceptance through the documented hc API.

Goal: make hx work natively in the Herdr app while preserving truthful telemetry, team isolation, and guarded context recovery. Playbook: poteto-mode Investigation, with interrogate's adversarial stance, performed inline as the assigned reviewer. Principles applied: Boundary Discipline puts transport outcomes and identity validation in Host; Separate Before Serializing Shared State requires lifecycle ownership around sends and restarts; Prove It Works limits claims to source inspection, read-only hc probes, and synthetic Python probes.

throughput checkpoint: n/a, read-only investigation

Reviewed `spec.md`, `plan.md`, `inventory.md`, `research-hc.md`, the six named Python callers, the U1 working diff, `hx/host.py`, the statusline hook, and relevant repository permission files. U1 is unfinished; findings below concern the design contract, not incidental unfinished edits. No teammate was messaged or interrupted. No host mutations, tests that write state, Git writes, credential reads, or provider-history reads were performed.

## Findings, ranked by severity

### 1. P1: U7 promises an in-place restart that hc does not expose

`spec.md`, Behaviour on hc / Reset and compaction; `plan.md`, U7: restarting a terminal with the same pane is not an available hc operation -> define replacement-terminal semantics and app placement explicitly, or defer U7 until an in-place mechanism has a disposable-terminal proof.

Evidence: `hc schema` lists `terminal.run`, `terminal.kill`, `terminal.keep`, `terminal.keys`, and `terminal.interrupt`, but no restart or replace-program method. `terminal.run` starts a **new terminal** and has no existing-terminal parameter. `terminal.kill` ends the program. The Terminal `keep` description explicitly says that exit with a code and kill remain final. The run `keep` description says recovery uses a shell and never reruns the original command. `hc --help` agrees that server restart gives kept terminals shells. A server restart is also core-wide, not a worker reset.

A new terminal can reuse a released name and labels, but that does not establish the same address or split-tree leaf. Specify old/new address mapping, app leaf replacement, parent, notify, outlive-parent policy, executable/model/config restoration, and partial-failure recovery. Workers currently default to ending with their parent; replacing the lead must not silently terminate its workers. Do not treat keep recovery as an agent relaunch.

The premise that slash commands always arrive as tagged chat also needs qualification. `hc --help` documents `notes = false` as disabling both parent notes and `<hc>` tags. This does not prove native slash commands work, and it is not a recommendation to change shared config. Probe a supported per-operation solution on disposable state before choosing restart as the only recovery mechanism.

### 2. P1: U3 has no provider-session binding and therefore cannot provide context telemetry or verify OpenCode's model

`spec.md`, Member.session_id and HcHost; `plan.md`, U3 and U6; `hooks/claude_statusline.py:88`; `hx/context.py:122-146,237-239,295-301`; `hx/doctor.py:106-110`: hc Members deliberately have no session ID, while all three provider readers need one -> add a terminal-generation-to-provider-session binding contract before claiming hc context or doctor acceptance.

Evidence: `hc schema` Terminal fields contain no provider session ID, tokens, or context counters. The existing Claude hook writes only `HERDR_PANE_ID`, while its lookup compares `herdr_pane_id` with the Member address. Merely changing the address to `t1` leaves no matching hook record on hc. Codex and OpenCode have no fallback binding in `_session`. Synthetic `context.session_telemetry` calls with `session_id=None` returned the following without reading any real provider history:

```text
codex ('', {'session_id': '', 'utilisation': None, 'sampled_at': None})
opencode ('', {'session_id': '', 'utilisation': None, 'sampled_at': None})
claude-unbound ('', {'session_id': '', 'utilisation': None, 'sampled_at': None})
```

The Claude case mocked only `_claude_session_for_pane` to return no match. U6's proposed status-only OSC plugin does not bind an OpenCode session. Doctor directly requires the OpenCode Member's `session_id`, so adding a fallback only in `context._session` would still leave its live-model check unknown. Doctor currently distinguishes unknown from drift and can exit zero with an unknown model; U3 must assert a known correct live model, not just exit zero.

Define a host/core identity plus terminal address plus launch generation mapping. Have Claude's hook publish the hc identity with its session ID, bind Codex through a supported session-start mechanism, and have OpenCode's plugin publish its provider session ID alongside status. Reject stale generations, ambiguous matches, and mismatched cwd. Never select the newest session by cwd alone because multiple terminals share this repo. Keep counters unknown until a binding is verified.

### 3. P1: restart changes the identity on which every compaction guarantee depends

`spec.md`, Reset and compaction / Risks 2; `plan.md`, U7; `hx/context.py:693-705,721-764,774-783`: checkpoint/restart/resend omits lease transfer, completion acknowledgement, cooldown continuity, and unknown-outcome recovery -> specify a replacement transaction distinct from same-session compaction and test each failure boundary.

Today's compaction reserves a token under the session ID, increments the per-task count and cooldown before sending, checks the same session immediately before each operation, requires fresh lower telemetry in that same session, and verifies an exact token plus Next action acknowledgement before releasing the reservation. After a submitted failure it retains `compact-unknown`; recovery also requires the same session.

A genuine new session fails `_session(current) == sid` and cannot satisfy this verifier. Simply switching to the new ID instead bypasses session-keyed guards. A synthetic lookup with an exhausted and failed old-session guard returned `guards.get(new_sid, {}) == {}`. Devin uses `devin-pane:<address>` as its fallback session, so an address replacement changes its guard key too; an in-place restart would instead incorrectly retain that identity unless a generation is added.

Keep a stable logical member/task recovery record with old and new provider IDs, a nonce, and explicit phases: reserved, old program stopped, replacement launched, session bound, checkpoint acknowledged, committed. Preserve counts, cooldowns, and failure guards across that transition. Fence ordinary dispatch throughout. Failures after stopping or starting must retain a recoverable reservation rather than permit another reset. The old guard must not disappear merely because telemetry reports a fresh session.

The spec also inaccurately calls a valid checkpoint the guard that `hx reset` has today. `hx/context.py:378-392` checks worker identity and readiness, sends the reset, and records it; it does not validate a checkpoint. Adding that requirement is a behavior change that needs explicit tests.

### 4. P1: broad raw hc permission bypasses the same-team boundary

`plan.md`, P1 and U2; `team/opencode-trusted-dev.jsonc:20-27`; `team/devin-pane.config.json:61`; `policy.json:230,236,242`; `hx/say.py:17-26,69-97`: allowing arbitrary `hc send` bypasses hx's sender validation, target-team restriction, pressure hold, and Devin deny-line injection -> retain hx say as the worker send permission and gate raw hc mutations to the lead.

Evidence: the repository OpenCode overlay already allows `hc send *`. The hc schema takes any terminal address or name and documents no team authorization parameter. Thus an allowed raw send need not pass `say.resolve`, `with_header`, or `_denied_line_for`. Existing Devin profiles deny `Exec(herdr)` but lack its hc equivalent; the inventory correctly calls out the missing twin, but the plan parks broad permissions separately without making host safety a prerequisite.

Keep read-only hc access separate from mutations. Define hc deny rules for worker mutation commands before enabling the adapter, and verify the actual permission engine's treatment of allowed `hx say` invoking hc internally. Any exception needs the creator approval already required by P1. The deny line remains advisory text, not authorization. Ensure launcher-created per-member configs and doctor compare the hc-aware deny profile, and exercise the real `kind=devin` lookup and deny-line injection on the hc path.

### 5. P1: the proposed Host interface cannot express existing roster and checked-wait operations

`spec.md`, Host protocol; `hx/devin.py:399-411,431-435,504`; `hx/usage.py:542-547,654`; `hx/context.py:110-111,744,758`; inventory sections 8 and 9: `team(prefix)` omits account-wide panes and `send -> int` omits turn completion and failure semantics -> add an explicit full live roster and checked send/wait outcomes to the contract.

Devin counts live panes of **any name and team** against a shared cap. A team-only list misses other teams and unnamed Devin terminals. A synthetic roster gave `team-only-live-count 0` and `all-live-count 1` with one other-team Devin. Counting only successfully named Members is unsafe; an unknown full-roster result must refuse admission.

U1 has already needed `HerdrHost.agents()` outside the stated Protocol and a `getattr` escape hatch in `all_members()`. A fake implementing exactly the five spec methods produced:

```text
full-roster None
wait-path AttributeError 'MinimalHost' object has no attribute 'command'
```

This is a contract probe, not a criticism of unfinished U1. The existing context controller still requires `.command(...)`, including wait flags. Make those semantics host-neutral instead of moving the old JSON command escape hatch into HcHost. `hc schema` reports `SendResult.started/status/turn/wait`; WaitResult has `ended`, `reason`, `turn`, and `status`. A timeout has `ended=false`, and `reason=not_started` or `message` is not verified completion. Preserve those distinctions and never equate a zero process exit with task completion.

### 6. P2: explicit reporting has no delivery guarantee when the lead is blocked

`spec.md`, Reporting and Host.send; `plan.md`, U2 and U5: explicit hx say does not guarantee every report reaches the lead -> make failed or unconfirmed delivery a durable pending report and expose retry/reconciliation to the lead.

Evidence: `hc schema` says terminal.send refuses blocked programs unless force is used, and lists `blocked`, `finished`, `unavailable`, and `status_mismatch` errors. Current `say` returns the send exit code and does not retain the report. A generated Report section cannot resolve a permission dialog. Do not forward hx say's pressure override as hc's `force`, which could answer that dialog.

Separate report creation from delivery with a per-sender outbox and message ID. A blocked lead leaves the message pending; receipt or a later acknowledgement marks delivery. Test blocked, exited, unknown-status, and timeout cases. This is especially necessary while OpenCode has no OSC state.

I did not reproduce loss of concurrent sends or busy-lead messages. The live schema says hc waits for a typing gap and holds human keys, so claims about character interleaving would be unsupported. For lifecycle/control sends, however, a prior readiness read is not an atomic admission guard. Use the documented `if` status condition and a per-member lifecycle reservation, and distinguish ordinary messages to a working teammate from control operations that require idle. A lost response after a successful send requires reconciliation, not blind resend.

### 7. P2: unknown hc status becomes measured availability

`spec.md`, status normalization; `hx/usage.py:622-639`; `plan.md`, U3 before U6: preserving unknown status while reusing today's availability calculation reports the undetected OpenCode worker as available -> define unknown availability explicitly and verify the OSC plugin before claiming usable team readiness.

Evidence: live `hc get t15 --json`, projected to identity/status only, showed name `dev-opencode`, labels `team=dev,role=opencode`, `state=live`, `reported=false`, `status=null`, and `turn=0`. The same record normalized to `kind=unknown,status=unknown` gave `usage.agents_usage(...)["available"] == True`. That follows the current calculation, which excludes only blocked and error. It must not be used as readiness evidence. A role label establishes logical identity, not observed agent kind or readiness.

Map unavailable, unknown, working, and ready deliberately for each consumer. For Devin capacity, unresolved possible Devin terminals require fail-closed handling rather than assuming they consume no slot. For normal communication, permit an explicit policy for unknown targets while preserving hc's blocked guard; do not fabricate idle to make sends or waits succeed.

### 8. P2: Member drops an existing output field without an explicit compatibility decision

`spec.md`, Member; inventory section 8 item 5; `hx/usage.py:630-637` compared with the U1 diff: `focused` disappears from the hx usage agent projection -> retain it as nullable host-neutral metadata or explicitly revise the usage output contract and its consumers.

Evidence: `host.member` applied to a synthetic old-host record containing `focused=True` yielded `"focused" in member == False`; `git diff -- hx/usage.py` shows the output key removed. The live hc Terminal schema has no focus field. Unknown/null on hc is honest; deletion on Herdr is a behavior change despite U1's no-behavior-change promise. No specific downstream failure was demonstrated, so severity is limited to compatibility.

## Identity and interface coverage

The remaining proposed Member fields cover current Python caller projections: logical name/team/role, observed kind/status, cwd, address, and provider session ID. The missing pieces are operational semantics and identity lifecycle, not more aliases for the old JSON envelope.

Read-only checks established that the current lead t1 has `name=null` and labels `team=dev,role=claude`; synthesize `dev-claude` from those labels and route to address t1. The default `hc ls --json` output contains no labels, so implement the label query plus terminal detail reads explicitly, or choose a richer list API. Do not infer the unnamed lead's role from list.agent, cwd, or screen text. Reject duplicate team/role matches instead of silently overwriting a dictionary entry. Normalize `claude-code` to kind `claude`; do not use the role label as proof of detected kind. Include core identity in persisted addresses because terminal IDs are local to a core.

For the inventory's 11 operations: own/list/info/read are covered after those corrections; ordinary send needs failure outcomes; checked send/wait needs a new contract; start/split/layout/name must be replaced by an hc plus app lifecycle contract; reset requires the revised U7 design. Shell emitters such as `host.prompt_command` also need that contract, otherwise the printed recovery commands can keep invoking the old host even after callers stop parsing its JSON.

## Recommended unit order and acceptance changes

1. Finish U1 around a complete Host contract: full roster, identity resolution, checked send/wait, explicit unknown outcomes, and the focused-field decision. Keep old-host behavior tests. Add contract tests that use only declared Host methods.
2. Build U2 together with the minimum safety changes from P1. Prove label identity for the unnamed lead, duplicate rejection, cross-team refusal, blocked-send refusal, and Devin deny-line injection. Do not broadly grant worker hc send as the workaround.
3. Move U6 and session-binding work ahead of U3's success claim. Status reporting and provider-session binding can share a plugin where appropriate, but require separate acceptance assertions. Then complete U3 with same-cwd distinct-session and stale-generation cases, account-wide Devin counting, and known live-model evidence.
4. U4 follows the corrected lifecycle contract. Verify idempotence against launching, live, blocked, unknown, finished, and wrong-cwd roles; name uniqueness alone does not handle the unnamed lead or duplicate labels. Assert program/model/config and app placement as well as role count. Use run's initial task delivery outcome, not an immediate send to a booting worker.
5. U5 can run independently once reporting semantics are settled. A Report section test verifies text generation only; include target resolution and actual delivery outcome in integration acceptance. The quoted-pipe rule needs a separately proven parser requirement; it is not an adapter prerequisite.
6. Keep U7 last, and defer it if a replacement transaction cannot preserve the existing invariants. Its check must cover checkpoint acknowledgement, new session binding, old/new leases, cooldowns, counts, and crashes after each phase, not just a fresh pane with copied labels.
7. Finish U8 after commands stabilize. Keep permission widening separately approved as planned. Delete the legacy path only after observed old-host usage is absent, not because a timer elapsed.

Cut the assumption of same-address restart and the blanket raw hc send grant. Merge shared contract acceptance across U1/U2/U3, rather than duplicating transport-shaped fixtures that can all pass while a protocol-only Host fails. The app grid and brief generator do not need to block each other.

## Reproducing the synthetic contract probes

Run from the repo with `python -B -` and the following stdin. It mocks Host access, invokes no host mutations, and reads no provider history:

```python
from unittest.mock import patch
from hx import context, host, devin, usage

base = dict(name='dev-codex', team='dev', role='codex', kind='codex',
            status='idle', cwd='repo', address='t11', session_id=None)
for kind in ('codex', 'opencode'):
    print(kind, context.session_telemetry(dict(base, kind=kind), {}))
with patch.object(context, '_claude_session_for_pane', return_value=''):
    print('claude-unbound', context.session_telemetry(dict(base, kind='claude'), {}))

class MinimalHost:
    def own(self): return base
    def team(self, prefix): return {'dev-codex': base}
    def info(self, name): return base
    def send(self, name, text): return 0
    def read(self, name, lines): return ''

with patch.object(host, 'detect', return_value=MinimalHost()):
    print('full-roster', host.all_members())
    try:
        context._herdr('prompt', 'dev-codex', '/compact')
    except Exception as e:
        print('wait-path', type(e).__name__, str(e))

raw = dict(name='dev-codex', agent='codex', agent_status='idle',
           pane_id='p1', cwd='repo', focused=True)
print('focused-projection', 'focused' in host.member(raw))
guards = {'old': {'last_at': 'now', 'tasks': {'task': {'count': 2, 'failed': True}}}}
print('guard-on-new-session', guards.get('new', {}))
for address in ('t11', 't19'):
    print('devin-sid', address, context._session(dict(base, kind='devin', address=address)))
m = dict(base, name='dev-opencode', role='opencode', kind='unknown', status='unknown')
print('unknown-team-available', usage.agents_usage({}, prefix='dev-', agents=[m])['available'])
outsider = dict(m, name='other-devin', team='other', role='devin', kind='devin')
print('team-only-live-count', len(devin.live_sessions([m])))
print('all-live-count', len(devin.live_sessions([m, outsider])))
```

Additional observed outputs were `focused-projection False`, `guard-on-new-session {}`, `devin-sid t11 devin-pane:t11`, `devin-sid t19 devin-pane:t19`, `unknown-team-available True`, `team-only-live-count 0`, and `all-live-count 1`. Read-only hc evidence came from `hc ls --json`, projected `hc get t1 --json` and `hc get t15 --json`, `hc --help`, and filtered `hc schema`. Dynamic statuses may change; the schema and absent bindings are the basis for the design findings. Runtime claims about actual restart recovery, concurrent send loss, and permission-engine enforcement remain untested by this read-only assignment.
