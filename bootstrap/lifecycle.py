"""Standardized VCBS lifecycle management: deploy, upgrade, uninstall, status."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil

import yaml

from activation import core as activation
from adoption.core import discover
from adoption.planning import build_adoption_plan
from adoption.revision import bind_target_revision
from adoption.semantic import build_semantic_input, validate_semantic_result
from assembly.core import assemble
from bootstrap.core import (
    ProductLoopError, ROOT, _activation_plan, _canonical, _current_catalog_revision, _inside,
    _materializer_rows, _owned_state, _parse_hosts, _profile_digest, _route, _sha,
    _unified_changes, apply_plan, build_plan, decide, inspect_project,
    load_json, render_summary, validate, validate_output_path, write_json
)
from bootstrap.recovery import (
    RecoveryError, list_deployments, load_record, prepare_deployment,
    rollback, update_record, update_record_after_failure
)
from composer.core import compose, load_yaml
from materializer.core import materialize


def _safe_relative(value, label="Path"):
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (not value or path.is_absolute() or PureWindowsPath(value).is_absolute()
            or PureWindowsPath(value).drive or ".." in path.parts):
        raise ProductLoopError(f"UNSAFE_PATH: {label} must be a relative path without '..': {value}")
    return path


def _contained(root, path_or_rel):
    if not path_or_rel:
        return None
    p = Path(path_or_rel)
    root_resolved = Path(root).resolve()
    if p.is_absolute():
        try:
            target = p.resolve()
            rel = target.relative_to(root_resolved)
        except ValueError:
            raise ProductLoopError(f"UNSAFE_PATH: path escapes its root: {path_or_rel}")
    else:
        rel = _safe_relative(path_or_rel)
    candidate = root_resolved
    for part in rel.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ProductLoopError(f"UNSAFE_PATH: path traverses a symlink: {candidate}")
    target = candidate.resolve()
    if not target.is_relative_to(root_resolved):
        raise ProductLoopError(f"UNSAFE_PATH: path escapes its root: {path_or_rel}")
    return candidate


def _uninstall_plan_digest(plan):
    return _sha(_canonical({key: value for key, value in plan.items() if key not in {"generated_at", "plan_digest"} and not key.startswith("_")}))


def get_status(project_root, host="codex", global_root=None, root=ROOT):
    """Query read-only VCBS status and managed components inventory."""
    project = Path(project_root).resolve(strict=True)
    if not project.is_dir():
        raise ProductLoopError("INVALID_PROJECT_ROOT: target must be a directory")
    target = bind_target_revision(project)
    target["path"] = str(project)

    catalog_revision = _current_catalog_revision(root)
    profile_sha = _profile_digest(root)

    deployments = list_deployments(project)
    recent_deployment = deployments[0] if deployments else None
    recovery_points = [d["deployment_id"] for d in deployments if d.get("status") in {"applied", "partial"}]

    catalog = load_yaml(Path(root) / "capabilities/catalog.yaml")
    records = {record["id"]: record for record in
               (load_yaml(Path(root) / "capabilities" / relative) for relative in catalog["capabilities"])}

    components = []
    conflicts = []
    manifest_path = project / ".vibe-coding-bootstrap/manifest.yaml"
    has_manifest = False
    manifest_corrupt = False

    if manifest_path.is_file():
        try:
            mdata = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(mdata, dict) or mdata.get("format_version") != 1 or not isinstance(mdata.get("items"), list):
                manifest_corrupt = True
                has_manifest = True
                conflicts.append("Bootstrap manifest.yaml is invalid or corrupted")
            else:
                has_manifest = True
                for item in mdata.get("items", []):
                    comp_id = item.get("component_id", "")
                    src_cap = item.get("source_capability", "")
                    scope = item.get("scope", "project")
                    dest_str = item.get("destination", "")
                    inst_ver = item.get("capability_version", "unspecified")
                    inst_sha = item.get("sha256", "")

                    dest_path = None
                    if scope == "project":
                        try:
                            dest_path = _contained(project, dest_str)
                        except ProductLoopError as exc:
                            conflicts.append(str(exc))
                    elif scope == "global":
                        if global_root:
                            try:
                                dest_path = _contained(Path(global_root).resolve(), dest_str)
                            except ProductLoopError as exc:
                                conflicts.append(str(exc))
                    elif scope == "external" or item.get("state") == "reference_only":
                        dest_path = None

                    actual_sha = None
                    if dest_path and dest_path.is_file():
                        actual_sha = _sha(dest_path.read_bytes())

                    desired_ver = inst_ver
                    desired_sha = inst_sha
                    rec = records.get(src_cap)
                    if rec:
                        desired_ver = rec.get("version", inst_ver)
                        comp_def = next((c for c in rec.get("components", []) if c["id"] == comp_id), None)
                        if comp_def and comp_def.get("assembly", {}).get("delivery") == "copy":
                            src_file = Path(root) / comp_def["assembly"]["source"]
                            if src_file.is_file():
                                desired_sha = _sha(src_file.read_bytes())

                    if item.get("state") == "reference_only" or scope == "external":
                        if dest_str and dest_path:
                            if not dest_path.exists():
                                c_status = "missing"
                                conflicts.append(f"Managed asset {dest_str} is missing")
                            elif dest_path.is_file():
                                if inst_sha and actual_sha != inst_sha:
                                    c_status = "conflict"
                                    conflicts.append(f"Managed asset {dest_str} was modified by user")
                                else:
                                    c_status = "reference_only"
                            else:
                                c_status = "conflict"
                                conflicts.append(f"Managed asset {dest_str} is not a regular file")
                        else:
                            c_status = "reference_only"
                    elif scope == "global" and global_root is None:
                        c_status = "location_unknown"
                    elif dest_path is None or not dest_path.exists():
                        c_status = "missing"
                    elif dest_path.is_file():
                        if actual_sha != inst_sha:
                            c_status = "conflict"
                            conflicts.append(f"Managed asset {dest_str} was modified by user")
                        elif desired_ver != inst_ver or desired_sha != inst_sha:
                            c_status = "update_available"
                        else:
                            c_status = "unchanged"
                    else:
                        c_status = "conflict"
                        conflicts.append(f"Managed asset {dest_str} is not a regular file")

                    components.append({
                        "component_id": comp_id,
                        "source_capability": src_cap,
                        "scope": scope,
                        "destination": dest_str,
                        "installed_version": inst_ver,
                        "desired_version": desired_ver,
                        "installed_sha256": inst_sha,
                        "actual_sha256": actual_sha,
                        "desired_sha256": desired_sha,
                        "status": c_status,
                    })
        except Exception:
            manifest_corrupt = True
            has_manifest = True
            conflicts.append("Failed to read Bootstrap manifest.yaml")

    activation_manifest_path = project / activation.MANIFEST_PATH
    has_activation = False
    codex_activation = {
        "status": "not_active",
        "destination": None,
        "installed_sha256": None,
        "actual_sha256": None,
        "desired_sha256": None,
    }
    native_activations = []

    if activation_manifest_path.is_file():
        try:
            adata = json.loads(activation_manifest_path.read_text(encoding="utf-8"))
            valid_manifest = False
            raw_activations = []
            if isinstance(adata, dict) and adata.get("format_version") == 1:
                if "activations" in adata and isinstance(adata["activations"], list):
                    if all(isinstance(a, dict) and "host" in a and "capability" in a and isinstance(a.get("artifacts"), list) for a in adata["activations"]):
                        valid_manifest = True
                        raw_activations = adata["activations"]
                elif adata.get("host") and adata.get("capability") and isinstance(adata.get("artifacts"), list):
                    valid_manifest = True
                    raw_activations = [{
                        "host": adata["host"],
                        "capability": adata["capability"],
                        "artifacts": adata.get("artifacts", [])
                    }]
            if not valid_manifest:
                conflicts.append("Activation manifest activation.json is invalid or corrupted")
                manifest_corrupt = True
                has_activation = True
            else:
                has_activation = bool(raw_activations)
                for act in raw_activations:
                    act_host = act["host"]
                    act_cap = act["capability"]
                    target_conf = activation._get_target_config(act_host, act_cap)
                    if not target_conf:
                        conflicts.append(f"Unknown activation target: {act_host}/{act_cap}")
                        continue
                    skill_dest = target_conf["skill_destination"]
                    agents_dest = target_conf["agents_destination"]
                    skill_path = _contained(project, skill_dest)
                    agents_path = _contained(project, agents_dest) if agents_dest else None

                    inst_skill = next((a for a in act.get("artifacts", []) if a.get("destination") == skill_dest), {})
                    inst_skill_sha = inst_skill.get("sha256")
                    act_actual_sha = _sha(skill_path.read_bytes()) if (skill_path and skill_path.is_file()) else None

                    desired_skill_file = Path(root) / target_conf["skill_source"]
                    desired_act_sha = _sha(desired_skill_file.read_bytes()) if desired_skill_file.is_file() else inst_skill_sha

                    expected_agents_sha = _sha(target_conf["agents_trigger"].encode("utf-8")) if target_conf["agents_trigger"] else None
                    actual_agents_sha = _sha(agents_path.read_bytes()) if (agents_path and agents_path.is_file()) else None

                    if not skill_path or not skill_path.exists() or (agents_path and not agents_path.exists()):
                        act_status = "missing"
                        conflicts.append(f"{act_host.capitalize()} activation artifacts are missing")
                    elif act_actual_sha != inst_skill_sha or (agents_path and actual_agents_sha != expected_agents_sha):
                        act_status = "conflict"
                        conflicts.append(f"{act_host.capitalize()} activation artifacts were modified by user")
                    elif desired_act_sha != inst_skill_sha:
                        act_status = "update_available"
                    else:
                        act_status = "active"

                    act_record = {
                        "host": act_host,
                        "capability": act_cap,
                        "status": act_status,
                        "destination": skill_dest,
                        "installed_sha256": inst_skill_sha,
                        "actual_sha256": act_actual_sha,
                        "desired_sha256": desired_act_sha,
                    }
                    if act_host == "antigravity":
                        act_record["shared_surface_with"] = "codex"
                        act_record["shared_surface_notice"] = (
                            "Shares .agents/skills discovery root with Codex; unmanaged in Codex without AGENTS.md trigger."
                        )
                    else:
                        act_record["shared_surface_with"] = None
                        act_record["shared_surface_notice"] = None
                    native_activations.append(act_record)

                    if act_host == "codex" and act_cap == "testing":
                        codex_activation = {
                            "status": act_status,
                            "destination": skill_dest,
                            "installed_sha256": inst_skill_sha,
                            "actual_sha256": act_actual_sha,
                            "desired_sha256": desired_act_sha,
                        }
        except Exception:
            conflicts.append("Failed to read activation manifest activation.json")
            manifest_corrupt = True
            has_activation = True

    # Scope breakdown
    proj_comps = [c["component_id"] for c in components if c["scope"] == "project"]
    glob_comps = [c["component_id"] for c in components if c["scope"] == "global"]

    deployed = has_manifest or has_activation
    if deployed:
        if conflicts or manifest_corrupt:
            overall_status = "conflict"
        elif any(c["status"] == "missing" for c in components) or any(a["status"] == "missing" for a in native_activations):
            overall_status = "partial"
        elif any(c["status"] == "update_available" for c in components) or any(a["status"] == "update_available" for a in native_activations):
            overall_status = "update_available"
        else:
            overall_status = "installed"
    else:
        if recent_deployment:
            overall_status = "uninstalled" if recent_deployment.get("status") in {"rolled_back", "applied"} else "partial"
        else:
            overall_status = "not_installed"

    managed_only, signals, unmanaged = _owned_state(project)
    route_prediction = "adoption" if unmanaged else "compose"

    try:
        parsed_hosts = _parse_hosts(host)
        is_supported = bool(parsed_hosts)
    except Exception:
        parsed_hosts = []
        is_supported = False

    host_support = {
        "host": host,
        "supported": is_supported,
        "native_activation": "supported" if any(h in {"codex", "claude", "antigravity"} for h in parsed_hosts) else "none",
        "known_hosts": {
            "codex": "supported",
            "none": "supported",
            "claude": "supported",
            "claude_code": "supported",
            "antigravity": "supported",
        },
    }

    result = {
        "format_version": 1,
        "project_root": str(project),
        "target": target,
        "catalog_revision": catalog_revision,
        "profile_sha256": profile_sha,
        "status": overall_status,
        "deployed": deployed,
        "components": components,
        "codex_activation": codex_activation,
        "native_activations": native_activations,
        "host_support": host_support,
        "scope_breakdown": {
            "project_components": proj_comps,
            "global_components": glob_comps,
        },
        "recent_deployment": recent_deployment,
        "recovery_points": recovery_points,
        "has_conflicts": bool(conflicts),
        "conflicts": conflicts,
        "update_available": overall_status == "update_available",
        "partial": overall_status == "partial",
        "route_prediction": route_prediction,
        "unmanaged_assets": unmanaged,
    }
    validate(result, "bootstrap-status.schema.json", root)
    return result


def render_status_summary(status_dict):
    """Render a human-readable summary of the project VCBS status."""
    target = status_dict["target"]
    repo_name = target.get("repository") or Path(status_dict["project_root"]).name
    dirty_str = "dirty" if target.get("dirty") else "clean"
    lines = [
        f"Project: {repo_name} @ {target.get('commit_sha', '')[:7]} ({dirty_str})",
        f"Location: {status_dict['project_root']}",
        f"VCBS Status: {status_dict['status'].upper()}",
        f"Catalog Revision: {status_dict['catalog_revision'][:8]}",
        f"Host: {status_dict['host_support']['host']} (native activation: {status_dict['host_support']['native_activation']})",
    ]

    components = status_dict["components"]
    if components:
        lines.append(f"\nManaged Components ({len(components)}):")
        for c in components:
            inst_h = (c.get("installed_sha256") or "")[:8]
            act_h = (c.get("actual_sha256") or "")[:8]
            lines.append(f"  - {c['component_id']} [{c['scope']}] (v{c['installed_version']}, {c['status']})")
            lines.append(f"    Destination: {c['destination']}")
            lines.append(f"    Hash: expected {inst_h} | actual {act_h}")
    else:
        lines.append("\nManaged Components: none")

    native_acts = status_dict.get("native_activations", [])
    if native_acts:
        for na in native_acts:
            lines.append(f"\n{na['host'].capitalize()} Native Activation: {na['status']}")
            lines.append(f"  Destination: {na['destination']}")
    else:
        act = status_dict["codex_activation"]
        if act["status"] != "not_active":
            lines.append(f"\nCodex Native Activation: {act['status']}")
            lines.append(f"  Destination: {act['destination']}")
        else:
            lines.append("\nCodex Native Activation: not active")

    scopes = status_dict["scope_breakdown"]
    lines.append(f"\nScope Breakdown: {len(scopes['project_components'])} project, {len(scopes['global_components'])} global")

    lines.append(f"\nRecovery:")
    recent = status_dict.get("recent_deployment")
    if recent:
        lines.append(f"  Recent Deployment: {recent['deployment_id']} ({recent.get('status')})")
    else:
        lines.append("  Recent Deployment: none")
    lines.append(f"  Available Recovery Points: {len(status_dict['recovery_points'])}")

    if status_dict["conflicts"]:
        lines.append("\nConflicts Detected:")
        for conf in status_dict["conflicts"]:
            lines.append(f"  ! {conf}")

    if status_dict["unmanaged_assets"]:
        lines.append("\nUnmanaged Pre-existing Environment Assets:")
        for asset in status_dict["unmanaged_assets"]:
            lines.append(f"  ? {asset}")

    return "\n".join(lines)


def _build_upgrade_plan_from_manifest(project_root, host="all", global_root=None, root=ROOT):
    """Synthesize an upgrade plan directly from installed manifest items against the Catalog."""
    project = Path(project_root).resolve(strict=True)
    manifest_path = project / ".vibe-coding-bootstrap/manifest.yaml"
    if not manifest_path.is_file():
        raise ProductLoopError("NOT_INSTALLED: project manifest missing; use deploy first")
    mdata = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(mdata, dict) or not isinstance(mdata.get("items"), list):
        raise ProductLoopError("CORRUPT_MANIFEST: manifest.yaml is invalid")

    catalog = load_yaml(Path(root) / "capabilities/catalog.yaml")
    records = {record["id"]: record for record in
               (load_yaml(Path(root) / "capabilities" / relative) for relative in catalog["capabilities"])}

    components = []
    selected_capabilities = []
    for item in mdata.get("items", []):
        cid = item.get("source_capability")
        comp_id = item.get("component_id")
        rec = records.get(cid)
        if not rec:
            continue
        if cid not in selected_capabilities:
            selected_capabilities.append(cid)
        comp_def = next((c for c in rec.get("components", []) if c["id"] == comp_id), None)
        if not comp_def or not comp_def.get("assembly"):
            continue
        meta = comp_def["assembly"]
        comp_item = {
            "component_id": meta["id"],
            "component_type": meta["type"],
            "source_capability": cid,
            "reason": "Upgrade existing installed component",
            "scope": meta["scope"],
            "source": meta["source"],
            "delivery": meta["delivery"],
            "action": "include",
            "capability_version": rec.get("version", "unspecified"),
            "configuration_preview": meta.get("configuration", {}),
            "dependencies": [],
            "conflicts": [],
        }
        if "destination" in meta:
            comp_item["destination"] = meta["destination"]
        components.append(comp_item)

    assembly_plan = {
        "project_context": {
            "name": project.name,
            "description": f"Installed project {project.name}",
            "project_type": "software-project",
            "lifecycle": "production",
            "technology_tags": [],
            "runtime": "existing",
            "platform": "local",
            "goal": "Upgrade installed VCBS components",
            "constraints": [],
            "collaboration": "solo",
        },
        "recommendation_source": "Installed VCBS manifest items",
        "selected_capabilities": selected_capabilities,
        "components": components,
        "skipped_components": [],
        "unresolved_conflicts": [],
        "warnings": [],
        "requires_confirmation": True,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    validate(assembly_plan, "assembly-plan.schema.json", root)
    materialized, rows = _materializer_rows(assembly_plan, project, global_root, root)

    activation_manifest_path = project / activation.MANIFEST_PATH
    activation_plans = []
    if activation_manifest_path.is_file():
        try:
            adata = json.loads(activation_manifest_path.read_text(encoding="utf-8"))
            parsed_hosts = _parse_hosts(host)
            raw_activations = []
            if adata.get("format_version") == 1:
                if "activations" in adata and isinstance(adata["activations"], list):
                    raw_activations = adata["activations"]
                elif adata.get("host") and adata.get("capability"):
                    raw_activations = [{
                        "host": adata["host"],
                        "capability": adata["capability"],
                        "artifacts": adata.get("artifacts", [])
                    }]
            for act in raw_activations:
                act_host = act.get("host")
                act_cap = act.get("capability")
                if act_host in parsed_hosts or (act_host == "claude" and "claude_code" in parsed_hosts):
                    subplan = activation.build_plan(project, capability=act_cap, host=act_host, root=root)
                    activation_plans.append(subplan)
        except Exception:
            pass

    unresolved = []
    warnings = []
    if any(p.get("host") == "antigravity" for p in activation_plans) or "antigravity" in parsed_hosts:
        shared_notice = (
            "Antigravity skill destination '.agents/skills/vcbs-manager/SKILL.md' shares the '.agents/skills' "
            "root with Codex. Codex discovers this directory, but will not invoke the skill without an AGENTS.md trigger."
        )
        if shared_notice not in warnings:
            warnings.append(shared_notice)
    if any(item["result"] in {"conflict", "unsupported"} for item in rows):
        unresolved.extend({"kind": "materialization", "component_id": item["component_id"], "reason": item.get("reason", item["result"])}
                          for item in rows if item["result"] in {"conflict", "unsupported"})
    for subplan in activation_plans:
        if subplan.get("status") in {"conflict", "review_required"}:
            unresolved.append({"kind": "host_activation", "reason": f"{subplan['host'].capitalize()} activation status: {subplan['status']}"})

    project_changes = _unified_changes(rows, "project")
    global_changes = _unified_changes(rows, "global")
    references = [item["component_id"] for item in rows if item["result"] == "reference_only"]
    unchanged = [item["component_id"] for item in rows if item["result"] == "unchanged"]
    for plan in activation_plans:
        if plan["status"] == "already_active":
            unchanged.append("codex-testing-activation" if plan["host"] == "codex" and plan["capability_id"] == "testing" else f"{plan['host']}-{plan['capability_id']}-activation")

    actual_writes = sum(item["result"] in {"copied", "upgraded"} for item in rows)
    actual_writes += sum(plan["status"] == "ready" for plan in activation_plans)
    blocked = bool(unresolved)
    profile_sha = _profile_digest(root)
    catalog_revision = _current_catalog_revision(root)

    current_target = bind_target_revision(project)
    current_target["path"] = str(project)

    value = {
        "format_version": 1,
        "target": current_target,
        "host": host,
        "route": "compose",
        "project_context": assembly_plan["project_context"],
        "catalog_revision": catalog_revision,
        "profile_sha256": profile_sha,
        "capabilities": selected_capabilities,
        "project_changes": project_changes,
        "global_changes": global_changes,
        "references": references,
        "host_activations": activation_plans,
        "unchanged": sorted(set(unchanged)),
        "unresolved": unresolved,
        "warnings": warnings,
        "underlying_plans": {
            "assembly_plan": assembly_plan,
            "activation_plans": activation_plans,
        },
        "requires_confirmation": bool(actual_writes and not blocked),
        "actual_changes": actual_writes,
        "executable": not blocked,
        "global_root": str(Path(global_root).resolve()) if global_root else None,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    all_changes = [*project_changes, *global_changes]
    value["conflicts"] = [item for item in all_changes if item["result"] in {"conflict", "unsupported"}]
    value["plan_digest"] = _sha(_canonical({key: val for key, val in value.items() if key not in {"generated_at", "plan_digest"}}))
    validate(value, "bootstrap-plan.schema.json", root)
    return value


def deploy(project_root, host="codex", context_path=None, semantic_result_path=None,
           global_root=None, apply=False, decision=None, plan=None, root=ROOT):
    """Standardized VCBS initial deployment or reconciliation."""
    st = get_status(project_root, host=host, global_root=global_root, root=root)
    # Check if already installed and up to date without changes
    active_plan = load_json(plan) if isinstance(plan, (str, Path)) else plan
    if active_plan is None:
        active_plan = build_plan(project_root, host=host, context_path=context_path,
                                 semantic_result_path=semantic_result_path, global_root=global_root, root=root)

    if active_plan["actual_changes"] == 0:
        return {"status": "no_changes", "route": active_plan["route"], "target": active_plan["target"], "plan": active_plan}

    if not apply:
        return {"status": "preview", "plan": active_plan, "summary": render_summary(active_plan)}

    if not decision:
        raise ProductLoopError("DECISION_REQUIRED: execution requires an explicit approved decision artifact; provide decision")
    dec = load_json(decision) if isinstance(decision, (str, Path)) else decision

    return apply_plan(active_plan, dec, project_root, global_root=global_root, root=root)


def upgrade(project_root, host="all", context_path=None, global_root=None,
            apply=False, decision=None, plan=None, root=ROOT):
    """Standardized VCBS safe upgrade."""
    st = get_status(project_root, host=host, global_root=global_root, root=root)
    if not st["deployed"] and st["status"] in {"not_installed", "uninstalled"} and not plan:
        raise ProductLoopError("NOT_INSTALLED: project does not have VCBS installed; use deploy first")
    if st["has_conflicts"] and not plan:
        raise ProductLoopError(f"UPGRADE_CONFLICT: cannot upgrade due to existing conflicts: {', '.join(st['conflicts'])}")

    active_plan = load_json(plan) if isinstance(plan, (str, Path)) else plan
    if active_plan is None:
        if not st["update_available"] and not context_path:
            return {"status": "no_changes", "message": "All components are already up to date.", "target": st["target"]}

        if context_path:
            active_plan = build_plan(project_root, host=host, context_path=context_path,
                                     global_root=global_root, root=root)
        else:
            active_plan = _build_upgrade_plan_from_manifest(project_root, host=host,
                                                             global_root=global_root, root=root)

    if active_plan["actual_changes"] == 0:
        return {"status": "no_changes", "message": "All components are already up to date.", "plan": active_plan}

    if not apply:
        return {"status": "preview", "plan": active_plan, "summary": render_summary(active_plan)}

    if not decision:
        raise ProductLoopError("DECISION_REQUIRED: execution requires an explicit approved decision artifact; provide decision")
    dec = load_json(decision) if isinstance(decision, (str, Path)) else decision

    return apply_plan(active_plan, dec, project_root, global_root=global_root, root=root)


def _matches_component_selection(comp_id, source_cap, selected_components):
    if selected_components is None:
        return True
    aliases = {comp_id}
    if source_cap:
        aliases.add(source_cap)
    for (h, c) in activation.ACTIVATION_TARGETS:
        skill_cid = f"{h}-native-{c}-skill"
        act_cid = "codex-testing-activation" if h == "codex" and c == "testing" else f"{h}-{c}-activation"
        if comp_id in {skill_cid, act_cid}:
            aliases.update({skill_cid, act_cid, c, h})
            if h == "claude":
                aliases.add("claude_code")
    return any(c in aliases for c in selected_components)


def build_uninstall_plan(project_root, component_id=None, global_root=None, root=ROOT):
    """Generate a revision-bound, conflict-checked Uninstall Plan."""
    project = Path(project_root).resolve(strict=True)
    if not project.is_dir():
        raise ProductLoopError("INVALID_PROJECT_ROOT: target must be a directory")
    target = bind_target_revision(project)
    target["path"] = str(project)

    manifest_path = project / ".vibe-coding-bootstrap/manifest.yaml"
    activation_manifest_path = project / activation.MANIFEST_PATH

    selected_components = None
    if component_id:
        if isinstance(component_id, str):
            selected_components = {c.strip() for c in component_id.split(",") if c.strip()}
        else:
            selected_components = {str(c).strip() for c in component_id if str(c).strip()}

    items = []
    activation_items = []
    conflicts = []
    unresolved = []
    manifest_updates = []
    remaining_manifest_items = []
    manifest_sha256 = None
    activation_manifest_sha256 = None

    if manifest_path.is_file():
        try:
            mbytes = manifest_path.read_bytes()
            manifest_sha256 = _sha(mbytes)
            mdata = yaml.safe_load(mbytes.decode("utf-8"))
            if not isinstance(mdata, dict) or mdata.get("format_version") != 1 or not isinstance(mdata.get("items"), list):
                unresolved.append({"kind": "corrupt_manifest", "reason": "Manifest is corrupted or invalid structure."})
            else:
                for entry in mdata.get("items", []):
                    entry_comp_id = entry.get("component_id")
                    if not _matches_component_selection(entry_comp_id, entry.get("source_capability"), selected_components):
                        remaining_manifest_items.append(entry)
                        continue

                    scope = entry.get("scope", "project")
                    if scope == "external" or entry.get("state") == "reference_only":
                        continue
                    dest_str = entry.get("destination", "")
                    expected_sha = entry.get("sha256", "")
                    dest_path = None
                    try:
                        if scope == "project":
                            dest_path = _contained(project, dest_str)
                        elif scope == "global":
                            if not global_root:
                                unresolved.append({
                                    "kind": "global_root_required",
                                    "reason": f"Global component {entry_comp_id} requires explicit --global-root."
                                })
                                continue
                            dest_path = _contained(Path(global_root).resolve(), dest_str)
                    except ProductLoopError as exc:
                        conflicts.append({"component_id": entry_comp_id, "reason": str(exc)})
                        continue

                    if dest_path is None or not dest_path.exists():
                        status = "already_absent"
                        reason = "File is already absent on disk."
                    elif dest_path.is_symlink() or not dest_path.is_file():
                        status = "conflict"
                        reason = "Target is a symlink or not a regular file; cannot safely uninstall."
                        conflicts.append({"component_id": entry_comp_id, "reason": reason})
                    else:
                        actual_sha = _sha(dest_path.read_bytes())
                        if actual_sha != expected_sha:
                            status = "conflict"
                            reason = f"File modified by user ({actual_sha[:8]} != {expected_sha[:8]}); cannot uninstall."
                            conflicts.append({"component_id": entry_comp_id, "reason": reason})
                        else:
                            status = "removable"
                            reason = None

                    item_record = {
                        "component_id": entry_comp_id,
                        "source_capability": entry.get("source_capability", ""),
                        "scope": scope,
                        "destination": str(dest_path) if dest_path else dest_str,
                        "relative_destination": dest_str,
                        "sha256": expected_sha,
                        "status": status,
                    }
                    if reason:
                        item_record["reason"] = reason
                    items.append(item_record)

                if len(remaining_manifest_items) != len(mdata.get("items", [])):
                    manifest_updates.append(".vibe-coding-bootstrap/manifest.yaml")
        except Exception as exc:
            unresolved.append({"kind": "manifest_error", "reason": str(exc)})

    if activation_manifest_path.is_file():
        try:
            abyles = activation_manifest_path.read_bytes()
            activation_manifest_sha256 = _sha(abyles)
            adata = json.loads(abyles.decode("utf-8"))
            if adata.get("format_version") == 1:
                raw_activations = []
                if "activations" in adata and isinstance(adata["activations"], list):
                    raw_activations = adata["activations"]
                elif adata.get("host") and adata.get("capability"):
                    raw_activations = [{
                        "host": adata["host"],
                        "capability": adata["capability"],
                        "artifacts": adata.get("artifacts", [])
                    }]
                for act in raw_activations:
                    h = act.get("host")
                    cap = act.get("capability")
                    cid = "codex-testing-activation" if h == "codex" and cap == "testing" else f"{h}-{cap}-activation"
                    skill_cid = f"{h}-native-{cap}-skill"
                    is_selected = _matches_component_selection(cid, cap, selected_components)
                    if not is_selected:
                        continue

                    for art in act.get("artifacts", []):
                        dest_str = art.get("destination", "")
                        expected_sha = art.get("sha256", "")
                        dest_path = _contained(project, dest_str)

                        if not dest_path.exists():
                            status = "already_absent"
                            reason = "File is already absent on disk."
                        elif dest_path.is_symlink() or not dest_path.is_file():
                            status = "conflict"
                            reason = "Target is not a regular file; cannot safely uninstall."
                            conflicts.append({"component_id": cid, "reason": reason})
                        else:
                            actual_sha = _sha(dest_path.read_bytes())
                            if actual_sha != expected_sha:
                                status = "conflict"
                                reason = f"Activation file modified by user ({actual_sha[:8]} != {expected_sha[:8]}); cannot uninstall."
                                conflicts.append({"component_id": cid, "reason": reason})
                            else:
                                status = "removable"
                                reason = None

                        art_record = {
                            "component_id": cid,
                            "source_capability": cap,
                            "scope": "project",
                            "destination": str(dest_path),
                            "relative_destination": dest_str,
                            "sha256": expected_sha,
                            "status": status,
                        }
                        if reason:
                            art_record["reason"] = reason
                        activation_items.append(art_record)

                if activation_items:
                    manifest_updates.append(activation.MANIFEST_PATH)
        except Exception as exc:
            unresolved.append({"kind": "activation_manifest_error", "reason": str(exc)})

    removals = [item["destination"] for item in (*items, *activation_items) if item["status"] == "removable"]
    already_absent = [item["destination"] for item in (*items, *activation_items) if item["status"] == "already_absent"]

    actual_changes = len(removals) + (1 if manifest_updates else 0)
    executable = len(conflicts) == 0 and len(unresolved) == 0

    plan = {
        "format_version": 1,
        "operation": "uninstall",
        "target": target,
        "catalog_revision": _current_catalog_revision(root),
        "profile_sha256": _profile_digest(root),
        "project_root": str(project),
        "global_root": str(Path(global_root).resolve()) if global_root else None,
        "items": items,
        "activation_items": activation_items,
        "removals": removals,
        "already_absent": already_absent,
        "conflicts": conflicts,
        "unresolved": unresolved,
        "manifest_updates": manifest_updates,
        "manifest_sha256": manifest_sha256,
        "activation_manifest_sha256": activation_manifest_sha256,
        "remaining_manifest_items": remaining_manifest_items,
        "requires_confirmation": bool(actual_changes and executable),
        "actual_changes": actual_changes,
        "executable": executable,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    plan["plan_digest"] = _uninstall_plan_digest(plan)
    validate(plan, "bootstrap-uninstall-plan.schema.json", root)
    return plan


def render_uninstall_summary(plan):
    """Render a human-readable summary of the uninstall plan."""
    lines = [
        f"Operation: UNINSTALL",
        f"Target: {plan['target']['repository'] or Path(plan['target']['path']).name} @ {plan['target']['commit_sha'][:7]}",
        f"Removable assets: {len(plan['removals'])}",
        f"Already absent: {len(plan['already_absent'])}",
        f"Manifest updates: {len(plan['manifest_updates'])}",
    ]
    if plan["removals"]:
        lines.append("\nAssets to be safely removed:")
        for r in plan["removals"]:
            lines.append(f"  - {r}")
    if plan["conflicts"]:
        lines.append("\nConflicts preventing uninstall:")
        for c in plan["conflicts"]:
            lines.append(f"  ! {c.get('component_id')}: {c.get('reason')}")
    if plan["unresolved"]:
        lines.append("\nUnresolved blockers:")
        for u in plan["unresolved"]:
            lines.append(f"  ! {u.get('kind')}: {u.get('reason')}")
    lines.append(f"\nConfirmation: {'1 approval required' if plan['requires_confirmation'] else 'none'}")
    return "\n".join(lines)


def decide_uninstall(plan, choice):
    """Create a Unified Decision artifact for an Uninstall Plan."""
    validate(plan, "bootstrap-uninstall-plan.schema.json")
    if plan["plan_digest"] != _uninstall_plan_digest(plan):
        raise ProductLoopError("DECISION_MISMATCH: uninstall plan digest is invalid")
    if choice == "approve" and (not plan["executable"] or not plan["requires_confirmation"]):
        raise ProductLoopError("PLAN_BLOCKED: only an executable uninstall plan with changes can be approved")
    if choice not in {"approve", "reject"}:
        raise ProductLoopError("INVALID_DECISION: use approve or reject")
    decision = {
        "format_version": 1,
        "plan_digest": plan["plan_digest"],
        "target_commit_sha": plan["target"]["commit_sha"],
        "decision": choice,
        "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    validate(decision, "bootstrap-decision.schema.json")
    return decision


def apply_uninstall(plan, decision, project_root, global_root=None, root=ROOT):
    """Apply an approved Uninstall Plan: safe removal with snapshot and rollback capability."""
    validate(plan, "bootstrap-uninstall-plan.schema.json", root)
    project = Path(project_root).resolve(strict=True)

    if plan["actual_changes"] == 0:
        return {"status": "no_changes", "message": "No VCBS-managed components to uninstall."}

    if not plan["executable"]:
        raise ProductLoopError("PLAN_BLOCKED: uninstall plan has conflicts or unresolved blockers")

    if not decision:
        raise ProductLoopError("DECISION_REQUIRED: execution requires an explicit approved decision artifact; provide decision")
    dec = load_json(decision) if isinstance(decision, (str, Path)) else decision
    validate(dec, "bootstrap-decision.schema.json", root)

    if (dec["decision"] != "approve" or dec["plan_digest"] != plan["plan_digest"]
            or dec["target_commit_sha"] != plan["target"]["commit_sha"]
            or plan["plan_digest"] != _uninstall_plan_digest(plan)):
        raise ProductLoopError("DECISION_MISMATCH: decision does not approve this exact plan and target")

    if plan["target"]["path"] != str(project):
        raise ProductLoopError("STALE_BOOTSTRAP_PLAN: target path differs from planned target")

    current = bind_target_revision(project)
    if current["commit_sha"] != plan["target"]["commit_sha"] or current["dirty"] != plan["target"]["dirty"]:
        raise ProductLoopError("STALE_BOOTSTRAP_PLAN: target revision or dirty state changed")

    # Strict Global Root verification
    planned_global = plan.get("global_root")
    has_global_items = any(item.get("scope") == "global" for item in plan.get("items", []))
    if has_global_items:
        if not global_root:
            raise ProductLoopError("GLOBAL_ROOT_REQUIRED: uninstall plan contains global components; pass --global-root")
        actual_global = str(Path(global_root).resolve())
        if not Path(global_root).resolve().is_dir():
            raise ProductLoopError(f"GLOBAL_ROOT_NOT_FOUND: global root directory does not exist: {global_root}")
        if planned_global is None or actual_global != planned_global:
            raise ProductLoopError(f"GLOBAL_ROOT_MISMATCH: provided global root '{actual_global}' does not match planned global root '{planned_global}'")
    else:
        if global_root:
            actual_global = str(Path(global_root).resolve())
            if planned_global and actual_global != planned_global:
                raise ProductLoopError(f"GLOBAL_ROOT_MISMATCH: provided global root '{actual_global}' does not match planned global root '{planned_global}'")

    # Strict Scope Containment verification
    for item in plan["items"]:
        dest = Path(item["destination"]).resolve()
        scope = item.get("scope")
        if scope == "project":
            if not _inside(dest, project):
                raise ProductLoopError(f"DESTINATION_ESCAPE: project component {item['component_id']} destination {dest} escapes project root")
        elif scope == "global":
            if not global_root:
                raise ProductLoopError("GLOBAL_ROOT_REQUIRED: global component destination requires global root")
            if not _inside(dest, Path(global_root).resolve()):
                raise ProductLoopError(f"DESTINATION_ESCAPE: global component {item['component_id']} destination {dest} escapes global root")
        elif scope == "external":
            if item.get("status") == "removable":
                raise ProductLoopError(f"UNSUPPORTED_REMOVAL: external reference component {item['component_id']} cannot be uninstalled")
        else:
            raise ProductLoopError(f"INVALID_SCOPE: unrecognized scope '{scope}' for item {item.get('component_id')}")

    for item in plan["activation_items"]:
        dest = Path(item["destination"]).resolve()
        if not _inside(dest, project):
            raise ProductLoopError(f"DESTINATION_ESCAPE: activation destination {dest} escapes project root")

    # Strict Manifest Re-verification
    manifest_path = project / ".vibe-coding-bootstrap/manifest.yaml"
    expected_msha = plan.get("manifest_sha256")
    if expected_msha is not None:
        if not manifest_path.is_file():
            raise ProductLoopError("STALE_BOOTSTRAP_PLAN: manifest.yaml was deleted after plan approval")
        actual_msha = _sha(manifest_path.read_bytes())
        if actual_msha != expected_msha:
            raise ProductLoopError(f"STALE_BOOTSTRAP_PLAN: manifest.yaml content drifted after plan approval ({actual_msha[:8]} != {expected_msha[:8]})")
        try:
            current_mdata = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ProductLoopError(f"STALE_BOOTSTRAP_PLAN: manifest.yaml corrupted: {exc}")
        if not isinstance(current_mdata, dict) or current_mdata.get("format_version") != 1 or not isinstance(current_mdata.get("items"), list):
            raise ProductLoopError("STALE_BOOTSTRAP_PLAN: manifest.yaml structure invalid")

        for item in plan["items"]:
            comp_id = item["component_id"]
            matched = [it for it in current_mdata.get("items", []) if it.get("component_id") == comp_id and it.get("scope") == item["scope"]]
            if not matched:
                raise ProductLoopError(f"STALE_BOOTSTRAP_PLAN: component '{comp_id}' is no longer recorded in manifest.yaml")
            if any(m.get("sha256") != item["sha256"] for m in matched):
                raise ProductLoopError(f"STALE_BOOTSTRAP_PLAN: component '{comp_id}' hash changed in manifest.yaml")

        if plan.get("remaining_manifest_items"):
            planned_removals_ids = {item["component_id"] for item in plan["items"] if item["status"] == "removable"}
            actual_remaining = [it for it in current_mdata.get("items", []) if it.get("component_id") not in planned_removals_ids]
            if _sha(_canonical(actual_remaining)) != _sha(_canonical(plan.get("remaining_manifest_items", []))):
                raise ProductLoopError("STALE_BOOTSTRAP_PLAN: remaining manifest items drifted after plan approval")
    else:
        if manifest_path.exists():
            raise ProductLoopError("STALE_BOOTSTRAP_PLAN: manifest.yaml was created after planning")

    activation_manifest_path = project / activation.MANIFEST_PATH
    expected_act_msha = plan.get("activation_manifest_sha256")
    if expected_act_msha is not None:
        if not activation_manifest_path.is_file():
            raise ProductLoopError("STALE_BOOTSTRAP_PLAN: activation.json was deleted after plan approval")
        actual_act_msha = _sha(activation_manifest_path.read_bytes())
        if actual_act_msha != expected_act_msha:
            raise ProductLoopError("STALE_BOOTSTRAP_PLAN: activation.json content drifted after plan approval")
    else:
        if activation_manifest_path.exists():
            raise ProductLoopError("STALE_BOOTSTRAP_PLAN: activation.json was created after planning")

    # Strict preflight check: files scheduled for removal must STILL match the planned SHA
    for item in plan["items"]:
        if item["status"] == "removable":
            target = Path(item["destination"])
            if not target.is_file() or _sha(target.read_bytes()) != item["sha256"]:
                raise ProductLoopError(f"STALE_BOOTSTRAP_PLAN: target file content drifted before uninstall: {item['destination']}")
    for item in plan["activation_items"]:
        if item["status"] == "removable":
            target = Path(item["destination"])
            if not target.is_file() or _sha(target.read_bytes()) != item["sha256"]:
                raise ProductLoopError(f"STALE_BOOTSTRAP_PLAN: activation file content drifted before uninstall: {item['destination']}")

    # Build recovery plan for snapshot
    recovery_plan = {
        "plan_digest": plan["plan_digest"],
        "project_changes": [
            {"destination": item["destination"], "result": "removed", "scope": item["scope"]}
            for item in plan["items"] if item["status"] == "removable" and item["scope"] == "project"
        ],
        "global_changes": [
            {"destination": item["destination"], "result": "removed", "scope": item["scope"]}
            for item in plan["items"] if item["status"] == "removable" and item["scope"] == "global"
        ],
        "global_root": str(Path(global_root).resolve()) if global_root else planned_global,
        "host_activations": [],
        "manifest_updates": plan.get("manifest_updates", []),
    }
    removable_acts = [item for item in plan["activation_items"] if item["status"] == "removable"]
    seen_host_caps = set()
    for item in removable_acts:
        cid = item.get("component_id", "")
        if cid.startswith("codex-"):
            h = "codex"
        elif cid.startswith("claude-") or cid.startswith("claude_code-"):
            h = "claude"
        elif cid.startswith("antigravity-"):
            h = "antigravity"
        else:
            h = "codex"
        cap = item["source_capability"]
        if (h, cap) not in seen_host_caps:
            seen_host_caps.add((h, cap))
            conf = activation._get_target_config(h, cap)
            skill_dst = conf["skill_destination"] if conf else item.get("relative_destination")
            agents_dst = conf["agents_destination"] if conf else None
            recovery_plan["host_activations"].append({
                "host": h,
                "capability_id": cap,
                "destination": skill_dst,
                "agents_destination": agents_dst,
                "status": "removed",
            })

    try:
        deployment = prepare_deployment(recovery_plan, project, global_root)
    except ValueError as exc:
        raise ProductLoopError(str(exc)) from exc

    # Execute safe removal
    touched_dirs = []
    global_dirs = []
    try:
        for item in plan["items"]:
            if item["status"] == "removable":
                p = Path(item["destination"])
                if p.is_file():
                    if item["scope"] == "project":
                        touched_dirs.append(p.parent)
                    else:
                        global_dirs.append(p.parent)
                    p.unlink()

        for item in plan["activation_items"]:
            if item["status"] == "removable":
                p = Path(item["destination"])
                if p.is_file():
                    touched_dirs.append(p.parent)
                    p.unlink()

        # Clean empty parent directories
        for d in sorted(set(touched_dirs), key=lambda p: len(p.parts), reverse=True):
            cur = d
            while cur != project:
                try:
                    cur.rmdir()
                except OSError:
                    break
                cur = cur.parent

        if global_root:
            groot = Path(global_root).resolve()
            for d in sorted(set(global_dirs), key=lambda p: len(p.parts), reverse=True):
                cur = d
                while cur != groot and cur.is_relative_to(groot):
                    try:
                        cur.rmdir()
                    except OSError:
                        break
                    cur = cur.parent

        # Update or remove manifests
        manifest_path = project / ".vibe-coding-bootstrap/manifest.yaml"
        if manifest_path.is_file() and ".vibe-coding-bootstrap/manifest.yaml" in plan.get("manifest_updates", []):
            remaining = plan.get("remaining_manifest_items", [])
            if remaining:
                mdata = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
                mdata["items"] = remaining
                manifest_path.write_text(yaml.safe_dump(mdata), encoding="utf-8")
            else:
                manifest_path.unlink()

        act_manifest_path = project / activation.MANIFEST_PATH
        if act_manifest_path.is_file() and any(item["status"] in {"removable", "already_absent"} for item in plan["activation_items"]):
            removed_pairs = set()
            for item in plan["activation_items"]:
                if item["status"] in {"removable", "already_absent"}:
                    cid = item.get("component_id", "")
                    if cid.startswith("codex-"):
                        h = "codex"
                    elif cid.startswith("claude-") or cid.startswith("claude_code-"):
                        h = "claude"
                    elif cid.startswith("antigravity-"):
                        h = "antigravity"
                    else:
                        h = "codex"
                    removed_pairs.add((h, item["source_capability"]))
            try:
                mdata = json.loads(act_manifest_path.read_text(encoding="utf-8"))
                current_acts = mdata.get("activations")
                if not current_acts and mdata.get("host") and mdata.get("capability"):
                    current_acts = [{
                        "host": mdata["host"],
                        "capability": mdata["capability"],
                        "artifacts": mdata.get("artifacts", [])
                    }]
                if not isinstance(current_acts, list):
                    current_acts = []
                remaining_acts = [a for a in current_acts if (a.get("host"), a.get("capability")) not in removed_pairs]
                if not remaining_acts:
                    act_manifest_path.unlink()
                else:
                    all_artifacts = []
                    seen_dest = set()
                    for act in remaining_acts:
                        for art in act.get("artifacts", []):
                            if art["destination"] not in seen_dest:
                                seen_dest.add(art["destination"])
                                all_artifacts.append(art)
                    updated_manifest = {
                        "format_version": 1,
                        "host": remaining_acts[0]["host"] if len(remaining_acts) == 1 else "multiple",
                        "capability": remaining_acts[0]["capability"] if len(remaining_acts) == 1 else "multiple",
                        "artifacts": all_artifacts,
                        "activations": remaining_acts,
                    }
                    act_manifest_path.write_text(json.dumps(updated_manifest, indent=2) + "\n", encoding="utf-8")
            except Exception:
                act_manifest_path.unlink()

        # Remove .vibe-coding-bootstrap directory if empty
        vcb_dir = project / ".vibe-coding-bootstrap"
        if vcb_dir.is_dir():
            try:
                vcb_dir.rmdir()
            except OSError:
                pass
    except Exception as exc:
        mutated = update_record_after_failure(project, deployment)
        if mutated:
            raise ProductLoopError(f"PARTIAL_APPLY: deployment paths changed before failure: {exc}") from exc
        raise

    # Post-apply verification
    try:
        for item in plan["items"]:
            if item["status"] == "removable":
                target = Path(item["destination"])
                if target.exists():
                    raise ProductLoopError(f"POST_APPLY_VERIFICATION_FAILED: failed to remove {item['destination']}")
        for item in plan["activation_items"]:
            if item["status"] == "removable":
                target = Path(item["destination"])
                if target.exists():
                    raise ProductLoopError(f"POST_APPLY_VERIFICATION_FAILED: failed to remove {item['destination']}")
    except Exception:
        update_record(project, deployment, "partial")
        raise

    update_record(project, deployment, "applied")
    return {
        "status": "applied",
        "operation": "uninstall",
        "target_before": plan["target"],
        "target_after": bind_target_revision(project),
        "removals": plan["removals"],
        "deployment_id": deployment["deployment_id"],
        "snapshot_ref": deployment["snapshot_ref"],
        "snapshot_commit": deployment["snapshot_commit"],
        "rollback_available": True,
        "verification": {"passed": True},
    }


def uninstall(project_root, component_id=None, global_root=None,
              apply=False, decision=None, plan=None, root=ROOT):
    """Standardized VCBS safe uninstall."""
    active_plan = load_json(plan) if isinstance(plan, (str, Path)) else plan
    if active_plan is None:
        active_plan = build_uninstall_plan(project_root, component_id=component_id,
                                           global_root=global_root, root=root)

    if active_plan["actual_changes"] == 0:
        return {"status": "no_changes", "message": "No VCBS-managed components to uninstall.", "plan": active_plan}

    if not apply:
        return {"status": "preview", "plan": active_plan, "summary": render_uninstall_summary(active_plan)}

    if not decision:
        raise ProductLoopError("DECISION_REQUIRED: execution requires an explicit approved decision artifact; provide decision")
    dec = load_json(decision) if isinstance(decision, (str, Path)) else decision

    return apply_uninstall(active_plan, dec, project_root, global_root=global_root, root=root)
