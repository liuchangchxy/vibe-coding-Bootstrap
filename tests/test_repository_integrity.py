"""Repository-wide cross-file checks; fixtures in this module are synthetic."""

from collections import defaultdict
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import tempfile
import unittest

from composer.core import ROOT, load_yaml, validate
import yaml
from adoption.planning import build_adoption_plan, plan_digest, validate_adoption_plan
from adoption.semantic import validate_semantic_result


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
    copy_destinations = {}
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
                relative_sources.append((root / uri).resolve())
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
                validate(assembly, "component.schema.json", root)
                if assembly["delivery"] == "copy":
                    if assembly["scope"] == "external":
                        raise ValueError(f"Copy component cannot have external scope: {cid}/{assembly['id']}")
                    source = assembly["source"]
                    source_path = (root / source).resolve()
                    if (Path(source).is_absolute() or ".." in Path(source).parts
                            or PureWindowsPath(source).is_absolute() or PureWindowsPath(source).drive
                            or "://" in source or not source_path.is_relative_to(root.resolve())
                            or not source_path.is_file()):
                        raise ValueError(f"Invalid local copy source: {cid}/{assembly['id']}")
                    destination = assembly["destination"].replace("\\", "/")
                    if (not destination or destination.startswith("/") or Path(destination).is_absolute()
                            or PureWindowsPath(destination).is_absolute() or PureWindowsPath(destination).drive
                            or ".." in PurePosixPath(destination).parts):
                        raise ValueError(f"Invalid component destination: {cid}/{assembly['id']}")
                    destination_key = (assembly["scope"], PurePosixPath(destination).as_posix())
                    identity = (cid, assembly["id"])
                    if destination_key in copy_destinations and copy_destinations[destination_key] != identity:
                        raise ValueError(f"Multiple components target the same destination: {destination_key}")
                    copy_destinations[destination_key] = identity
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
    for cid, capability in records.items():
        for experience_id in capability.get("experience_ids", []):
            experience = experiences.get(experience_id)
            experience_path = next((path for path in (root / "experiences/verified").glob("*.yaml")
                                    if experience and load_yaml(path)["id"] == experience_id), None)
            if (not experience or experience_path is None or experience.get("status") != "verified"
                    or experience.get("review", {}).get("review_status") != "accepted"
                    or cid not in experience.get("capability_ids", [])):
                raise ValueError(f"Invalid curated verified Experience link in {cid}: {experience_id}")

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
            status = proposal["approval_status"]
            if status in {"pending", "deferred"} and proposal["current_version"] != target["version"]:
                raise ValueError(f"Stale Capability proposal version: {path}")
            if status == "approved" and not proposal.get("approved_change_reference"):
                raise ValueError(f"Approved Capability proposal requires approved_change_reference: {path}")
            if any(ref not in experience_proposal_ids for ref in proposal.get("source_experience_proposals", [])):
                raise ValueError(f"Unknown source Experience proposal: {path}")
        else:
            validate(proposal, "experience-proposal.schema.json", root)
            if proposal["target_capability"] not in ids or proposal["source_experience"] not in experiences:
                raise ValueError(f"Unresolved Experience proposal reference: {path}")


class RepositoryIntegrityTests(unittest.TestCase):
    def test_current_repository_integrity(self):
        check_repository()

    def test_dayspark_pilot_artifacts_are_revision_bound_and_catalog_complete(self):
        base = ROOT / "examples/adoption/dayspark"
        package = json.loads((base / "semantic-review-input.json").read_text(encoding="utf-8"))
        result = json.loads((base / "semantic-review-result.json").read_text(encoding="utf-8"))
        report = json.loads((base / "semantic-adoption-report.json").read_text(encoding="utf-8"))
        plan = json.loads((base / "adoption-plan.json").read_text(encoding="utf-8"))
        validate(package, "semantic-review-input.schema.json")
        validate_semantic_result(result, package)
        validate_adoption_plan(plan)
        self.assertEqual("liuchangchxy/dayspark", package["target"]["repository"])
        self.assertRegex(package["target"]["commit_sha"], r"^[0-9a-f]{40}$")
        self.assertEqual(package["target"], report["target"])
        self.assertEqual(package["target"], plan["target"])
        self.assertEqual(plan["plan_digest"], plan_digest(plan))
        self.assertEqual("review_required", plan["overall_decision"])
        catalog = load_yaml(ROOT / "capabilities/catalog.yaml")
        catalog_ids = {load_yaml(ROOT / "capabilities" / item)["id"] for item in catalog["capabilities"]}
        self.assertEqual(catalog_ids, {item["capability_id"] for item in plan["items"]})
        self.assertEqual(0, plan["materializable_changes"])
        serialized = json.dumps([package, report, plan], ensure_ascii=False)
        self.assertNotIn("C:\\Users\\", serialized)
        self.assertNotIn("C:/Users/", serialized)
        self.assertFalse((base / "adoption-decision.json").exists())

    def test_easyexam_pilot_is_profile_aware_revision_bound_and_review_only(self):
        base = ROOT / "examples/adoption/easyexam"
        package = json.loads((base / "semantic-review-input.json").read_text(encoding="utf-8"))
        result = json.loads((base / "semantic-review-result.json").read_text(encoding="utf-8"))
        report = json.loads((base / "semantic-adoption-report.json").read_text(encoding="utf-8"))
        plan = json.loads((base / "adoption-plan.json").read_text(encoding="utf-8"))
        validate(package, "semantic-review-input.schema.json")
        validate_semantic_result(result, package)
        validate_adoption_plan(plan)
        self.assertEqual("liuchangchxy/easy-exam", package["target"]["repository"])
        self.assertEqual("main", package["target"]["branch"])
        self.assertEqual("7d54ef451ca9954b671c71e2673cc7e9b861218e", package["target"]["commit_sha"])
        self.assertEqual(package["target"]["commit_sha"], package["target"]["remote_commit_sha"])
        self.assertFalse(package["target"]["dirty"])
        self.assertEqual(package["target"], report["target"])
        self.assertEqual(package["target"], plan["target"])
        self.assertEqual(plan["plan_digest"], plan_digest(plan))
        self.assertEqual(19, sum(asset["asset_type"] == "skill" for asset in package["discovered_assets"]))
        self.assertTrue(all(asset["path"].endswith("/SKILL.md")
                            for asset in package["discovered_assets"] if asset["asset_type"] == "skill"))
        assets = {asset["path"]: asset for asset in package["discovered_assets"]}
        worktree_asset = assets[".agents/skills/using-git-worktrees/SKILL.md"]
        self.assertEqual("conditional", worktree_asset["activation_hint"])
        self.assertEqual("historical", assets["docs/superpowers/plans/2026-10-06-native-reviewer-transport-cleanup.md"]["authority_hint"])
        finding = next(item for item in result["preference_findings"] if item["preference_id"] == "worktree_strategy")
        self.assertEqual("compatible", finding["status"])
        self.assertEqual(8, len(report["preference_findings"]))
        self.assertEqual("review_required", plan["overall_decision"])
        self.assertEqual(0, plan["materializable_changes"])
        self.assertFalse((base / "adoption-decision.json").exists())
        serialized = json.dumps([package, result, report, plan], ensure_ascii=False)
        self.assertNotIn("C:\\Users\\", serialized)
        self.assertNotIn("C:/Users/", serialized)

    def test_codex_activation_acceptance_evidence_is_sanitized_and_ab_matched(self):
        base = ROOT / "examples/activation/codex"
        control = json.loads((base / "control-result.json").read_text(encoding="utf-8"))
        activated = json.loads((base / "activated-result.json").read_text(encoding="utf-8"))
        plan = json.loads((base / "activation-plan.json").read_text(encoding="utf-8"))
        self.assertTrue(control["evidence_sanitized"] and activated["evidence_sanitized"] and plan["evidence_sanitized"])
        self.assertEqual(control["target_base_commit"], activated["target_base_commit"])
        self.assertEqual(control["prompt"], activated["prompt"])
        self.assertEqual(control["prompt_sha256"], activated["prompt_sha256"])
        self.assertFalse(control["pilot_marker_observed"])
        self.assertTrue(activated["pilot_marker_observed"])
        self.assertEqual("YES", activated["codex_consumed_native_skill"])
        self.assertEqual("AGENTS-triggered repo-local skill", activated["activation_mechanism"])
        self.assertEqual(control["target_base_commit"], plan["target_commit_sha"])
        serialized = json.dumps([control, activated, plan], ensure_ascii=False)
        self.assertNotIn("C:\\Users\\", serialized)
        self.assertNotIn("AppData", serialized)

    def test_catalog_copy_destinations_are_unique_within_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            testing_path = root / "capabilities/quality/testing.yaml"
            documentation_path = root / "capabilities/workflow/documentation.yaml"
            testing = load_yaml(testing_path)
            documentation = load_yaml(documentation_path)
            destination = next(c["assembly"]["destination"] for c in testing["components"]
                               if c["id"] == "project-test-guidance")
            next(c["assembly"] for c in documentation["components"]
                 if c["id"] == "project-documentation-outline")["destination"] = destination
            testing_path.write_text(yaml.safe_dump(testing, sort_keys=False), encoding="utf-8")
            documentation_path.write_text(yaml.safe_dump(documentation, sort_keys=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Multiple components target the same destination"):
                check_repository(root)

    def test_only_testing_capability_advanced_to_version_two(self):
        catalog = load_yaml(ROOT / "capabilities/catalog.yaml")
        versions = {load_yaml(ROOT / "capabilities" / path)["id"]: load_yaml(ROOT / "capabilities" / path)["version"]
                    for path in catalog["capabilities"]}
        self.assertEqual(9, len(versions))
        self.assertEqual("2", versions["testing"])
        self.assertEqual({"1"}, {version for cid, version in versions.items() if cid != "testing"})

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
            proposals.mkdir(exist_ok=True)
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
            proposal["current_version"] = "2"
            path.write_text(yaml.safe_dump(proposal, sort_keys=False), encoding="utf-8")
            check_repository(root)

    def test_historical_semantic_snapshot_validates_but_cannot_plan_against_new_catalog(self):
        base = ROOT / "examples/adoption/dayspark"
        package = json.loads((base / "semantic-review-input.json").read_text(encoding="utf-8"))
        result = json.loads((base / "semantic-review-result.json").read_text(encoding="utf-8"))
        validate_semantic_result(result, package)
        with self.assertRaisesRegex(ValueError, "STALE_CATALOG_REVIEW"):
            build_adoption_plan(package, result, ROOT)

    def test_codex_reuse_before_after_evidence_matches_generated_plans(self):
        base = ROOT / "examples/reuse/codex-maintained-project"
        before = json.loads((base / "before-promotion.json").read_text(encoding="utf-8"))
        after = json.loads((base / "after-promotion.json").read_text(encoding="utf-8"))
        recommendation = load_yaml(base / "recommendation.yaml")
        assembly = load_yaml(base / "assembly-plan.yaml")
        self.assertEqual("7f390d2dd5f396c11eded1106fc1e3e433995284", before["base_sha"])
        self.assertEqual(before["base_sha"], before["bootstrap_base_sha"])
        self.assertEqual("1", before["testing_capability_version"])
        self.assertEqual("pending", before["testing_catalog_evidence_state"])
        self.assertEqual([], before["testing_linked_experience_ids"])
        self.assertEqual([], before["recommendation_experience_ids"])
        self.assertFalse(before["assembly_contains_codex_native_testing_component"])
        self.assertEqual("2", after["testing_version"])
        self.assertEqual("supported", after["testing_evidence_state"])
        self.assertIn("exp-codex-testing-activation-v1", after["testing_experience_ids"])
        self.assertIn("exp-codex-testing-activation-v1", after["recommendation_experience_ids"])
        self.assertTrue(after["codex_native_component_present"])
        self.assertEqual("reference", after["codex_native_component_delivery"])
        self.assertFalse(after["automatic_activation"])
        testing = next(row for row in recommendation["recommendations"] if row["capability_id"] == "testing")
        self.assertEqual("experiences/verified/codex-testing-activation-v1.yaml", testing["experiences"][0]["source"])
        self.assertTrue(any(item["component_id"] == "codex-native-testing-skill"
                            and item["delivery"] == "reference" for item in assembly["components"]))
        serialized = json.dumps([before, after], ensure_ascii=False)
        self.assertNotIn("C:\\Users\\", serialized)
        self.assertNotIn("/Users/", serialized)

    def test_proposal_version_history_and_approval_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            proposal_path = root / "proposals/capability/testing-codex-native-activation-v2.yaml"
            proposal = load_yaml(proposal_path)
            proposal["approval_status"] = "deferred"
            proposal_path.write_text(yaml.safe_dump(proposal, sort_keys=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Stale Capability proposal"):
                check_repository(root)
            proposal["approval_status"] = "approved"
            proposal.pop("approved_change_reference")
            proposal_path.write_text(yaml.safe_dump(proposal, sort_keys=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "requires approved_change_reference"):
                check_repository(root)
            proposal["approved_change_reference"] = "capabilities/quality/testing.yaml@2"
            proposal_path.write_text(yaml.safe_dump(proposal, sort_keys=False), encoding="utf-8")
            check_repository(root)
            proposal["current_version"] = "0"
            proposal["approval_status"] = "rejected"
            proposal.pop("approved_change_reference")
            proposal_path.write_text(yaml.safe_dump(proposal, sort_keys=False), encoding="utf-8")
            check_repository(root)

    def test_only_accepted_verified_experiences_can_be_linked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            capability_path = root / "capabilities/quality/testing.yaml"
            capability = load_yaml(capability_path)
            capability["experience_ids"] = ["synthetic-link"]
            capability_path.write_text(yaml.safe_dump(capability, sort_keys=False), encoding="utf-8")
            for state, review_status in (("candidate", "accepted"), ("rejected", "accepted"), ("verified", "pending")):
                source = load_yaml(root / "experiences/candidate/testing-gap.yaml")
                source["id"] = source["identity"] = "synthetic-link"
                source["capability_ids"] = ["testing"]
                source["status"] = "verified"
                source["review"] = {"review_status": review_status, "reviewer": "fixture", "reviewed_at": "2026-10-16T00:00:00Z"}
                if not any(item.get("type") == "project_result" for item in source["evidence"]):
                    source["evidence"] = [{"type": "project_result", "source": "fixture", "description": "fixture", "strength": "moderate", "verification": {"status": "checked"}}]
                fixture = root / "experiences" / state / "synthetic-link.yaml"
                source["status"] = state
                if state == "verified":
                    source["review"]["review_status"] = review_status
                fixture.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")
                if state == "verified":
                    with self.assertRaisesRegex(Exception, "accepted"):
                        check_repository(root)
                else:
                    with self.assertRaisesRegex(ValueError, "Invalid curated verified Experience link"):
                        check_repository(root)
                fixture.unlink()

    def test_product_loop_evidence_is_sanitized_and_bound_to_one_pilot(self):
        evidence = ROOT / "examples/product-loop/codex-new-project"
        first = json.loads((evidence / "first-plan-summary.json").read_text(encoding="utf-8"))
        applied = json.loads((evidence / "apply-result.json").read_text(encoding="utf-8"))
        second = json.loads((evidence / "second-plan-summary.json").read_text(encoding="utf-8"))
        for item in (first, applied, second):
            self.assertTrue(item["evidence_sanitized"])
            serialized = json.dumps(item, ensure_ascii=False)
            self.assertNotIn("C:\\Users\\", serialized)
            self.assertNotIn("/Users/", serialized)
            self.assertNotRegex(serialized, r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}")
            self.assertNotRegex(serialized, r"(?i)(token|api[_-]?key|secret)\\s*[:=]")
        self.assertEqual(first["target_base_sha"], applied["target_base_sha"])
        self.assertEqual(first["target_base_sha"], second["target_base_sha"])
        self.assertEqual(first["route"], "compose")
        self.assertTrue(applied["business_files_unchanged"] and applied["verification_passed"])
        self.assertEqual(second["new_project_writes"] + second["new_global_writes"] + second["new_activations"], 0)
        self.assertFalse(second["requires_confirmation"])


if __name__ == "__main__":
    unittest.main()
