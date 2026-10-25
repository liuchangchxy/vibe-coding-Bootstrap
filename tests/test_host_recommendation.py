"""Tests for Host-driven AI recommendation package and validation."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

from bootstrap.core import ProductLoopError
from bootstrap.home import init_vcbs_home, load_profile, save_profile
from bootstrap.recommendation import (
    build_recommendation_package,
    validate_host_recommendation,
)
from composer.core import ROOT


class HostRecommendationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name).resolve()
        self.project = self.base / "project"
        self.project.mkdir()

        # Initialize git repo for project
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test Developer")
        self.git("config", "user.email", "test@example.invalid")
        (self.project / "pyproject.toml").write_text("[project]\nname = 'test-proj'\n", encoding="utf-8")
        (self.project / "README.md").write_text("# Test Project\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "initial commit")

        # Set up VCBS Home
        self.vcbs_home = self.base / ".vcbs"
        init_vcbs_home(self.vcbs_home, seed_root=ROOT)

    def tearDown(self):
        self.temp_dir.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.project), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_fact_package_contents_and_facts_distinguishable_from_recommendations(self):
        pkg = build_recommendation_package(self.project, host="codex", root=ROOT, vcbs_home=self.vcbs_home)
        self.assertEqual(1, pkg["format_version"])

        # Project facts
        self.assertIn("target", pkg)
        self.assertEqual(self.git("rev-parse", "HEAD"), pkg["target"]["commit_sha"])
        self.assertIn("project_facts", pkg)
        self.assertIn("Python", pkg["project_facts"]["technologies"])

        # Existing and managed assets
        self.assertIn("existing_assets", pkg)
        self.assertIn("managed_components", pkg)
        self.assertEqual([], pkg["managed_components"])

        # Available capabilities (from Catalog + user home)
        self.assertIn("available_capabilities", pkg)
        cap_ids = [c["capability_id"] for c in pkg["available_capabilities"]]
        self.assertIn("testing", cap_ids)
        self.assertIn("vcbs-manager", cap_ids)

        # Selected Host and personal preferences
        self.assertIn("selected_host", pkg)
        self.assertEqual("codex", pkg["selected_host"])
        self.assertIn("personal_preferences", pkg)
        self.assertIn("testing_approach", pkg["personal_preferences"])

        # Conflict signals and matching experiences
        self.assertIn("conflict_signals", pkg)
        self.assertIn("matching_experiences", pkg)

        # Fallback static recommendations must be distinct and marked as fallback
        self.assertIn("fallback_static_recommendations", pkg)
        if pkg["fallback_static_recommendations"]:
            for item in pkg["fallback_static_recommendations"]:
                self.assertTrue(item.get("is_fallback"))
                self.assertEqual("fallback_static_rules", item.get("source"))

    def test_valid_host_recommendation_validation(self):
        commit_sha = self.git("rev-parse", "HEAD")
        rec = {
            "format_version": 1,
            "target": {
                "commit_sha": commit_sha,
                "repository": "test-proj",
            },
            "host": "codex",
            "recommendations": [
                {
                    "capability_id": "testing",
                    "component_ids": ["project-test-guidance"],
                    "scope": "project",
                    "reason": "Project has Python code and requires repeatable tests.",
                }
            ],
            "not_recommended": [
                {
                    "capability_id": "agent-development",
                    "reason": "Not an agent system.",
                }
            ],
            "conflicts": [],
            "install_nothing": False,
        }
        validated = validate_host_recommendation(rec, self.project, root=ROOT, vcbs_home=self.vcbs_home)
        self.assertEqual("codex", validated["host"])
        self.assertEqual(1, len(validated["recommendations"]))
        self.assertFalse(validated["install_nothing"])

    def test_valid_noop_install_nothing_recommendation(self):
        commit_sha = self.git("rev-parse", "HEAD")
        rec = {
            "format_version": 1,
            "target": {
                "commit_sha": commit_sha,
                "repository": "test-proj",
            },
            "host": "codex",
            "recommendations": [],
            "not_recommended": [
                {"capability_id": "testing", "reason": "Already handled by project tooling."}
            ],
            "conflicts": [],
            "install_nothing": True,
            "reason": "Install nothing; existing environment is already sufficient.",
        }
        validated = validate_host_recommendation(rec, self.project, root=ROOT, vcbs_home=self.vcbs_home)
        self.assertTrue(validated["install_nothing"])
        self.assertEqual([], validated["recommendations"])

    def test_reject_unknown_capability_or_component_id(self):
        commit_sha = self.git("rev-parse", "HEAD")
        rec = {
            "format_version": 1,
            "target": {"commit_sha": commit_sha},
            "host": "codex",
            "recommendations": [
                {
                    "capability_id": "non-existent-capability-12345",
                    "reason": "Imaginary capability.",
                }
            ],
        }
        with self.assertRaisesRegex(ProductLoopError, "INVALID_RECOMMENDATION: unknown capability ID"):
            validate_host_recommendation(rec, self.project, root=ROOT, vcbs_home=self.vcbs_home)

        rec_bad_comp = {
            "format_version": 1,
            "target": {"commit_sha": commit_sha},
            "host": "codex",
            "recommendations": [
                {
                    "capability_id": "testing",
                    "component_ids": ["non-existent-component-xyz"],
                    "reason": "Testing with fake component.",
                }
            ],
        }
        with self.assertRaisesRegex(ProductLoopError, "INVALID_RECOMMENDATION: unknown component ID"):
            validate_host_recommendation(rec_bad_comp, self.project, root=ROOT, vcbs_home=self.vcbs_home)

    def test_reject_stale_target_revision(self):
        # Different commit SHA
        rec = {
            "format_version": 1,
            "target": {"commit_sha": "0123456789abcdef0123456789abcdef01234567"},
            "host": "codex",
            "recommendations": [{"capability_id": "testing", "reason": "Reason"}],
        }
        with self.assertRaisesRegex(ProductLoopError, "STALE_RECOMMENDATION"):
            validate_host_recommendation(rec, self.project, root=ROOT, vcbs_home=self.vcbs_home)

    def test_reject_recommendation_bypassing_unmanaged_conflict(self):
        commit_sha = self.git("rev-parse", "HEAD")
        # Create an unmanaged file at project-test-guidance destination
        dest = self.project / ".vibe-coding-bootstrap/components/test-guidance.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("# Pre-existing unmanaged file\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "add pre-existing file")
        new_commit = self.git("rev-parse", "HEAD")

        rec = {
            "format_version": 1,
            "target": {"commit_sha": new_commit},
            "host": "codex",
            "recommendations": [
                {
                    "capability_id": "testing",
                    "component_ids": ["project-test-guidance"],
                    "scope": "project",
                    "reason": "Try to overwrite unmanaged file.",
                }
            ],
        }
        with self.assertRaisesRegex(ProductLoopError, "RECOMMENDATION_CONFLICT"):
            validate_host_recommendation(rec, self.project, root=ROOT, vcbs_home=self.vcbs_home)


if __name__ == "__main__":
    unittest.main()
