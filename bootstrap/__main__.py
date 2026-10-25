"""Command line interface for the unified Bootstrap loop."""

import argparse
import json
from pathlib import Path
import sys

from . import __version__
from .core import apply_plan, build_plan, decide, inspect_project, load_json, render_summary, validate_output_path, write_json
from .guided import build_context, inspect_setup
from .experience import harvest_experience, harvest_successful_event, review_experience, verify_evidence
from .lifecycle import (
    apply_uninstall, build_uninstall_plan, decide_uninstall, deploy,
    get_status, render_status_summary, render_uninstall_summary,
    uninstall, upgrade
)
from .recovery import rollback


def _parser():
    parser = argparse.ArgumentParser(prog="python -m bootstrap", description="Inspect, plan, confirm once, and apply a project Bootstrap plan.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    setup = commands.add_parser("setup", help="Guided project setup without authoring VCBS YAML")
    setup_steps = setup.add_subparsers(dest="setup_action", required=True)
    setup_inspect = setup_steps.add_parser("inspect", help="Detect project assets, Hosts, and selectable Catalog components")
    setup_inspect.add_argument("--project-root", default=".")
    setup_inspect.add_argument("--host", default="codex", help="Host choice: codex, claude, antigravity, none, or comma-separated")
    setup_inspect.add_argument("--json", action="store_true")
    setup_inspect.add_argument("--vcbs-home", help="User-owned VCBS Home directory (default: ~/.vcbs)")
    setup_inspect.add_argument("--recommendation-input", help="Write bounded recommendation input fact package to file")
    setup_plan = setup_steps.add_parser("plan", help="Create a reviewable setup plan from guided options")
    setup_plan.add_argument("--project-root", default=".")
    setup_plan.add_argument("--host", default="codex")
    setup_plan.add_argument("--goal")
    setup_plan.add_argument("--project-type", default="software project")
    setup_plan.add_argument("--lifecycle", choices=("prototype", "short_term", "long_lived", "production"))
    setup_plan.add_argument("--technology", action="append", default=[])
    setup_plan.add_argument("--platform", default="local")
    setup_plan.add_argument("--collaboration", choices=("solo", "team", "open_source"))
    setup_plan.add_argument("--constraint", action="append", default=[])
    setup_plan.add_argument("--capability", action="append", default=[], help="Explicitly select Catalog capability IDs; repeat or comma-separate")
    setup_plan.add_argument("--exclude-capability", action="append", default=[], help="Decline optional Catalog capability IDs")
    setup_plan.add_argument("--exclude-component", action="append", default=[], help="Decline individual registered component IDs")
    setup_plan.add_argument("--scope", choices=("project", "global", "both"), default="project")
    setup_plan.add_argument("--global-root")
    setup_plan.add_argument("--semantic-result", help="Host-generated review result for existing project assets")
    setup_plan.add_argument("--recommendation", help="Host-generated structured recommendation file path or JSON string")
    setup_plan.add_argument("--vcbs-home", help="User-owned VCBS Home directory (default: ~/.vcbs)")
    setup_plan.add_argument("--output", required=True, help="Write exact plan outside the consumer project")
    setup_plan.add_argument("--json", action="store_true")
    setup_approve = setup_steps.add_parser("approve", help="Record explicit approval of the exact displayed plan")
    setup_approve.add_argument("--plan", required=True)
    setup_approve.add_argument("--output", help="Decision path; defaults to <plan>.decision.json")
    setup_apply = setup_steps.add_parser("apply", help="Apply only a plan with its matching approval artifact")
    setup_apply.add_argument("--project-root", default=".")
    setup_apply.add_argument("--global-root")
    setup_apply.add_argument("--plan", required=True)
    setup_apply.add_argument("--decision", required=False, default=None, help="Decision path (optional if using --confirm)")
    setup_apply.add_argument("--confirm", "--yes", action="store_true", dest="confirm", help="Confirm and apply the plan in a single step")
    setup_apply.add_argument("--vcbs-home", help="User-owned VCBS Home directory (default: ~/.vcbs)")

    # Standard Lifecycle Commands (Phase 12)
    status_cmd = commands.add_parser("status", help="Query read-only VCBS status and managed components inventory")
    status_cmd.add_argument("--project-root", default=".", help="Target project root directory")
    status_cmd.add_argument("--global-root", help="Global root directory if inspecting global scope")
    status_cmd.add_argument("--host", default="codex", help="Target AI host adapter (codex, claude, claude_code, antigravity, none)")
    status_cmd.add_argument("--json", action="store_true", help="Output status as structured JSON")

    deploy_cmd = commands.add_parser("deploy", help="Standardized VCBS initial deployment or reconciliation")
    deploy_cmd.add_argument("--project-root", default=".", help="Target project root directory")
    deploy_cmd.add_argument("--host", default="codex", help="Target AI host adapter (codex, claude, claude_code, antigravity, none)")
    deploy_cmd.add_argument("--context", help="Project Context file path (required for Compose route)")
    deploy_cmd.add_argument("--semantic-result", help="Semantic Result file path (required for Adoption route)")
    deploy_cmd.add_argument("--global-root", help="Global root directory")
    deploy_cmd.add_argument("--output", help="Save generated plan to output path")
    deploy_cmd.add_argument("--plan", help="Previously generated and approved plan file path")
    deploy_cmd.add_argument("--decision", help="Decision file path (if pre-decided)")
    deploy_cmd.add_argument("--apply", action="store_true", help="Approve and apply the deployment")
    deploy_cmd.add_argument("--json", action="store_true", help="Output result as JSON")

    upgrade_cmd = commands.add_parser("upgrade", help="Standardized VCBS safe upgrade")
    upgrade_cmd.add_argument("--project-root", default=".", help="Target project root directory")
    upgrade_cmd.add_argument("--host", default="all", help="Hosts to upgrade (default: all installed hosts; codex, claude, antigravity, none)")
    upgrade_cmd.add_argument("--context", help="Optional updated Project Context file path")
    upgrade_cmd.add_argument("--global-root", help="Global root directory")
    upgrade_cmd.add_argument("--output", help="Save generated plan to output path")
    upgrade_cmd.add_argument("--plan", help="Previously generated and approved upgrade plan file path")
    upgrade_cmd.add_argument("--decision", help="Decision file path (if pre-decided)")
    upgrade_cmd.add_argument("--apply", action="store_true", help="Approve and apply the upgrade")
    upgrade_cmd.add_argument("--json", action="store_true", help="Output result as JSON")

    uninstall_cmd = commands.add_parser("uninstall", help="Standardized VCBS safe uninstall")
    uninstall_cmd.add_argument("--project-root", default=".", help="Target project root directory")
    uninstall_cmd.add_argument("--component", "--components", dest="component", help="Optional specific component ID(s) to uninstall (comma-separated)")
    uninstall_cmd.add_argument("--global-root", help="Global root directory")
    uninstall_cmd.add_argument("--output", help="Save generated uninstall plan to output path")
    uninstall_cmd.add_argument("--plan", help="Previously generated and approved uninstall plan file path")
    uninstall_cmd.add_argument("--decision", help="Decision file path (if pre-decided)")
    uninstall_cmd.add_argument("--apply", action="store_true", help="Approve and apply the uninstall")
    uninstall_cmd.add_argument("--json", action="store_true", help="Output result as JSON")

    # Diagnostic & Specialized Commands
    inspect = commands.add_parser("inspect", help="Read target facts and select compose or adoption")
    inspect.add_argument("--project-root", required=True)
    inspect.add_argument("--host", default="codex", help="Target AI host adapter (codex, claude, claude_code, antigravity, none)")
    inspect.add_argument("--output")
    plan = commands.add_parser("plan", help="Build a revision-bound unified plan")
    plan.add_argument("--project-root", required=True)
    plan.add_argument("--host", default="codex", help="Target AI host adapter (codex, claude, claude_code, antigravity, none)")
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
    harvest_cmd.add_argument("--host", default="codex", help="Target AI host adapter (codex, claude, claude_code, antigravity, none)")
    harvest_cmd.add_argument("--output")
    harvest_cmd.add_argument("--vcbs-home", help="User-owned VCBS Home directory (default: ~/.vcbs)")
    event_harvest_cmd = commands.add_parser(
        "harvest-event", help="Create an unverified candidate from a successful exact-head merge event"
    )
    event_harvest_cmd.add_argument("--project-root", required=True)
    event_harvest_cmd.add_argument("--event", required=True, help="Normalized VCCP/GitHub event evidence (JSON or YAML)")
    event_harvest_cmd.add_argument("--observation", required=True, help="Host-produced bounded Experience observation")
    event_harvest_cmd.add_argument("--host", default="codex")
    event_harvest_cmd.add_argument("--vcbs-home", help="User-owned VCBS Home directory (default: ~/.vcbs)")
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
    review_cmd.add_argument("--vcbs-home", help="User-owned VCBS Home directory (default: ~/.vcbs)")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if getattr(args, "project_root", None) is not None:
            args.project_root = str(Path(args.project_root).resolve())
        if args.command == "setup" and args.setup_action == "inspect":
            value = inspect_setup(args.project_root, args.host, vcbs_home=getattr(args, "vcbs_home", None))
            if getattr(args, "recommendation_input", None):
                validate_output_path(args.recommendation_input, args.project_root)
                write_json(args.recommendation_input, value["recommendation_input"], args.project_root)
            if args.json:
                print(json.dumps(value, indent=2, ensure_ascii=False))
            else:
                print(f"Project: {value['project']['name']} ({', '.join(value['project']['technologies']) or 'technology not detected'})")
                print(f"Route: {value['route']} | Hosts selected: {', '.join(value['host_selection'])}")
                print("Detected host commands: " + ", ".join(f"{h}={'available' if x['available'] else 'not detected'}" for h, x in value['available_hosts'].items()))
                print("Existing project assets: " + (", ".join(a['path'] for a in value['existing_assets']) or "none"))
                if value["recommendations"] is not None:
                    print("Explainable shortlist:")
                    for item in value["recommendations"]:
                        print(f"  {item['capability_id']} ({item['confidence']}): {item['reason']}")
                print("Selectable Catalog capabilities:")
                for option in value["capability_options"]:
                    statuses = ", ".join(f"{item['id']}={item['status']}" for item in option["components"])
                    print(f"  {option['capability_id']}: {option['purpose']} [{statuses or 'no registered component'}]")
                if getattr(args, "recommendation_input", None):
                    print(f"Saved recommendation input fact package to: {Path(args.recommendation_input).resolve()}")
                if value["semantic_review_required"]:
                    print("Next: let the selected Host review semantic_package and save its result; then run setup plan --semantic-result <file>.")
                else:
                    print("Next: run setup plan with your goal, capability choices, and output path.")
                print(value["consumption_note"])
        elif args.command == "setup" and args.setup_action == "plan":
            inspection = inspect_project(args.project_root, args.host)
            selected = [cid.strip() for entry in args.capability for cid in entry.split(",") if cid.strip()]
            declined = [cid.strip() for entry in args.exclude_capability for cid in entry.split(",") if cid.strip()]
            declined_components = [cid.strip() for entry in args.exclude_component for cid in entry.split(",") if cid.strip()]
            rec_arg = getattr(args, "recommendation", None)
            vcbs_home = getattr(args, "vcbs_home", None)
            if rec_arg:
                plan = build_plan(args.project_root, host=args.host, global_root=args.global_root,
                                  scope=args.scope, recommendation=rec_arg, vcbs_home=vcbs_home)
            elif inspection["route"] == "adoption":
                if not args.semantic_result:
                    raise ValueError("HOST_SEMANTIC_REVIEW_REQUIRED: setup inspect provides the bounded review package; pass the Host-generated --semantic-result")
                plan = build_plan(args.project_root, host=args.host, semantic_result_path=args.semantic_result,
                    global_root=args.global_root, context_value=None,
                    selected_capability_ids=selected or None, excluded_capability_ids=declined,
                    excluded_component_ids=declined_components, scope=args.scope, vcbs_home=vcbs_home)
            else:
                context_value = build_context(args.project_root, goal=args.goal, project_type=args.project_type,
                    lifecycle=args.lifecycle, technologies=args.technology or None, platform=args.platform,
                    collaboration=args.collaboration, constraints=args.constraint)
                plan = build_plan(args.project_root, host=args.host, semantic_result_path=args.semantic_result,
                    global_root=args.global_root, context_value=context_value,
                    selected_capability_ids=selected or None, excluded_capability_ids=declined,
                    excluded_component_ids=declined_components, scope=args.scope, vcbs_home=vcbs_home)
            validate_output_path(args.output, args.project_root)
            write_json(args.output, plan, args.project_root)
            if args.json:
                print(json.dumps({"plan": plan, "plan_path": str(Path(args.output).resolve())}, indent=2, ensure_ascii=False))
            else:
                summary = render_summary(plan)
                print(summary)
                print(f"Plan: {Path(args.output).resolve()}")
                if plan.get("underlying_plans", {}).get("install_nothing"):
                    print("Recommendation: Install nothing. Plan contains no mutations.")
                else:
                    print("Next: review the plan, then run setup apply --plan <plan> --confirm.")
        elif args.command == "setup" and args.setup_action == "approve":
            plan = load_json(args.plan)
            value = decide(plan, "approve")
            output = args.output or f"{Path(args.plan).resolve()}.decision.json"
            validate_output_path(output, plan["target"]["path"])
            write_json(output, value, plan["target"]["path"])
            print(f"Approved exact plan {value['plan_digest'][:12]} for {plan['target']['commit_sha'][:7]}\nDecision: {Path(output).resolve()}")
        elif args.command == "setup" and args.setup_action == "apply":
            plan = load_json(args.plan)
            if getattr(args, "confirm", False):
                decision = decide(plan, "approve")
            elif getattr(args, "decision", None):
                decision = load_json(args.decision)
            else:
                raise ValueError("DECISION_REQUIRED: pass --confirm / --yes to approve and apply, or provide an approved --decision artifact")
            value = apply_plan(plan, decision, args.project_root, args.global_root)
            print(json.dumps(value, indent=2, ensure_ascii=False))
        elif args.command == "status":
            value = get_status(args.project_root, host=args.host, global_root=args.global_root)
            if getattr(args, "json", False):
                print(json.dumps(value, indent=2, ensure_ascii=False))
            else:
                print(render_status_summary(value))
        elif args.command == "deploy":
            value = deploy(args.project_root, host=args.host, context_path=args.context,
                           semantic_result_path=args.semantic_result, global_root=args.global_root,
                           apply=args.apply, decision=args.decision, plan=args.plan)
            if args.output and "plan" in value:
                write_json(args.output, value["plan"], args.project_root)
            if getattr(args, "json", False):
                print(json.dumps(value, indent=2, ensure_ascii=False))
            else:
                if value.get("status") == "preview":
                    print(value["summary"])
                    print("\n[Preview mode] To execute, save the plan (--output <plan.json>), create an approved decision:")
                    print("  python -m bootstrap decide <plan.json> --decision approve --output <decision.json>")
                    print("and run with:")
                    print("  python -m bootstrap deploy --plan <plan.json> --decision <decision.json> --apply")
                elif value.get("status") == "no_changes":
                    print(value.get("message", "No changes needed."))
                else:
                    print(json.dumps(value, indent=2, ensure_ascii=False))
        elif args.command == "upgrade":
            value = upgrade(args.project_root, host=args.host, context_path=args.context,
                            global_root=args.global_root, apply=args.apply, decision=args.decision,
                            plan=args.plan)
            if args.output and "plan" in value:
                write_json(args.output, value["plan"], args.project_root)
            if getattr(args, "json", False):
                print(json.dumps(value, indent=2, ensure_ascii=False))
            else:
                if value.get("status") == "preview":
                    print(value["summary"])
                    print("\n[Preview mode] To execute, save the plan (--output <plan.json>), create an approved decision:")
                    print("  python -m bootstrap decide <plan.json> --decision approve --output <decision.json>")
                    print("and run with:")
                    print("  python -m bootstrap upgrade --plan <plan.json> --decision <decision.json> --apply")
                elif value.get("status") == "no_changes":
                    print(value.get("message", "All components are already up to date."))
                else:
                    print(json.dumps(value, indent=2, ensure_ascii=False))
        elif args.command == "uninstall":
            value = uninstall(args.project_root, component_id=args.component,
                              global_root=args.global_root, apply=args.apply, decision=args.decision,
                              plan=args.plan)
            if args.output and "plan" in value:
                write_json(args.output, value["plan"], args.project_root)
            if getattr(args, "json", False):
                print(json.dumps(value, indent=2, ensure_ascii=False))
            else:
                if value.get("status") == "preview":
                    print(value["summary"])
                    print("\n[Preview mode] To execute, save the plan (--output <plan.json>), create an approved decision:")
                    print("  python -m bootstrap decide <plan.json> --decision approve --output <decision.json>")
                    print("and run with:")
                    print("  python -m bootstrap uninstall --plan <plan.json> --decision <decision.json> --apply")
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
            if plan.get("operation") == "uninstall":
                value = decide_uninstall(plan, args.decision)
            else:
                value = decide(plan, args.decision)
            target_path = plan.get("project_root") or plan["target"]["path"]
            write_json(args.output, value, target_path)
            print(f"Decision: {value['decision']} ({value['plan_digest'][:12]})")
        elif args.command == "harvest":
            value = harvest_experience(args.project_root, args.observation, args.host, args.output, vcbs_home=getattr(args, "vcbs_home", None))
            print(f"Candidate: {value['id']} ({value['title']})")
        elif args.command == "harvest-event":
            value = harvest_successful_event(args.project_root, args.event, args.observation, args.host, vcbs_home=getattr(args, "vcbs_home", None))
            candidate = value["candidate"]
            print(json.dumps({"status": "created" if value["created"] else "duplicate",
                              "reason": value["reason"], "experience_id": candidate["id"],
                              "candidate_status": candidate["status"],
                              "event_hook_status": value["event_hook_status"],
                              "note": "Caller-supplied event facts and AI inference remain unverified."},
                             indent=2, ensure_ascii=False))
        elif args.command == "verify-evidence":
            idx = "all" if args.all else args.evidence_index
            value = verify_evidence(args.experience, idx, args.verifier, args.method, args.status)
            print(f"Verified evidence for {value['id']}: index {idx} -> {args.status}")
        elif args.command == "review-experience":
            value = review_experience(args.experience, args.decision, args.reviewer, args.notes, vcbs_home=getattr(args, "vcbs_home", None))
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
