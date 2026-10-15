"""Resolve recommendation entries into a deterministic, review-only plan."""

from datetime import datetime, timezone

from composer.core import ROOT, load_yaml, validate


def _catalog(root):
    catalog = load_yaml(root / "capabilities/catalog.yaml")
    records = {}
    for relative in catalog["capabilities"]:
        record = load_yaml(root / "capabilities" / relative)
        if record["id"] in records:
            raise ValueError(f"Duplicate capability ID: {record['id']}")
        records[record["id"]] = record
    return records


def assemble(recommendation, root=ROOT, generated_at=None, recommendation_source=None):
    """Build a plan without writing inputs or accessing network/runtime services."""
    validate(recommendation, "recommendation.schema.json", root)
    records = _catalog(root)
    selected_rows = recommendation["recommendations"]
    selected = {row["capability_id"] for row in selected_rows}
    excluded = {row["capability_id"] for row in recommendation["excluded"]}
    rules = load_yaml(root / "assembly/rules.yaml")
    rule_by_id = {rule["id"]: rule for rule in rules["rules"]}
    source_by_capability = {
        cid: {source["id"]: source for source in record.get("component_sources", [])}
        for cid, record in records.items()
    }
    items = []
    warnings = []
    unresolved = []

    for row in selected_rows:
        cid = row["capability_id"]
        if cid not in records:
            raise ValueError(f"Unknown recommended capability: {cid}")
        record = records[cid]
        declared = record.get("components", [])
        if not declared:
            warnings.append(
                f"{cid}: Capability exists, but no installable implementation component is registered."
            )
        for component in declared:
            metadata = component.get("assembly")
            if metadata is None:
                source_record = source_by_capability[cid].get(component["source_id"], {})
                metadata = {
                    "id": component["id"], "name": source_record.get("title", component["role"]),
                    "type": "workflow_reference" if component["kind"] == "workflow" else "document",
                    "source": source_record.get("uri", component["source_id"]),
                    "provider": source_record.get("title", "unspecified"),
                    "version": "unspecified", "scope": "external", "delivery": "reference",
                    "optional": True, "provenance": {
                        "source_id": component["source_id"],
                        "relationship": source_record.get("relationship", "reference"),
                    },
                }
            validate(metadata, "component.schema.json", root)
            action = "include"
            reason = row["reason"]
            if metadata["scope"] == "external":
                rule = rule_by_id["documentation-reference"] if metadata["type"] == "document" else rule_by_id["external-reference"]
                action = rule["action"]
                reason = rule["reason"] + " " + reason
            component_items = {
                "component_id": metadata["id"], "component_type": metadata["type"],
                "source_capability": cid, "reason": reason, "scope": metadata["scope"],
                "source": metadata["source"], "delivery": metadata["delivery"], "action": action,
                "capability_version": record.get("version", "unspecified"),
                "configuration_preview": metadata.get("configuration", {}),
                "dependencies": metadata.get("dependencies", []),
                "conflicts": metadata.get("conflicts", []),
            }
            if "destination" in metadata:
                component_items["destination"] = metadata["destination"]
            items.append(component_items)

    # A prerequisite absent from Recommendation is an unresolved issue; exclusions
    # remain authoritative and are never overridden here.
    for row in selected_rows:
        cid = row["capability_id"]
        for dependency in row["requires"]:
            if dependency not in selected:
                reason = (f"Required capability {dependency} was explicitly excluded."
                          if dependency in excluded else f"Required capability {dependency} is missing from Recommendation.")
                unresolved.append({"kind": "capability", "items": [cid, dependency], "reason": reason})
                for item in items:
                    if item["source_capability"] == cid:
                        item["action"] = "review_required"

    for pair in recommendation["conflicts"]:
        unresolved.append({"kind": "capability", "items": pair, "reason": "Recommendation reports a Capability conflict; no winner was selected."})

    for cid in selected:
        for other in records[cid].get("conflicts_with", []):
            if other in selected and not any(
                issue["kind"] == "capability" and set(issue["items"]) == {cid, other}
                for issue in unresolved
            ):
                unresolved.append({"kind": "capability", "items": sorted([cid, other]), "reason": "Catalog Capabilities conflict; no winner was selected."})

    for row in selected_rows:
        warnings.extend(f"{row['capability_id']}: {constraint}" for constraint in row["constraints"])

    for index, item in enumerate(items):
        for other_id in item["conflicts"]:
            matches = [other for other in items if other["component_id"] == other_id]
            if matches:
                unresolved.append({"kind": "component", "items": [item["component_id"], other_id], "reason": "Registered components declare a conflict; no winner was selected."})
                item["action"] = "review_required"
                for other in matches:
                    other["action"] = "review_required"
        for other in items[index + 1:]:
            if other["component_id"] != item["component_id"]:
                continue
            if other["scope"] != item["scope"]:
                unresolved.append({"kind": "scope", "items": [item["component_id"], other["component_id"]], "reason": "The same component is registered with incompatible scopes."})
                item["action"] = other["action"] = "review_required"
            if other["delivery"] != item["delivery"]:
                unresolved.append({"kind": "delivery", "items": [item["component_id"], other["component_id"]], "reason": "The same component has mutually exclusive delivery strategies."})
                item["action"] = other["action"] = "review_required"

    for item in items:
        for dependency in item["dependencies"]:
            if not any(other["component_id"] == dependency for other in items):
                unresolved.append({"kind": "component", "items": [item["component_id"], dependency], "reason": "Required component is not registered in the selected Assembly."})
                item["action"] = "review_required"

    # A recommendation conflict also makes all components from either side review-only.
    conflict_caps = {cid for issue in unresolved if issue["kind"] == "capability" for cid in issue["items"]}
    for item in items:
        if item["source_capability"] in conflict_caps:
            item["action"] = "review_required"

    timestamp = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    if timestamp.endswith("+00:00"):
        timestamp = timestamp[:-6] + "Z"
    plan = {
        "project_context": recommendation["project_context"],
        "recommendation_source": recommendation_source or recommendation.get("source", "recommendation plan (in-memory input)"),
        "selected_capabilities": sorted(selected), "components": items,
        "skipped_components": [], "unresolved_conflicts": unresolved,
        "warnings": sorted(set(warnings)), "requires_confirmation": True,
        "generated_at": timestamp,
    }
    validate(plan, "assembly-plan.schema.json", root)
    return plan
