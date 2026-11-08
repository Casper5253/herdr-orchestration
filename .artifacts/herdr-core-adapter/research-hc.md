# hca-r1: what herdr-core (`hc`) and the Herdr app offer a multi-agent team

## Goal

The creator said on 8 Nov, "I foresee that the herdr app will from this point on be the primary place where we work, no longer in the terminal/powershell as before." Our tooling speaks only to the old `herdr` CLI. This document maps the new host so the lead can write the adapter spec. It is read-only research.

Playbook: poteto-mode, Investigation.
Principles applied: **Prove It Works** (every claim checked by running `hc` or by a file:line, not inferred from a self-report), **Guard the Context Window** (bulk `hc.exe` string scans kept to targeted reads), **Laziness Protocol** (one table per question, no duplicated prose), **Attack the Premise** (Q2 tests the assumption that an "agent" needs a manifest or hook, and finds its status is only an OSC report).

Throughput checkpoint: n/a, read-only investigation.

Evidence collected live on 8 Nov 2026 with `hc 0.1.18`, `herdr-app ls` and `herdr-app status`. Terminals t1 (lead), t2/t9 (codex), t4 (devin) and t8 (opencode) were live.

## Overview

Two programs make up the host.

- `herdr-app.exe` is the window. It holds spaces, tabs, panes, layouts and mods. It is a client.
- `herdr-core.exe` runs every terminal and agent. Its CLI is `hc`. Terminals survive the window closing.

Every team operation is a core operation. The app is one client of the core, and the core is addressable from a script (`hc ls`, `hc run`, `hc send`, `hc wait`). The app adds arrangement (spaces, tabs, layouts) and presentation (mods) on top. Source: `~/.config/herdr-app/AGENTS.md:17-25`, `hc --help` ("It does not arrange windows; use `herdr-app --skill` for that").

The core's own manual is `hc --skill`. The core's full protocol is `hc schema` (OpenRPC JSON Schema). Both are the primary sources below.

## Q1. Full `hc` surface

`hc --help` lists the commands. `hc <cmd> --help` gives exact flags. `hc schema` gives the same operations as API methods.

### Terminal and agent commands

| Command | Key flags | What it gives an orchestrator |
|---|---|---|
| `hc run <cmd\|template> [task]` | `--bg`, `--name`, `--cwd`, `--on`, `--label K=V`, `--parent`, `--no-parent`, `--no-notify`, `--outlive-parent`, `--rm`, `--keep`, `--timeout`, `--json` | Start a program or agent, optionally with a first task. Without `--bg` it waits and streams the screen. `--bg` returns the address at once. |
| `hc ls` | `--all`, `--label K=V`, `--json` | List live terminals, or all including finished. Filter by labels. This is the team roster. |
| `hc get <t>` | `--json` | One terminal's full facts: address, argv, cwd, foreground, labels, name, parent, state, status, template, turn, keep, notify, exit reason. |
| `hc read <t>` | `--lines N`, `--json` | Screen plus history as text. `--lines 0` is the visible screen only. |
| `hc send <t> <text>` | `--force`, `--if idle\|working`, `--wait`, `--timeout` | Paste text and press Enter after the person pauses. Refuses a `blocked` program unless `--force`. `--wait` waits for the turn it started. |
| `hc keys <t> <keys...>` | `--json` | Press named keys (`enter`, `esc`, `up`, `ctrl+c`, `alt+x`, `f5`, characters). |
| `hc interrupt <t>` | `--timeout` | Press the stop key (`esc` if the program reports status, else `ctrl+c`) and report the outcome (`stopped`, `exited`, `still_working`, `unknown`). |
| `hc wait <t>` | `--output X`, `--timeout`; with `--children any\|all` | Wait until a terminal stops working, prints text, or a child reports. The orchestrator's turn-end primitive. |
| `hc label <t> K=V...` | `--json` | Set or remove free-form labels. `key=` removes. Labels are never interpreted by hc. |
| `hc keep <t>` | `--off` | Replace a lost program with a shell in place. Never reruns the task. |
| `hc put <t> <file>` | `--json` | Copy a local file beside the terminal on its machine and print where it landed. |
| `hc attach [t]` | `--observe`, `--history N` | Put a person's terminal on it. Ctrl+\ detaches. |
| `hc kill <t>` | `--json` | End the program (hangup, then escalate). |
| `hc rm <t>` | `--json` | Remove a finished terminal. |

Sources: `hc run --help`, `hc ls --help`, `hc get --help`, `hc read --help`, `hc send --help`, `hc keys --help`, `hc interrupt --help`, `hc wait --help`, `hc label --help`, `hc keep --help`, `hc put --help`, `hc attach --help`, `hc kill --help`, `hc rm --help`.

### Coordination-wide commands

| Command | Key flags | What it gives an orchestrator |
|---|---|---|
| `hc templates` | `--json` | List role templates by name, description and command. |
| `hc events` | `--terminal`, `--children-of`, `--label K=V`, `--json` | A snapshot followed by one JSON line per fact change. Event kinds: `created`, `lost`, `restarted`, `keep`, `finished`, `removed`, `status`, `turn`, `title`, `labels`, `size`, `doc`, plus `heartbeat` every 15 s and `missed` with a fresh snapshot. This is the live team feed. |
| `hc server status\|start\|stop\|restart` | `--json` | Inspect or control the core. `stop` parks kept terminals. Never stop another user's core. |
| `hc exec [--on m] <cmd>` | `--on`, `--cwd`, `--env K=V`, `--timeout`, `-i`, `--json` | Run one program with no terminal and get exactly its stdout/stderr and exit code. `--env` exists here, not on `run`. |
| `hc file read\|write\|edit` | `--on`, `--cwd`, `--offset`, `--limit`; edits `--old X --new Y` | Read, write or exact-replace a file here or on a machine. An agent's hands on a remote machine. |
| `hc machine add\|ls\|login\|update\|rename\|rm` | ssh target or `--via` | Other machines over ssh. Their terminals address as `machine/t7`. |
| `hc update` | `--version X`, `--from FILE`, `--force` | Swap this `hc` for another build and restart the core, terminals kept. |
| `hc connect` | `--session` | Speak the API over stdin/stdout, for `ssh box hc connect`. |
| `hc schema` | `--json` | The whole API as an OpenRPC document: every method with params, result and errors in JSON Schema. |

Sources: `hc templates --help`, `hc events --help`, `hc server --help`, `hc exec --help`, `hc file --help`, `hc machine --help`, `hc update --help`, `hc connect --help`, `hc schema --help`.

### API and socket

`hc schema` is the machine-readable contract. The methods are:

```
hello, server.status, server.stop, server.restart,
terminal.run, terminal.list, terminal.get, terminal.kill, terminal.rm,
terminal.keep, terminal.label, terminal.read, terminal.attach, terminal.send,
terminal.wait, terminal.keys, terminal.interrupt, terminal.put,
templates.list, events.subscribe, doc.get, doc.set,
machine.exec, file.read, file.write, file.edit,
machine.add, machine.list, machine.connect, machine.rename, machine.rm,
rpc.discover
```

Sources: `hc schema` (method names), `hc --help` (`connect`, `schema`).

The API is richer than the CLI. `terminal.run` accepts `env`, `size: {cols, rows}` and `display: {fg, bg, cursor, cell}` that the CLI does not expose. `terminal.attach` opens a raw stream. `doc.get` and `doc.set` are versioned JSON documents (the app's space/tab document is one). `events.subscribe` is the event feed. The transport is a local core socket or pipe, selected by session (`HC_SESSION`, `HC_SOCKET`) and reachable through `hc connect`. Source: `hc schema` (`Terminal`, `Status`, `AttachResult`, `SubscribeResult`, `DocGetResult`, `DocSetResult`), `hc --help`, `hc.exe 0.1.18` strings (`HC_SESSION`, `HC_SOCKET`, `XDG_STATE_HOME`).

State on this machine is in `%LOCALAPPDATA%\herdr-core\default\`: `core.json` (core id, name), `terminals.json`, `docs.json` (the app's space/tab document), `screens/`, `core.lock`, `core.log`. Source: directory listing of `%LOCALAPPDATA%\herdr-core\default`, contents of `core.json` and `docs.json`.

## Q2. Agent detection and status

**Detection is an OSC 7501 report, not a manifest or a hook.** `hc --skill` says, "State is `live` or `finished`. Status comes from the program's OSC 7501 reports: `idle`, `working`, `done`, `blocked`, `error`; plain programs report none. Do not guess status from screen text."

The exact wire format is visible in the app's own bench script: `sys.stdout.write("\033]7501;state=working:app=pi\033\\")`. That is `ESC ] 7501 ; state=working:app=pi ESC \`. Source: `herdr-app/vendor/herdr-app/scripts/bench.py:35`.

Fields accepted for one record, from the `Status` schema: `state` (`idle`, `working`, `done`, `blocked`, `error`), `app` (agent name such as `claude-code`, `codex`, `pi`), `msg` (a status message), `kind` (`permission`, `question`, `auth`, blocked only), `progress` (0 to 100), `title`. Source: `hc schema` (`Status`), and the binary error hint `]7501;?:states=idle,working,done,blocked,error:kinds=permission,question,auth` in `hc.exe 0.1.18`.

The app's model consumes the word as follows. `done` means finished while the person was not looking, and becomes `idle` once anyone looks. A space or tab status is the most urgent agent status, `blocked > done > working > idle`. Sources: `~/.config/herdr-app/AGENTS.md:150-153`, `herdr.d.ts:81` and `:157-178`, `herdr-app/vendor/herdr-app/src/model.rs:460-503`.

`turn` goes up each time the status leaves `working`. That is what `hc wait` keys off. Source: `hc schema` (`Terminal.turn`), `hc --skill`.

### Why OpenCode is not detected

It sends no OSC 7501 report. `hc get t8` shows `"reported": false`, `"status": null`, `"status_ms": null`. The other terminals show `"reported": true` and a status object, for example `hc get t1` shows `"status": {"app":"claude-code","msg":"Listing all hc terminals including finished ones","state":"working"}`. `herdr-app ls` agrees: t8 has `"status": null, "app": null`, while t1, t4 and t9 have an `app`. Source: live `hc get t1`, `hc get t8`, `herdr-app ls`.

The `agent` field in `hc ls` is the reported `app` when there is one, else the foreground program. t8 has no report, so it shows `"agent":"powershell"` even though OpenCode runs inside it. The foreground process is `powershell.EXE` running `opencode`. Source: live `hc ls --json`, `hc get t8` (`foreground.name`). The app resolves a program similarly, agent report first, else the program it runs: `herdr-app/vendor/herdr-app/src/programs.rs:183-214`.

**What would make OpenCode detected.** An OpenCode plugin or hook that writes `ESC ] 7501 ; state=<word>:app=opencode[ :msg=... ] ESC \` to its own terminal at turn start, turn end, and on a permission prompt. Nothing else is needed. There is no manifest file that teaches hc about an agent. The `manifest` strings in `hc.exe` belong to the release updater (`releases/core/latest.json`), not to agents. `programs.json` only changes a program's display name, icon and tile, not detection. Sources: `hc.exe 0.1.18` strings, `~/.config/herdr-app/AGENTS.md:733-770` (programs.json is presentation only), `hc --skill` (status comes only from 7501).

The launch wrapper can only set an initial word by printing it before the agent starts. Every later change must come from the agent process itself.

## Q3. Parent and child

A terminal records `parent` once, at start. The default parent is the terminal it was started from. `--no-parent` clears it. Source: `hc run --help`, `hc schema` (`Terminal.parent`, `terminal.run.parent`, `no_parent`).

When a child's turn ends, or it blocks, errors or exits, hc types a note into the parent's input. `hc --skill` says, "hc itself notifies you when a child cannot report (blocked, error, exited); inspect its screen." Notes are on by default because `Terminal.notify` is true. `--no-notify` (`no_notify`) turns them off for that child. Source: `hc run --help` (`--no-notify`), `hc schema` (`Terminal.notify`, `terminal.run.no_notify`), live `hc get t1` (`"notify": true`).

### Exact note format

Every note is wrapped in the same tag as a child report:

```
<hc from="t7 review">Two bugs in x.rs; details in /path/to/findings.md</hc>
```

and a launch task carries a reach hint:

```
<hc from="t3 lead" reach="hc send t3 when done or stuck">Review x.rs</hc>
```

Source: `hc --skill`, "Reports arrive tagged", and the exact format strings in `hc.exe 0.1.18` (`notes.rs`): `<hc from="`...`">`...`</hc>` and `<hc from="`...`" reach="hc send `...` when done or stuck">`...`</hc>`.

The automatic note bodies are built from these literals in the same `notes.rs` string table:

- ` has finished`, with `, turn ` and ` (turn ` fragments, so a turn end reads `<address> has finished` with the turn number.
- ` is blocked (` `<kind>` `)`, then `. Last lines:` and the screen, ending with a `: hc read <address>` pointer.
- `hit an error: ` `<word>`.
- `exited (` `<code>` `). See`.
- ` <address> is lost, waiting for its new shell`.
- `the parent got busy` when delivery cannot happen.

Source: `hc.exe 0.1.18` strings in the `notes.rs` region at byte offsets about 6.19 MB and 7.19 MB. The wrapper is literal. The joined punctuation of the blocked and exit bodies is reconstructed from the literals. I could not capture a live note because the only parent, t1, was working, and `hc read` of a full-screen app returns only its screen while it works (`hc --skill`).

### `--outlive-parent`

By default a child's program ends when its parent's program ends. `--outlive-parent` keeps the child running. The API's `die_with_parent` is ignored, and `outlive_parent` is the only control. Source: `hc run --help`, `hc schema` (`terminal.run.die_with_parent` "Ignored: children end with their parent unless `outlive_parent`").

### `hc wait --children any` and `all`

From `hc wait --help` and the `terminal.wait` schema:

- `all` waits until none of the children is working. A child that has not reported yet counts as working. A child with a sent launch task counts as working until its turn advances past delivery, or it reports `done`, `blocked`, `error`, or exits. Startup idle does not complete that task. Without a launch task, idle counts as stopped.
- `any` returns on the parent's unseen news (a turn ended, it blocked, it hit an error, it exited), else on the next child to stop working. For the parent itself it also returns when a message is typed into it while it worked, with `reason: message`.
- A timeout is a check-in, not a failure (`ended: false`, current status). `reason: not_started` means a `send --wait` did not begin working.
- The result is a `WaitResult`, with `children` holding one entry per child and `reason` (`status`, `exit`, `running`).

Sources: `hc wait --help`, `hc schema` (`WaitResult`, `terminal.wait`), `hc --skill`.

## Q4. Templates

Templates are Markdown files in `~/.config/herdr-core/templates/<name>.md`. On this machine the folder does not exist and `hc templates --json` returns `{"templates":[]}`. Sources: `hc --skill`, `hc templates --help`, live `hc templates --json`, directory check of `~/.config/herdr-core`.

Format. Frontmatter is delimited by `---`. The documented keys are `name`, `description` and `command` (the launch command, including the model). The body is the instructions. `hc run <template> "<task>"` combines the body and the task into the first message. Source: `hc --skill`.

The binary's `templates.rs` strings add three accepted keys: `submit` (the key that submits a message), `stop` (the stop key) and `paste_gap_ms` (the pause between paste and submit). Errors include `no frontmatter (start the file with ---)`, `frontmatter never closes (---)`, and `paste_gap_ms must be a number`. Source: `hc.exe 0.1.18` strings (`templates.rs`), and `hc schema` (`Terminal.submit`, `Terminal.stop`, `Terminal.paste_gap_ms`, `TemplateInfo`).

`TemplateInfo` exposes only `name`, `description`, `command`, `error`. There is no template field for env vars, cwd, labels or the terminal name. Source: `hc schema` (`TemplateInfo`).

**Could one template per team role replace the launcher?** No, not alone. A template can own the role's program and model and its standing instructions. It cannot set the terminal name, team and role labels, or cwd. The launcher must still call:

```
hc run --bg --name dev-<role> --cwd <repo> --label team=dev --label role=<role> <template> "<task>"
```

If the template `command` is a shell line it can carry `cd` and inline env, but name and labels still come from `hc run`. Source: `hc run --help` flag list, `hc schema` (`terminal.run` params), `TemplateInfo`.

## Q5. Identity

A terminal's address (`t7`) is permanent for the life of the core. Its `name` is set at `hc run --name` and is unique among live terminals (1 to 32 of `a-z 0-9 - _`, and `.` in the API schema). Source: `hc run --help`, `hc schema` (`RunResult.name`).

**There is no rename.** The CLI has no rename command, and the API method list contains no `terminal.rename`. Source: `hc --help`, `hc schema` method list. The lead t1 demonstrates the gap: `hc get t1` has `"name": null`, even though it carries `role=claude`. A terminal started without `--name` keeps a null name.

**Labels are the right team and role carrier.** They are free-form `key=value`, never interpreted, set at start (`--label`) or later (`hc label`), and usable as filters on `hc ls --label`, `hc events --label`, and the API `terminal.list`. Live t1 to t9 carry `team=dev` and `role=<role>`, and `hc ls --label team=dev` returns exactly the four team terminals. Sources: `hc --help` ("Labels use ids local to that machine"), `hc label --help`, live `hc get t1`/`t8`, live `hc ls --label team=dev`.

For a friendly identity on the lead, pass `--name` when the lead is launched. For an already running lead, labels are the only identity that can be added.

## Q6. The app side

### What the app is

`herdr-app` is a native GPUI window and one client of the core. Its whole interface is files in `~/.config/herdr-app`, and it redraws from them live. Shipped layouts and mods are read-only and replaced on every start. Clone before editing. Source: `~/.config/herdr-app/AGENTS.md:1-15`, `:183-208`.

### The model

- A **space** is a place to work on one machine. A **tab** is a piece of work in a space. A **pane** is a terminal or a mod beside terminals. A tab's panes form a split tree. Source: `~/.config/herdr-app/AGENTS.md:92-135`.
- Every terminal is a core terminal. The app labels the ones it starts with `herdr.space=<id>` and `herdr.tab=<id>`. **A terminal carrying those labels shows up in that space and tab, even one you start yourself.** Source: `~/.config/herdr-app/AGENTS.md:136-146`.
- Agents are terminals that report status. Sub-agents are terminals an agent started with `hc run`. The core records the starter as `parent`, and the sub-agent belongs to its parent's tab without a pane. Source: `~/.config/herdr-app/AGENTS.md:150-160`.

### Mods and layouts

Mods are JavaScript programs using gpui-kit and the `herdr` module. A mod folder is `mods/<name>/main.js` plus `mod.json`. The module gives live data (`spaces()`, `agents()`, `terminals()`, `panes()`, `focused()`, `theme()`), actions (`run(command, args)`), process and file access (`exec`, `hc(args)`, `readTextFile`), and overlays and notifications. A mod can therefore draw a team board and drive the same commands a key does. Sources: `~/.config/herdr-app/AGENTS.md:772-819`, `:928-998`, `herdr.d.ts:79-270`, `herdr.d.ts:157-217`.

Layouts are JSON trees in `layouts/<name>.json`. Node types are `block`, `row`, `column`, `stack`. A block's content is a mod, a terminal, the active tab, or an agent. Layout commands are `layout.switch`, `layout.show`, `layout.hide`, `layout.resize`, `layout.select`, `layout.set`, `layout.reset`, `layout.clone`. Source: `~/.config/herdr-app/AGENTS.md:165-266`, `:598-602`.

### Can a script place a terminal in a chosen space or tab?

Yes, two ways.

1. **Adoption by labels.** Start or relabel the terminal with the target ids:

```
hc run --keep --label herdr.space=<space-id> --label herdr.tab=<tab-id> -- claude
```

The app adopts it, and adoption also enables core keep. Labels use ids local to that machine. Remote ids shown by the app carry a `machine/` prefix that must be stripped when writing labels on that machine. An unknown id becomes a new space or tab. Source: `~/.config/herdr-app/AGENTS.md:136-149`.

2. **App commands.** `herdr-app do space.new {name?, folder?, tab?, argv?, task?, machine?}` and `herdr-app do tab.new {name?, space?, folder?, argv?, task?}` start the terminal inside the chosen space or tab. `herdr-app do` prints the created `{space, tab, terminal}`. `herdr-app ls` prints each space, tab, terminal and `sub_agents`. Sources: `~/.config/herdr-app/AGENTS.md:532-552`, `:622-633`, live `herdr-app ls`.

Live proof of both the labels and the document: `docs.json` holds space `sp204da0afc9fd8317f273dd6e37fd9b75` with tab `tb1fc8667c089eec1c6fb613f9560c849a`, whose tree places t1 and t4 in the left column and t9 and t8 in the right column. Every team terminal carries those two ids as `herdr.space` and `herdr.tab`. Source: `%LOCALAPPDATA%\herdr-core\default\docs.json`, live `hc get t1`/`t8`/`t9`.

The `where` label on every live team terminal (`kickoff test 2 / 1`) mirrors the space name and tab name. The app documents `herdr.space` and `herdr.tab` as its contract; `where` is additional display metadata.

## Q7. Context telemetry

**No. `hc` exposes no token or context counters.** The only per-terminal telemetry is the OSC 7501 record: `state`, `app`, `msg`, `kind`, `progress`, `title`, plus `status_ms` (when the word changed) and `turn`. Source: `hc schema` (`Status`, `Terminal`). `Terminal` has no token, context, cost or usage field.

What hx reads today is outside `hc`:

- Claude's status line hook writes `context_window` to a JSON file in the shared state dir.
- Codex rollouts are read from `~/.codex/sessions/.../rollout-*.jsonl`, looking for `token_count`.
- OpenCode usage is read from its SQLite database.

Sources: `hooks/claude_statusline.py:1-99`, `hx/context.py:257-293`, `hx/context.py:329-341`.

Implication for the adapter. Context telemetry must keep using those three readers, or read the agent's screen or footer. `hc get <t>` gives the screen only through `hc read`, and the record's `msg` is the agent's own status text, not a token figure. The app's `terminals()` passthrough exposes the same record and nothing more. Source: `herdr.d.ts:214-217`, `herdr-app/vendor/herdr-app/src/telemetry.rs:37` ("Every terminal reporting an agent: its 7501 `app`").

## Gotchas

- **A program without 7501 has no status and no turn.** `hc wait` on it waits for exit, and `hc wait --children` treats it as not working only when it has no launch task. Treat such an agent as opaque. Source: `hc --skill`.
- **`send` refuses a blocked program.** `--force` exists but must not be used to answer a permission decision. Read the screen and ask the person. Source: `hc --help`, `hc send --help`.
- **`run` without `--bg` streams the whole screen.** Always use `--bg` for agents, and put the first task in `run` because a send to a booting agent can be dropped. Source: `hc --skill`.
- **Codex must run with `--no-daemon`, including through a template.** Otherwise its commands execute outside hc. Source: `hc --skill`.
- **`die_with_parent` is ignored.** Use `--outlive-parent`. Source: `hc schema`.
- **The app labels are the placement contract.** A terminal without `herdr.space` and `herdr.tab` stays out of Herdr's screens. Source: `~/.config/herdr-app/AGENTS.md:147-149`.
- **Shipped layouts and mods are replaced on every start.** Clone before editing. Source: `~/.config/herdr-app/AGENTS.md:183-208`.
- **State is per session.** `hc` inside a terminal knows the address from `$HC_TERMINAL_ID`. A script uses the session's core, selected by `HC_SESSION` or `HC_SOCKET`. Source: `hc --skill`, `hc --help`.

## Operation map

| Orchestrator operation | Exact command | Notes |
|---|---|---|
| Start an agent | `hc run --bg --name dev-<role> --cwd <repo> --label team=dev --label role=<role> <cmd\|template> "<task>"` | Add `--keep` for survival after loss. Put the task in `run`. |
| List the team | `hc ls --label team=dev --json` | Filter by `team=dev`; each row has address, name, agent, status, turn, parent. |
| Send a message | `hc send <t> "<text>"` | `--wait` waits for the turn; `--if idle` guards a busy agent. |
| Read the screen | `hc read <t> --lines <n>` | `--lines 0` is the screen only; history may be truncated while working or blocked. |
| Wait for a turn end | `hc wait <t>` or `hc wait --children any` | `any` returns on the parent's unseen news or the next child to stop; timeout is a check-in. |
| Status | `hc get <t>` (fields `status`, `status_ms`, `turn`) or `hc ls` | `status` is the OSC 7501 word; null when the program never reported. |
| Identity | `--name` at launch; `--label K=V` and `hc label <t> K=V` after; no rename | Labels are the durable team and role identity. |
| Place in a space or tab | `hc run --keep --label herdr.space=<id> --label herdr.tab=<id> ...` or `herdr-app do tab.new` | Adoption needs `--keep`. |
| Follow changes live | `hc events --label team=dev --json` | Snapshot, then one JSON line per fact change; heartbeat every 15 s. |
| Templates | `hc templates` | Files in `~/.config/herdr-core/templates/<name>.md`. |
| Remote machine | `hc run --on <machine> ...`, `hc exec --on <machine> ...` | Address is `machine/t7`. |
