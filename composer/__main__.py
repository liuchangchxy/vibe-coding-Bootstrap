"""Validation-only CLI: read declared inputs and write one recommendation file."""

import argparse
from pathlib import Path

import yaml

from .core import compose, load_yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("context", type=Path)
    parser.add_argument("--output", type=Path, default=Path("recommendation.yaml"))
    args = parser.parse_args()
    plan = compose(load_yaml(args.context))
    # Exclusive creation prevents an output argument from overwriting project inputs.
    with args.output.open("x", encoding="utf-8") as stream:
        yaml.safe_dump(plan, stream, sort_keys=False, allow_unicode=True)
    print(f"Recommendation written to {args.output}; human confirmation required.")


if __name__ == "__main__":
    main()
