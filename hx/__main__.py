"""Entry point: python -m hx <command>."""

from __future__ import annotations

import argparse
import json
import sys

from hx import checkpoint, context, ledger, ocrun, route, usage


def _add_usage_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "usage",
        help="Print a JSON snapshot of provider availability and quota.",
    )
    parser.set_defaults(func=lambda _args: usage.print_usage())


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

    list_ = task_sub.add_parser("list", help="List tasks.")
    list_.add_argument("--status")
    list_.set_defaults(func=_task_list)

    show = task_sub.add_parser("show", help="Show one task.")
    show.add_argument("id")
    show.set_defaults(func=_task_show)


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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hx",
        description="Herdr team CLI: usage, routing, ledger, checkpoint, context.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_usage_parser(subparsers)
    _add_task_parser(subparsers)
    _add_checkpoint_parser(subparsers)
    context_parser = subparsers.add_parser("context", help="Session context decisions and recovery.")
    context_sub = context_parser.add_subparsers(dest="context_command", required=True)
    decide = context_sub.add_parser("decide")
    decide.add_argument("agent")
    decide.add_argument("--task")
    decide.add_argument("--related", action="store_true")
    decide.set_defaults(func=lambda args: print(json.dumps(context.Controller().decide(args.agent, args.task, args.related))))
    recover = context_sub.add_parser("recover")
    recover.add_argument("agent")
    recover.set_defaults(func=lambda args: print(json.dumps(context.Controller().recover(args.agent))))
    compact = subparsers.add_parser("compact", help="Compact an idle worker at a completed task boundary.")
    compact.add_argument("agent")
    compact.add_argument("--task")
    compact.add_argument("--related", action="store_true")
    compact.set_defaults(func=_compact)
    return parser


def _compact(args: argparse.Namespace) -> None:
    result = context.Controller().compact(args.agent, args.task, args.related)
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
    )
    print(json.dumps(task, indent=2))


def _task_assign(args: argparse.Namespace) -> None:
    if args.method == "persistent" and not args.agent:
        raise ValueError("task assign --method persistent requires --agent")
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
    print(json.dumps(task, indent=2))


def _task_failed(args: argparse.Namespace) -> None:
    led = ledger.default_ledger()
    task = led.set_status(args.id, "failed")
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
    path = checkpoint.write_checkpoint(
        args.id,
        ledger=led,
        objective=args.objective,
        owner=args.owner,
        status=args.status,
        decisions=args.decisions,
        errors=args.errors,
        tests=args.tests,
        next_action=args.next_action,
        references=args.references,
    )
    print(json.dumps({"checkpoint": str(path)}, indent=2))


def _checkpoint_validate(args: argparse.Namespace) -> None:
    led = ledger.default_ledger()
    result = checkpoint.validate_checkpoint(args.id, ledger=led)
    print(json.dumps(result, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:]) if argv is None else list(argv)
    if args_list and args_list[0] in {"route", "ocrun"}:
        handler = route.main if args_list[0] == "route" else ocrun.main
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
