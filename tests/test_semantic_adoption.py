"""Tests for bounded Host AI semantic adoption review."""

from pathlib import Path
import tempfile
import unittest

from adoption.semantic import (
    authority_hint, build_semantic_input, relevant_excerpt,
    validate_semantic_result,
)
from composer.core import ROOT, validate
from tests.test_adoption import tree_snapshot


class SemanticAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "sample"
        self.project.mkdir()
        self.put("AGENTS.md", "# Project instructions\nFollow documented requirements and test changes.\n")
        self.package = build_semantic_input(self.project)
        self.catalog_ids = [item["id"] for item in self.package["capability_catalog"]]

    def tearDown(self):
        self.temporary.cleanup()

    def put(self, relative, content):
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def valid_result(self):
        return {
            "project_name": self.package["project"]["name"],
            "reviewer": "host-ai",
            "semantic_coverage": [
                {"capability_id": capability_id, "status": "unknown", "confidence": "low",
                 "evidence": [{"path": "AGENTS.md", "claim": "Contains project instructions."}],
                 "reason": "Applicability or substantive coverage is not established.",
                 "recommended_action": "no_action"}
                for capability_id in self.catalog_ids
            ],
        }

    def test_input_and_result_schemas(self):
        validate(self.package, "semantic-review-input.schema.json", ROOT)
        validate(self.valid_result(), "semantic-review-result.schema.json", ROOT)

    def test_authority_hints(self):
        expected = {
            "AGENTS.md": "primary", "CLAUDE.md": "primary", "SPEC.md": "primary",
            "docs/CONSTRAINTS.md": "primary", "docs/ROADMAP.md": "primary",
            "docs/START_HERE.md": "active", "docs/process/TESTING.md": "active",
            ".opencode/skills/x/SKILL.md": "active", ".github/workflows/ci.yml": "active",
            "docs/archive/old.md": "historical", "docs/superpowers/plans/old.md": "historical",
            "README.md": "reference", "docs/mcp-setup.md": "reference",
        }
        for path, hint in expected.items():
            with self.subTest(path=path):
                self.assertEqual(hint, authority_hint(path))

    def test_excerpt_contains_opening_and_rule_and_obeys_bound(self):
        source = "# Main title\n\nIntro paragraph.\n\n## Validation\n\nChanges must pass a repeatable regression test.\n\n" + ("filler text " * 900)
        excerpt = relevant_excerpt(source, [], max_chars=2600)
        self.assertLessEqual(len(excerpt), 2600)
        self.assertIn("Main title", excerpt)
        self.assertIn("must pass a repeatable regression test", excerpt)

    def test_each_excerpt_in_package_is_bounded_and_profile_is_explicit(self):
        self.assertTrue(all(len(asset["relevant_excerpt"]) <= 2600 for asset in self.package["discovered_assets"]))
        self.assertEqual({"defaults": [], "exclusions": [], "explicit_preferences": {}}, self.package["profile_constraints"])

    def test_evidence_must_resolve_to_discovered_asset(self):
        result = self.valid_result()
        result["semantic_coverage"][0]["evidence"][0]["path"] = "not-in-package.md"
        with self.assertRaisesRegex(ValueError, "not a discovered asset"):
            validate_semantic_result(result, self.package)

    def test_catalog_ids_must_match_exactly_and_duplicates_are_rejected(self):
        result = self.valid_result()
        result["semantic_coverage"][0]["capability_id"] = "invented-capability"
        with self.assertRaisesRegex(ValueError, "match the current Catalog"):
            validate_semantic_result(result, self.package)
        result = self.valid_result()
        result["semantic_coverage"][1]["capability_id"] = result["semantic_coverage"][0]["capability_id"]
        with self.assertRaisesRegex(ValueError, "Duplicate Capability"):
            validate_semantic_result(result, self.package)

    def test_missing_and_conflict_require_reason_and_evidence(self):
        result = self.valid_result()
        result["semantic_coverage"][0].update(status="missing", reason=" ", recommended_action="review_gap")
        with self.assertRaisesRegex(ValueError, "requires a reason"):
            validate_semantic_result(result, self.package)
        result["semantic_coverage"][0].update(status="conflict", reason="Incompatible requirement.", evidence=[], recommended_action="review_conflict")
        with self.assertRaisesRegex(ValueError, "requires supporting evidence"):
            validate_semantic_result(result, self.package)

    def test_recommended_actions_match_status(self):
        for status, action in (("partial", "no_action"), ("conflict", "review_gap"), ("unknown", "review_gap"), ("satisfied", "review_gap"), ("missing", "keep_existing")):
            with self.subTest(status=status, action=action):
                result = self.valid_result()
                result["semantic_coverage"][0].update(status=status, recommended_action=action)
                with self.assertRaises(ValueError):
                    validate_semantic_result(result, self.package)

    def test_invalid_bootstrap_component_action_is_rejected(self):
        result = self.valid_result()
        item = next(entry for entry in result["semantic_coverage"] if entry["capability_id"] == "engineering-baseline")
        item.update(status="missing", recommended_action="consider_bootstrap_component", reason="Applicable gap.")
        with self.assertRaisesRegex(ValueError, "No applicable project-scoped"):
            validate_semantic_result(result, self.package)
    def test_semantic_package_is_read_only_for_target(self):
        before = tree_snapshot(self.project)
        build_semantic_input(self.project)
        after = tree_snapshot(self.project)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()\n