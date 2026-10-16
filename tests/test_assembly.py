import copy
import shutil
import socket
import tempfile
import unittest
from pathlib import Path

import yaml

from assembly.core import assemble
from composer.core import ROOT, load_yaml, validate


class AssemblyTests(unittest.TestCase):
    def test_codex_native_testing_component_is_a_project_reference(self):
        context = load_yaml(ROOT / "examples/reuse/codex-maintained-project/project-context.yaml")
        from composer.core import compose
        recommendation = compose(context)
        plan = assemble(recommendation)
        item = next(item for item in plan["components"] if item["component_id"] == "codex-native-testing-skill")
        self.assertEqual("testing", item["source_capability"])
        self.assertEqual("skill", item["component_type"])
        self.assertEqual("project", item["scope"])
        self.assertEqual("reference", item["delivery"])
        self.assertNotIn("destination", item)

    def recommendation(self, name):
        return load_yaml(ROOT / "examples" / name / "recommendation.yaml")

    def test_examples_resolve_scopes_and_minimal_plan(self):
        expected = {
            "long-lived-web": {"global", "project", "external"},
            "agent": {"external", "project"},
            "one-off-experiment": set(),
        }
        for name, scopes in expected.items():
            plan = assemble(self.recommendation(name), generated_at="2026-10-15T00:00:00Z")
            validate(plan, "assembly-plan.schema.json")
            self.assertEqual(scopes, {item["scope"] for item in plan["components"]})
            self.assertTrue(plan["requires_confirmation"])
            self.assertEqual(plan, assemble(self.recommendation(name), generated_at="2026-10-15T00:00:00Z"))
        minimal = assemble(self.recommendation("one-off-experiment"))
        self.assertEqual([], minimal["components"])
        self.assertTrue(minimal["requires_confirmation"])

    def test_capability_dependency_expansion_and_exclusion(self):
        recommendation = self.recommendation("agent")
        plan = assemble(recommendation)
        self.assertEqual({"agent-development", "testing"}, set(plan["selected_capabilities"]))
        recommendation["recommendations"] = [row for row in recommendation["recommendations"] if row["capability_id"] != "testing"]
        recommendation["excluded"] = [{"capability_id": "testing", "reason": "User excluded testing."}]
        plan = assemble(recommendation)
        self.assertTrue(any("explicitly excluded" in issue["reason"] for issue in plan["unresolved_conflicts"]))
        self.assertTrue(all(item["action"] == "review_required" for item in plan["components"]))
        self.assertNotIn("testing", plan["selected_capabilities"])

    def test_conflicts_are_recorded_without_winner(self):
        recommendation = self.recommendation("long-lived-web")
        recommendation["conflicts"] = [["engineering-baseline", "documentation"]]
        plan = assemble(recommendation)
        self.assertTrue(any(issue["kind"] == "capability" for issue in plan["unresolved_conflicts"]))
        self.assertTrue(all(item["action"] == "review_required" for item in plan["components"] if item["source_capability"] in {"engineering-baseline", "documentation"}))

    def test_registered_external_references_are_not_fabricated_as_installers(self):
        plan = assemble(self.recommendation("agent"))
        self.assertTrue(all(item["action"] == "review_required" for item in plan["components"] if item["scope"] == "external"))
        self.assertTrue(all(item["delivery"] == "reference" for item in plan["components"] if item["scope"] == "external"))
        codex = next(item for item in plan["components"] if item["component_id"] == "codex-native-testing-skill")
        self.assertEqual("reference", codex["delivery"])

    def test_capability_without_components_stays_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("schemas", "capabilities", "assembly"):
                shutil.copytree(ROOT / name, root / name)
            record_path = root / "capabilities/workflow/documentation.yaml"
            record = load_yaml(record_path)
            record.pop("components")
            record_path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
            recommendation = self.recommendation("long-lived-web")
            recommendation["recommendations"] = [
                row for row in recommendation["recommendations"]
                if row["capability_id"] == "documentation"
            ]
            plan = assemble(recommendation, root)
            self.assertEqual([], plan["components"])
            self.assertTrue(any("no installable implementation component is registered" in warning for warning in plan["warnings"]))

    def test_component_scope_delivery_and_dependency_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("schemas", "capabilities", "assembly"):
                shutil.copytree(ROOT / name, root / name)
            record_path = root / "capabilities/engineering/engineering-baseline.yaml"
            record = load_yaml(record_path)
            record["components"].append({
                "id": "alternate-shared-prompt", "kind": "prompt",
                "role": "Fixture for scope and delivery conflict checks", "source_id": "primary",
                "assembly": {
                    "id": "reviewable-changes", "name": "Reviewable changes prompt",
                    "type": "prompt", "source": "https://example.invalid/reviewable-changes.md",
                    "provider": "vibe-coding-bootstrap", "version": "1", "scope": "project",
                    "delivery": "native_install", "optional": True, "provenance": {"test": True},
                    "dependencies": ["missing-component"],
                },
            })
            record_path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
            plan = assemble(self.recommendation("long-lived-web"), root)
            kinds = {issue["kind"] for issue in plan["unresolved_conflicts"]}
            self.assertTrue({"scope", "delivery", "component"}.issubset(kinds))
            self.assertTrue(all(item["action"] == "review_required" for item in plan["components"] if item["component_id"] == "reviewable-changes"))

    def test_duplicate_copy_destinations_are_reported_for_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("schemas", "capabilities", "assembly"):
                shutil.copytree(ROOT / name, root / name)
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
            plan = assemble(self.recommendation("long-lived-web"), root)
            conflicts = [issue for issue in plan["unresolved_conflicts"] if issue["kind"] == "destination"]
            self.assertEqual(1, len(conflicts))
            self.assertIn("Multiple components target the same destination", conflicts[0]["reason"])
            self.assertTrue(all(item["action"] == "review_required" for item in plan["components"]
                                if item["component_id"] in {"project-test-guidance", "project-documentation-outline"}))

    def test_input_is_unchanged_and_no_network_is_used(self):
        recommendation = self.recommendation("long-lived-web")
        before = copy.deepcopy(recommendation)
        original_socket = socket.socket
        def fail_network(*args, **kwargs):
            raise AssertionError("assembly attempted network access")
        socket.socket = fail_network
        try:
            assemble(recommendation)
        finally:
            socket.socket = original_socket
        self.assertEqual(before, recommendation)


if __name__ == "__main__":
    unittest.main()
