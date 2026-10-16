"""Plan and manage Codex-native Capability activation."""

import argparse
import json
from pathlib import Path

from .core import apply, build_plan, deactivate, make_decision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan_parser = commands.add_parser("codex-plan")
    plan_parser.add_argument("--project-root", type=Path, required=True)
    plan_parser.add_argument("--capability", default="testing")
    plan_parser.add_argument("--output", type=Path, required=True)
    decide_parser = commands.add_parser("decide")
    decide_parser.add_argument("plan", type=Path)
    decide_parser.add_argument("--decision", choices=("approve", "reject"), required=True)
    decide_parser.add_argument("--output", type=Path, required=True)
    apply_parser = commands.add_parser("apply")
    apply_parser.add_argument("--plan", type=Path, required=True)
    apply_parser.add_argument("--decision", type=Path, required=True)
    apply_parser.add_argument("--project-root", type=Path, required=True)
    deactivate_parser = commands.add_parser("deactivate")
    deactivate_parser.add_argument("--project-root", type=Path, required=True)
    deactivate_parser.add_argument("--host", default="codex")
    deactivate_parser.add_argument("--capability", default="testing")
    args = parser.parse_args()

    if args.command == "codex-plan":
        value = build_plan(args.project_root, args.capability)
        _write_new(args.output, value)
    elif args.command == "decide":
        plan = _load(args.plan)
        _write_new(args.output, make_decision(plan, args.decision))
    elif args.command == "apply":
        print(json.dumps(apply(_load(args.plan), _load(args.decision), args.project_root)))
    else:
        print(json.dumps(deactivate(args.project_root, args.host, args.capability)))


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
