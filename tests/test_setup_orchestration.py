"""Tests for the simplified Host-driven setup orchestration flow (Issue #29)."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from bootstrap.core import ROOT, ProductLoopError, apply_plan, build_plan, decide, render_summary
from bootstrap.guided import inspect_setup
from bootstrap.home import init_vcbs_home, load_profile, save_profile
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

    def test_apply_with_custom_vcbs_home_and_customized_profile(self):
        """Test that custom VCBS Home with modified profile plans and applies successfully."""
        custom_home = self.base / "custom_vcbs_home"
        init_vcbs_home(custom_home)
        # Modify personal profile in custom home using valid schema values
        profile_data = load_profile(custom_home)
        profile_data["preferences"]["testing_approach"] = "comprehensive"
        save_profile(profile_data, home_path=custom_home)

        rec_file = self.base / "custom-rec.json"
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
                    "reason": "Testing guidance with custom profile.",
                    "scope": "project",
                    "component_ids": ["project-test-guidance"],
                }
            ],
            "not_recommended": [],
            "conflicts": [],
            "install_nothing": False,
        }
        rec_file.write_text(json.dumps(rec_data, indent=2), encoding="utf-8")

        plan_file = self.base / "custom-plan.json"
        plan_res = self.cli(
            "setup", "plan",
            "--project-root", self.project,
            "--host", "none",
            "--recommendation", rec_file,
            "--vcbs-home", custom_home,
            "--output", plan_file,
        )
        self.assertEqual(0, plan_res.returncode, plan_res.stderr)

        # Apply using --vcbs-home custom_home (without VCBS_HOME env set)
        apply_res = self.cli(
            "setup", "apply",
            "--project-root", self.project,
            "--plan", plan_file,
            "--confirm",
            "--vcbs-home", custom_home,
        )
        self.assertEqual(0, apply_res.returncode, apply_res.stderr)
        applied = json.loads(apply_res.stdout)
        self.assertEqual("applied", applied["status"])

    def test_apply_aborts_when_custom_profile_mutated_after_planning(self):
        """Test that apply aborts with STALE_BOOTSTRAP_PROFILE when profile changes between plan and apply."""
        custom_home = self.base / "custom_vcbs_home_stale"
        init_vcbs_home(custom_home)

        rec_file = self.base / "stale-rec.json"
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
                    "reason": "Testing guidance.",
                    "scope": "project",
                    "component_ids": ["project-test-guidance"],
                }
            ],
            "not_recommended": [],
            "conflicts": [],
            "install_nothing": False,
        }
        rec_file.write_text(json.dumps(rec_data, indent=2), encoding="utf-8")

        plan_file = self.base / "stale-plan.json"
        plan_res = self.cli(
            "setup", "plan",
            "--project-root", self.project,
            "--host", "none",
            "--recommendation", rec_file,
            "--vcbs-home", custom_home,
            "--output", plan_file,
        )
        self.assertEqual(0, plan_res.returncode, plan_res.stderr)

        # Mutate profile in custom home after planning
        profile_data = load_profile(custom_home)
        profile_data["preferences"]["interaction_style"] = "detailed"
        save_profile(profile_data, home_path=custom_home)

        # Apply must fail with STALE_BOOTSTRAP_PROFILE
        apply_res = self.cli(
            "setup", "apply",
            "--project-root", self.project,
            "--plan", plan_file,
            "--confirm",
            "--vcbs-home", custom_home,
        )
        self.assertNotEqual(0, apply_res.returncode)
        self.assertIn("STALE_BOOTSTRAP_PROFILE", apply_res.stderr)

    def test_render_summary_scope_matches_plan_actual_applied_scope(self):
        """Test that render_summary outputs the actual applied scope matching plan changes."""
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
                    "reason": "Testing guidance.",
                    "scope": "project",
                    "component_ids": ["project-test-guidance"],
                }
            ],
            "not_recommended": [],
            "conflicts": [],
            "install_nothing": False,
        }
        # Build plan with global_root passed, but project-only recommendation
        global_root = self.base / "global_root"
        global_root.mkdir()
        plan = build_plan(
            self.project,
            host="none",
            recommendation=rec_data,
            global_root=global_root,
            vcbs_home=self.home,
        )
        summary = render_summary(plan)
        # Even with global_root passed, actual applied scope is 'project' because only project changes exist
        self.assertIn("Scope: project", summary)
        self.assertNotIn("Scope: both", summary)

        # Build plan with global recommendation
        global_rec = {
            "format_version": 1,
            "target": {
                "commit_sha": self.head_sha,
                "dirty": False,
                "path": str(self.project),
            },
            "host": "none",
            "recommendations": [
                {
                    "capability_id": "engineering-baseline",
                    "reason": "Global baseline git config.",
                    "scope": "global",
                    "component_ids": ["reviewable-changes"],
                }
            ],
            "not_recommended": [],
            "conflicts": [],
            "install_nothing": False,
        }
        global_plan = build_plan(
            self.project,
            host="none",
            recommendation=global_rec,
            global_root=global_root,
            vcbs_home=self.home,
        )
        global_summary = render_summary(global_plan)
        self.assertIn("Scope: global", global_summary)

    def test_default_vcbs_home_via_env_honored_when_cli_flag_omitted(self):
        """Test that VCBS_HOME env is honored without passing --vcbs-home flag to CLI commands."""
        import hashlib
        home_env = self.base / "home_via_env"
        init_vcbs_home(home_env)
        custom_profile = {
            "id": "env-user-profile",
            "name": "Env User Profile",
            "description": "Customized personal profile loaded via VCBS_HOME.",
            "preferences": {
                "testing_approach": "comprehensive",
                "additional": {
                    "custom_flag": "enabled_by_env_profile",
                },
            },
            "default_capability_ids": [],
            "excluded_capability_ids": [],
            "reusable_experience_ids": [],
            "created_at": "2026-10-26T00:00:00Z",
            "updated_at": "2026-10-26T00:00:00Z",
        }
        save_profile(custom_profile, home_env)
        expected_profile_sha = hashlib.sha256((home_env / "profile.yaml").read_bytes()).hexdigest()

        user_cap_file = home_env / "capabilities" / "user-env-tool.yaml"
        user_cap_file.parent.mkdir(parents=True, exist_ok=True)
        user_cap_file.write_text(
            """id: user-env-tool
version: "1"
name: User Env Tool
kind: documentation
purpose: Custom tool provided via default VCBS Home.
selection_hints:
  signals: []
  constraints: []
components: []
""",
            encoding="utf-8",
        )

        old_vcbs_home = os.environ.get("VCBS_HOME")
        os.environ["VCBS_HOME"] = str(home_env)
        try:
            # 1. setup inspect without --vcbs-home
            inspect_file = self.base / "env-inspect-facts.json"
            inspect_res = self.cli(
                "setup", "inspect",
                "--project-root", self.project,
                "--host", "none",
                "--recommendation-input", inspect_file,
                "--json",
            )
            self.assertEqual(0, inspect_res.returncode, inspect_res.stderr)
            facts = json.loads(inspect_file.read_text(encoding="utf-8"))
            available_ids = [c["capability_id"] for c in facts["available_capabilities"]]
            self.assertIn("user-env-tool", available_ids)
            self.assertEqual("comprehensive", facts["personal_preferences"].get("testing_approach"))
            self.assertEqual("enabled_by_env_profile", facts["personal_preferences"].get("additional", {}).get("custom_flag"))

            # 2. Host recommends user-env-tool
            rec_file = self.base / "env-rec.json"
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
                        "capability_id": "user-env-tool",
                        "reason": "Host recommended user tool loaded from default VCBS Home.",
                        "scope": "project",
                    }
                ],
                "not_recommended": [],
                "conflicts": [],
                "install_nothing": False,
            }
            rec_file.write_text(json.dumps(rec_data, indent=2), encoding="utf-8")

            # 3. setup plan without --vcbs-home
            plan_file = self.base / "env-plan.json"
            plan_res = self.cli(
                "setup", "plan",
                "--project-root", self.project,
                "--host", "none",
                "--recommendation", rec_file,
                "--output", plan_file,
            )
            self.assertEqual(0, plan_res.returncode, plan_res.stderr)
            self.assertTrue(plan_file.is_file())
            plan = json.loads(plan_file.read_text(encoding="utf-8"))
            self.assertIn("user-env-tool", plan["capabilities"])
            self.assertEqual(expected_profile_sha, plan["profile_sha256"])
        finally:
            if old_vcbs_home is None:
                os.environ.pop("VCBS_HOME", None)
            else:
                os.environ["VCBS_HOME"] = old_vcbs_home

    def test_customized_home_profile_never_silently_replaced_by_repository_seed(self):
        """Negative test: a customized profile in VCBS Home is authoritative and never silently replaced by repository seed."""
        home_custom = self.base / "home_profile_authoritative"
        init_vcbs_home(home_custom)
        custom_profile = {
            "id": "authoritative-profile",
            "name": "Authoritative Profile",
            "description": "Explicitly excludes testing capability.",
            "preferences": {
                "testing_approach": "minimal",
                "additional": {
                    "strict_home_profile": "true",
                },
            },
            "default_capability_ids": [],
            "excluded_capability_ids": ["testing"],
            "reusable_experience_ids": [],
            "created_at": "2026-10-26T00:00:00Z",
            "updated_at": "2026-10-26T00:00:00Z",
        }
        save_profile(custom_profile, home_custom)

        # In repository seed (profiles/personal.yaml), 'testing' is NOT excluded
        repo_profile = load_profile(home_path=None, root=ROOT)
        loaded_custom = load_profile(home_path=home_custom, root=ROOT)
        self.assertIn("testing", loaded_custom["excluded_capability_ids"])

        old_vcbs_home = os.environ.get("VCBS_HOME")
        os.environ["VCBS_HOME"] = str(home_custom)
        try:
            # 1. In inspect fact package, personal_preferences comes from custom profile, not repository seed
            inspect_facts = self.base / "authoritative-facts.json"
            res = self.cli(
                "setup", "inspect",
                "--project-root", self.project,
                "--host", "none",
                "--recommendation-input", inspect_facts,
            )
            self.assertEqual(0, res.returncode, res.stderr)
            facts = json.loads(inspect_facts.read_text(encoding="utf-8"))
            self.assertEqual("minimal", facts["personal_preferences"].get("testing_approach"))
            self.assertEqual("true", facts["personal_preferences"].get("additional", {}).get("strict_home_profile"))

            # 2. Recommending 'testing' must be rejected because it is excluded by the authoritative home profile
            rec_file = self.base / "exclude-rec.json"
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
                        "reason": "Host wants testing, but user profile excluded it.",
                        "scope": "project",
                    }
                ],
                "not_recommended": [],
                "conflicts": [],
                "install_nothing": False,
            }
            rec_file.write_text(json.dumps(rec_data, indent=2), encoding="utf-8")

            plan_file = self.base / "authoritative-plan.json"
            plan_res = self.cli(
                "setup", "plan",
                "--project-root", self.project,
                "--host", "none",
                "--recommendation", rec_file,
                "--output", plan_file,
            )
            # Must fail with CAPABILITY_EXCLUDED because home profile excluded 'testing'
            self.assertNotEqual(0, plan_res.returncode)
            self.assertIn("CAPABILITY_EXCLUDED", plan_res.stderr)
        finally:
            if old_vcbs_home is None:
                os.environ.pop("VCBS_HOME", None)
            else:
                os.environ["VCBS_HOME"] = old_vcbs_home


if __name__ == "__main__":
    unittest.main()
