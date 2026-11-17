# Q-572

Goal. Make hx say receipts trustworthy for scrolled Codex input and OpenCode sidebars.

Playbook. Poteto-mode Bug fix. Run all roles inline under the assigned-worker rule.

1. Reproduce it yourself on the matching surface via the control skill (Non-negotiables), even when a debug or instrumentation protocol says to ask the user to reproduce. Ask the user only with a stated, specific reason the control surface cannot reach the target, and only after driving it as far as it goes. If it won't reproduce directly, synthesize the trigger, tighten conditions, or instrument until it fires.
2. Binary-search the cause.
3. Plan the fix.
4. Verify on the same surface.
5. Stage the commits so the failing repro lands before the fix in git history.
6. Run Opening a PR. Skip. Local branch handoff requested, no network authorized.

Throughput checkpoint. One worker owns screen capture, fixture regressions, parser changes and suite verification in this worktree. No delegation. Capture availability is the current bottleneck.

Grounding. say._deliver checks the composer before sending. say._submission_evidence checks again after sending and after one provider-specific submit key. Both use host._composer_text. A false empty result permanently delivers an outbox record. A false nonempty result prevents sending. The parser returns str for identified input, including empty input, and None for unknown input.

Design candidates. Keep the current string-or-None boundary and correct provider-specific screen geometry, or introduce a structured region type and migrate callers. Prefer the first if captured evidence supports a local correction. A new abstraction would add caller churn without changing the receipt contract.

Principles. Fix Root Causes keeps the correction at screen interpretation. Test Behavior, Not Implementation checks receipt and typing behavior through say. Model the Domain keeps the existing explicit empty, nonempty and unknown input states.

Evidence. The captured Codex scrolled input parses correctly before the fix. A synthetic delayed-frame sequence proves an empty frame can earn queued before the input arrives. No live send establishes the original incident timing. The t47 capture proves its home placeholder is incorrectly treated as a draft. A synthetic right sidebar appended to that real capture reproduces row contamination. The actual incident todo panel is unavailable.

Correction. The initial live typing probe was a mistake. The lead prohibited further composer typing, including into this worker. No further typing probes were performed. Subsequent runtime checks were read-only or fixture based.

Verification. Initial focused regressions failed twice. Receipt and host checks passed 84 tests. First full suite had 1318 passes and one date-sensitive failure in tests/test_say.py. The same test fails on unchanged main because its 16 November record has expired. Its fixture now uses a fresh timestamp, preserving the expiry policy. Final full suite passed 1320 tests in 140.45 seconds.

Review. No new production comments or suppression directives were added. All design and review roles ran inline under the assigned-worker restriction. No independent cross-family review was performed. Lead review remains available at handoff.
