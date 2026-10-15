"""Read-only command line interface for Existing Project discovery."""

import argparse
import json
from pathlib import Path

from .core import discover
from .semantic import build_semantic_input, load_json, validate_semantic_result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("discover", help="inspect a project without changing it")
    command.add_argument("project_root", type=Path)
    command.add_argument("--output", type=Path, help="write the report outside the target project")
    semantic_command = subparsers.add_parser("semantic-package", help="prepare a bounded Host AI review input")
    semantic_command.add_argument("project_root", type=Path)
    semantic_command.add_argument("--output", type=Path, required=True)
    validate_command = subparsers.add_parser("validate-semantic-review", help="validate a Host AI result against its input package")
    validate_command.add_argument("result", type=Path)
    validate_command.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "discover":
        value = discover(args.project_root)
        if args.output:
            _write_new_json(args.output, args.project_root, value, parser)
        else:
            _print_json(value)
    elif args.command == "semantic-package":
        package = build_semantic_input(args.project_root)
        _write_new_json(args.output, args.project_root, package, parser)
    else:
        value = validate_semantic_result(load_json(args.result), load_json(args.input))
        print(f"Valid semantic review for {value['project_name']}: {len(value['semantic_coverage'])} Catalog Capabilities.")


def _print_json(value):
    print(json.dumps(value, indent=2, ensure_ascii=False))


def _write_new_json(path, project_root, value, parser):
    destination = path.resolve()
    if destination.is_relative_to(project_root.resolve()):
        parser.error("--output must be outside the target project")
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
