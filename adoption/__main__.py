"""Read-only command line interface for Existing Project discovery."""

import argparse
import json
from pathlib import Path

from .core import discover


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("discover", help="inspect a project without changing it")
    command.add_argument("project_root", type=Path)
    command.add_argument("--output", type=Path, help="write the report outside the target project")
    args = parser.parse_args()
    report = discover(args.project_root)
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        destination = args.output.resolve()
        if destination.is_relative_to(args.project_root.resolve()):
            parser.error("--output must be outside the target project")
        with destination.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
