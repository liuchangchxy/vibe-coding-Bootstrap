"""Host-driven AI recommendation package builder and validator.

Responsibilities:
- Build bounded machine-readable recommendation fact packages (facts, catalog, preferences, experience).
- Validate Host semantic recommendation results against schemas, target revisions, known IDs, and conflict rules.
- Support "install nothing" as a first-class valid recommendation.
- Ensure static rules remain solely an explicit fallback.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import yaml

from activation import core as activation
from adoption.core import discover
from adoption.revision import bind_target_revision
from bootstrap.core import ProductLoopError, _owned_state, _parse_hosts
from bootstrap.home import (
    get_vcbs_home,
    load_profile,
    load_user_capabilities,
    load_verified_experiences,
)
from composer.core import ROOT, compose, load_yaml, validate


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _detect_technologies(project: Path) -> list[str]:
    files = {p.name.lower() for p in project.iterdir() if p.is_file()}
    found = []
    if "pyproject.toml" in files or "setup.py" in files or any(name.startswith("requirements") and name.endswith(".txt") for name in files):
        found.append("Python")
    if "package.json" in files:
        found.append("TypeScript" if "tsconfig.json" in files else "JavaScript")
    if "cargo.toml" in files:
        found.append("Rust")
    if "go.mod" in files:
        found.append("Go")
    if "pom.xml" in files or "build.gradle" in files or "build.gradle.kts" in files:
        found.append("Java")
    if any(p.suffix.lower() == ".csproj" for p in project.iterdir() if p.is_file()):
        found.append("C#")
    if "pubspec.yaml" in files:
        found.extend(["Dart", "Flutter"])
    return sorted(set(found))


def _managed_components_inventory(project: Path) -> list[dict]:
    managed = []
    manifest_file = project / ".vibe-coding-bootstrap/manifest.yaml"
    if manifest_file.is_file():
        try:
            data = yaml.safe_load(manifest_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("format_version") == 1:
                for item in data.get("items", []):
                    managed.append({
                        "component_id": item.get("component_id"),
                        "scope": item.get("scope"),
                        "destination": item.get("destination"),
                        "version": item.get("version", "1"),
                        "state": item.get("state"),
                    })
        except Exception:
            pass

    act_file = project / activation.MANIFEST_PATH
    if act_file.is_file():
        try:
            data = json.loads(act_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("format_version") == 1:
                for act in data.get("activations", []):
                    managed.append({
                        "component_id": f"{act.get('host')}-{act.get('capability')}-activation",
                        "scope": "project",
                        "destination": act.get("destination"),
                        "host": act.get("host"),
                        "status": "active",
                    })
        except Exception:
            pass
    return managed


def _all_capabilities(root=ROOT, vcbs_home=None) -> list[dict]:
    """Combine built-in Catalog capabilities with user-owned VCBS Home capabilities."""
    capabilities = []
    seen_ids = set()

    # 1. Built-in Catalog capabilities
    catalog = load_yaml(Path(root) / "capabilities/catalog.yaml")
    for relative in catalog["capabilities"]:
        rec = load_yaml(Path(root) / "capabilities" / relative)
        rec["origin"] = "catalog"
        capabilities.append(rec)
        seen_ids.add(rec["id"])

    # 2. User capabilities from VCBS Home
    user_caps = load_user_capabilities(vcbs_home)
    for u_rec in user_caps:
        if u_rec["id"] not in seen_ids:
            u_rec["origin"] = "user_home"
            capabilities.append(u_rec)
            seen_ids.add(u_rec["id"])

    return capabilities


def build_recommendation_package(project_root, host="codex", root=ROOT, vcbs_home=None) -> dict:
    """Build a bounded machine-readable fact package for current Host AI reasoning.

    Facts remain strictly distinguishable from recommendations.
    """
    project = Path(project_root).resolve(strict=True)
    if not project.is_dir():
        raise ProductLoopError("INVALID_PROJECT_ROOT: target must be a directory")

    target = bind_target_revision(project)
    target["path"] = str(project)

    report = discover(project, root)
    technologies = _detect_technologies(project)
    managed_components = _managed_components_inventory(project)
    managed_only, signals, unmanaged = _owned_state(project)

    available_hosts = {
        "codex": {"available": shutil.which("codex") is not None, "command": "codex"},
        "claude": {"available": shutil.which("claude") is not None, "command": "claude"},
        "antigravity": {"available": shutil.which("agy") is not None, "command": "agy"},
    }
    selected_hosts = _parse_hosts(host)

    # Personal preferences from VCBS Home
    profile = load_profile(vcbs_home, root=root)
    personal_preferences = profile.get("preferences", {})

    # Reusable verified Experience matching project technologies or type
    all_exp = load_verified_experiences(vcbs_home, root=root)
    matching_exp = []
    for exp in all_exp:
        exp_ctx = exp.get("project_context", {})
        exp_techs = set(exp_ctx.get("technologies", []))
        if not exp_techs or exp_techs.intersection(set(technologies)):
            matching_exp.append({
                "id": exp.get("id"),
                "title": exp.get("title"),
                "summary": exp.get("summary"),
                "applicable_capabilities": exp.get("applicable_capabilities", []),
                "origin": exp.get("origin", "user_home"),
            })

    # Available Capabilities & Component Options
    raw_caps = _all_capabilities(root=root, vcbs_home=vcbs_home)
    available_capabilities = []
    conflict_signals = []

    for cap in raw_caps:
        components = []
        for comp in cap.get("components", []):
            assembly = comp.get("assembly")
            if not assembly:
                components.append({
                    "id": comp["id"],
                    "kind": comp.get("kind", "reference"),
                    "status": "external_reference",
                    "reason": "Catalog entry has no local installable asset.",
                })
                continue

            delivery, scope = assembly["delivery"], assembly["scope"]
            status = "installable"
            reason = None

            if delivery == "copy" and scope in {"project", "global"}:
                source_base = (
                    Path(cap["source_path"]).parent
                    if cap.get("origin") == "user_home" and "source_path" in cap
                    else Path(root)
                )
                source = source_base / assembly["source"]
                if not source.is_file():
                    status, reason = "unsupported", "Source template is not available."
                elif scope == "project":
                    dest = project / assembly.get("destination", "")
                    if dest.exists():
                        # Check if managed
                        is_managed = any(m.get("destination") == assembly.get("destination") for m in managed_components)
                        if not is_managed:
                            status, reason = "conflict", f"A pre-existing unmanaged file occupies '{assembly.get('destination')}'."
                            conflict_signals.append({
                                "component_id": comp["id"],
                                "destination": assembly.get("destination"),
                                "reason": reason,
                            })
            elif delivery == "reference" and scope == "project" and assembly.get("provenance", {}).get("activation_adapter"):
                adapter = assembly["provenance"]["activation_adapter"]
                capability_name = "testing" if adapter == "codex" else "vcbs-manager"
                skill_plan = activation.build_plan(project, capability_name, adapter, root or activation.ROOT)
                if skill_plan["status"] in {"conflict", "review_required"}:
                    status, reason = "conflict", "Native skill destination or activation manifest requires review."
                    conflict_signals.append({
                        "component_id": comp["id"],
                        "destination": skill_plan.get("destination"),
                        "reason": reason,
                    })
                elif not available_hosts.get(adapter, {}).get("available"):
                    status, reason = "installable", f"Host runtime '{adapter}' not detected; files can be installed."
            elif delivery == "reference" and scope == "external":
                status, reason = "external_reference", "External reference; not locally installed."
            else:
                status, reason = "reference_only", "Project reference component."

            components.append({
                "id": assembly["id"],
                "type": assembly.get("type", comp.get("kind", "template")),
                "scope": scope,
                "delivery": delivery,
                "status": status,
                "destination": assembly.get("destination"),
                **({"reason": reason} if reason else {}),
            })

        available_capabilities.append({
            "capability_id": cap["id"],
            "name": cap.get("name", cap["id"]),
            "purpose": cap.get("purpose", ""),
            "applicability": cap.get("applicability", {}),
            "requires": cap.get("requires", []),
            "conflicts_with": cap.get("conflicts_with", []),
            "origin": cap.get("origin", "catalog"),
            "components": components,
        })

    # Fallback static recommendations (explicitly isolated as fallback)
    fallback_static_recommendations = []
    try:
        synth_context = {
            "name": project.name,
            "description": report["project"].get("description") or project.name,
            "project_type": "software project",
            "lifecycle": "production" if any((project / f).exists() for f in ("pyproject.toml", "package.json", "Cargo.toml")) else "prototype",
            "technology_tags": technologies,
            "runtime": ", ".join(technologies) if technologies else "Not detected",
            "platform": "local",
            "goal": "Recommended baseline setup.",
            "constraints": [],
            "collaboration": "solo",
        }
        raw_rec = compose(synth_context, root)
        for item in raw_rec.get("recommendations", []):
            fallback_static_recommendations.append({
                "capability_id": item["capability_id"],
                "reason": item["reason"],
                "confidence": item["confidence"],
                "is_fallback": True,
                "source": "fallback_static_rules",
            })
    except Exception:
        pass

    return {
        "format_version": 1,
        "target": target,
        "project_facts": {
            "name": project.name,
            "project_type": "software project",
            "technologies": technologies,
            "description": report["project"].get("description") or project.name,
            "unmanaged_signals": unmanaged,
        },
        "selected_host": host,
        "available_hosts": available_hosts,
        "existing_assets": [
            {
                "path": a["path"],
                "asset_type": a["asset_type"],
                "summary": a.get("summary", ""),
            }
            for a in report.get("discovered_assets", [])
            if a.get("asset_type") != "documentation"
        ],
        "managed_components": managed_components,
        "available_capabilities": available_capabilities,
        "conflict_signals": conflict_signals,
        "personal_preferences": personal_preferences,
        "matching_experiences": matching_exp,
        "fallback_static_recommendations": fallback_static_recommendations,
        "recommendation_guidance": (
            "Current AI Host: reason over project_facts, existing_assets, and personal_preferences. "
            "Output structured recommendation choosing only from available_capabilities. "
            "You may output install_nothing: true if existing assets or tooling are sufficient."
        ),
    }


def _detect_project_name(project: Path) -> str | None:
    pyproject = project / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            name = data.get("project", {}).get("name")
            if name:
                return str(name)
        except Exception:
            pass
    package_json = project / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
            name = data.get("name")
            if name:
                return str(name)
        except Exception:
            pass
    return None


def validate_host_recommendation(rec: dict, project_root, root=ROOT, vcbs_home=None) -> dict:
    """Validate structured Host recommendation against schema, target revision, IDs, and conflicts."""
    project = Path(project_root).resolve(strict=True)
    if not project.is_dir():
        raise ProductLoopError("INVALID_PROJECT_ROOT: target must be a directory")

    # 1. Validate against schema
    validate(rec, "host-recommendation.schema.json", root)

    # 2. Validate target revision binding
    target = bind_target_revision(project)
    rec_target = rec.get("target", {})
    expected_sha = target["commit_sha"]
    rec_sha = rec_target.get("commit_sha", "")

    if not expected_sha.startswith(rec_sha) and not rec_sha.startswith(expected_sha):
        raise ProductLoopError(
            f"STALE_RECOMMENDATION: recommendation target revision {rec_sha[:7]} does not match current target {expected_sha[:7]}"
        )

    if rec_target.get("dirty") is not None and rec_target["dirty"] != target.get("dirty", False):
        raise ProductLoopError(
            "STALE_RECOMMENDATION: target dirty state has changed since recommendation was prepared"
        )

    if rec_target.get("path") is not None:
        rec_path = os.path.normcase(str(Path(rec_target["path"]).resolve()))
        cur_path = os.path.normcase(str(project.resolve()))
        if rec_path != cur_path:
            raise ProductLoopError(
                f"STALE_RECOMMENDATION: recommendation target path '{rec_target['path']}' does not match current target path '{project}'"
            )

    if rec_target.get("repository") is not None:
        rec_repo = rec_target["repository"].strip().lower()
        if rec_repo.endswith(".git"):
            rec_repo = rec_repo[:-4]
        cur_repo = (target.get("repository") or "").strip().lower()
        if cur_repo.endswith(".git"):
            cur_repo = cur_repo[:-4]
        project_dir_name = project.name.strip().lower()
        detected_name = (_detect_project_name(project) or "").strip().lower()

        candidates = set()
        if cur_repo:
            candidates.add(cur_repo)
            if "/" in cur_repo:
                candidates.add(cur_repo.split("/")[-1])
        if project_dir_name:
            candidates.add(project_dir_name)
        if detected_name:
            candidates.add(detected_name)

        matches = False
        for cand in candidates:
            if rec_repo == cand or rec_repo.endswith("/" + cand) or cand.endswith("/" + rec_repo):
                matches = True
                break

        if not matches:
            expected_desc = cur_repo or project_dir_name
            raise ProductLoopError(
                f"STALE_RECOMMENDATION: recommendation target repository '{rec_target['repository']}' does not match current target repository '{expected_desc}'"
            )

    # 3. Known Catalog and user capabilities / components
    raw_caps = _all_capabilities(root=root, vcbs_home=vcbs_home)
    cap_map = {cap["id"]: cap for cap in raw_caps}
    comp_map = {}
    cap_comp_map = {}
    for cap in raw_caps:
        cap_comp_map[cap["id"]] = set()
        for comp in cap.get("components", []):
            comp_map[comp["id"]] = comp
            cap_comp_map[cap["id"]].add(comp["id"])
            if comp.get("assembly"):
                assembly_id = comp["assembly"]["id"]
                comp_map[assembly_id] = comp
                cap_comp_map[cap["id"]].add(assembly_id)

    # 4. Check 'install_nothing'
    install_nothing = rec.get("install_nothing", False)
    recommendations = rec.get("recommendations", [])
    if install_nothing and recommendations:
        raise ProductLoopError(
            "INVALID_RECOMMENDATION: install_nothing is true but recommendations list is not empty"
        )

    # 5. Validate recommendations
    managed_components = _managed_components_inventory(project)
    for item in recommendations:
        cid = item.get("capability_id")
        if cid not in cap_map:
            raise ProductLoopError(f"INVALID_RECOMMENDATION: unknown capability ID '{cid}'")

        component_ids = item.get("component_ids", [])
        for comp_id in component_ids:
            if comp_id not in comp_map:
                raise ProductLoopError(f"INVALID_RECOMMENDATION: unknown component ID '{comp_id}'")
            if comp_id not in cap_comp_map[cid]:
                raise ProductLoopError(
                    f"INVALID_RECOMMENDATION: component '{comp_id}' does not belong to capability '{cid}'"
                )

            # Check for conflict with existing unmanaged project file
            comp_def = comp_map[comp_id]
            assembly = comp_def.get("assembly", {})
            if assembly.get("scope") == "project" and assembly.get("destination"):
                dest = project / assembly["destination"]
                if dest.exists():
                    is_managed = any(m.get("destination") == assembly.get("destination") for m in managed_components)
                    if not is_managed:
                        raise ProductLoopError(
                            f"RECOMMENDATION_CONFLICT: component '{comp_id}' conflicts with existing unmanaged project file '{assembly['destination']}'"
                        )

        # Validate item scope against registered component scopes
        rec_scope = item.get("scope")
        if rec_scope is not None:
            if component_ids:
                for comp_id in component_ids:
                    comp_def = comp_map[comp_id]
                    asm = comp_def.get("assembly", {})
                    asm_scope = asm.get("scope")
                    if asm_scope == "project" and rec_scope in {"global", "both"}:
                        raise ProductLoopError(
                            f"INVALID_RECOMMENDATION: component '{comp_id}' registered scope is project; cannot be recommended for '{rec_scope}' scope"
                        )
                    if asm_scope == "global" and rec_scope in {"project", "both"}:
                        raise ProductLoopError(
                            f"INVALID_RECOMMENDATION: component '{comp_id}' registered scope is global; cannot be recommended for '{rec_scope}' scope"
                        )
            else:
                cap_components = cap_map[cid].get("components", [])
                asm_scopes = {
                    c.get("assembly", {}).get("scope")
                    for c in cap_components
                    if c.get("assembly", {}).get("scope") in {"project", "global"}
                }
                if asm_scopes == {"project"} and rec_scope in {"global", "both"}:
                    raise ProductLoopError(
                        f"INVALID_RECOMMENDATION: capability '{cid}' has only project-scope components; cannot be recommended for '{rec_scope}' scope"
                    )
                if asm_scopes == {"global"} and rec_scope in {"project", "both"}:
                    raise ProductLoopError(
                        f"INVALID_RECOMMENDATION: capability '{cid}' has only global-scope components; cannot be recommended for '{rec_scope}' scope"
                    )

    # 6. Validate not_recommended
    for item in rec.get("not_recommended", []):
        cid = item.get("capability_id")
        if cid not in cap_map:
            raise ProductLoopError(f"INVALID_RECOMMENDATION: unknown capability ID '{cid}' in not_recommended")

    return rec
