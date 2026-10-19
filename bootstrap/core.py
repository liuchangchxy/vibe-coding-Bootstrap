"""Thin orchestration over the validated Bootstrap Phase 1-10 modules."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

import yaml

from adoption.core import discover
from adoption.planning import build_adoption_plan
from adoption.revision import bind_target_revision
from adoption.semantic import build_semantic_input, validate_semantic_result
from activation import core as activation
from assembly.core import assemble
from composer.core import ROOT, compose, load_yaml, validate
from materializer.core import materialize
from bootstrap.recovery import prepare_deployment, update_record, update_record_after_failure


class ProductLoopError(ValueError):
    """A concise, user-facing product-loop error."""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _plan_digest(plan):
    return _sha(_canonical({key: value for key, value in plan.items() if key not in {"generated_at", "plan_digest"}}))


def _profile_digest(root=ROOT):
    return _sha((Path(root) / "profiles/personal.yaml").read_bytes())


def _inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def _owned_state(project):
    """Return whether all environment signals are exact Bootstrap-owned assets."""
    project = Path(project)
    managed = set()
    manifest = project / ".vibe-coding-bootstrap/manifest.yaml"
    if manifest.is_file():
        try:
            data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("format_version") == 1:
                for item in data.get("items", []):
                    if item.get("scope") != "project" or item.get("state") != "materialized":
                        continue
                    dest = item.get("destination", "")
                    try:
                        p = Path(dest)
                        rel = p.resolve().relative_to(project.resolve()).as_posix() if p.is_absolute() else p.as_posix()
                    except ValueError:
                        rel = dest
                    path = project / rel
                    if (rel and path.is_file() and item.get("sha256")
                            and _sha(path.read_bytes()) == item["sha256"]):
                        managed.add(rel)
        except (OSError, yaml.YAMLError):
            pass
    activation_manifest = project / activation.MANIFEST_PATH
    if activation_manifest.is_file():
        try:
            data = json.loads(activation_manifest.read_text(encoding="utf-8"))
            if data.get("format_version") == 1 and data.get("host") == "codex":
                for item in data.get("artifacts", []):
                    dest = item.get("destination", "")
                    try:
                        p = Path(dest)
                        rel = p.resolve().relative_to(project.resolve()).as_posix() if p.is_absolute() else p.as_posix()
                    except ValueError:
                        rel = dest
                    path = project / rel
                    if (rel and path.is_file() and item.get("sha256")
                            and _sha(path.read_bytes()) == item["sha256"]):
                        managed.add(rel)
        except (OSError, json.JSONDecodeError):
            pass
    signals = []
    for name in ("AGENTS.md", "CLAUDE.md", ".cursorrules"):
        if (project / name).is_file():
            signals.append(name)
    for base in (".agents/skills", ".claude/skills", ".opencode/skills"):
        directory = project / base
        if directory.is_dir():
            signals.extend(path.relative_to(project).as_posix() for path in directory.rglob("SKILL.md") if path.is_file())
    for name in (".github/workflows", ".cursor/rules", ".github/vccp"):
        directory = project / name
        if directory.is_dir():
            signals.extend(path.relative_to(project).as_posix() for path in directory.rglob("*") if path.is_file())
    unmanaged = [path for path in signals if path not in managed]
    return not unmanaged, signals, unmanaged


def _route(project, report):
    managed_only, signals, unmanaged = _owned_state(project)
    # Discovery intentionally inventories README and project documents; they are not route evidence.
    environment_assets = [asset for asset in report["discovered_assets"]
                          if asset["asset_type"] in {"agent_instruction", "skill", "workflow", "prompt", "mcp_configuration", "test_policy", "development_rule"}]
    detected = bool(environment_assets)
    if detected and managed_only and signals:
        return "compose", signals, []
    return ("adoption" if detected else "compose"), signals, unmanaged


def inspect_project(project_root, host="codex", root=ROOT):
    if host not in {"codex", "none"}:
        raise ProductLoopError("UNSUPPORTED_HOST: supported hosts are codex and none")
    project = Path(project_root).resolve(strict=True)
    if not project.is_dir():
        raise ProductLoopError("INVALID_PROJECT_ROOT: target must be a directory")
    target = bind_target_revision(project)
    target["path"] = str(project)
    report = discover(project, root)
    route, signals, unmanaged = _route(project, report)
    value = {
        "format_version": 1, "target": target, "host": host, "route": route,
        "recognized_assets": [asset for asset in report["discovered_assets"] if asset["asset_type"] != "documentation"],
        "existing_environment": {"detected": bool(signals), "assets": signals, "unmanaged_assets": unmanaged},
        "host_input_required": route == "compose" or route == "adoption",
        "next_action": "provide_project_context" if route == "compose" else "host_semantic_review",
    }
    if route == "adoption":
        value["semantic_package"] = build_semantic_input(project, root)
        value["host_input_required"] = True
    return value


def _activation_plan(project, host, capabilities, root):
    if host != "codex" or "testing" not in capabilities:
        return []
    return [activation.build_plan(project, "testing", "codex", root)]


def _materializer_rows(assembly_plan, project, global_root, root):
    result = materialize(assembly_plan, project, global_root, apply=False, root=root)
    return result, [dict(item) for item in result["items"]]


def _unified_changes(items, scope):
    result = []
    for item in items:
        if item["scope"] != scope:
            continue
        result.append({key: item[key] for key in item if key in {
            "component_id", "source_capability", "scope", "delivery", "destination", "result", "reason", "sha256",
            "old_sha256", "old_version", "new_version", "delta"
        }})
    return result


def build_plan(project_root, host="codex", context_path=None, semantic_result_path=None,
               global_root=None, root=ROOT):
    current = inspect_project(project_root, host, root)
    project = Path(project_root).resolve(strict=True)
    route = current["route"]
    context = None
    recommendation = assembly_plan = adoption_plan = None
    warnings, unresolved, capabilities = [], [], []
    materialized = {"items": []}
    if route == "compose":
        if not context_path:
            raise ProductLoopError("PROJECT_CONTEXT_REQUIRED: provide an explicit Project Context file")
        context = load_yaml(context_path)
        recommendation = compose(context, root)
        assembly_plan = assemble(recommendation, root)
        capabilities = assembly_plan["selected_capabilities"]
        materialized, rows = _materializer_rows(assembly_plan, project, global_root, root)
        warnings.extend(assembly_plan["warnings"])
        unresolved.extend(assembly_plan["unresolved_conflicts"])
        if any(item["result"] == "skipped" and "explicit --global-root" in item.get("reason", "")
               for item in rows if item["scope"] == "global" and item["delivery"] == "copy"):
            raise ProductLoopError("GLOBAL_ROOT_REQUIRED: planning includes global components; provide --global-root")
    else:
        if not semantic_result_path:
            raise ProductLoopError("HOST_SEMANTIC_REVIEW_REQUIRED: inspect contains the bounded semantic review package")
        package = current["semantic_package"]
        semantic_result = json.loads(Path(semantic_result_path).read_text(encoding="utf-8"))
        validate_semantic_result(semantic_result, package, root)
        adoption_plan = build_adoption_plan(package, semantic_result, project, str(semantic_result_path), root)
        capabilities = sorted(item["capability_id"] for item in adoption_plan["items"]
                              if item["semantic_status"] == "missing" and item["materialization_eligible"])
        if capabilities:
            # Resolve only the exact eligible component recorded in Adoption Plan.
            catalog = load_yaml(Path(root) / "capabilities/catalog.yaml")
            records = {record["id"]: record for record in
                       (load_yaml(Path(root) / "capabilities" / relative) for relative in catalog["capabilities"])}
            selected_items = [item for item in adoption_plan["items"] if item["materialization_eligible"]]
            components = []
            for item in selected_items:
                eligible = item["bootstrap_component"]
                source_record = records[eligible["source_capability"]]
                component = next(value for value in source_record["components"] if value["id"] == eligible["component_id"])
                metadata = component["assembly"]
                components.append({
                    "component_id": metadata["id"], "component_type": metadata["type"],
                    "source_capability": eligible["source_capability"], "reason": item["reason"],
                    "scope": metadata["scope"], "source": metadata["source"],
                    "delivery": metadata["delivery"], "action": "include",
                    "capability_version": source_record.get("version", "unspecified"),
                    "configuration_preview": metadata.get("configuration", {}),
                    "dependencies": [], "conflicts": [], "destination": metadata["destination"],
                })
            assembly_plan = {
                "project_context": {"name": package["project"]["name"], "description": package["project"]["description"],
                                    "project_type": package["project"]["project_type"], "lifecycle": "production",
                                    "technology_tags": package["project"]["technologies"], "runtime": "Existing project",
                                    "platform": "local", "goal": "Adoption-approved component", "constraints": [], "collaboration": "solo"},
                "recommendation_source": "exact eligible Adoption Plan components",
                "selected_capabilities": capabilities, "components": components, "skipped_components": [],
                "unresolved_conflicts": [], "warnings": [], "requires_confirmation": True,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            }
            validate(assembly_plan, "assembly-plan.schema.json", root)
            materialized, rows = _materializer_rows(assembly_plan, project, None, root)
        else:
            rows = []
        warnings.extend(adoption_plan["items"][i]["reason"] for i in range(len(adoption_plan["items"]))
                        if adoption_plan["items"][i]["disposition"] in {"review_gap", "review_conflict"})
        if adoption_plan["overall_decision"] in {"review_required", "blocked"}:
            unresolved.append({"kind": "adoption_review", "reason": adoption_plan["overall_decision"]})
    activation_plans = _activation_plan(project, host, capabilities, root) if route == "compose" else []
    if any(plan["status"] == "review_required" for plan in activation_plans):
        unresolved.append({"kind": "host_activation", "reason": "Existing Codex activation files require review and will not be overwritten."})
    if any(item["result"] in {"conflict", "unsupported"} for item in rows):
        unresolved.extend({"kind": "materialization", "component_id": item["component_id"], "reason": item.get("reason", item["result"])}
                          for item in rows if item["result"] in {"conflict", "unsupported"})
    project_changes = _unified_changes(rows, "project")
    global_changes = _unified_changes(rows, "global")
    references = [item["component_id"] for item in rows if item["result"] == "reference_only"]
    unchanged = [item["component_id"] for item in rows if item["result"] == "unchanged"]
    for plan in activation_plans:
        if plan["status"] == "already_active":
            unchanged.append("codex-testing-activation")
    actual_writes = sum(item["result"] in {"copied", "upgraded"} for item in rows)
    actual_writes += sum(plan["status"] == "ready" for plan in activation_plans)
    blocked = bool(unresolved)
    profile_sha = _profile_digest(root)
    catalog_revision = recommendation["catalog_revision"] if recommendation else _current_catalog_revision(root)
    value = {
        "format_version": 1, "target": current["target"], "host": host, "route": route,
        "project_context": context or (adoption_plan and current["semantic_package"]["project"]),
        "catalog_revision": catalog_revision, "profile_sha256": profile_sha,
        "capabilities": capabilities, "project_changes": project_changes, "global_changes": global_changes,
        "references": references, "host_activations": activation_plans,
        "unchanged": sorted(set(unchanged)), "unresolved": unresolved, "warnings": warnings,
        "underlying_plans": {"recommendation": recommendation, "assembly_plan": assembly_plan,
                              "adoption_plan": adoption_plan, "activation_plans": activation_plans},
        "requires_confirmation": bool(actual_writes and not blocked), "actual_changes": actual_writes,
        "executable": not blocked, "global_root": str(Path(global_root).resolve()) if global_root else None,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    all_changes = [*project_changes, *global_changes]
    value["conflicts"] = [item for item in all_changes if item["result"] in {"conflict", "unsupported"}]
    value["plan_digest"] = _plan_digest(value)
    validate(value, "bootstrap-plan.schema.json", root)
    return value


def render_summary(plan):
    lines = [f"Route: {plan['route']}", f"Target: {plan['target']['repository'] or Path(plan['target']['path']).name} @ {plan['target']['commit_sha'][:7]}",
             f"Host: {plan['host']}"]
    if plan["route"] == "adoption":
        decisions = (plan.get("underlying_plans", {}).get("adoption_plan") or {}).get("items", [])
        covered = sum(item["semantic_status"] == "satisfied" for item in decisions)
        gaps = sum(item["disposition"] == "review_gap" for item in decisions)
        lines.extend((f"Covered: {covered} capabilities", f"Gaps for review: {gaps}"))
    else:
        lines.append("Selected: " + (", ".join(plan["capabilities"]) or "none"))
    project_writes = sum(x['result'] in {'copied', 'upgraded'} for x in plan['project_changes'])
    global_writes = sum(x['result'] in {'copied', 'upgraded'} for x in plan['global_changes'])
    lines.extend((f"Project changes: {project_writes}", f"Global changes: {global_writes}"))
    upgrades = [item for item in [*plan["project_changes"], *plan["global_changes"]] if item.get("result") == "upgraded" or item.get("old_sha256")]
    for subplan in plan.get("host_activations", []):
        if subplan.get("is_upgrade"):
            upgrades.append({
                "component_id": "codex-testing-activation",
                "old_sha256": subplan.get("old_skill_sha256", "")[:8],
                "sha256": subplan.get("new_skill_sha256", "")[:8],
                "old_version": "v1",
                "new_version": "v2",
            })
    if upgrades:
        lines.append(f"Upgrades: {len(upgrades)}")
        for item in upgrades:
            old_v = item.get("old_version", "v1")
            new_v = item.get("new_version", "v2")
            old_h = (item.get("old_sha256") or "")[:8]
            new_h = (item.get("sha256") or "")[:8]
            lines.append(f"  - {item['component_id']} ({old_v} [{old_h}] -> {new_v} [{new_h}])")
    if plan["host_activations"]:
        lines.append("Codex activation: " + ", ".join(item["status"] for item in plan["host_activations"]))
    lines.extend((f"References: {len(plan['references'])}", f"Unchanged: {len(plan['unchanged'])}"))
    lines.append("Blocked: " + ("; ".join(item.get("reason", item.get("kind", "conflict")) for item in plan["unresolved"]) if plan["unresolved"] else "none"))
    lines.append("Confirmation: " + ("1 approval required" if plan["requires_confirmation"] else "none"))
    return "\n".join(lines)


def decide(plan, choice):
    validate(plan, "bootstrap-plan.schema.json")
    if plan["plan_digest"] != _plan_digest(plan):
        raise ProductLoopError("DECISION_MISMATCH: plan digest is invalid")
    if choice == "approve" and (not plan["executable"] or not plan["requires_confirmation"]):
        raise ProductLoopError("PLAN_BLOCKED: only an executable plan with changes can be approved")
    if choice not in {"approve", "reject"}:
        raise ProductLoopError("INVALID_DECISION: use approve or reject")
    decision = {"format_version": 1, "plan_digest": plan["plan_digest"],
                "target_commit_sha": plan["target"]["commit_sha"], "decision": choice,
                "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
    validate(decision, "bootstrap-decision.schema.json")
    return decision


def _current_catalog_revision(root):
    # Reuse Composer's snapshot definition, including every Catalog record.
    probe = compose({"name":"revision-check","description":"revision-check","project_type":"software-project",
                     "lifecycle":"production","technology_tags":[],"runtime":"other","platform":"local",
                     "goal":"revision-check","constraints":[],"collaboration":"solo"}, root)
    return probe["catalog_revision"]


def _verify(plan, project, root):
    for action in [*plan["project_changes"], *plan["global_changes"]]:
        if action["result"] not in {"copied", "upgraded"}:
            continue
        base = Path(project) if action["scope"] == "project" else Path(plan["global_root"])
        target = base / action["destination"]
        if not target.is_file() or _sha(target.read_bytes()) != action["sha256"]:
            raise ProductLoopError(f"POST_APPLY_VERIFICATION_FAILED: {action['destination']}")
    if plan["project_changes"] and any(x["result"] in {"copied", "upgraded"} for x in plan["project_changes"]):
        manifest = Path(project) / ".vibe-coding-bootstrap/manifest.yaml"
        if not manifest.is_file():
            raise ProductLoopError("POST_APPLY_VERIFICATION_FAILED: materializer manifest missing")
    for subplan in plan["host_activations"]:
        if subplan["status"] == "ready":
            skill = Path(project) / activation.SKILL_DESTINATION
            agents = Path(project) / activation.AGENTS_DESTINATION
            manifest = Path(project) / activation.MANIFEST_PATH
            expected_skill = _sha((Path(root) / subplan["source"]).read_bytes())
            expected_agents = _sha(activation.AGENTS_TRIGGER.encode("utf-8"))
            if (not skill.is_file() or _sha(skill.read_bytes()) != expected_skill
                    or not agents.is_file() or _sha(agents.read_bytes()) != expected_agents
                    or not manifest.is_file()):
                raise ProductLoopError("POST_APPLY_VERIFICATION_FAILED: Codex activation artifacts")
    return True


def _validate_materialization_preflight(plan, dry):
    """Require every approved copy mutation to retain its exact identity and bytes."""
    def mutations(items):
        return {
            (item["source_capability"], item["component_id"], item["scope"], item.get("destination")): {
                "result": item["result"], "sha256": item.get("sha256"),
            }
            for item in items
            if item.get("delivery") == "copy" and item.get("result") in {
                "copied", "upgraded", "unchanged", "conflict", "unsupported", "skipped",
            }
        }

    expected = mutations([*plan["project_changes"], *plan["global_changes"]])
    expected = {key: value for key, value in expected.items() if value["result"] in {"copied", "upgraded"}}
    fresh = mutations(dry["items"])
    for key, approved in expected.items():
        observed = fresh.get(key)
        if (observed is None or observed["result"] not in {"copied", "upgraded"}
                or observed["sha256"] != approved["sha256"]):
            raise ProductLoopError(
                "STALE_BOOTSTRAP_PLAN: materialization source or action changed after planning"
            )


def apply_plan(plan, decision, project_root, global_root=None, root=ROOT):
    validate(plan, "bootstrap-plan.schema.json", root)
    project = Path(project_root).resolve(strict=True)
    if not plan["requires_confirmation"] and plan["actual_changes"] == 0:
        return {"status": "no_changes", "route": plan["route"], "target_before": plan["target"],
                "target_after": bind_target_revision(project), "project_changes": [], "global_changes": [],
                "activation_results": [], "references": plan["references"], "unchanged": plan["unchanged"],
                "conflicts": [], "verification": {"passed": True}}
    if not plan["executable"]:
        raise ProductLoopError("PLAN_BLOCKED: resolve listed conflicts and generate a new plan")
    if plan["actual_changes"] == 0:
        return {"status": "no_changes", "route": plan["route"], "target_before": plan["target"],
                "target_after": bind_target_revision(project), "project_changes": [], "global_changes": [],
                "activation_results": [], "references": plan["references"], "unchanged": plan["unchanged"],
                "conflicts": [], "verification": {"passed": True}}
    if not decision:
        raise ProductLoopError("DECISION_REQUIRED: provide the Unified Decision artifact")
    validate(decision, "bootstrap-decision.schema.json", root)
    if (decision["decision"] != "approve" or decision["plan_digest"] != plan["plan_digest"]
            or decision["target_commit_sha"] != plan["target"]["commit_sha"]
            or plan["plan_digest"] != _plan_digest(plan)):
        raise ProductLoopError("DECISION_MISMATCH: decision does not approve this exact plan and target")
    if plan["target"]["path"] != str(project):
        raise ProductLoopError("STALE_BOOTSTRAP_PLAN: target path differs from the planned target")
    current = bind_target_revision(project)
    if current["commit_sha"] != plan["target"]["commit_sha"] or current["dirty"] != plan["target"]["dirty"]:
        raise ProductLoopError("STALE_BOOTSTRAP_PLAN: target revision or dirty state changed")
    if _current_catalog_revision(root) != plan["catalog_revision"]:
        raise ProductLoopError("STALE_BOOTSTRAP_CATALOG: Catalog changed after planning")
    if _profile_digest(root) != plan["profile_sha256"]:
        raise ProductLoopError("STALE_BOOTSTRAP_PROFILE: Personal Profile changed after planning")
    intended_global = plan.get("global_root")
    if plan["global_changes"] and (not global_root or str(Path(global_root).resolve()) != intended_global):
        raise ProductLoopError("GLOBAL_ROOT_REQUIRED: pass the exact global root from the plan")
    underlying = plan["underlying_plans"]
    material_plan = underlying.get("assembly_plan")
    dry = None
    if material_plan:
        dry = materialize(material_plan, project, global_root, apply=False, root=root)
        _validate_materialization_preflight(plan, dry)
        if any(item["result"] in {"conflict", "unsupported"} for item in dry["items"]):
            raise ProductLoopError("STALE_BOOTSTRAP_PLAN: materialization conditions changed after planning")
    for subplan in plan["host_activations"]:
        fresh = activation.build_plan(project, "testing", "codex", root)
        if fresh["plan_digest"] != subplan["plan_digest"] or fresh["status"] != subplan["status"]:
            raise ProductLoopError("STALE_BOOTSTRAP_PLAN: Codex activation subplan changed")
    try:
        deployment = prepare_deployment(plan, project, global_root)
    except ValueError as exc:
        raise ProductLoopError(str(exc)) from exc
    target_before = current
    activation_results = []
    completed = []
    try:
        for subplan in plan["host_activations"]:
            projected = {"format_version": 1, "plan_digest": subplan["plan_digest"],
                         "target_commit_sha": subplan["target_commit_sha"], "decision": "approve",
                         "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
            activation_results.append(activation.apply(subplan, projected, project, root))
            if subplan["status"] == "ready":
                completed.append("codex-activation")
        if material_plan:
            applied = materialize(material_plan, project, global_root, apply=True, root=root)
            if any(item["result"] in {"conflict", "unsupported"} for item in applied["items"]):
                raise ProductLoopError("PARTIAL_APPLY: materializer failed after activation")
            completed.extend(item["component_id"] for item in applied["items"] if item["result"] in {"copied", "upgraded"})
    except Exception as exc:
        mutated = update_record_after_failure(project, deployment)
        if mutated:
            raise ProductLoopError(f"PARTIAL_APPLY: deployment paths changed before failure: {exc}") from exc
        raise
    try:
        _verify(plan, project, root)
    except Exception:
        update_record(project, deployment, "partial")
        raise
    update_record(project, deployment, "applied")
    return {"status": "applied", "route": plan["route"], "target_before": target_before,
            "target_after": bind_target_revision(project), "project_changes": plan["project_changes"],
            "global_changes": plan["global_changes"], "activation_results": activation_results,
            "references": plan["references"], "unchanged": plan["unchanged"], "conflicts": [],
            "verification": {"passed": True}, "deployment_id": deployment["deployment_id"],
            "snapshot_ref": deployment["snapshot_ref"], "snapshot_commit": deployment["snapshot_commit"],
            "rollback_available": True}


def write_json(path, value, target_root=None):
    destination = Path(path).resolve()
    if target_root and _inside(destination, target_root):
        raise ProductLoopError("OUTPUT_INSIDE_TARGET: artifact outputs must be outside the target project")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def validate_output_path(path, target_root):
    """Reject target-local artifacts before an apply can write anything."""
    if path and _inside(Path(path).resolve(), target_root):
        raise ProductLoopError("OUTPUT_INSIDE_TARGET: artifact outputs must be outside the target project")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
