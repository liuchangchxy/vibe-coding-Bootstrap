"""Unit tests for read-only Existing Project discovery using synthetic projects."""

import hashlib
from pathlib import Path
import tempfile
import unittest

from adoption.core import discover
from composer.core import ROOT, validate


def tree_snapshot(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


class AdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "sample"
        self.project.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def put(self, relative, content):
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_known_instruction_skill_workflow_and_mcp_assets_are_discovered(self):
        self.put("AGENTS.md", "# Engineering baseline\nFollow development rules and testing policy.\n")
        self.put("CLAUDE.md", "# Project instructions\n")
        self.put(".claude/skills/release/SKILL.md", "# Release\n")
        self.put(".agents/skills/triage/SKILL.md", "# Triage\n")
        self.put(".opencode/skills/review/SKILL.md", "# Code review\n")
        self.put(".github/workflows/ci.yml", "name: CI\n")
        self.put(".github/vccp/reviewer-task-prompt.md", "# Review prompt\n")
        self.put("docs/mcp-setup.md", "# MCP setup\nConfigure MCP tools.\n")
        self.put("docs/process/TESTING.md", "# Testing\nRun flutter test.\n")
        self.put("docs/notes/ordinary.md", "# Ordinary note\nPersonal note.\n")

        report = discover(self.project)
        by_path = {asset["path"]: asset for asset in report["discovered_assets"]}
        for relative in (
            "AGENTS.md", "CLAUDE.md", ".claude/skills/release/SKILL.md",
            ".agents/skills/triage/SKILL.md", ".opencode/skills/review/SKILL.md",
            ".github/workflows/ci.yml", "docs/mcp-setup.md", "docs/process/TESTING.md",
        ):
            self.assertIn(relative, by_path)
            self.assertEqual("project_owned", by_path[relative]["ownership"])
        self.assertEqual("agent_instruction", by_path["AGENTS.md"]["asset_type"])
        self.assertEqual("skill", by_path[".opencode/skills/review/SKILL.md"]["asset_type"])
        self.assertEqual("workflow", by_path[".github/workflows/ci.yml"]["asset_type"])
        self.assertIn("MCP setup", by_path["docs/mcp-setup.md"]["detected_signals"])
        self.assertNotIn("docs/notes/ordinary.md", by_path)

    def test_all_supported_skill_roots_and_doc_filter(self):
        self.put(".claude/skills/a/SKILL.md", "# A")
        self.put(".agents/skills/b/SKILL.md", "# B")
        self.put(".opencode/skills/c/SKILL.md", "# C")
        self.put("docs/CONSTRAINTS.md", "# Constraints")
        self.put("docs/random.md", "# Random")
        paths = {asset["path"] for asset in discover(self.project)["discovered_assets"]}
        self.assertTrue({".claude/skills/a/SKILL.md", ".agents/skills/b/SKILL.md", ".opencode/skills/c/SKILL.md", "docs/CONSTRAINTS.md"}.issubset(paths))
        self.assertNotIn("docs/random.md", paths)

    def test_capability_mapping_prefers_unknown_without_evidence(self):
        self.put("README.md", "# Sample\nA small application.\n")
        report = discover(self.project)
        statuses = {item["capability_id"]: item["status"] for item in report["capability_coverage"]}
        self.assertEqual("unknown", statuses["research-workflow"])
        self.assertEqual("unknown", statuses["debugging"])
        self.assertEqual("unknown", statuses["agent-development"])
        self.assertTrue(all(item["status"] != "missing" for item in report["capability_coverage"]))

    def test_evidence_paths_resolve_and_report_satisfies_schema(self):
        self.put("AGENTS.md", "# Development rules\n")
        self.put("docs/TESTING.md", "# Testing\nThe test policy runs regression tests.\n")
        report = discover(self.project)
        validate(report, "adoption-report.schema.json", ROOT)
        asset_paths = {asset["path"] for asset in report["discovered_assets"]}
        for item in report["capability_coverage"]:
            for evidence in item["evidence"]:
                self.assertIn(evidence["path"], asset_paths)
                self.assertTrue((self.project / evidence["path"]).is_file())

    def test_discovery_leaves_target_tree_unchanged(self):
        self.put("AGENTS.md", "# Instructions\n")
        self.put("docs/process/TESTING.md", "# Testing\n")
        before = tree_snapshot(self.project)
        discover(self.project)
        after = tree_snapshot(self.project)
        self.assertEqual(before, after)

    def test_project_context_uses_manifest_and_known_directories(self):
        self.put("pubspec.yaml", "name: sample\n")
        self.put("README.md", "# Sample\nA Flutter cross-platform app.\n")
        self.put("server/README.md", "# Server\n")
        self.put("test/widget_test.dart", "")
        self.put(".github/workflows/ci.yml", "name: CI\n")
        report = discover(self.project)
        self.assertEqual(["Dart", "Flutter"], report["project"]["technologies"])
        self.assertEqual("cross-platform application", report["project"]["project_type"])
        self.assertTrue({"server component", "tests", "GitHub Actions"}.issubset(report["project"]["development_signals"]))


if __name__ == "__main__":
    unittest.main()
