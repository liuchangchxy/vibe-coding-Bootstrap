"""Command line interface for the unified Bootstrap loop."""

import argparse
import json
from pathlib import Path
import sys

from .core import apply_plan, build_plan, decide, inspect_project, load_json, render_summary, validate_output_path, write_json
from .experience import harvest_experience, review_experience, verify_evidence
from .lifecycle import (
    apply_uninstall, build_uninstall_plan, decide_uninstall, deploy,
    get_status, render_status_summary, render_uninstall_summary,
    uninstall, upgrade
)
from .recovery import rollback


def _parser():
    parser = argparse.ArgumentParser(prog="python -m bootstrap", description="Inspect, plan, confirm once, and apply a project Bootstrap plan.")
    commands = parser.add_subparsers(dest="command", required=True)

    # Standard Lifecycle Commands (Phase 12)
    status_cmd = commands.add_parser("status", help="Query read-only VCBS status and managed components inventory")
    status_cmd.add_argument("--project-root", required=True, help="Target project root directory")
    status_cmd.add_argument("--global-root", help="Global root directory if inspecting global scope")
    status_cmd.add_argument("--host", choices=("codex", "none"), default="codex")
    status_cmd.add_argument("--json", action="store_true", help="Output status as structured JSON")

    deploy_cmd = commands.add_parser("deploy", help="Standardized VCBS initial deployment or reconciliation")
    deploy_cmd.add_argument("--project-root", required=True, help="Target project root directory")
    deploy_cmd.add_argument("--host", choices=("codex", "none"), default="codex")
    deploy_cmd.add_argument("--context", help="Project Context file path (required for Compose route)")
    deploy_cmd.add_argument("--semantic-result", help="Semantic Result file path (required for Adoption route)")
    deploy_cmd.add_argument("--global-root", help="Global root directory")
    deploy_cmd.add_argument("--output", help="Save generated plan to output path")
    deploy_cmd.add_argument("--decision", help="Decision file path (if pre-decided)")
    deploy_cmd.add_argument("--apply", action="store_true", help="Approve and apply the deployment")
    deploy_cmd.add_argument("--json", action="store_true", help="Output result as JSON")

    upgrade_cmd = commands.add_parser("upgrade", help="Standardized VCBS safe upgrade")
    upgrade_cmd.add_argument("--project-root", required=True, help="Target project root directory")
    upgrade_cmd.add_argument("--host", choices=("codex", "none"), default="codex")
    upgrade_cmd.add_argument("--context", help="Optional updated Project Context file path")
    upgrade_cmd.add_argument("--global-root", help="Global root directory")
    upgrade_cmd.add_argument("--output", help="Save generated plan to output path")
    upgrade_cmd.add_argument("--decision", help="Decision file path (if pre-decided)")
    upgrade_cmd.add_argument("--apply", action="store_true", help="Approve and apply the upgrade")
    upgrade_cmd.add_argument("--json", action="store_true", help="Output result as JSON")

    uninstall_cmd = commands.add_parser("uninstall", help="Standardized VCBS safe uninstall")
    uninstall_cmd.add_argument("--project-root", required=True, help="Target project root directory")
    uninstall_cmd.add_argument("--component", help="Optional specific component ID to uninstall")
    uninstall_cmd.add_argument("--global-root", help="Global root directory")
    uninstall_cmd.add_argument("--output", help="Save generated uninstall plan to output path")
    uninstall_cmd.add_argument("--decision", help="Decision file path (if pre-decided)")
    uninstall_cmd.add_argument("--apply", action="store_true", help="Approve and apply the uninstall")
    uninstall_cmd.add_argument("--json", action="store_true", help="Output result as JSON")

    # Diagnostic & Specialized Commands
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
    rollback_cmd = commands.add_parser("rollback", help="Preview or safely roll back one local Bootstrap deployment")
    rollback_cmd.add_argument("--project-root", required=True)
    rollback_cmd.add_argument("--deployment", required=True)
    rollback_cmd.add_argument("--apply", action="store_true", help="Apply the conflict-checked rollback")
    harvest_cmd = commands.add_parser("harvest", help="Extract candidate Experience from project observation")
    harvest_cmd.add_argument("--project-root", required=True)
    harvest_cmd.add_argument("--observation", required=True)
    harvest_cmd.add_argument("--host", choices=("codex", "none"), default="codex")
    harvest_cmd.add_argument("--output")
    verify_cmd = commands.add_parser("verify-evidence", help="Verify candidate Experience evidence prior to approval")
    verify_cmd.add_argument("experience", help="Candidate experience path or ID")
    verify_cmd.add_argument("--verifier", required=True, help="Named verifier")
    verify_cmd.add_argument("--method", required=True, help="Verification method description")
    verify_cmd.add_argument("--evidence-index", type=int, default=0, help="Index of evidence item to verify (default: 0)")
    verify_cmd.add_argument("--all", action="store_true", help="Verify all evidence items")
    verify_cmd.add_argument("--status", choices=("checked", "independently_confirmed"), default="checked")
    review_cmd = commands.add_parser("review-experience", help="Review and approve/reject candidate Experience for personal reuse")
    review_cmd.add_argument("experience", help="Candidate experience path or ID")
    review_cmd.add_argument("--decision", choices=("approve", "reject"), required=True)
    review_cmd.add_argument("--reviewer", help="Named reviewer (required for approve)")
    review_cmd.add_argument("--notes")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command == "status":
            value = get_status(args.project_root, host=args.host, global_root=args.global_root)
            if getattr(args, "json", False):
                print(json.dumps(value, indent=2, ensure_ascii=False))
            else:
                print(render_status_summary(value))
        elif args.command == "deploy":
            value = deploy(args.project_root, host=args.host, context_path=args.context,
                           semantic_result_path=args.semantic_result, global_root=args.global_root,
                           apply=args.apply, decision=args.decision)
            if args.output and "plan" in value:
                write_json(args.output, value["plan"], args.project_root)
            if getattr(args, "json", False):
                print(json.dumps(value, indent=2, ensure_ascii=False))
            else:
                if value.get("status") == "preview":
                    print(value["summary"])
                    print("\n[Preview mode] Run with --apply to approve and execute deployment.")
                elif value.get("status") == "no_changes":
                    print(value.get("message", "No changes needed."))
                else:
                    print(json.dumps(value, indent=2, ensure_ascii=False))
        elif args.command == "upgrade":
            value = upgrade(args.project_root, host=args.host, context_path=args.context,
                            global_root=args.global_root, apply=args.apply, decision=args.decision)
            if args.output and "plan" in value:
                write_json(args.output, value["plan"], args.project_root)
            if getattr(args, "json", False):
                print(json.dumps(value, indent=2, ensure_ascii=False))
            else:
                if value.get("status") == "preview":
                    print(value["summary"])
                    print("\n[Preview mode] Run with --apply to approve and execute upgrade.")
                elif value.get("status") == "no_changes":
                    print(value.get("message", "All components are already up to date."))
                else:
                    print(json.dumps(value, indent=2, ensure_ascii=False))
        elif args.command == "uninstall":
            value = uninstall(args.project_root, component_id=args.component,
                              global_root=args.global_root, apply=args.apply, decision=args.decision)
            if args.output and "plan" in value:
                clean_plan = {k: v for k, v in value["plan"].items() if not k.startswith("_")}
                write_json(args.output, clean_plan, args.project_root)
            if getattr(args, "json", False):
                print(json.dumps(value, indent=2, ensure_ascii=False))
            else:
                if value.get("status") == "preview":
                    print(value["summary"])
                    print("\n[Preview mode] Run with --apply to approve and execute uninstall.")
                elif value.get("status") == "no_changes":
                    print(value.get("message", "No VCBS-managed components to uninstall."))
                else:
                    print(json.dumps(value, indent=2, ensure_ascii=False))
        elif args.command == "rollback":
            value = rollback(args.project_root, args.deployment, args.apply)
            print(json.dumps(value, indent=2, ensure_ascii=False))
        elif args.command == "inspect":
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
        elif args.command == "harvest":
            value = harvest_experience(args.project_root, args.observation, args.host, args.output)
            print(f"Candidate: {value['id']} ({value['title']})")
        elif args.command == "verify-evidence":
            idx = "all" if args.all else args.evidence_index
            value = verify_evidence(args.experience, idx, args.verifier, args.method, args.status)
            print(f"Verified evidence for {value['id']}: index {idx} -> {args.status}")
        elif args.command == "review-experience":
            value = review_experience(args.experience, args.decision, args.reviewer, args.notes)
            print(f"Experience {value['id']}: {value['status']} (review: {value['review']['review_status']})")
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
