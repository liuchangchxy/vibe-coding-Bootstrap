"""Command line interface for the unified Bootstrap loop."""

import argparse
import json
from pathlib import Path
import sys

from .core import apply_plan, build_plan, decide, inspect_project, load_json, render_summary, validate_output_path, write_json


def _parser():
    parser = argparse.ArgumentParser(prog="python -m bootstrap", description="Inspect, plan, confirm once, and apply a project Bootstrap plan.")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Read target facts and select compose or adoption")
    inspect.add_argument("--project-root", required=True)
    inspect.add_argument("--host", choices=("codex", "none"), default="codex")
    inspect.add_argument("--output")
    plan = commands.add_parser("plan", help="Build a revision-bound unified plan")
    plan.add_argument("--project-root", required=True)
    plan.add_argument("--host", choices=("codex", "none"), default="codex")
    plan.add_argument("--context")
    plan.add_argument("--semantic-result")
    plan.add_argument("--global-root")
    plan.add_argument("--output")
    decide_cmd = commands.add_parser("decide", help="Create one exact-plan decision artifact")
    decide_cmd.add_argument("plan")
    decide_cmd.add_argument("--decision", choices=("approve", "reject"), required=True)
    decide_cmd.add_argument("--output", required=True)
    apply_cmd = commands.add_parser("apply", help="Apply an approved unified plan")
    apply_cmd.add_argument("--plan", required=True)
    apply_cmd.add_argument("--decision")
    apply_cmd.add_argument("--project-root", required=True)
    apply_cmd.add_argument("--global-root")
    apply_cmd.add_argument("--output")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command == "inspect":
            value = inspect_project(args.project_root, args.host)
            if args.output:
                write_json(args.output, value, args.project_root)
            print(json.dumps(value, indent=2, ensure_ascii=False) if not args.output else f"Route: {value['route']}\nNext: {value['next_action']}")
        elif args.command == "plan":
            value = build_plan(args.project_root, args.host, args.context, args.semantic_result, args.global_root)
            if args.output:
                write_json(args.output, value, args.project_root)
            print(render_summary(value))
        elif args.command == "decide":
            plan = load_json(args.plan)
            value = decide(plan, args.decision)
            write_json(args.output, value, plan["target"]["path"])
            print(f"Decision: {value['decision']} ({value['plan_digest'][:12]})")
        else:
            plan = load_json(args.plan)
            decision = load_json(args.decision) if args.decision else None
            validate_output_path(args.output, args.project_root)
            value = apply_plan(plan, decision, args.project_root, args.global_root)
            if args.output:
                write_json(args.output, value, args.project_root)
            print(json.dumps(value, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        message = str(exc)
        print(message, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
