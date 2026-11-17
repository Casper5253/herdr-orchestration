# Q-572 composer receipts

Goal. The lead must be able to trust hx say receipts. Followed the poteto-mode Bug fix playbook. Fix Root Causes placed the change at composer interpretation and receipt evidence. Test Behavior, Not Implementation checks delivered receipts, held drafts and emitted submit keys through say. Model the Domain preserved the existing empty, nonempty and unidentified composer states.

Changes are on branch `q572-composer` in `C:/Users/caspe/Documents/Projects/herdr-orchestration-q572`. Main was not edited or committed. No pushes were run.

The OpenCode parser included right-hand screen content in the input region. It now clips at the input box bottom border before reading its rows. The captured home screen also exposed an unrecognized placeholder, now treated as empty.

The captured scrolled Codex input is already parsed as nonempty on main. A synthetic sequence reproduces the false queued receipt when the first post-send frame is still empty. Submission verification now polls for input or a turn advance. An empty frame without progress ends uncertain, never queued or submitted. Once input appears, the existing provider submit key is used and the input is rechecked. An automatically queued send with no observable turn advance can therefore remain uncertain. It is not blindly retried.

Initial failing output from the two new regressions was:

```text
2 failed, 35 deselected in 0.43s
```

The captured OpenCode home screen printed `pending (composer not empty)`. The delayed Codex sequence printed queued with `fake.keys == []` instead of the required Tab. The additional synthetic sidebar regression also failed before the clipping change.

After the fixes, focused verification printed:

```text
84 passed in 6.62s
```

The first complete run printed:

```text
1 failed, 1318 passed in 146.99s (0:02:26)
```

That failure also occurs on unchanged main. The retry fixture uses 16 November as the creation date, so its record is now expired. The test now creates a fresh record. Production expiry behavior is unchanged.

Final complete run printed:

```text
1320 passed in 140.45s (0:02:20)
```

`git diff --check` passed.

Evidence limits. t47 is currently on its home screen, without a todo panel. Its screen was captured read-only. The todo sidebar test appends synthetic content to this real capture. The Codex delayed-render sequence is synthetic, using captured wrapped input and the observed busy footer. No live send proves that timing caused the original incident. No end-to-end live send is claimed.

The initial live typing probe was a mistake. The lead prohibited further live composer typing, including into this worker. Subsequent checks used fixtures and read-only calls. No keys were sent to t2x. No further live composer typing was performed after that correction.

Commits. `2035e77` and `033619c` contain regressions before the fix. `2662c95` contains the production correction, the draft-preservation check and the fresh timestamp fixture. Design and review roles ran inline under the assigned-worker rule. Cross-family review remains for the lead.
