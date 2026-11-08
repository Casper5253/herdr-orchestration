# OpenCode policy model pin

The goal is to make team OpenCode launch on policy.persistent_opencode_model and make doctor detect stale installed copies and live model mismatches. I used the Bug fix playbook and the correct skill, with all roles performed inline as required for an assigned Herdr brief.

The principles applied were Model the Domain, Fix Root Causes, and Prove It Works. A shared install-file manifest owns the eight source-copy mappings. The launcher passes the policy model explicitly, removing the overlay default as the deciding input. Doctor reads the installed overlay and the live session database, and reports unknown when live model evidence is unavailable.

## Changes

- team/Start-HerdrTeam.ps1 reads policy for OpenCode as well as Devin, validates the model ID before any Herdr action, and passes `-- -m opencode-go/<model>` to OpenCode.
- team/install-files.json is the shared source-copy manifest consumed by install.ps1 and doctor. It covers the five .herdr-team files, the Codex rules file, and both pstack local skill sources.
- hx/doctor.py compares installed copies byte for byte, reports absent installed targets as drift, and includes those checks in exit status. Its OpenCode model comes from the latest user or assistant message of the session identified by the live team OpenCode agent. The database connection uses mode=ro. A missing session, unreadable DB, or malformed latest message is unknown, never a fallback to the overlay model. A different provider also counts as model drift.
- tests/test_doctor.py covers the original stale installed Qwen overlay, matching repo DeepSeek overlay, session binding, latest message ordering, wrong models/providers, unavailable evidence, and drift/missing targets for all eight copied files.
- tests/test_team_launcher.py executes the real launcher in temporary state with a recording Herdr function stub. It verifies policy model arguments despite a stale Qwen overlay, a changed policy model, and rejection of missing or provider-prefixed policy values before any Herdr action.

The overlay is unchanged, including its existing DeepSeek model line and the lead's permissions block. Explicit launch arguments now decide the startup model. No install, live-pane mutation, Git write, network call, or port access was performed.

## Failing-before evidence

Command run before production edits:

`C:/Users/caspe/AppData/Local/Microsoft/WindowsApps/python.exe -m pytest tests/test_doctor.py::test_installed_qwen_repo_deepseek_regression -q -p no:cacheprovider`

The synthetic installed overlay held `opencode-go/qwen3.7-plus`; the repo overlay held `opencode-go/deepseek-v4.1-flash`, reproducing the exact model values from the brief without accessing the live backup.

```text
>       assert report["drift"] is True
E       assert False is True
1 failed in 0.82s
```

## Verification

`C:/Users/caspe/AppData/Local/Microsoft/WindowsApps/python.exe -m pytest tests -q -p no:cacheprovider`

```text
647 passed in 50.95s
```

The final full-suite rerun after passing the loaded policy through to live team selection also passed with `647 passed in 52.87s`. `git diff --check` passed.

Both PowerShell files also passed the PowerShell parser without running the installer.

Read-only `hx doctor` exited 1. It reported the live OpenCode session model as `deepseek-v4.1-flash`, matching policy. Installed checks reported drift in Start-HerdrTeam.ps1, devin-pane.config.json, and both local pstack skill copies. Installed claude-settings.json, opencode-trusted-dev.jsonc, README.md, and herdr-team.rules matched their repo sources. Installed files were left untouched.

The launch check uses a stub to avoid touching live panes. The live acceptance covers doctor reading the real session and installed copies. The installed launcher will acquire the new explicit model argument only after the lead arranges installation.

No commit or PR was made because the brief forbids Git writes. The lead owns integration and independent review.
