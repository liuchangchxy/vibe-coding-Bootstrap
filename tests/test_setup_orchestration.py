"""Tests for the simplified Host-driven setup orchestration flow (Issue #29)."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from bootstrap.core import ROOT, apply_plan, build_plan, decide
from bootstrap.guided import inspect_setup
from bootstrap.home import init_vcbs_home, save_profile
from bootstrap.lifecycle import get_status


class SetupOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / "consumer"
        self.project.mkdir()
        self.home = self.base / "vcbs_home"
        init_vcbs_home(self.home)

        self.git("init", "-b", "main")
        self.git("config", "user.name", "Setup Test")
        self.git("config", "user.email", "setup@example.invalid")
        self.put("pyproject.toml", "[project]\nname = 'consumer'\nversion = '0.1.0'\n")
        self.put("README.md", "# Consumer App\nA test project.\n")
        self.git("add", ".")
        self.git("commit", "-m", "Initial commit")
        self.head_sha = self.git("rev-parse", "HEAD")

    def tearDown(self):
        self.temp.cleanup()

    def put(self, relative, content):
        target = self.project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.project), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def cli(self, *args):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        return subprocess.run(
            [sys.executable, "-m", "bootstrap", *map(str, args)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_simplified_flow_inspect_recommend_confirm_apply(self):
        """Test full simplified 1-approval flow: inspect -> recommend -> plan -> apply --confirm."""
        fact_file = self.base / "facts.json"
        inspect_res = self.cli(
            "setup", "inspect",
            "--project-root", self.project,
            "--host", "none",
            "--vcbs-home", self.home,
            "--recommendation-input", fact_file,
            "--json",
        )
        self.assertEqual(0, inspect_res.returncode, inspect_res.stderr)
        self.assertTrue(fact_file.is_file())
        facts = json.loads(fact_file.read_text(encoding="utf-8"))
        self.assertEqual(self.head_sha, facts["target"]["commit_sha"])
        self.assertEqual(["Python"], facts["project_facts"]["technologies"])

        # Host reasons and writes recommendation
        rec_file = self.base / "recommendation.json"
        rec_data = {
            "format_version": 1,
            "target": {
                "commit_sha": self.head_sha,
                "dirty": False,
                "path": str(self.project),
            },
            "host": "none",
            "recommendations": [
                {
                    "capability_id": "testing",
                    "reason": "Standard automated testing policy is recommended for Python projects.",
                    "scope": "project",
                    "component_ids": ["project-test-guidance"],
                }
            ],
            "not_recommended": [
                {
                    "capability_id": "vcbs-manager",
                    "reason": "No Host adapter selected.",
                }
            ],
            "conflicts": [],
            "install_nothing": False,
            "reason": "Add testing guidance component",
        }
        rec_file.write_text(json.dumps(rec_data, indent=2), encoding="utf-8")

        # Plan with Host recommendation
        plan_file = self.base / "plan.json"
        plan_res = self.cli(
            "setup", "plan",
            "--project-root", self.project,
            "--host", "none",
            "--recommendation", rec_file,
            "--vcbs-home", self.home,
            "--output", plan_file,
        )
        self.assertEqual(0, plan_res.returncode, plan_res.stderr)
        self.assertIn("Recommendation Source: Host AI semantic judgment", plan_res.stdout)
        self.assertIn("What:\n  - testing (project): project-test-guidance", plan_res.stdout)
        self.assertIn("Why: Standard automated testing policy is recommended for Python projects.", plan_res.stdout)
        self.assertIn("Scope: project", plan_res.stdout)
        self.assertIn("Next: review the plan, then run setup apply --plan <plan> --confirm", plan_res.stdout)
        self.assertTrue(plan_file.is_file())
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        self.assertEqual(["testing"], plan["capabilities"])
        self.assertTrue(plan["executable"])
        self.assertGreater(plan["actual_changes"], 0)

        # Single user approval: apply with --confirm
        apply_res = self.cli(
            "setup", "apply",
            "--project-root", self.project,
            "--plan", plan_file,
            "--confirm",
        )
        self.assertEqual(0, apply_res.returncode, apply_res.stderr)
        applied = json.loads(apply_res.stdout)
        self.assertEqual("applied", applied["status"])
        self.assertTrue((self.project / ".vibe-coding-bootstrap/components/test-guidance.md").is_file())

        status = get_status(self.project, host="none")
        self.assertEqual("installed", status["status"])
        self.assertFalse(status["has_conflicts"])

    def test_install_nothing_valid_noop_recommendation(self):
        """Test 'install nothing' as a first-class valid recommendation."""
        rec_file = self.base / "noop-recommendation.json"
        rec_data = {
            "format_version": 1,
            "target": {
                "commit_sha": self.head_sha,
                "dirty": False,
                "path": str(self.project),
            },
            "host": "none",
            "recommendations": [],
            "not_recommended": [],
            "conflicts": [],
            "install_nothing": True,
            "reason": "Project already has sufficient configuration; no changes required.",
        }
        rec_file.write_text(json.dumps(rec_data, indent=2), encoding="utf-8")

        plan_file = self.base / "noop-plan.json"
        plan_res = self.cli(
            "setup", "plan",
            "--project-root", self.project,
            "--host", "none",
            "--recommendation", rec_file,
            "--vcbs-home", self.home,
            "--output", plan_file,
        )
        self.assertEqual(0, plan_res.returncode, plan_res.stderr)
        self.assertIn("Recommendation: Install nothing. Plan contains no mutations.", plan_res.stdout)

        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        self.assertEqual(0, plan["actual_changes"])
        self.assertTrue(plan["executable"])
        self.assertTrue(plan["underlying_plans"]["install_nothing"])

        # Apply with --confirm: completes cleanly with no_changes
        apply_res = self.cli(
            "setup", "apply",
            "--project-root", self.project,
            "--plan", plan_file,
            "--confirm",
        )
        self.assertEqual(0, apply_res.returncode, apply_res.stderr)
        applied = json.loads(apply_res.stdout)
        self.assertEqual("no_changes", applied["status"])
        self.assertFalse((self.project / ".vibe-coding-bootstrap").exists())

    def test_vcbs_home_preferences_and_experiences_loaded_into_fact_package(self):
        """Test that VCBS Home profile and verified experience populate recommendation_input."""
        # Save custom profile in VCBS Home
        custom_profile = {
            "id": "test-user-profile",
            "name": "Test User",
            "preferences": {
                "testing_approach": "comprehensive",
                "interaction_style": "concise",
                "additional": {
                    "solution_complexity": "minimal",
                },
            },
            "default_capability_ids": ["testing"],
            "excluded_capability_ids": [],
            "reusable_experience_ids": [],
            "created_at": "2026-10-26T00:00:00Z",
            "updated_at": "2026-10-26T00:00:00Z",
        }
        save_profile(custom_profile, self.home)

        # Save verified experience in VCBS Home
        exp_file = self.home / "experiences" / "verified" / "exp-python-testing.yaml"
        exp_file.parent.mkdir(parents=True, exist_ok=True)
        exp_file.write_text(
            """id: exp-python-testing
status: verified
title: Python Testing Baseline
summary: Recommended pytest setup for Python projects.
project_context:
  project_type: software_development
  technologies:
    - Python
applicable_capabilities:
  - testing
""",
            encoding="utf-8",
        )

        inspected = inspect_setup(self.project, "none", vcbs_home=self.home)
        rec_input = inspected["recommendation_input"]

        # Verify personal preferences from VCBS Home
        self.assertEqual(
            "comprehensive",
            rec_input["personal_preferences"].get("testing_approach"),
        )
        self.assertEqual(
            "minimal",
            rec_input["personal_preferences"].get("additional", {}).get("solution_complexity"),
        )

        # Verify matching verified experience from VCBS Home
        matched_ids = [e["id"] for e in rec_input["matching_experiences"]]
        self.assertIn("exp-python-testing", matched_ids)

        # Verify isolation: no files created in consumer project
        self.assertFalse((self.project / ".vibe-coding-bootstrap").exists())
        self.assertFalse((self.project / "exp-python-testing.yaml").exists())

    def test_rejection_of_unknown_capability_in_recommendation(self):
        """Test that unknown capability IDs in Host recommendation are rejected."""
        rec_file = self.base / "bad-rec.json"
        rec_data = {
            "format_version": 1,
            "target": {
                "commit_sha": self.head_sha,
                "dirty": False,
                "path": str(self.project),
            },
            "host": "none",
            "recommendations": [
                {
                    "capability_id": "non-existent-capability-12345",
                    "reason": "Made up by rogue host",
                }
            ],
            "not_recommended": [],
            "conflicts": [],
            "install_nothing": False,
        }
        rec_file.write_text(json.dumps(rec_data, indent=2), encoding="utf-8")

        plan_file = self.base / "bad-plan.json"
        plan_res = self.cli(
            "setup", "plan",
            "--project-root", self.project,
            "--host", "none",
            "--recommendation", rec_file,
            "--vcbs-home", self.home,
            "--output", plan_file,
        )
        self.assertNotEqual(0, plan_res.returncode)
        self.assertIn("unknown capability ID", plan_res.stderr)

    def test_user_capability_from_home_can_be_recommended_and_planned(self):
        """Test that user-defined capabilities in VCBS Home can be recommended and planned."""
        cap_file = self.home / "capabilities" / "user-custom-tool.yaml"
        cap_file.parent.mkdir(parents=True, exist_ok=True)
        cap_file.write_text(
            """id: user-custom-tool
version: "1"
name: User Custom Tool
kind: documentation
description: User specific custom helper reference.
status: validated
maturity: established
purpose: Provide user-specific custom helper.
applicability:
  use_when:
    - User custom tool is desired.
  not_for:
    - Other projects.
evidence:
  state: supported
  summary: Defined in personal environment.
  references:
    - https://example.com/custom-tool
selection_hints:
  signals: []
  constraints: []
components:
  - id: custom-ref
    kind: reference
    role: User helper reference
    assembly:
      id: custom-ref
      name: Custom Ref
      type: document
      source: https://example.com/custom-tool
      provider: user-home
      version: "1"
      scope: external
      delivery: reference
      optional: true
      provenance:
        source_id: custom-ref
        relationship: reference
""",
            encoding="utf-8",
        )

        inspected = inspect_setup(self.project, "none", vcbs_home=self.home)
        opt_ids = [opt["capability_id"] for opt in inspected["capability_options"]]
        self.assertIn("user-custom-tool", opt_ids)

        # Host recommends user-custom-tool
        rec_file = self.base / "user-cap-rec.json"
        rec_data = {
            "format_version": 1,
            "target": {
                "commit_sha": self.head_sha,
                "dirty": False,
                "path": str(self.project),
            },
            "host": "none",
            "recommendations": [
                {
                    "capability_id": "user-custom-tool",
                    "reason": "User specified custom capability in VCBS Home.",
                    "scope": "project",
                }
            ],
            "not_recommended": [],
            "conflicts": [],
            "install_nothing": False,
        }
        rec_file.write_text(json.dumps(rec_data, indent=2), encoding="utf-8")

        plan_file = self.base / "user-cap-plan.json"
        plan_res = self.cli(
            "setup", "plan",
            "--project-root", self.project,
            "--host", "none",
            "--recommendation", rec_file,
            "--vcbs-home", self.home,
            "--output", plan_file,
        )
        self.assertEqual(0, plan_res.returncode, plan_res.stderr)
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        self.assertIn("user-custom-tool", plan["capabilities"])

    def test_fallback_to_static_recommendations_when_no_host_recommendation(self):
        """Test fallback to static selection/rules when --recommendation is omitted."""
        plan_file = self.base / "fallback-plan.json"
        plan_res = self.cli(
            "setup", "plan",
            "--project-root", self.project,
            "--host", "none",
            "--goal", "Setup testing guidelines",
            "--capability", "testing",
            "--exclude-component", "codex-native-testing-skill",
            "--output", plan_file,
        )
        self.assertEqual(0, plan_res.returncode, plan_res.stderr)
        self.assertTrue(plan_file.is_file())
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        self.assertEqual(["testing"], plan["capabilities"])
        self.assertEqual(1, plan["actual_changes"])


if __name__ == "__main__":
    unittest.main()
