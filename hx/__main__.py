"""Entry point: hx <command>."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from hx import __version__
from hx import balance, boundary, check, checkpoint, context, doctor, handoff, jotarium, ledger, next as next_mod, ocrun, pace, profile, reconcile, review, route, runtime, say, task_index, team_stalls, team_watch, usage


def _add_usage_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "usage",
        help="Print a JSON snapshot of provider availability and quota.",
    )
    parser.set_defaults(func=lambda _args: usage.print_usage())


def _add_pace_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "pace",
        help="Pool pacing from hx usage: used share, elapsed share and pace per window.",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.set_defaults(func=lambda args: pace.command(as_json=args.as_json))


def _add_say_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("say", help="Message a teammate on your own team.")
    parser.add_argument("target", nargs="?", help="any live teammate role (claude, codex, devin, ...) or the full <team>-<role> name")
    parser.add_argument("message", nargs="*")
    parser.add_argument("--force", action="store_true", help="Send despite fresh worker context pressure and compaction admission (for recovery/control messages).")
    parser.add_argument("--control", action="store_true", help="Control message: never held by compaction admission.")
    parser.add_argument("--pending", action="store_true", help="List and retry this sender's pending messages.")
    parser.add_argument("--list", dest="list_uncertain", action="store_true",
                        help="With --pending, show the uncertain records kept for reconciliation.")
    parser.add_argument("--task", help="Ledger task this worker brief belongs to; a closed task cancels delivery. Reports to the lead are never cancelled.")
    parser.add_argument("--ttl", type=float, help="Seconds a worker brief stays deliverable (default from policy say.work_ttl_seconds). Reports to the lead never expire.")
    parser.add_argument("--supersedes", help="Id of the outbox record this message replaces.")
    parser.set_defaults(func=lambda args: say.retry_pending(list_uncertain=args.list_uncertain) if args.pending or args.list_uncertain else say.say(
        args.target, " ".join(args.message), force=args.force, control=args.control,
        task=args.task, ttl=args.ttl, supersedes=args.supersedes))


def _add_reconcile_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "reconcile",
        help="Settle send records against the receiver transcript (read-only).",
    )
    parser.add_argument("--since", metavar="DATE", help="first send date, YYYY-MM-DD")
    parser.add_argument("--until", metavar="DATE", help="last send date, YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.set_defaults(func=lambda args: reconcile.command(
        since=args.since, until=args.until, as_json=args.as_json))


def _add_jotarium_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("jotarium", help="Read-only Jotarium checks.")
    sub = parser.add_subparsers(dest="jotarium_command", required=True)
    audit = sub.add_parser(
        "audit",
        help="Flag in-progress items with no open hx task, live worker or Needs-you card.",
    )
    audit.set_defaults(func=lambda _args: jotarium.audit_command())


def _add_task_parser(subparsers: argparse._SubParsersAction) -> None:
    task_parser = subparsers.add_parser("task", help="Task ledger commands.")
    task_sub = task_parser.add_subparsers(dest="task_command", required=True)

    add = task_sub.add_parser("add", help="Add a new task.")
    add.add_argument("id")
    add.add_argument("--title", required=True)
    add.add_argument("--owner", required=True)
    add.add_argument("--files", nargs="+", required=True)
    add.add_argument("--depends", nargs="*", default=[])
    add.add_argument("--worktree")
    add.add_argument("--model")
    add.add_argument("--effort")
    add.add_argument("--method")
    add.add_argument("--review-required", action="store_true")
    add.add_argument("--jotarium", help="Jotarium item ref this task delivers, for example Q-412.")
    add.add_argument("--weight", choices=("heavy", "light"), help="Work weight; default follows hx next's ledger-task rule.")
    add.set_defaults(func=_task_add)

    assign = task_sub.add_parser("assign", help="Assign a task to an owner.")
    assign.add_argument("id")
    assign.add_argument("--owner", required=True)
    assign.add_argument("--worktree")
    assign.add_argument("--method")
    assign.add_argument("--agent")
    assign.set_defaults(func=_task_assign)

    status = task_sub.add_parser("status", help="Update task status.")
    status.add_argument("id")
    status.add_argument("--status", required=True)
    status.set_defaults(func=_task_status)

    done = task_sub.add_parser("done", help="Mark a task done after checkpoint.")
    done.add_argument("id")
    _add_checkpoint_flags(done)
    done.set_defaults(func=_task_done)

    failed = task_sub.add_parser("failed", help="Mark a task failed.")
    failed.add_argument("id")
    failed.set_defaults(func=_task_failed)

    release = task_sub.add_parser(
        "release",
        help="Release stranded session reservations for a task.",
    )
    release.add_argument("id")
    release.set_defaults(func=_task_release)

    files = task_sub.add_parser("files", help="Add or remove files a task owns.")
    files.add_argument("id")
    files.add_argument("--add", nargs="+", default=[])
    files.add_argument("--remove", nargs="+", default=[])
    files.set_defaults(func=_task_files)

    list_ = task_sub.add_parser("list", help="List tasks.")
    list_.add_argument("--status")
    list_.set_defaults(func=_task_list)

    show = task_sub.add_parser("show", help="Show one task.")
    show.add_argument("id")
    show.set_defaults(func=_task_show)

    index = task_sub.add_parser("index", help="Sync this repo's open tasks into the user-level cross-repo index.")
    index.set_defaults(func=_task_index)


def _add_checkpoint_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--objective", default="")
    parser.add_argument("--owner", default="")
    parser.add_argument("--status", default="")
    parser.add_argument("--decisions", default="")
    parser.add_argument("--errors", default="")
    parser.add_argument("--tests", default="")
    parser.add_argument("--next", default="", dest="next_action")
    parser.add_argument("--references", default="")


def _add_checkpoint_parser(subparsers: argparse._SubParsersAction) -> None:
    cp_parser = subparsers.add_parser("checkpoint", help="Checkpoint commands.")
    cp_sub = cp_parser.add_subparsers(dest="checkpoint_command", required=True)

    write = cp_sub.add_parser("write", help="Write a checkpoint for a task.")
    write.add_argument("id")
    _add_checkpoint_flags(write)
    write.set_defaults(func=_checkpoint_write)

    validate = cp_sub.add_parser("validate", help="Validate a checkpoint.")
    validate.add_argument("id")
    validate.set_defaults(func=_checkpoint_validate)


def _add_step_parser(subparsers: argparse._SubParsersAction) -> None:
    step_parser = subparsers.add_parser(
        "step",
        help="Task step log: harvest a worker transcript or record a step by hand.",
    )
    step_sub = step_parser.add_subparsers(dest="step_command", required=True)

    harvest = step_sub.add_parser(
        "harvest",
        help="Append a worker's steps since its brief to the task checkpoint.",
    )
    harvest.add_argument("id")
    harvest.add_argument("--agent", required=True,
                         help="worker whose transcript to read, for example dev-opencode")
    window = harvest.add_mutually_exclusive_group()
    window.add_argument("--brief", help="send record id that carried the brief")
    window.add_argument("--since", help="ISO timestamp to search in one unambiguous worker session")
    harvest.add_argument("--json", action="store_true", dest="as_json")
    harvest.set_defaults(func=_step_harvest)

    add = step_sub.add_parser("add", help="Record a step by hand, for Devin and bare shells.")
    add.add_argument("id")
    add.add_argument("--note", required=True)
    add.add_argument("--agent", default="", help="who did it; defaults to manual")
    add.set_defaults(func=_step_add)


def _add_activate_parser(subparsers: argparse._SubParsersAction) -> None:
    activate = subparsers.add_parser(
        "activate",
        help="Pin the live hx runtime to a reviewed commit fetched on origin/main.",
    )
    activate.add_argument("commit", nargs="?",
                          help="commit to activate (default: HEAD of the fetched origin/main ref)")
    activate.add_argument("--rollback", action="store_true",
                          help="switch the pointer back to the previous build")
    activate.add_argument("--runtime-root", help="runtime root (default: ~/.hx-runtime)")
    activate.add_argument("--source", help="source checkout (default: this checkout or the recorded origin)")
    activate.add_argument("--release-ref", help="reviewed ref (only origin/main is accepted)")
    activate.add_argument("--allow-state-change", action="store_true",
                          help="allow activating across a state-format change after review")
    activate.set_defaults(func=_activate)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hx",
        description="Herdr team CLI: usage, routing, ledger, checkpoint, context.",
    )
    parser.add_argument("--version", action="version", version=f"hx {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_activate_parser(subparsers)
    _add_usage_parser(subparsers)
    _add_pace_parser(subparsers)
    _add_task_parser(subparsers)
    _add_say_parser(subparsers)
    _add_reconcile_parser(subparsers)
    _add_jotarium_parser(subparsers)
    _add_checkpoint_parser(subparsers)
    _add_step_parser(subparsers)
    boundary = subparsers.add_parser("boundary", help="Record a context decision without executing compaction. `hx boundary check|gen` handles the worker boundary manifest.")
    boundary.add_argument("agent")
    boundary.add_argument("--task")
    boundary.add_argument("--work-item", help="Stable lead work-item key for its compaction guard.")
    boundary.add_argument("--session")
    boundary.add_argument("--reset-sent", action="store_true",
                          help="Record a reset you already sent, so pressure holds clear until the new session samples.")
    boundary.set_defaults(func=_boundary)
    reset = subparsers.add_parser("reset", help="Send an idle worker's native reset command and record it.")
    reset.add_argument("agent")
    reset.add_argument("--session")
    reset.set_defaults(func=lambda args: print(json.dumps(
        context.Controller().reset(args.agent, session_id=args.session))))
    # route and ocrun dispatch to their own main(argv) in main() before argparse
    # runs; these subparsers exist so `hx --help` lists them.
    subparsers.add_parser(
        "route",
        help="Deterministic task router: task -> worker, model, effort, method.",
    )
    subparsers.add_parser(
        "ocrun",
        help="Headless OpenCode worker wrapper with silent-failure detection.",
    )
    subparsers.add_parser(
        "next",
        help="Print the next unit of work that does not need the creator.",
    )
    balance_parser = subparsers.add_parser("balance", help="Heavy/light dispatch advice and stuck-worker watchdog.")
    balance_parser.add_argument("--json", action="store_true", dest="as_json")
    balance_parser.set_defaults(func=lambda args: balance.command(as_json=args.as_json))
    subparsers.add_parser("doctor", help="Read-only drift report: policy vs live adapters.")
    subparsers.add_parser("handoff", help="Write a portable handoff file and print its re-brief line.")
    subparsers.add_parser("review", help="One review record per change on the ledger task.")
    subparsers.add_parser("profile", help="Show a rendered worker config for a named execution profile.")
    context_parser = subparsers.add_parser("context", help="Session context decisions and recovery.")
    context_sub = context_parser.add_subparsers(dest="context_command", required=True)
    decide = context_sub.add_parser("decide")
    decide.add_argument("agent")
    decide.add_argument("--task")
    decide.add_argument("--related", action="store_true")
    decide.add_argument("--session", help="Claude session id when Herdr does not provide one.")
    decide.add_argument("--work-item")
    decide.set_defaults(func=lambda args: print(json.dumps(context.Controller().decide(args.agent, args.task, args.related, session_id=args.session, work_item=args.work_item))))
    confirmed = context_sub.add_parser("self-compacted", help="Confirm that a Claude lead has compacted.")
    confirmed.add_argument("agent")
    confirmed.add_argument("--session", required=True)
    confirmed.add_argument("--work-item")
    confirmed.set_defaults(func=lambda args: print(json.dumps(context.Controller().self_compacted(args.agent, args.session, work_item=args.work_item))))
    recover = context_sub.add_parser("recover")
    recover.add_argument("agent")
    recover.add_argument("--session")
    recover.add_argument("--token")
    recover.set_defaults(func=lambda args: print(json.dumps(
        context.Controller().recover(args.agent, session_id=args.session, token=args.token))))
    compact = subparsers.add_parser("compact", help="Compact an idle worker at a completed task boundary.")
    compact.add_argument("agent")
    compact.add_argument("--task")
    compact.add_argument("--related", action="store_true")
    compact.add_argument("--quick", action="store_true", help="Manual compaction: record the operation before sending keys, with no task checkpoint.")
    compact.set_defaults(func=_compact)
    team_parser = subparsers.add_parser("team", help="Team observer commands.")
    team_sub = team_parser.add_subparsers(dest="team_command", required=True)
    watch_parser = team_sub.add_parser(
        "watch",
        help="Wake when a worker goes from working to idle/done/blocked or exits; compact when needed and print JSON lines.")
    watch_parser.add_argument("--team", help="Team name; default is this pane's own team.")
    watch_parser.add_argument("--interval", type=float, default=1.0, help="Roster rescan period in seconds.")
    watch_parser.add_argument("--timeout", type=float, default=None, help="Seconds to watch before exiting quietly.")
    watch_parser.set_defaults(func=lambda args: _team_watch(args))
    stalls_parser = team_sub.add_parser(
        "stalls",
        help="One-shot stall check: alert the lead when a working worker's screen has not changed for stall_minutes.",
    )
    stalls_parser.add_argument("--team", help="Team name; default is this pane's team, else the one live team with a lead.")
    stalls_parser.add_argument("--dry-run", action="store_true",
                               help="Print every worker's state without sending or saving state; the run line is still logged.")
    stalls_parser.set_defaults(
        func=lambda args: team_stalls.run(args.team, dry_run=args.dry_run))
    return parser


def _activate(args: argparse.Namespace) -> None:
    if args.rollback:
        result = runtime.rollback(root=args.runtime_root)
    else:
        result = runtime.activate(
            args.commit,
            root=args.runtime_root,
            repo=args.source,
            release=args.release_ref,
            allow_state_change=args.allow_state_change,
        )
    print(json.dumps(result, indent=2))


def _boundary(args: argparse.Namespace) -> None:
    controller = context.Controller()
    if args.reset_sent:
        reset = controller.record_reset(args.agent, session_id=args.session)
        result = controller.boundary(args.agent, args.task, work_item=args.work_item, session_id=args.session)
        result["reset"] = reset
    else:
        result = controller.boundary(args.agent, args.task, work_item=args.work_item, session_id=args.session)
    print(json.dumps(result))


def _team_watch(args: argparse.Namespace) -> int:
    return team_watch.watch(args.team, interval=args.interval, timeout=args.timeout)


def _compact(args: argparse.Namespace) -> None:
    controller = context.Controller()
    result = (controller.compact_quick(args.agent) if args.quick
              else controller.compact(args.agent, args.task, args.related))
    print(json.dumps(result))
    if result["action"] == "compact-unknown":
        raise SystemExit(1)


def _task_add(args: argparse.Namespace) -> None:
    led = ledger.default_ledger()
    task = led.add_task(
        task_id=args.id,
        title=args.title,
        owner=args.owner,
        files=args.files,
        depends=args.depends,
        worktree=args.worktree,
        model=args.model,
        effort=args.effort,
        method=args.method,
        review_required=args.review_required,
        jotarium=args.jotarium,
        weight=args.weight or next_mod._follow_up_weight({"effort": args.effort}),
    )
    print(json.dumps(task, indent=2))


def _task_index(args: argparse.Namespace) -> None:
    led = ledger.default_ledger()
    count = task_index.sync_repo(
        repo_root=led.repo_root,
        ledger_path=led.ledger_path,
        tasks=led.list_tasks(),
        open_statuses=ledger.Ledger.OPEN_STATUSES,
    )
    print(json.dumps({"indexed": count, "path": str(task_index.default_index_path())}, indent=2))


def _task_assign(args: argparse.Namespace) -> None:
    if ledger.is_persistent_method(args.method) and not args.agent:
        raise ValueError(f"task assign --method {args.method} requires --agent")
    led = ledger.default_ledger()
    task = led.assign_task(
        args.id,
        owner=args.owner,
        worktree=args.worktree,
        method=args.method,
        agent=args.agent,
    )
    print(json.dumps(task, indent=2))


def _task_status(args: argparse.Namespace) -> None:
    led = ledger.default_ledger()
    task = led.set_status(args.id, args.status)
    print(json.dumps(task, indent=2))


def _task_done(args: argparse.Namespace) -> None:
    led = ledger.default_ledger()
    checkpoint.write_completion_checkpoint(
        args.id,
        ledger=led,
        status="done",
        objective=args.objective,
        owner=args.owner,
        status_note=args.status,
        decisions=args.decisions,
        errors=args.errors,
        tests=args.tests,
        next_action=args.next_action,
        references=args.references,
    )
    result = checkpoint.validate_checkpoint(args.id, ledger=led)
    if not result["ok"]:
        raise RuntimeError(f"checkpoint validation failed: {'; '.join(result['errors'])}")
    task = led.get_task(args.id)
    task["boundary"] = context.Controller(led=led).boundary(task["owner"], args.id, source="task-done")
    print(json.dumps(task, indent=2))


def _task_failed(args: argparse.Namespace) -> None:
    led = ledger.default_ledger()
    task = led.set_status(args.id, "failed")
    print(json.dumps(task, indent=2))


def _task_release(args: argparse.Namespace) -> None:
    led = ledger.default_ledger()
    released = led.release_task_reservations(args.id)
    print(json.dumps({"task": args.id, "released": released}, indent=2))


def _task_files(args: argparse.Namespace) -> None:
    led = ledger.default_ledger()
    task = led.update_task_files(args.id, add=args.add, remove=args.remove)
    print(json.dumps(task, indent=2))


def _task_list(args: argparse.Namespace) -> None:
    led = ledger.default_ledger()
    tasks = led.list_tasks(status=args.status)
    print(json.dumps(tasks, indent=2))


def _task_show(args: argparse.Namespace) -> None:
    led = ledger.default_ledger()
    task = led.get_task(args.id)
    print(json.dumps(task, indent=2))


def _checkpoint_write(args: argparse.Namespace) -> None:
    led = ledger.default_ledger()
    sections = dict(
        objective=args.objective,
        owner=args.owner,
        status=args.status,
        decisions=args.decisions,
        errors=args.errors,
        tests=args.tests,
        next_action=args.next_action,
        references=args.references,
    )
    standalone_lead = False
    if args.id == "lead":
        try:
            led.get_task("lead")
        except ledger.TaskNotFound:
            standalone_lead = True
    if standalone_lead:
        path = context.write_lead_checkpoint(led, **sections)
    else:
        path = checkpoint.write_checkpoint(args.id, ledger=led, **sections)
    print(json.dumps({"checkpoint": str(path)}, indent=2))


def _checkpoint_validate(args: argparse.Namespace) -> None:
    led = ledger.default_ledger()
    result = checkpoint.validate_checkpoint(args.id, ledger=led)
    print(json.dumps(result, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


def _step_harvest(args: argparse.Namespace) -> int:
    result = checkpoint.harvest(
        args.id, args.agent, brief=args.brief, since=args.since)
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print(checkpoint.render_harvest(result))
    return 0


def _step_add(args: argparse.Namespace) -> int:
    line = checkpoint.format_step(
        dt.datetime.now(dt.timezone.utc), args.agent or "manual", "note", args.note)
    path, added = checkpoint.append_steps(args.id, [line])
    state = "recorded" if added else "already recorded"
    print(f"hx step add {args.id}: {state} {line}")
    print(f"checkpoint {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:]) if argv is None else list(argv)
    if len(args_list) > 1 and args_list[0] == "boundary" and args_list[1] in {"check", "gen"}:
        try:
            return int(boundary.main(args_list[1:]) or 0)
        except SystemExit as exc:
            return int(exc.code) if exc.code else 0
        except Exception as exc:
            print(f"hx: {exc}", file=sys.stderr)
            return 1
    if args_list and args_list[0] in {"route", "ocrun", "doctor", "handoff", "review", "profile", "check", "next"}:
        handler = {
            "route": route.main,
            "ocrun": ocrun.main,
            "doctor": doctor.main,
            "handoff": handoff.main,
            "review": review.main,
            "profile": profile.main,
            "check": check.main,
            "next": next_mod.main,
        }[args_list[0]]
        try:
            return int(handler(args_list[1:]) or 0)
        except SystemExit as exc:
            return int(exc.code) if exc.code else 0
        except Exception as exc:
            print(f"hx: {exc}", file=sys.stderr)
            return 1
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        rc = args.func(args)
    except Exception as exc:
        print(f"hx: {exc}", file=sys.stderr)
        return 1
    return int(rc) if isinstance(rc, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
