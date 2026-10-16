"""Build bounded semantic-review packages and validate Host AI assessments."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re

from composer.core import ROOT, load_yaml, validate
from .core import _read_text, discover
from .revision import bind_target_revision


MAX_EXCERPT_CHARS = 2600
RULE_OR_COMMAND = re.compile(
    r"\b(?:must|must not|never|always|required|shall|forbidden|prohibited)\b|"
    r"(?:必须|禁止|不得|严禁|应当|必须先)|`[^`\n]{1,100}`|\bworktrees?\b",
    re.IGNORECASE,
)
WORD = re.compile(r"[a-z0-9]{4,}|[\u4e00-\u9fff]{2,}", re.IGNORECASE)


def authority_hint(relative_path):
    """Classify current authority and freshness using a few visible path rules."""
    path = relative_path.replace("\\", "/").lower()
    if path in {"agents.md", "claude.md", "spec.md", "docs/constraints.md", "docs/roadmap.md"}:
        return "primary"
    if path.startswith(("docs/archive/", "archive/")) or "/plans/" in f"/{path}" or path.startswith("plans/"):
        return "historical"
    if (path == "docs/start_here.md" or path.startswith(("docs/process/", ".claude/skills/", ".agents/skills/", ".opencode/skills/", ".github/workflows/", ".github/vccp/", ".cursor/rules/"))):
        return "active"
    return "reference"


def activation_hint(relative_path, text):
    """Describe when an asset is used from its own trigger wording, not its path."""
    if not relative_path.lower().endswith("/skill.md"):
        return "unknown"
    opening = "\n".join(text.splitlines()[:12]).lower()
    if "any conversation" in opening or "every conversation" in opening:
        return "always"
    if any(marker in opening for marker in ("use when", "use for", "when to use", "trigger:")):
        return "conditional"
    if "optional" in opening:
        return "optional"
    return "unknown"


def _capability_terms(record):
    values = [record["id"].replace("-", " "), record["purpose"]]
    values.extend(record["selection_hints"]["signals"])
    return {word.lower() for value in values for word in WORD.findall(value)}


def relevant_excerpt(text, candidate_records, max_chars=MAX_EXCERPT_CHARS):
    """Select a bounded set of headings, opening paragraphs, and relevant rules."""
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    terms = set().union(*(_capability_terms(record) for record in candidate_records)) if candidate_records else set()
    ranked = []
    non_heading_count = 0
    for index, block in enumerate(blocks):
        heading = block.lstrip().startswith("#")
        directive = bool(RULE_OR_COMMAND.search(block))
        hits = len({word.lower() for word in WORD.findall(block)} & terms)
        score = (5 if heading else 0) + (6 if directive else 0) + min(hits, 5)
        if index == 0 and heading:
            score += 100
        if not heading and non_heading_count < 2:
            score += 3
            non_heading_count += 1
        if "worktree" in block.lower() or "worktrees" in block.lower():
            score += 6
        ranked.append((score, index, block))

    selected = []
    used = 0
    for _, index, block in sorted(ranked, key=lambda item: (-item[0], item[1])):
        addition = len(block) + (2 if selected else 0)
        if used + addition > max_chars:
            room = max_chars - used - (2 if selected else 0)
            if room > 0:
                selected.append((index, block[:room]))
            break
        selected.append((index, block))
        used += addition
    selected.sort(key=lambda item: item[0])
    return "\n\n".join(block for _, block in selected)[:max_chars]


def _catalog_records(root):
    catalog = load_yaml(root / "capabilities/catalog.yaml")
    records = []
    for relative in catalog["capabilities"]:
        record = load_yaml(root / "capabilities" / relative)
        copy_components = []
        for component in record.get("components", []):
            assembly = component.get("assembly")
            if assembly:
                copy_components.append({
                    "id": component["id"],
                    "delivery": assembly["delivery"],
                    "scope": assembly["scope"],
                })
        records.append({
            "id": record["id"],
            "purpose": record["purpose"],
            "applicability": record["applicability"],
            "selection_hints": record["selection_hints"],
            "requires": record.get("requires", []),
            "conflicts_with": record.get("conflicts_with", []),
            "bootstrap_components": copy_components,
        })
    return records


def build_semantic_input(project_root, root=ROOT):
    """Create a validated Host AI input package without writing to the target."""
    project = Path(project_root).resolve(strict=True)
    baseline = discover(project, root)
    records = _catalog_records(root)
    record_by_id = {record["id"]: record for record in records}
    baseline_by_id = {item["capability_id"]: item for item in baseline["capability_coverage"]}
    assets = []
    for asset in baseline["discovered_assets"]:
        path = asset["path"]
        candidates = [record_by_id[cid] for cid in asset["candidate_capabilities"]]
        assets.append({
            "path": path,
            "asset_type": asset["asset_type"],
            "ownership": asset["ownership"],
            "summary": asset["summary"],
            "relevant_excerpt": relevant_excerpt(_read_text(project / path), candidates),
            "baseline_candidate_capabilities": asset["candidate_capabilities"],
            "authority_hint": authority_hint(path),
            "activation_hint": activation_hint(path, _read_text(project / path)),
        })

    profile = load_yaml(root / "profiles/personal.yaml")
    validate(profile, "profile.schema.json", root)
    preferences = profile.get("preferences", {})
    explicit_preference_fields = {
        "preferred_languages", "preferred_tools", "interaction_style", "planning_level",
        "testing_approach", "review_before_changes", "additional",
    }
    package = {
        "target": bind_target_revision(project),
        "project": baseline["project"],
        "capability_catalog": records,
        "discovered_assets": assets,
        "baseline_coverage": [
            {key: item[key] for key in ("capability_id", "status", "evidence", "reason")}
            for item in baseline["capability_coverage"]
        ],
        "baseline_report_source": "deterministic discovery generated during semantic-package construction",
        "profile_constraints": {
            "defaults": profile.get("default_capability_ids", []),
            "exclusions": profile.get("excluded_capability_ids", []),
            "explicit_preferences": {
                key: preferences[key] for key in explicit_preference_fields if key in preferences
            },
        },
        "review_instructions": [
            "Review asset meaning from relevant_excerpt and authority_hint; do not treat a path or filename as proof of capability.",
            "Use only the supplied Capability Catalog IDs. Compare the existing assets with each Catalog purpose and applicability.",
            "Judge satisfied by substantive coverage, not by the number of cited files. Judge missing only when applicability is established and the core purpose is not covered; otherwise use unknown.",
            "A Skill's presence proves availability only. Infer activation from its trigger wording and current project-authority references; a Skill's path alone never proves activation or mandate.",
            "Call a Profile conflict only when the preference is explicit, the project requirement is current and authoritative, both constrain the same behavior, and they cannot reasonably be satisfied together. Historical plans and a Skill's mere presence cannot establish conflict.",
            "MCP or AI coding-tool use alone does not establish agent-development. Distinguish the product's actual behavior from development tooling.",
            "Every evidence item must cite a discovered project-relative path and explain the supporting meaning. Use no absolute local paths.",
            "Do not recommend installation, modification, activation, or automatic resolution. A component may be considered only for a confirmed missing capability and an applicable project-scoped copy component listed in the Catalog.",
            "Treat Profile preferences as defaults, not prohibitions; retain reasonable project-specific exceptions and safety approvals.",
            "Assess each explicit preference separately. Use only aligned, compatible, tension, conflict, or unknown; cite current discovered evidence and keep preference findings separate from Capability coverage.",
            "For worktree_strategy, determine whether current authoritative project rules require worktrees. The existence of using-git-worktrees/SKILL.md alone is not a mandate.",
            "Do not recommend changes where existing coverage is sufficient. Complementary Skills are not conflicts; do not propose duplicate Bootstrap capabilities.",
        ],
    }
    validate(package, "semantic-review-input.schema.json", root)
    return package


def validate_semantic_result(result, package, root=ROOT):
    """Validate schema, catalog membership, evidence links, and action/status fit."""
    validate(package, "semantic-review-input.schema.json", root)
    validate(result, "semantic-review-result.schema.json", root)
    if result["project_name"] != package["project"]["name"]:
        raise ValueError("Semantic review project does not match input package")
    current_catalog = _catalog_records(root)
    if package["capability_catalog"] != current_catalog:
        raise ValueError("Input Capability Catalog does not match the current Catalog")
    records = {record["id"]: record for record in current_catalog}
    package_baseline_ids = [item["capability_id"] for item in package["baseline_coverage"]]
    if len(package_baseline_ids) != len(set(package_baseline_ids)) or set(package_baseline_ids) != set(records):
        raise ValueError("Baseline coverage must contain every Catalog Capability exactly once")
    package_asset_paths = [asset["path"] for asset in package["discovered_assets"]]
    if len(package_asset_paths) != len(set(package_asset_paths)):
        raise ValueError("Duplicate discovered asset path in input package")
    found = [item["capability_id"] for item in result["semantic_coverage"]]
    if len(found) != len(set(found)):
        raise ValueError("Duplicate Capability result")
    if set(found) != set(records):
        raise ValueError("Result Capability IDs must match the current Catalog exactly")
    asset_paths = {asset["path"] for asset in package["discovered_assets"]}
    preference_findings = result.get("preference_findings", [])
    preference_ids = [item["preference_id"] for item in preference_findings]
    if len(preference_ids) != len(set(preference_ids)):
        raise ValueError("Duplicate Profile preference finding")
    explicit = package["profile_constraints"]["explicit_preferences"]
    expected_preferences = (set(explicit) - {"additional"}) | set(explicit.get("additional", {}))
    if {"prefer_simple_solutions", "avoid_unnecessary_abstraction"}.issubset(expected_preferences):
        expected_preferences.difference_update({"prefer_simple_solutions", "avoid_unnecessary_abstraction"})
        expected_preferences.add("solution_complexity")
    if set(preference_ids) != expected_preferences:
        raise ValueError("Preference findings must assess every explicit Profile preference exactly once")
    authoritative_paths = {asset["path"] for asset in package["discovered_assets"]
                           if asset["authority_hint"] == "primary"}
    for finding in preference_findings:
        if any(evidence["path"] not in asset_paths for evidence in finding["evidence"]):
            raise ValueError(f"Preference evidence path is not a discovered asset: {finding['preference_id']}")
        if finding["status"] == "conflict" and not authoritative_paths.intersection(
                evidence["path"] for evidence in finding["evidence"]):
            raise ValueError(f"Preference conflict requires current authoritative evidence: {finding['preference_id']}")
    for item in result["semantic_coverage"]:
        capability_id = item["capability_id"]
        if any(evidence["path"] not in asset_paths for evidence in item["evidence"]):
            raise ValueError(f"Evidence path is not a discovered asset: {capability_id}")
        if item["status"] in {"missing", "conflict"} and not item["reason"].strip():
            raise ValueError(f"{item['status']} requires a reason: {capability_id}")
        if item["status"] == "conflict" and not item["evidence"]:
            raise ValueError(f"Conflict requires supporting evidence: {capability_id}")
        action = item["recommended_action"]
        if item["status"] == "conflict" and action != "review_conflict":
            raise ValueError(f"Conflict must use review_conflict: {capability_id}")
        if item["status"] == "partial" and action != "review_gap":
            raise ValueError(f"Partial coverage must use review_gap: {capability_id}")
        if item["status"] == "unknown" and action != "no_action":
            raise ValueError(f"Unknown coverage must use no_action: {capability_id}")
        if item["status"] == "satisfied" and action not in {"keep_existing", "no_action"}:
            raise ValueError(f"Satisfied coverage cannot recommend an addition: {capability_id}")
        if item["status"] == "missing" and action not in {"review_gap", "consider_bootstrap_component"}:
            raise ValueError(f"Missing coverage must use a gap-review action: {capability_id}")
        if action == "consider_bootstrap_component":
            available = records[capability_id]["bootstrap_components"]
            if item["status"] != "missing" or not any(
                    component["delivery"] == "copy" and component["scope"] == "project"
                    for component in available):
                raise ValueError(f"No applicable project-scoped Bootstrap component for {capability_id}")
    return result


def build_semantic_adoption_report(package, result, baseline_report_source):
    """Combine an already-validated semantic result with its deterministic baseline."""
    baseline = {item["capability_id"]: item for item in package["baseline_coverage"]}
    semantic = {item["capability_id"]: item for item in result["semantic_coverage"]}
    delta = [
        {"capability_id": capability_id, "baseline": baseline[capability_id]["status"],
         "semantic": semantic[capability_id]["status"], "change": "status_changed"}
        for capability_id in sorted(semantic)
        if baseline[capability_id]["status"] != semantic[capability_id]["status"]
    ]
    return {
        "target": package["target"],
        "project": package["project"],
        "baseline_report_source": baseline_report_source,
        "baseline_coverage": package["baseline_coverage"],
        "semantic_coverage": result["semantic_coverage"],
        "preference_findings": result.get("preference_findings", []),
        "semantic_delta": delta,
        "conflicts": [item["capability_id"] for item in semantic.values() if item["status"] == "conflict"],
        "gaps": [item["capability_id"] for item in semantic.values() if item["status"] == "missing"],
        "recommendations": [
            {"capability_id": item["capability_id"], "status": item["status"],
             "action": item["recommended_action"], "reason": item["reason"]}
            for item in result["semantic_coverage"]
            if item["recommended_action"] not in {"keep_existing", "no_action"}
        ],
        "explicitly_no_action": [
            item["capability_id"] for item in result["semantic_coverage"]
            if item["recommended_action"] in {"keep_existing", "no_action"}
        ],
        "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "reviewer": result["reviewer"],
    }


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
