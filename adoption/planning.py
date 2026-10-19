"""Deterministic Adoption Plan construction and validation."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from composer.core import ROOT, load_yaml, validate
from .revision import bind_target_revision
from .semantic import _catalog_records, validate_semantic_result


DISPOSITIONS = {"keep_existing", "no_action", "review_gap", "review_conflict", "consider_component", "blocked"}


def _component_for(root, capability_id, host=None):
    catalog = load_yaml(root / "capabilities/catalog.yaml")
    for relative in catalog["capabilities"]:
        record = load_yaml(root / "capabilities" / relative)
        if record["id"] != capability_id:
            continue
        for component in record.get("components", []):
            assembly = component.get("assembly", {})
            if assembly.get("delivery") == "copy" and assembly.get("scope") == "project":
                return {
                    "component_id": component["id"], "source_capability": capability_id,
                    "delivery": assembly["delivery"], "scope": assembly["scope"],
                    "source": component.get("source"), "destination": assembly.get("destination"),
                }
        ref_components = [
            c for c in record.get("components", [])
            if c.get("assembly", {}).get("delivery") == "reference" and c.get("assembly", {}).get("scope") == "project"
        ]
        if host and ref_components:
            target_host = "claude" if host in {"claude", "claude_code"} else host
            for component in ref_components:
                assembly = component.get("assembly", {})
                prov = assembly.get("provenance", {})
                adapter = prov.get("activation_adapter")
                if adapter == target_host or (target_host in component.get("id", "")):
                    return {
                        "component_id": component["id"], "source_capability": capability_id,
                        "delivery": assembly["delivery"], "scope": assembly["scope"],
                        "source": component.get("source"), "destination": assembly.get("destination"),
                    }
        if ref_components:
            component = ref_components[0]
            assembly = component.get("assembly", {})
            return {
                "component_id": component["id"], "source_capability": capability_id,
                "delivery": assembly["delivery"], "scope": assembly["scope"],
                "source": component.get("source"), "destination": assembly.get("destination"),
            }
def _valid_components_for(root, capability_id):
    catalog = load_yaml(root / "capabilities/catalog.yaml")
    results = []
    for relative in catalog["capabilities"]:
        record = load_yaml(root / "capabilities" / relative)
        if record["id"] != capability_id:
            continue
        for component in record.get("components", []):
            assembly = component.get("assembly", {})
            if (assembly.get("delivery") in {"copy", "reference"}) and assembly.get("scope") == "project":
                results.append({
                    "component_id": component["id"], "source_capability": capability_id,
                    "delivery": assembly["delivery"], "scope": assembly["scope"],
                    "source": component.get("source"), "destination": assembly.get("destination"),
                })
    return results


def _digest_payload(plan):
    return {key: value for key, value in plan.items() if key not in {"generated_at", "plan_digest"}}


def plan_digest(plan):
    encoded = json.dumps(_digest_payload(plan), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_adoption_plan(package, result, project_root, semantic_review_source="semantic-review-result.json", root=ROOT, host=None):
    """Build a plan only when the review still binds to the current target state."""
    validate_semantic_result(result, package, root)
    if package["capability_catalog"] != _catalog_records(root):
        raise ValueError("STALE_CATALOG_REVIEW: regenerate Semantic Review against the current Catalog before planning")
    expected = package.get("target")
    if not expected:
        raise ValueError("Semantic input has no target revision binding; regenerate discovery and semantic review")
    current = bind_target_revision(Path(project_root).resolve(strict=True))
    if current["commit_sha"] != expected["commit_sha"] or current["dirty"] != expected["dirty"]:
        raise ValueError("STALE_REVIEW: target revision or dirty state changed; regenerate Discovery and Semantic Review")

    items = []
    for review in sorted(result["semantic_coverage"], key=lambda item: item["capability_id"]):
        status, action = review["status"], review["recommended_action"]
        valid = {
            ("satisfied", "keep_existing"): "keep_existing",
            ("satisfied", "no_action"): "no_action",
            ("unknown", "no_action"): "no_action",
            ("partial", "review_gap"): "review_gap",
            ("missing", "review_gap"): "review_gap",
            ("conflict", "review_conflict"): "review_conflict",
        }
        if (status, action) == ("missing", "consider_bootstrap_component"):
            component = _component_for(root, review["capability_id"], host=host)
            if component is None:
                raise ValueError(f"No applicable project-scoped copy component for {review['capability_id']}")
            disposition = "consider_component"
        else:
            component = None
            disposition = valid.get((status, action))
            if disposition is None:
                raise ValueError(f"Invalid semantic status/action combination for {review['capability_id']}: {status}/{action}")
        eligible = disposition == "consider_component"
        items.append({
            "capability_id": review["capability_id"], "semantic_status": status,
            "confidence": review["confidence"], "disposition": disposition,
            "reason": review["reason"], "evidence": review["evidence"],
            "bootstrap_component": component, "materialization_eligible": eligible,
            "requires_confirmation": eligible,
        })

    dispositions = {item["disposition"] for item in items}
    if "review_conflict" in dispositions:
        overall = "review_required"
    elif "consider_component" in dispositions:
        overall = "changes_available"
    elif "review_gap" in dispositions:
        overall = "review_required"
    else:
        overall = "no_changes"
    plan = {
        "format_version": 1, "target": current,
        "semantic_review_source": semantic_review_source,
        "items": items, "overall_decision": overall,
        "materializable_changes": sum(item["materialization_eligible"] for item in items),
        "requires_confirmation": True,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    plan["plan_digest"] = plan_digest(plan)
    validate_adoption_plan(plan, root)
    return plan


def validate_adoption_plan(plan, root=ROOT):
    validate(plan, "adoption-plan.schema.json", root)
    ids = [item["capability_id"] for item in plan["items"]]
    catalog = load_yaml(root / "capabilities/catalog.yaml")
    catalog_ids = {load_yaml(root / "capabilities" / path)["id"] for path in catalog["capabilities"]}
    if len(ids) != len(set(ids)) or set(ids) != catalog_ids:
        raise ValueError("Adoption Plan must contain every Catalog Capability exactly once")
    if ids != sorted(ids):
        raise ValueError("Adoption Plan items must be sorted by capability_id")
    for item in plan["items"]:
        eligible = item["disposition"] == "consider_component"
        expected_disposition = {
            ("satisfied", "keep_existing"), ("satisfied", "no_action"),
            ("unknown", "no_action"), ("partial", "review_gap"),
            ("missing", "review_gap"), ("conflict", "review_conflict"),
        }
        pair = (item["semantic_status"], item["disposition"])
        if pair not in expected_disposition and not (pair[0] == "missing" and pair[1] == "consider_component"):
            if pair[1] != "blocked":
                raise ValueError(f"Invalid semantic status/disposition for {item['capability_id']}")
        if item["materialization_eligible"] != eligible or (not eligible and item["bootstrap_component"] is not None):
            raise ValueError(f"Invalid materialization eligibility or component for {item['capability_id']}")
        if eligible:
            valid_components = _valid_components_for(root, item["capability_id"])
            if item["semantic_status"] != "missing" or item["bootstrap_component"] not in valid_components:
                raise ValueError(f"Invalid Bootstrap component for {item['capability_id']}")
    if plan["materializable_changes"] != sum(item["materialization_eligible"] for item in plan["items"]):
        raise ValueError("materializable_changes does not match eligible items")
    dispositions = {item["disposition"] for item in plan["items"]}
    expected_overall = (
        "blocked" if "blocked" in dispositions else
        "review_required" if "review_conflict" in dispositions else
        "changes_available" if "consider_component" in dispositions else
        "review_required" if "review_gap" in dispositions else "no_changes"
    )
    if plan["overall_decision"] != expected_overall:
        raise ValueError("overall_decision does not match plan dispositions")
    if plan["plan_digest"] != plan_digest(plan):
        raise ValueError("Adoption Plan digest mismatch")
    return plan
