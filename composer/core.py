"""Deterministic, local-only composition from explicit facts."""

import hashlib
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


_SOURCE_ROOT = Path(__file__).resolve().parents[1]
_PACKAGED_ROOT = Path(__file__).resolve().parent.parent / "bootstrap" / "data"
ROOT = _PACKAGED_ROOT if (_PACKAGED_ROOT / "schemas").is_dir() else _SOURCE_ROOT


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject ambiguous authored input instead of silently discarding values."""


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise ValueError(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node)
    return result


UniqueKeyLoader.add_constructor("tag:yaml.org,2002:map", _mapping)


def load_yaml(path):
    return yaml.load(Path(path).read_text(encoding="utf-8"), Loader=UniqueKeyLoader)


def validate(value, schema_name, root=ROOT):
    schemas = [json.loads(path.read_text(encoding="utf-8"))
               for path in sorted((root / "schemas").glob("*.json"))]
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas
    )
    schema = json.loads((root / "schemas" / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator(schema, registry=registry, format_checker=FormatChecker()).validate(value)


def compose(context, root=ROOT, selected_capability_ids=None, excluded_capability_ids=None, vcbs_home=None):
    validate(context, "project-context.schema.json", root)
    catalog = load_yaml(root / "capabilities/catalog.yaml")
    validate(catalog, "catalog.schema.json", root)
    records = {}
    paths = {}
    digest = hashlib.sha256((root / "capabilities/catalog.yaml").read_bytes())
    for path in catalog["capabilities"]:
        record_path = root / "capabilities" / path
        record = load_yaml(record_path)
        validate(record, "catalog-entry.schema.json", root)
        cid = record["id"]
        if cid in records:
            raise ValueError(f"Duplicate capability ID: {cid}")
        records[cid] = record
        paths[cid] = f"capabilities/{path}"
        digest.update(path.encode("utf-8"))
        digest.update(record_path.read_bytes())

    if vcbs_home is not None:
        from bootstrap.home import load_user_capabilities
        for user_cap in load_user_capabilities(vcbs_home):
            cid = user_cap["id"]
            if cid not in records:
                records[cid] = user_cap
                paths[cid] = user_cap.get("source_path", f"user_home/capabilities/{cid}.yaml")

    def require_id(cid):
        if cid not in records:
            raise ValueError(f"Unknown capability ID: {cid}")

    for record in records.values():
        for field in ("requires", "related_capabilities", "conflicts_with"):
            for cid in record.get(field, []):
                require_id(cid)

    if vcbs_home is not None:
        from bootstrap.home import load_profile
        profile = load_profile(vcbs_home, root=root)
    else:
        profile = load_yaml(root / "profiles/personal.yaml")
    validate(profile, "profile.schema.json", root)
    defaults = profile.get("default_capability_ids", [])
    excluded = {cid: "Explicitly excluded by profiles/personal.yaml."
                for cid in profile.get("excluded_capability_ids", [])}
    for cid in [*defaults, *excluded]:
        require_id(cid)
    selected = list(dict.fromkeys(selected_capability_ids or []))
    declined = list(dict.fromkeys(excluded_capability_ids or []))
    for cid in selected:
        require_id(cid)
        if cid in excluded:
            raise ValueError(f"CAPABILITY_EXCLUDED: {cid} is excluded by profiles/personal.yaml")
    for cid in declined:
        require_id(cid)
    if set(selected).intersection(declined):
        raise ValueError("CAPABILITY_SELECTION_CONFLICT: a capability cannot be both selected and declined")
    for cid in declined:
        excluded[cid] = "Declined in guided setup; it will not be installed."

    experiences = []
    lifecycle = {}
    referenced_ids = {experience_id for record in records.values()
                      for experience_id in record.get("experience_ids", [])}
    referenced_ids.update(profile.get("reusable_experience_ids", []))
    for experience_id in sorted(referenced_ids):
        matches = sorted((root / "experiences" / "verified").glob("*.yaml"))
        entry = None
        for path in matches:
            if path.stem not in {experience_id, experience_id.removeprefix("exp-")}:
                continue
            experience = load_yaml(path)
            validate(experience, "experience.schema.json", root)
            if experience.get("id") != experience_id:
                raise ValueError(f"Experience filename and ID do not match: {path}")
            entry = (path, experience)
            break
        if entry:
            lifecycle[experience_id] = entry

    # Legacy root-level records remain eligible, but malformed unlinked records are irrelevant.
    for path in sorted((root / "experiences").glob("*.yaml")):
        try:
            experience = load_yaml(path)
            validate(experience, "experience.schema.json", root)
        except Exception:
            continue
        for cid in experience.get("capability_ids", []):
            require_id(cid)
        experiences.append((path.relative_to(root).as_posix(), experience))
    for record in records.values():
        for experience_id in record.get("experience_ids", []):
            entry = lifecycle.get(experience_id)
            if not entry or entry[0].parent.name != "verified":
                raise ValueError(f"Capability experience_ids must resolve to a verified Experience: {experience_id}")
            experience = entry[1]
            if (experience.get("status") != "verified" or not experience.get("review")
                    or experience["review"].get("review_status") != "accepted"
                    or record["id"] not in experience.get("capability_ids", [])):
                raise ValueError(f"Capability experience_ids must link an accepted verified Experience for {record['id']}: {experience_id}")
            experiences.append((entry[0].relative_to(root).as_posix(), experience))

    for experience_id in profile.get("reusable_experience_ids", []):
        entry = lifecycle.get(experience_id)
        if not entry or entry[0].parent.name != "verified":
            raise ValueError(f"Personal reusable experience must resolve to a verified Experience: {experience_id}")
        experience = entry[1]
        if (experience.get("status") != "verified" or not experience.get("review")
                or experience["review"].get("review_status") != "accepted"):
            raise ValueError(f"Personal reusable experience must link an accepted verified Experience: {experience_id}")
        evidence_items = experience.get("evidence", [])
        if not evidence_items or any(item.get("verification", {}).get("status") not in {"checked", "independently_confirmed"} for item in evidence_items):
            raise ValueError(f"Personal reusable experience contains unverified evidence: {experience_id}")
        has_qualifying = any(
            item.get("type") not in {"ai_judgment", "single_attempt"}
            and item.get("strength") in {"moderate", "strong"}
            and item.get("verification", {}).get("status") in {"checked", "independently_confirmed"}
            for item in evidence_items
        )
        if not has_qualifying:
            raise ValueError(f"Personal reusable experience lacks qualifying verified evidence: {experience_id}")
        for cid in experience.get("capability_ids", []):
            require_id(cid)
        experiences.append((entry[0].relative_to(root).as_posix(), experience))

    candidates = {}

    def add(cid, reason, confidence, source):
        require_id(cid)
        candidates.setdefault(cid, []).append({
            "reason": reason, "confidence": confidence, "source": source,
        })

    rules = load_yaml(root / "composer/rules.yaml")
    validate(rules, "composer-rules.schema.json", root)
    for rule in rules["rules"]:
        for cid in rule["recommend"]:
            require_id(cid)
        if all((any(tag in values for tag in context[key]) if isinstance(context[key], list)
                else context[key] in values) for key, values in rule["condition"].items()):
            facts = {key: context[key] for key in rule["condition"]}
            for cid in rule["recommend"]:
                add(cid, f"{rule['reason']} Matched: {facts}.", rule["confidence"],
                    f"composer/rules.yaml#{rule['id']}")
    if selected:
        # An explicit guided selection is the user's chosen set; dependencies below
        # remain visible and are added only when the Catalog requires them.
        candidates.clear()
    else:
        for cid in defaults:
            add(cid, "Explicit personal profile default; applicability still needs review.",
                "medium", "profiles/personal.yaml#default_capability_ids")
    for cid in selected:
        add(cid, "Explicitly selected by the project owner; applicability constraints still require review.",
            "medium", "guided setup selection")

    visited = set()

    def dependencies(cid, active):
        if cid in active:
            raise ValueError(f"Cyclic capability prerequisites: {cid}")
        if cid in visited:
            return
        for dependency in records[cid].get("requires", []):
            add(dependency, f"Catalog prerequisite of {cid}.", "high", f"{paths[cid]}#requires")
            dependencies(dependency, active | {cid})
        visited.add(cid)

    for cid in list(candidates):
        dependencies(cid, set())
    # Propagate explicit exclusions through the prerequisite graph, never override them.
    changed = True
    while changed:
        changed = False
        for cid in candidates:
            blocked = [dep for dep in records[cid].get("requires", []) if dep in excluded]
            if blocked and cid not in excluded:
                excluded[cid] = f"Required capabilities excluded: {', '.join(blocked)}."
                changed = True

    recommendations = []
    for cid, matches in candidates.items():
        if cid in excluded:
            continue
        record = records[cid]
        supporting = []
        for path, experience in experiences:
            if (experience["project_context"]["project_type"] == context["project_type"]
                    and cid in experience.get("capability_ids", [])):
                if not any(item["record"]["id"] == experience["id"] for item in supporting):
                    supporting.append({"source": path, "record": experience})
        recommendations.append({
            "capability_id": cid,
            "reason": " ".join(match["reason"] for match in matches),
            "confidence": max((match["confidence"] for match in matches),
                              key=["low", "medium", "high"].index),
            "source": [match["source"] for match in matches],
            "catalog_record": paths[cid],
            "status": record.get("status", "validated"), "maturity": record.get("maturity", "established"),
            "evidence": record.get("evidence", {"state": "supported", "summary": "User defined in VCBS Home", "references": ["https://example.com"]}),
            "constraints": record.get("selection_hints", {}).get("constraints", []),
            "not_for": record.get("applicability", {}).get("not_for", []),
            "requires": record.get("requires", []),
            "experiences": supporting,
        })
    selected = {item["capability_id"] for item in recommendations}
    conflicts = sorted({tuple(sorted((cid, other))) for cid in selected
                        for other in records[cid].get("conflicts_with", []) if other in selected})
    plan = {
        "project_context": context,
        "catalog_revision": f"sha256:{digest.hexdigest()}",
        "profile_source": "profiles/personal.yaml",
        "experience_sources": sorted(list(dict.fromkeys(path for path, _ in experiences))),
        "recommendations": recommendations,
        "excluded": [{"capability_id": cid, "reason": reason} for cid, reason in excluded.items()],
        "conflicts": [list(pair) for pair in conflicts],
        "requires_confirmation": True,
        "notes": [
            "Confidence describes rule relevance, not proven capability effectiveness.",
            "Free-text goals, constraints, runtime and platform require human applicability review.",
            "Experiences match project type only; original evidence and limits are retained without changing recommendations.",
            "Conflicting candidates have no automatic winner; resolve them before any later assembly.",
        ],
    }
    validate(plan, "recommendation.schema.json", root)
    return plan
