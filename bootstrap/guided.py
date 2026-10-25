"""Small, deterministic helpers for the guided VCBS setup interface."""

from pathlib import Path
import shutil

from adoption.core import discover
from adoption.revision import bind_target_revision
from activation import core as activation
from bootstrap.core import _parse_hosts, inspect_project
from composer.core import compose, load_yaml


def _technologies(project):
    files = {path.name.lower() for path in project.iterdir()}
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
    if any(path.suffix.lower() == ".csproj" for path in project.iterdir()):
        found.append("C#")
    if "pubspec.yaml" in files:
        found.extend(("Dart", "Flutter"))
    return sorted(set(found))


def build_context(project_root, goal=None, project_type=None, lifecycle=None,
                  technologies=None, platform=None, collaboration=None,
                  constraints=None):
    project = Path(project_root).resolve(strict=True)
    report = discover(project)
    target = bind_target_revision(project)
    technologies = sorted(set(technologies or _technologies(project)))
    inferred_lifecycle = lifecycle or ("long_lived" if any(
        (project / name).exists() for name in ("pyproject.toml", "package.json", "Cargo.toml", "go.mod", "pom.xml")
    ) else "prototype")
    inferred_type = project_type or "software project"
    inferred_runtime = ", ".join(technologies) if technologies else "Not detected"
    remote = bool(target.get("remote"))
    description = report["project"].get("description") or project.name
    return {
        "name": project.name,
        "description": description,
        "project_type": inferred_type,
        "lifecycle": inferred_lifecycle,
        "technology_tags": technologies,
        "runtime": inferred_runtime,
        "platform": platform or "local",
        "goal": goal or "Configure a suitable set of AI development capabilities.",
        "constraints": sorted(set(constraints or [])),
        "collaboration": collaboration or ("team" if remote else "solo"),
    }


def inspect_setup(project_root, host="codex", root=None, vcbs_home=None):
    from bootstrap.recommendation import build_recommendation_package
    project = Path(project_root).resolve(strict=True)
    effective_root = Path(root) if root else Path(__file__).resolve().parents[1]
    rec_pkg = build_recommendation_package(project, host=host, root=effective_root, vcbs_home=vcbs_home)
    report = discover(project, root) if root else discover(project)
    inspection = inspect_project(project, host, root) if root else inspect_project(project, host)
    target = bind_target_revision(project)
    target["path"] = str(project)
    available = {
        "codex": {"available": shutil.which("codex") is not None, "command": "codex"},
        "claude": {"available": shutil.which("claude") is not None, "command": "claude"},
        "antigravity": {"available": shutil.which("agy") is not None, "command": "agy"},
    }
    selected_hosts = _parse_hosts(host)
    catalog = load_yaml((Path(root) if root else Path(__file__).resolve().parents[1]) / "capabilities/catalog.yaml")
    records = [load_yaml((Path(root) if root else Path(__file__).resolve().parents[1]) / "capabilities" / entry)
               for entry in catalog["capabilities"]]
    if vcbs_home is not None:
        from bootstrap.home import load_user_capabilities
        records.extend(load_user_capabilities(vcbs_home))
    options = []
    for record in records:
        components = []
        for component in record.get("components", []):
            metadata = component.get("assembly")
            if not metadata:
                components.append({"id": component["id"], "status": "external_reference",
                                   "reason": "Catalog entry has no local installable asset."})
                continue
            delivery, scope = metadata["delivery"], metadata["scope"]
            reason = None
            if delivery == "copy" and scope in {"project", "global"}:
                source = Path(root or Path(__file__).resolve().parents[1]) / metadata["source"]
                status = "installable" if source.is_file() else "unsupported"
                if not source.is_file():
                    reason = "The registered local source file is unavailable."
                else:
                    destination = Path(metadata.get("destination", ""))
                    component_target = (project if scope == "project" else None)
                    if scope == "project" and component_target and (component_target / destination).exists():
                        status, reason = "conflict", "A file already occupies the registered destination; ownership must be reviewed."
            elif delivery == "reference" and scope == "external":
                status, reason = "external_reference", "The resource stays a reference; VCBS does not download or execute it."
            elif delivery == "reference" and scope == "project" and metadata.get("provenance", {}).get("activation_adapter"):
                adapter = metadata["provenance"]["activation_adapter"]
                capability = "testing" if adapter == "codex" else "vcbs-manager"
                skill = activation.build_plan(project, capability, adapter, root or activation.ROOT)
                status = "conflict" if skill["status"] in {"conflict", "review_required"} else "installable"
                if status == "conflict":
                    reason = "The native Skill destination or ownership manifest requires review."
                elif not available.get(adapter, {}).get("available"):
                    reason = f"Workspace Skill files can be prepared, but the {adapter} runtime was not detected."
            elif delivery == "reference" and scope == "project":
                status, reason = "reference_only", "This item is a project reference, not an installed asset."
            else:
                status, reason = "unsupported", f"No supported local delivery path for {scope}/{delivery}."
            components.append({"id": metadata["id"], "type": metadata["type"], "scope": scope,
                               "delivery": delivery, "status": status, **({"reason": reason} if reason else {})})
        options.append({"capability_id": record["id"], "name": record["name"],
                        "purpose": record["purpose"], "requires": record.get("requires", []),
                        "constraints": record["selection_hints"]["constraints"],
                        "components": components})
    shortlist = None
    if inspection["route"] == "compose":
        context = build_context(project)
        recommendation = compose(context, root, vcbs_home=vcbs_home) if root else compose(context, vcbs_home=vcbs_home)
        ranked = sorted(recommendation["recommendations"],
                        key=lambda item: (-["low", "medium", "high"].index(item["confidence"]), item["capability_id"]))
        shortlist = [{"rank": index, "capability_id": item["capability_id"],
                      "confidence": item["confidence"], "reason": item["reason"],
                      "constraints": item["constraints"], "requires": item["requires"]}
                     for index, item in enumerate(ranked, start=1)]
    return {
        "format_version": 1,
        "target": target,
        "project": {**report["project"], "technologies": _technologies(project)},
        "route": inspection["route"],
        "host_selection": selected_hosts,
        "available_hosts": available,
        "existing_assets": [{"path": a["path"], "type": a["asset_type"], "summary": a["summary"]}
                           for a in report["discovered_assets"] if a["asset_type"] != "documentation"],
        "capability_options": options,
        "recommendations": shortlist,
        "recommendation_input": rec_pkg,
        "managed_components": rec_pkg["managed_components"],
        "personal_preferences": rec_pkg["personal_preferences"],
        "matching_experiences": rec_pkg["matching_experiences"],
        "conflict_signals": rec_pkg["conflict_signals"],
        "fallback_static_recommendations": rec_pkg["fallback_static_recommendations"],
        "semantic_review_required": inspection["route"] == "adoption",
        **({"semantic_package": inspection["semantic_package"]} if inspection["route"] == "adoption" else {}),
        "consumption_note": "Host command availability and installed files do not prove a host discovered or used a Skill.",
    }
