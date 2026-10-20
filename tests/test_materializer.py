"""Materializer tests use temporary roots and never modify a user project."""

from pathlib import Path
import os
import tempfile
import unittest

import yaml

from assembly.core import assemble
from composer.core import ROOT, compose, load_yaml, validate
from materializer import materialize


class MaterializerTests(unittest.TestCase):
    def plan(self, **updates):
        item = {
            "component_id": "fixture", "component_type": "document", "source_capability": "testing",
            "capability_version": "1", "reason": "Fixture", "scope": "project",
            "source": "components/project/test-guidance.md", "delivery": "copy",
            "destination": ".vibe-coding-bootstrap/components/test-guidance.md", "action": "include",
            "configuration_preview": {}, "dependencies": [], "conflicts": [],
        }
        item.update(updates)
        plan = assemble(load_yaml(ROOT / "examples/one-off-experiment/recommendation.yaml"))
        plan["components"] = [item]
        return plan

    def duplicate_copy_plan(self, scope="project", same_source=False):
        first = self.plan(scope=scope, destination="components/shared.md")["components"][0]
        first["component_id"] = "shared-a"
        second = dict(first, component_id="shared-b", source_capability="documentation")
        if not same_source:
            second["source"] = "components/project/documentation-outline.md"
        return self.plan(), [first, second]

    def test_dry_run_and_apply_then_idempotent_reapply(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            plan = self.plan()
            dry = materialize(plan, project)
            target = project / ".vibe-coding-bootstrap/components/test-guidance.md"
            self.assertEqual("dry_run", dry["mode"])
            self.assertEqual("copied", dry["items"][0]["result"])
            self.assertFalse(target.exists())
            self.assertFalse((project / ".vibe-coding-bootstrap").exists())

            applied = materialize(plan, project, apply=True)
            self.assertEqual("copied", applied["items"][0]["result"])
            self.assertTrue(target.is_file())
            manifest_path = Path(applied["manifest_path"])
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(1, manifest["format_version"])
            self.assertEqual("testing", manifest["items"][0]["source_capability"])
            second = materialize(plan, project, apply=True)
            self.assertEqual("unchanged", second["items"][0]["result"])
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(1, len(manifest["items"]))

    def test_scope_reference_global_and_unsupported(self):
        with tempfile.TemporaryDirectory() as directory:
            project, global_root = Path(directory) / "project", Path(directory) / "global"
            project.mkdir()
            plan = self.plan(scope="global", destination="components/guidance.md")
            result = materialize(plan, project, apply=True)
            self.assertEqual("skipped", result["items"][0]["result"])
            self.assertFalse((project / "components/guidance.md").exists())
            result = materialize(plan, project, global_root, apply=True)
            self.assertEqual("copied", result["items"][0]["result"])
            self.assertTrue((global_root / "components/guidance.md").is_file())
            self.assertFalse((project / "components/guidance.md").exists())

            plan = self.plan(scope="external", delivery="reference",
                             source="https://example.invalid/docs")
            self.assertEqual("reference_only", materialize(plan, project, apply=True)["items"][0]["result"])
            for delivery in ("native_install", "symlink", "generated"):
                plan = self.plan(delivery=delivery)
                self.assertEqual("unsupported", materialize(plan, project, apply=True)["items"][0]["result"])

    def test_paths_and_existing_content_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            for source in ("../../secret.txt", "https://example.invalid/file"):
                plan = self.plan(source=source)
                with self.assertRaises(ValueError):
                    materialize(plan, project)
            plan = self.plan(destination="../outside.md")
            with self.assertRaises(Exception):
                materialize(plan, project)
            plan = self.plan()
            target = project / plan["components"][0]["destination"]
            target.parent.mkdir(parents=True)
            target.write_text("user content", encoding="utf-8")
            result = materialize(plan, project, apply=True)
            self.assertEqual("conflict", result["items"][0]["result"])
            self.assertEqual("user content", target.read_text(encoding="utf-8"))
            self.assertIsNone(result["manifest_path"])

    def test_identical_unowned_file_is_not_adopted_or_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            plan = self.plan()
            target = project / plan["components"][0]["destination"]
            target.parent.mkdir(parents=True)
            target.write_bytes((ROOT / plan["components"][0]["source"]).read_bytes())
            result = materialize(plan, project, apply=True)
            self.assertEqual("conflict", result["items"][0]["result"])
            self.assertIsNone(result["manifest_path"])
            self.assertFalse((project / ".vibe-coding-bootstrap/manifest.yaml").exists())

    def test_version_only_change_refreshes_manifest_without_rewriting_file(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            plan = self.plan()
            materialize(plan, project, apply=True)
            target = project / plan["components"][0]["destination"]
            original = target.read_bytes()
            plan["components"][0]["capability_version"] = "2"
            result = materialize(plan, project, apply=True)
            self.assertEqual("upgraded", result["items"][0]["result"])
            self.assertEqual(original, target.read_bytes())
            manifest = yaml.safe_load((project / ".vibe-coding-bootstrap/manifest.yaml").read_text(encoding="utf-8"))
            self.assertEqual("2", manifest["items"][0]["capability_version"])

    def test_existing_manifest_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            manifest = project / ".vibe-coding-bootstrap/manifest.yaml"
            manifest.parent.mkdir()
            manifest.write_text("user: data\n", encoding="utf-8")
            result = materialize(self.plan(), project, apply=True)
            self.assertEqual("conflict", result["items"][0]["result"])
            self.assertFalse((manifest.parent / "components/test-guidance.md").exists())
            self.assertEqual("user: data\n", manifest.read_text(encoding="utf-8"))

    def test_modified_managed_file_is_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            plan = self.plan()
            materialize(plan, project, apply=True)
            target = project / plan["components"][0]["destination"]
            target.write_text("user edit", encoding="utf-8")
            result = materialize(plan, project, apply=True)
            self.assertEqual("conflict", result["items"][0]["result"])
            self.assertEqual("user edit", target.read_text(encoding="utf-8"))

    def test_destination_symlink_cannot_escape_project_root(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project, outside = base / "project", base / "outside"
            project.mkdir()
            outside.mkdir()
            link = project / ".vibe-coding-bootstrap"
            try:
                os.symlink(outside, link, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"Directory symlinks are unavailable: {error}")
            with self.assertRaises(ValueError):
                materialize(self.plan(), project, apply=True)
            self.assertEqual([], list(outside.iterdir()))

    def test_composer_assembly_materializer_end_to_end(self):
        with tempfile.TemporaryDirectory() as directory:
            project, global_root = Path(directory) / "project", Path(directory) / "global"
            project.mkdir()
            recommendation = compose(load_yaml(ROOT / "examples/long-lived-web/project-context.yaml"))
            plan = assemble(recommendation, recommendation_source="Composer output")
            dry = materialize(plan, project, global_root)
            self.assertTrue(all(not (project / item["destination"]).exists()
                                for item in plan["components"] if item["scope"] == "project"
                                and item["delivery"] == "copy"))
            self.assertTrue(any(item["scope"] == "external" and item["result"] == "reference_only"
                                for item in dry["items"]))
            applied = materialize(plan, project, global_root, apply=True)
            copied = {Path(item["destination"]).name for item in applied["items"] if item["result"] == "copied"}
            self.assertEqual({"test-guidance.md", "documentation-outline.md", "reviewable-changes.md"}, copied)
            self.assertTrue((project / ".vibe-coding-bootstrap/manifest.yaml").is_file())
            repeated = materialize(plan, project, global_root, apply=True)
            self.assertTrue(all(item["result"] in {"unchanged", "reference_only"}
                                for item in repeated["items"]))
            manifest_path = project / ".vibe-coding-bootstrap/manifest.yaml"
            manifest_items = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))["items"]
            references = {(item["source_capability"], item["component_id"])
                          for item in manifest_items if item["state"] == "reference_only"}
            self.assertTrue({("testing", "reference"), ("documentation", "reference")}.issubset(references))
            self.assertIn(("testing", "codex-native-testing-skill"), references)
            self.assertFalse((project / ".agents/skills/bootstrap-testing/SKILL.md").exists())
            self.assertIn(("engineering-baseline", "pro-git-reference"), references)
            count = len(manifest_items)
            repeated_again = materialize(plan, project, global_root, apply=True)
            self.assertEqual(count, len(yaml.safe_load(manifest_path.read_text(encoding="utf-8"))["items"]))
            self.assertTrue(all(item["result"] in {"unchanged", "reference_only"}
                                for item in repeated_again["items"]))
            validate(repeated, "materialization-result.schema.json")

    def test_manifest_keeps_same_id_external_items_from_different_capabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            plan = self.plan()
            first = dict(plan["components"][0], component_id="reference", scope="external",
                         delivery="reference", source="https://example.invalid/testing")
            second = dict(first, source_capability="documentation", source="https://example.invalid/docs")
            plan["components"] = [first, second]
            materialize(plan, project, apply=True)
            manifest = yaml.safe_load((project / ".vibe-coding-bootstrap/manifest.yaml").read_text(encoding="utf-8"))
            identities = {(item["source_capability"], item["component_id"])
                          for item in manifest["items"]}
            self.assertTrue({("testing", "reference"), ("documentation", "reference")}.issubset(identities))
            self.assertEqual(2, len(manifest["items"]))
            self.assertTrue(all(item["destination"] == "" and item["state"] == "reference_only"
                                and item["capability_version"] == "1" and "source" in item
                                for item in manifest["items"]))

    def test_duplicate_project_destination_conflicts_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            plan, components = self.duplicate_copy_plan()
            plan["components"] = components
            result = materialize(plan, project, apply=True)
            self.assertEqual(["conflict", "conflict"], [item["result"] for item in result["items"]])
            self.assertTrue(all("same destination" in item["reason"] for item in result["items"]))
            self.assertFalse((project / "components/shared.md").exists())

    def test_duplicate_global_destination_conflicts_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            project, global_root = Path(directory) / "project", Path(directory) / "global"
            project.mkdir()
            plan, components = self.duplicate_copy_plan(scope="global")
            plan["components"] = components
            result = materialize(plan, project, global_root, apply=True)
            self.assertEqual(["conflict", "conflict"], [item["result"] for item in result["items"]])
            self.assertFalse((global_root / "components/shared.md").exists())

    def test_same_relative_destination_in_project_and_global_roots_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            project, global_root = Path(directory) / "project", Path(directory) / "global"
            project.mkdir()
            plan, components = self.duplicate_copy_plan()
            components[1]["scope"] = "global"
            plan["components"] = components
            result = materialize(plan, project, global_root, apply=True)
            self.assertEqual(["copied", "copied"], [item["result"] for item in result["items"]])
            self.assertTrue((project / "components/shared.md").is_file())
            self.assertTrue((global_root / "components/shared.md").is_file())

    def test_same_content_duplicate_plan_still_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            plan, components = self.duplicate_copy_plan(same_source=True)
            plan["components"] = components
            result = materialize(plan, project, apply=True)
            self.assertEqual(["conflict", "conflict"], [item["result"] for item in result["items"]])
            self.assertFalse((project / "components/shared.md").exists())


if __name__ == "__main__":
    unittest.main()
