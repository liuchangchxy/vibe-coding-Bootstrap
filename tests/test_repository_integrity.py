"""Repository-wide cross-file checks; fixtures in this module are synthetic."""

from collections import defaultdict
from pathlib import Path
import shutil
import tempfile
import unittest

from composer.core import ROOT, load_yaml, validate
import yaml


def check_repository(root=ROOT):
    """Validate repository records and references that JSON Schema cannot join."""
    catalog_path = root / "capabilities/catalog.yaml"
    catalog = load_yaml(catalog_path)
    validate(catalog, "catalog.schema.json", root)
    paths = catalog["capabilities"]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate catalog path")

    catalog_paths = set()
    records = {}
    components = set()
    requires = defaultdict(set)
    conflicts = set()
    relative_sources = []
    assembly_references = []
    for relative_path in paths:
        path = (root / "capabilities" / relative_path).resolve()
        if not path.is_relative_to((root / "capabilities").resolve()) or not path.is_file():
            raise ValueError(f"Invalid catalog path: {relative_path}")
        catalog_paths.add(path)
        category, filename = Path(relative_path).parts
        record = load_yaml(path)
        validate(record, "catalog-entry.schema.json", root)
        cid = record["id"]
        if cid in records:
            raise ValueError(f"Duplicate capability ID: {cid}")
        if record.get("category") != category or filename != f"{cid}.yaml":
            raise ValueError(f"Capability path does not match category/id: {relative_path}")
        records[cid] = record
        for source in record.get("component_sources", []):
            uri = source["uri"]
            if "://" not in uri and not uri.startswith("/"):
                relative_sources.append((path.parent / uri).resolve())
        source_ids = [s["id"] for s in record.get("component_sources", [])]
        component_ids = [c["id"] for c in record.get("components", [])]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError(f"Duplicate component source ID in {cid}")
        if len(component_ids) != len(set(component_ids)):
            raise ValueError(f"Duplicate component ID in {cid}")
        used = {c["source_id"] for c in record.get("components", [])}
        if not used.issubset(source_ids) or set(source_ids) != used:
            raise ValueError(f"Component source mismatch in {cid}")
        for component in record.get("components", []):
            components.add(component["id"])
            assembly = component.get("assembly")
            if assembly:
                components.add(assembly["id"])
                assembly_references.extend((cid, assembly["id"], field, ref)
                                           for field in ("dependencies", "conflicts")
                                           for ref in assembly.get(field, []))
        requires[cid].update(record.get("requires", []))
        for field in ("requires", "related_capabilities", "conflicts_with"):
            if cid in record.get(field, []):
                raise ValueError(f"Self reference in {cid}.{field}")
        for other in record.get("conflicts_with", []):
            pair = frozenset((cid, other))
            if pair in conflicts:
                raise ValueError(f"Mirrored conflict edge: {cid} and {other}")
            conflicts.add(pair)

    all_capability_paths = {path.resolve() for path in (root / "capabilities").rglob("*.yaml") if path.name != "catalog.yaml"}
    if catalog_paths != all_capability_paths:
        raise ValueError("Catalog must index every Capability YAML exactly once")
    ids = set(records)
    for cid, record in records.items():
        for field in ("requires", "related_capabilities", "conflicts_with"):
            if not set(record.get(field, [])).issubset(ids):
                raise ValueError(f"Unknown Capability reference in {cid}.{field}")
    visiting, visited = set(), set()
    def visit(cid):
        if cid in visiting:
            raise ValueError("Capability requires cycle")
        if cid in visited:
            return
        visiting.add(cid)
        for dependency in requires[cid]:
            visit(dependency)
        visiting.remove(cid)
        visited.add(cid)
    for cid in ids:
        visit(cid)
    for left, right in conflicts:
        if right in records[left].get("requires", []) or left in records[right].get("requires", []):
            raise ValueError(f"Conflicting Capability pair is also required: {left}, {right}")
        if right in records[left].get("related_capabilities", []) or left in records[right].get("related_capabilities", []):
            raise ValueError(f"Conflicting Capability pair is also related: {left}, {right}")
    for cid, component_id, field, ref in assembly_references:
        if ref == component_id or ref not in components:
            raise ValueError(f"Unknown or self component {field} reference in {cid}.{component_id}: {ref}")
    closures = {}
    def closure(cid):
        if cid not in closures:
            result = {cid}
            for dependency in requires[cid]:
                result.update(closure(dependency))
            closures[cid] = result
        return closures[cid]
    for cid in ids:
        prerequisites = closure(cid)
        if any(left in prerequisites and right in prerequisites for left, right in conflicts):
            raise ValueError(f"Prerequisite closure contains conflicting Capabilities: {cid}")
    for source in relative_sources:
        if not source.exists():
            raise ValueError(f"Missing relative component source: {source}")

    profile = load_yaml(root / "profiles/personal.yaml")
    validate(profile, "profile.schema.json", root)
    defaults = set(profile.get("default_capability_ids", []))
    excluded = set(profile.get("excluded_capability_ids", []))
    if not defaults.union(excluded).issubset(ids):
        raise ValueError("Unknown Profile Capability reference")
    if defaults.intersection(excluded):
        raise ValueError("Profile Capability is both default and excluded")

    experiences = {}
    for path in sorted((root / "experiences").rglob("*.yaml")):
        if path.name == ".gitkeep":
            continue
        record = load_yaml(path)
        validate(record, "experience.schema.json", root)
        if record["id"] in experiences:
            raise ValueError(f"Duplicate Experience ID: {record['id']}")
        experiences[record["id"]] = record
        if record.get("identity", record["id"]) != record["id"]:
            raise ValueError(f"Experience identity does not match id: {path}")
        if path.parent.name in {"raw", "candidate", "verified", "rejected", "archived"} and record.get("status") != path.parent.name:
            raise ValueError(f"Experience folder/status mismatch: {path}")
        if not set(record.get("capability_ids", [])).issubset(ids):
            raise ValueError(f"Unknown Experience Capability reference: {path}")

    proposal_paths = sorted((root / "proposals").rglob("*.yaml")) if (root / "proposals").exists() else []
    proposal_records = [(path, load_yaml(path)) for path in proposal_paths]
    experience_proposal_ids = {proposal["proposal_id"] for _, proposal in proposal_records if "source_experience" in proposal}
    if len(experience_proposal_ids) != sum("source_experience" in proposal for _, proposal in proposal_records):
        raise ValueError("Duplicate Experience Proposal ID")
    for path, proposal in proposal_records:
        if "current_version" in proposal:
            validate(proposal, "capability-proposal.schema.json", root)
            target = records.get(proposal["target_capability"])
            if not target:
                raise ValueError(f"Unknown proposal Capability: {proposal['target_capability']}")
            if proposal["current_version"] != target["version"]:
                raise ValueError(f"Stale Capability proposal version: {path}")
            if any(ref not in experience_proposal_ids for ref in proposal.get("source_experience_proposals", [])):
                raise ValueError(f"Unknown source Experience proposal: {path}")
        else:
            validate(proposal, "experience-proposal.schema.json", root)
            if proposal["target_capability"] not in ids or proposal["source_experience"] not in experiences:
                raise ValueError(f"Unresolved Experience proposal reference: {path}")


class RepositoryIntegrityTests(unittest.TestCase):
    def test_current_repository_integrity(self):
        check_repository()

    def test_all_catalog_capabilities_use_simple_version_one(self):
        catalog = load_yaml(ROOT / "capabilities/catalog.yaml")
        versions = [load_yaml(ROOT / "capabilities" / path)["version"] for path in catalog["capabilities"]]
        self.assertEqual(8, len(versions))
        self.assertEqual(["1"] * 8, versions)

    def test_bad_and_valid_experience_capability_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            path = root / "experiences/candidate/testing-gap.yaml"
            record = load_yaml(path)
            record["capability_ids"] = ["quality/testing"]
            path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unknown Experience Capability"):
                check_repository(root)
            record["capability_ids"] = ["testing"]
            path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
            check_repository(root)

    def test_legacy_and_lifecycle_evidence_contracts(self):
        legacy_capability = {"id": "legacy", "name": "Legacy", "kind": "workflow", "description": "Phase 1 fixture", "status": "draft"}
        validate(legacy_capability, "capability.schema.json")
        with self.assertRaises(Exception):
            validate(legacy_capability, "catalog-entry.schema.json")
        legacy = {
            "id": "legacy", "title": "Legacy", "project_context": {"project_type": "test", "goal": "test"},
            "approach": "test", "outcome": {"summary": "test", "result": "inconclusive"},
            "evidence": [{"description": "Legacy evidence"}], "confidence": "low", "recorded_at": "2026-10-15T00:00:00Z",
        }
        validate(legacy, "experience.schema.json")
        lifecycle = dict(legacy, status="raw")
        with self.assertRaises(Exception):
            validate(lifecycle, "experience.schema.json")

    def test_verified_evidence_gate(self):
        record = load_yaml(ROOT / "experiences/candidate/testing-gap.yaml")
        record.update(status="verified", review={"review_status": "accepted", "reviewer": "reviewer", "reviewed_at": "2026-10-15T00:00:00Z"})
        record["evidence"] = [
            {"type": kind, "source": "synthetic", "description": "Synthetic test fixture only", "strength": "weak", "verification": {"status": "unverified"}}
            for kind in ("ai_judgment", "single_attempt")
        ]
        for field in ("identity", "source", "context", "observation", "solution", "scope", "reasoning"):
            if field not in record:
                record[field] = {"project": "synthetic", "recorded_by": "test"} if field == "source" else ({"interpretation": "synthetic", "limits": []} if field == "reasoning" else "Synthetic fixture")
        with self.assertRaises(Exception):
            validate(record, "experience.schema.json")
        record["evidence"] = [{"type": "project_result", "source": "synthetic fixture", "description": "Synthetic test fixture only", "strength": "moderate", "verification": {"status": "checked"}}]
        validate(record, "experience.schema.json")

    def test_evidence_strength_and_proposal_review_gates(self):
        evidence = {"type": "ai_judgment", "source": "synthetic", "description": "Synthetic fixture", "strength": "strong", "verification": {"status": "unverified"}}
        with self.assertRaises(Exception):
            validate(evidence, "evidence.schema.json")
        evidence["type"] = "single_attempt"
        evidence["strength"] = "moderate"
        with self.assertRaises(Exception):
            validate(evidence, "evidence.schema.json")

        experience_proposal = {
            "proposal_id": "synthetic", "source_experience": "synthetic", "target_capability": "testing",
            "change_type": "no_action", "reason": "Synthetic", "evidence": [{"type": "other", "source": "fixture", "description": "Synthetic", "strength": "weak", "verification": {"status": "unverified"}}],
            "confidence": "low", "scope": ["test"], "risk": "none", "review_status": "accepted",
        }
        with self.assertRaises(Exception):
            validate(experience_proposal, "experience-proposal.schema.json")
        experience_proposal.update(reviewer="reviewer", reviewed_at="2026-10-15T00:00:00Z")
        validate(experience_proposal, "experience-proposal.schema.json")

        capability_proposal = {
            "proposal_id": "synthetic", "target_capability": "testing", "current_version": "1", "proposed_change": "Synthetic",
            "reason": "Synthetic", "evidence": [{"type": "other", "source": "fixture", "description": "Synthetic", "strength": "weak", "verification": {"status": "unverified"}}],
            "affected_scope": ["test"], "risk": "none", "approval_status": "approved",
        }
        with self.assertRaises(Exception):
            validate(capability_proposal, "capability-proposal.schema.json")
        capability_proposal.update(reviewer="reviewer", reviewed_at="2026-10-15T00:00:00Z")
        validate(capability_proposal, "capability-proposal.schema.json")

    def test_proposal_version_must_match_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            proposals = root / "proposals"
            proposals.mkdir()
            proposal = {
                "proposal_id": "synthetic-test", "target_capability": "testing", "current_version": "0",
                "proposed_change": "Synthetic test only", "reason": "Synthetic test only",
                "evidence": [{"type": "other", "source": "synthetic", "description": "Synthetic test fixture", "strength": "weak", "verification": {"status": "unverified"}}],
                "affected_scope": ["test"], "risk": "none in fixture", "approval_status": "pending",
            }
            path = proposals / "capability.yaml"
            path.write_text(yaml.safe_dump(proposal, sort_keys=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Stale Capability proposal"):
                check_repository(root)
            proposal["current_version"] = "1"
            path.write_text(yaml.safe_dump(proposal, sort_keys=False), encoding="utf-8")
            check_repository(root)


if __name__ == "__main__":
    unittest.main()
