# Spec: hx on herdr-core (the Herdr app)

## Goal
After this, the whole team works natively in the Herdr app: hx starts, lists, messages, reads, resets and checks the team through `hc`, and every report reaches the lead.

Creator, 8 Nov: "I foresee that the herdr app will from this point on be the primary place where we work, no longer in the terminal/powershell as before." And: "briefs should include how to report back."

## For whom
The creator, who watches the team in the app's 2x2 grid (lead top-left, Codex top-right, Devin bottom-left, OpenCode bottom-right). The lead and the workers, who use hx.

## Evidence
- `inventory.md`: every herdr coupling. There are 11 operations, and the code reaches into herdr's JSON directly at the call sites in §8.
- `research-hc.md`: the hc surface, OSC 7501 status, parent notes, templates and the operation map.

## Smallest version that proves it
In the app, with no old herdr server running:
1. `hx team up <repo>` brings up the four-agent grid, and running it again changes nothing.
2. A worker's `hx say claude '…'` lands in the lead's terminal.
3. `hx doctor` passes, and `hx usage` shows Devin's real session count.
4. OpenCode shows idle or working in the app like the others.

## Design
One host boundary, `hx/host.py`. It is the only module that runs `herdr` or `hc`.

```python
class Member(TypedDict):        # one live teammate, host-neutral
    name: str                   # "<team>-<role>", e.g. "dev-codex"
    team: str                   # "dev"
    role: str                   # "claude" | "codex" | "opencode" | "devin" | any
    kind: str                   # agent program: "claude" | "codex" | "opencode" | "devin" | "unknown"
    status: str                 # "idle" | "working" | "done" | "blocked" | "error" | "unknown"
    cwd: str
    address: str                # herdr pane id or hc terminal address ("t9")
    session_id: str | None      # agent session id where the host knows it (herdr only)

class Host(Protocol):
    def own(self) -> Member | None: ...
    def team(self, prefix: str) -> dict[str, Member]: ...
    def info(self, name: str) -> Member: ...
    def send(self, name: str, text: str) -> int: ...
    def read(self, name: str, lines: int) -> str: ...

def detect() -> Host | None   # HC_TERMINAL_ID set -> HcHost; HERDR_ENV == "1" -> HerdrHost; else None
```

- **HcHost.** Identity comes from the `team` and `role` labels (the lead t1 has no name, and hc can't rename). The team list is `hc ls --json` filtered on `team=<team>`. Status is the 7501 word, or `unknown` when there's no report. `send` is `hc send <address> <text>`. `read` is `hc read <address> --lines N`.
- **HerdrHost.** Today's code moves behind the same interface unchanged. It stays until a week passes with no team run on the old server, then it's deleted (migrate callers, then delete the legacy path).
- Callers stop parsing host JSON: `usage.own_agent_name` and `team_agents`, `context.agent_info`, `_herdr` and `_herdr_read`, `say.say`, `devin.live_sessions`, `doctor`, and the `HERDR_ENV` gate in `ocrun`.

## Behaviour on hc that differs from herdr
- **Reset and compaction.** `hc send` wraps text in `<hc from=…>`, so `/clear`, `/new` and `/compact` arrive as chat. On hc, a reset is a terminal restart: same name, labels, cwd and pane. Compaction is checkpoint, then restart, then resend the checkpoint pointer.
- **Reporting.** hc's done-note is lost when the lead is busy, and OpenCode sends no status. A report is always an explicit `hx say claude` (which runs `hc send` to the lead's address). `hx brief new` writes the Report section, so no brief can leave it out.
- **Codex** starts with `--no-daemon`, or its commands run outside hc.
- **OpenCode status.** A small OpenCode plugin writes `ESC ] 7501 ; state=<word>:app=opencode ESC \` at turn start, turn end and on a permission prompt.

## Risks
1. **Two hosts drift.** Sign: a fix that lands in one host only. Guard: one test suite runs against both fakes through `Host`.
2. **A restart-based reset loses work in flight.** Sign: a reset of a busy worker. Guard: refuse unless the worker is idle and has a valid checkpoint (the same guard `hx compact` has today).
3. **Permission widening.** Letting Codex run `hc` and letting Devin fetch the web widens agent permissions. The auto-mode check blocked the Devin change on 8 Nov. Those edits ship only after the creator approves them, in their own commit.

## Ambitious version (parked)
- hx reads `hc events` live instead of polling.
- Team roles become hc templates.
- The usage bar shows each worker's context.
- `hx team up --on <machine>` runs workers on another machine.
