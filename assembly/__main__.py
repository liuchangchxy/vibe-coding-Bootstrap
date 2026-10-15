"""Create a human-readable Assembly Plan; never install or modify a project."""

import argparse
from pathlib import Path

import yaml

from .core import assemble
from composer.core import load_yaml


def render_summary(plan):
    lines = ["Selected Capabilities:"]
    lines.extend(f"- {cid}" for cid in plan["selected_capabilities"])
    if not plan["selected_capabilities"]:
        lines.append("- none")
    lines.append("\nResolved Components:")
    for item in plan["components"]:
        lines.append(f"- [{item['action']}] {item['source_capability']}/{item['component_id']} ({item['scope']})")
        lines.append(f"  Source: {item['source']} | Delivery: {item['delivery']}")
        lines.append(f"  Reason: {item['reason']}")
    if not plan["components"]:
        lines.append("- none")
    lines.append("\nUnresolved:")
    lines.extend(f"- {issue['reason']}" for issue in plan["unresolved_conflicts"])
    if not plan["unresolved_conflicts"]:
        lines.append("- none")
    lines.extend(["\nWarnings:", *[f"- {warning}" for warning in plan["warnings"]]])
    if not plan["warnings"]:
        lines.append("- none")
    lines.append("\nHuman confirmation required.")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recommendation", type=Path)
    parser.add_argument("--output", type=Path, default=Path("assembly-plan.yaml"))
    parser.add_argument("--preview", action="store_true", help="Print the plan instead of writing a file.")
    args = parser.parse_args()
    recommendation = load_yaml(args.recommendation)
    plan = assemble(recommendation, recommendation_source=args.recommendation.as_posix())
    rendered = yaml.safe_dump(plan, sort_keys=False, allow_unicode=True)
    if args.preview:
        print(render_summary(plan))
        return
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(rendered)
    print(f"Assembly Plan written to {args.output}; human confirmation required.")


if __name__ == "__main__":
    main()
