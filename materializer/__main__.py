"""Preview or explicitly apply an Assembly Plan."""

import argparse
from pathlib import Path

import yaml

from composer.core import load_yaml
from .core import materialize


def render_summary(result):
    labels = {
        "copied": "Would copy" if result["mode"] == "dry_run" else "Copied",
        "unchanged": "Unchanged", "reference_only": "Reference only",
        "skipped": "Skipped", "unsupported": "Unsupported", "conflict": "Conflicts",
    }
    lines = []
    for state, label in labels.items():
        selected = [item for item in result["items"] if item["result"] == state]
        lines.append(f"{label}:")
        lines.extend(f"- {item.get('destination', item['source'])}" for item in selected)
        if not selected:
            lines.append("- none")
        for item in selected:
            if "reason" in item:
                lines.append(f"  {item['reason']}")
    if result["mode"] == "dry_run":
        lines.append("No files were changed.")
    elif result["manifest_path"]:
        lines.append(f"Manifest: {result['manifest_path']}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assembly_plan", type=Path)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--global-root", type=Path)
    parser.add_argument("--apply", action="store_true", help="Write planned files and a manifest.")
    args = parser.parse_args()
    plan = load_yaml(args.assembly_plan)
    result = materialize(plan, args.project_root, args.global_root, args.apply,
                         assembly_source=args.assembly_plan.as_posix())
    print(render_summary(result))
    if any(item["result"] == "conflict" for item in result["items"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
