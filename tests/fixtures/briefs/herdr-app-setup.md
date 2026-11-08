# Brief: Herdr app setup, the Triage layout plus our own usage and context bar (Q-196)

Use poteto-mode, Feature playbook. Open your report with the goal, the playbook and the principles you applied.

## Rules

- Work only inside C:\Users\caspe\Documents\Projects\herdr-orchestration\herdr-app\ (new folder). No git commits, pushes or deletes. Do not write to ~/.config/herdr-app: the lead installs after reviewing.
- Never print or copy tokens or credentials.

## Goal

The creator installed the Herdr desktop app (herdr-app 0.1.13, core CLI hc 0.1.18) and chose, from the research page C:\Users\caspe\Documents\Projects\todo-rpg\.artifacts\q196\herdr-customization.html, "Triage layout + our own usage/context bar". Their original idea: "Include for sure the usage % for all sessions and context %."

## Do

1. **Read the app's mod contract first:** ~/.config/herdr-app/AGENTS.md, herdr.d.ts and gpui-kit.d.ts beside it (read-only). Also run `herdr-app --skill` and `hc --skill`.
2. **Vendor Triage for review:** `git clone --depth 1 https://github.com/sgomez/herdr-triage herdr-app/vendor/herdr-triage`. Read its RECIPE.md and every mod file. List in your report everything it runs, reads or sends over the network.
3. **Build the bar:** a mod `herdr-app/mods/hx-usage/`, written to the contract from step 1. Use https://github.com/mikesemenov/herdr-agent-limits as a reference only.
   - Show the subscription pools from `hx usage --json` (or whatever machine-readable output hx has; check `hx usage --help`): one short entry per pool with its 5h %, and tag estimated or unknown values.
   - Show each live agent's context % from `hx context decide <agent>` (agents from `hc` or `herdr agent list`).
   - Refresh every 60 s at most, never block the UI, and show "?" on errors, never a crash.
   - It must fit in the Triage layout's status bar. If Triage has no slot, say how to add it.
4. Run `herdr-app check herdr-app/mods/hx-usage` and fix every error.
5. Write `herdr-app/INSTALL.md`: the exact PowerShell copy and switch commands for the lead.

## Report

Via `hx say claude`, under 10 lines: the files, the check result, what Triage runs or sends over the network, and the install commands.

## Update from the lead (second run)

A first run found that ~/.config/herdr-app does not exist yet, because the app has never been started. Use the canonical copies in https://github.com/herdrdev/herdr-app under `defaults/` (AGENTS.md, herdr.d.ts, gpui-kit.d.ts). Fetch them with `gh api` or a shallow clone into herdr-app/vendor/herdr-app-defaults, choosing the tag that matches 0.1.13 if one exists. `hx usage` always prints JSON. `hx context decide <agent>` returns `{action, telemetry: {utilisation}, reason}`. If `herdr-app check` cannot run without the config folder, say so and move on: the lead will run it after the creator opens the app.

## Update from the lead (third run)

`gh` and `git clone` are denied for you, and a denied command ends your run, so do not run them. The lead has cloned everything you need:
- herdr-app/vendor/herdr-app/defaults/ (AGENTS.md, herdr.d.ts, layouts/, mods/ including the shipped status bar)
- herdr-app/vendor/herdr-triage/ (RECIPE.md and its mods)
- herdr-app/vendor/herdr-agent-limits/ (reference only)
Skip step 2's clone and go straight to reading these. The previous run already mapped the hx usage fields: opencode.pool.windows["5h"].utilisation, codex.rate_limits.primary.used_percent, claude.rate_limits.five_hour, devin status/slots, agents.agents[].name.
