"""End-to-End Acceptance Tests for Issue #2 (P0-A, P0-B, P0-C).

Covers all 5 acceptance tests required by the issue specification:
- Test A: Project A produces experience -> VCBS harvests candidate -> Human approves ->
          Saved as personal reusable experience -> Project B automatically reuses it.
- Test B: Unapproved/rejected/mismatched experience cannot be reused.
- Test C: VCBS-owned v1 -> Discover v2 -> Display upgrade plan -> User approves ->
          Safe upgrade -> Rollback restores v1.
- Test D: User modifies installed file -> Conflict detected -> Not overwritten.
- Test E: Execute again after upgrade -> Idempotent no-op (NO_NEW_CHANGES).
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

from activation import core as activation
from bootstrap.core import apply_plan, build_plan, decide, inspect_project, render_summary
from bootstrap.experience import ExperienceError, harvest_experience, review_experience, verify_evidence
from bootstrap.recovery import rollback
from composer.core import ROOT, compose, load_yaml


class EndToEndIssue2AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        # Isolated VCBS environment
        self.vcbs_root = self.base / "vcbs"
        for name in ["schemas", "capabilities", "composer", "profiles", "experiences", "components", "host_adapters", "assembly", "examples"]:
            shutil.copytree(ROOT / name, self.vcbs_root / name)

        # Disposable Project A
        self.project_a = self.base / "project_a"
        self.project_a.mkdir()
        self._git(self.project_a, "init", "-q")
        self._git(self.project_a, "config", "user.email", "dev_a@example.invalid")
        self._git(self.project_a, "config", "user.name", "Dev A")
        (self.project_a / "README.md").write_text("# Project A\n")
        (self.project_a / "calculator.py").write_text("def multiply(a, b): return a * b\n")
        (self.project_a / "test_calculator.py").write_text("def test_multiply(): assert multiply(2, 3) == 6\n")
        self._git(self.project_a, "add", ".")
        self._git(self.project_a, "commit", "-qm", "Initial Project A commit")

        # Disposable Project B
        self.project_b = self.base / "project_b"
        self.project_b.mkdir()
        self._git(self.project_b, "init", "-q")
        self._git(self.project_b, "config", "user.email", "dev_b@example.invalid")
        self._git(self.project_b, "config", "user.name", "Dev B")
        (self.project_b / "README.md").write_text("# Project B\n")
        (self.project_b / "app.py").write_text("def run(): pass\n")
        self._git(self.project_b, "add", ".")
        self._git(self.project_b, "commit", "-qm", "Initial Project B commit")

        self.global_root = self.base / "global"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _git(self, project, *args):
        return subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True, text=True).stdout.strip()

    def _run_cli(self, *args, check=True):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        result = subprocess.run([sys.executable, "-m", "bootstrap", *map(str, args)],
                                cwd=self.vcbs_root, env=env, capture_output=True, text=True)
        if check and result.returncode != 0:
            self.fail(f"CLI command failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}")
        return result

    def test_a_project_a_experience_harvest_human_approve_project_b_auto_reuse(self):
        """Test A: Project A experience -> harvest candidate -> human approve -> personal reuse in Project B."""
        # 1. Project A produces structured observation
        obs = {
            "title": "Use deterministic seed fixtures in property tests",
            "approach": "Bound random seed fixture to ensure reproducible failures.",
            "capability_ids": ["testing"],
            "outcome": {
                "summary": "Property tests reproduced flake deterministically.",
                "result": "success",
                "measures": [{"name": "reproducibility", "value": "100%"}],
            },
            "observation": "Unseeded property tests caused random CI failures.",
            "solution": "Pin seed fixture and report seed on test failure.",
            "lesson": "Deterministic seeds isolate property test failures.",
            "reasoning": {
                "interpretation": "Seeded fixtures maintain reproducibility across runs.",
                "limits": ["Applies to property-based unit testing."],
            },
            "scope": ["testing", "property-testing"],
            "confidence": "high",
            "evidence": [
                {
                    "type": "project_result",
                    "source": "test_calculator.py",
                    "description": "50 consecutive test passes without seed drift.",
                    "strength": "moderate",
                }
            ],
            "project_context": {
                "project_type": "software-project",
                "goal": "Stable CI testing",
                "technologies": ["Python", "pytest"],
                "constraints": [],
            },
        }

        # 2. VCBS extracts candidate
        candidate = harvest_experience(self.project_a, obs, root=self.vcbs_root)
        exp_id = candidate["id"]
        self.assertEqual("candidate", candidate["status"])
        self.assertEqual("pending", candidate["review"]["review_status"])
        self.assertEqual("unverified", candidate["evidence"][0]["verification"]["status"])

        # 3. Negative check: approving candidate with unverified evidence fails
        with self.assertRaises(ExperienceError) as ctx:
            review_experience(exp_id, "approve", reviewer="Senior Maintainer", root=self.vcbs_root)
        self.assertIn("UNVERIFIED_EVIDENCE", str(ctx.exception))

        # 4. Independent fact-checking: verify candidate evidence
        verified_ev = verify_evidence(exp_id, verifier="QA Lead", method="pytest suite passed on Project A commit",
                                      status="checked", root=self.vcbs_root)
        self.assertEqual("checked", verified_ev["evidence"][0]["verification"]["status"])

        # 5. Human reviewer approves candidate
        verified = review_experience(exp_id, "approve", reviewer="Senior Maintainer",
                                     notes="Verified test evidence in Project A", root=self.vcbs_root)
        self.assertEqual("verified", verified["status"])
        self.assertEqual("accepted", verified["review"]["review_status"])
        self.assertEqual("checked", verified["evidence"][0]["verification"]["status"])

        # 6. Personal profile contains the reusable experience
        profile = load_yaml(self.vcbs_root / "profiles/personal.yaml")
        self.assertIn(exp_id, profile.get("reusable_experience_ids", []))

        # 7. Generic capability catalog was NOT modified
        catalog_testing = load_yaml(self.vcbs_root / "capabilities/quality/testing.yaml")
        self.assertNotIn(exp_id, catalog_testing.get("experience_ids", []))

        # 8. Project B automatically reuses the experience
        context_b = {
            "name": "project_b", "description": "Second consumer project",
            "project_type": "software-project", "lifecycle": "production",
            "technology_tags": ["Python"], "runtime": "Python", "platform": "local",
            "goal": "Build dependable service", "constraints": [], "collaboration": "solo",
        }
        rec_b = compose(context_b, root=self.vcbs_root)
        testing_rec = next(r for r in rec_b["recommendations"] if r["capability_id"] == "testing")
        reused_ids = [e["record"]["id"] for e in testing_rec["experiences"]]
        self.assertIn(exp_id, reused_ids)

    def test_cli_harvest_and_review_experience(self):
        """Verify CLI commands for harvest, verify-evidence, and review-experience work seamlessly."""
        obs = {
            "title": "Use isolated temp directories for filesystem tests",
            "approach": "Used tempfile.TemporaryDirectory per test.",
            "capability_ids": ["testing"],
            "outcome": {"summary": "No test side effects on disk.", "result": "success"},
            "observation": "Tests wrote to project root directly.",
            "solution": "Wrap filesystem tests in temporary directories.",
            "lesson": "Isolated test roots prevent test cross-contamination.",
            "reasoning": {"interpretation": "Temporary roots isolate artifacts.", "limits": []},
            "scope": ["testing", "filesystem"], "confidence": "high",
            "evidence": [{"type": "project_result", "source": "test_calculator.py", "description": "Passes cleanly", "strength": "moderate"}],
            "project_context": {"project_type": "software-project", "goal": "Clean test runs"},
        }
        obs_file = self.base / "obs.json"
        obs_file.write_text(json.dumps(obs), encoding="utf-8")

        # CLI: harvest
        res_h = self._run_cli("harvest", "--project-root", self.project_a, "--observation", obs_file)
        self.assertIn("Candidate:", res_h.stdout)
        exp_id = res_h.stdout.split("Candidate:")[1].split("(")[0].strip()

        # CLI: negative check - review-experience fails on unverified evidence
        res_fail = self._run_cli("review-experience", exp_id, "--decision", "approve", "--reviewer", "Lead Maintainer", check=False)
        self.assertNotEqual(0, res_fail.returncode)
        self.assertIn("UNVERIFIED_EVIDENCE", res_fail.stderr)

        # CLI: verify-evidence
        res_v = self._run_cli("verify-evidence", exp_id, "--verifier", "QA Lead", "--method", "Automated test suite pass")
        self.assertIn("Verified evidence", res_v.stdout)

        # CLI: review-experience approve
        res_r = self._run_cli("review-experience", exp_id, "--decision", "approve", "--reviewer", "Lead Maintainer")
        self.assertIn("verified (review: accepted)", res_r.stdout)

        # Profile updated
        profile = load_yaml(self.vcbs_root / "profiles/personal.yaml")
        self.assertIn(exp_id, profile.get("reusable_experience_ids", []))

    def test_b_unapproved_or_rejected_experience_cannot_be_reused(self):
        """Test B: Unapproved, rejected, or wrong-context experience cannot be reused."""
        obs = {
            "title": "Hypothetical unverified test pattern",
            "approach": "Unverified approach",
            "capability_ids": ["testing"],
            "outcome": {"summary": "Did not finish validation", "result": "inconclusive"},
            "observation": "Observation", "solution": "Solution", "lesson": "Lesson",
            "reasoning": {"interpretation": "Unknown", "limits": []},
            "scope": ["testing"], "confidence": "low",
            "evidence": [{"type": "other", "source": "local", "description": "Preliminary", "strength": "moderate"}],
            "project_context": {"project_type": "software-project", "goal": "Explore"},
        }
        candidate = harvest_experience(self.project_a, obs, root=self.vcbs_root)
        cid = candidate["id"]

        context_b = {
            "name": "project_b", "description": "Consumer project",
            "project_type": "software-project", "lifecycle": "production",
            "technology_tags": ["Python"], "runtime": "Python", "platform": "local",
            "goal": "Build service", "constraints": [], "collaboration": "solo",
        }

        # Case B1: Candidate state -> Composer does not reuse
        rec1 = compose(context_b, root=self.vcbs_root)
        testing_rec1 = next(r for r in rec1["recommendations"] if r["capability_id"] == "testing")
        self.assertNotIn(cid, [e["record"]["id"] for e in testing_rec1["experiences"]])

        # Case B2: Rejected state -> Composer does not reuse
        review_experience(cid, "reject", reviewer="Lead", notes="Insufficient proof", root=self.vcbs_root)
        rec2 = compose(context_b, root=self.vcbs_root)
        testing_rec2 = next(r for r in rec2["recommendations"] if r["capability_id"] == "testing")
        self.assertNotIn(cid, [e["record"]["id"] for e in testing_rec2["experiences"]])

        # Case B3: If candidate ID is forced into profile manually without verification -> compose raises error
        profile_path = self.vcbs_root / "profiles/personal.yaml"
        profile = load_yaml(profile_path)
        profile["reusable_experience_ids"] = [cid]
        profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            compose(context_b, root=self.vcbs_root)
        self.assertIn("must resolve to a verified Experience", str(ctx.exception))

    def test_c_managed_v1_to_v2_upgrade_and_rollback(self):
        """Test C: VCBS-owned v1 -> Discover v2 -> Display upgrade plan -> User approves -> Safe upgrade -> Rollback restores v1."""
        context = {
            "name": "project_c", "description": "Upgrade project",
            "project_type": "software-project", "lifecycle": "production",
            "technology_tags": ["Python"], "runtime": "Python", "platform": "local",
            "goal": "Maintain system", "constraints": ["AI coding host is Codex"],
            "collaboration": "solo",
        }
        ctx_file = self.base / "ctx_c.yaml"
        ctx_file.write_text(yaml.safe_dump(context), encoding="utf-8")

        # 1. Install v1
        plan_v1 = build_plan(self.project_a, host="codex", context_path=ctx_file, global_root=self.global_root, root=self.vcbs_root)
        dec_v1 = decide(plan_v1, "approve")
        applied_v1 = apply_plan(plan_v1, dec_v1, self.project_a, global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual("applied", applied_v1["status"])
        self.assertTrue(applied_v1["rollback_available"])

        skill_file = self.project_a / activation.SKILL_DESTINATION
        guide_file = self.project_a / ".vibe-coding-bootstrap/components/test-guidance.md"
        v1_skill_bytes = skill_file.read_bytes()
        v1_guide_bytes = guide_file.read_bytes()

        # 2. Update VCBS source to v2
        (self.vcbs_root / activation.SKILL_SOURCE).write_text("# Skill v2\nUpdated instructions.\n", encoding="utf-8")
        (self.vcbs_root / "components/project/test-guidance.md").write_text("# Test Guidance v2\nNew guidance.\n", encoding="utf-8")

        # 3. Inspect target
        inspect_result = inspect_project(self.project_a, host="codex", root=self.vcbs_root)
        self.assertEqual("compose", inspect_result["route"])

        # 4. Plan detects upgrade and displays delta
        plan_v2 = build_plan(self.project_a, host="codex", context_path=ctx_file, global_root=self.global_root, root=self.vcbs_root)
        summary = render_summary(plan_v2)
        self.assertIn("Upgrades:", summary)
        self.assertGreater(plan_v2["actual_changes"], 0)
        self.assertTrue(plan_v2["requires_confirmation"])

        # 5. User approves upgrade
        dec_v2 = decide(plan_v2, "approve")
        applied_v2 = apply_plan(plan_v2, dec_v2, self.project_a, global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual("applied", applied_v2["status"])
        self.assertEqual("# Skill v2\nUpdated instructions.\n", skill_file.read_text(encoding="utf-8"))
        self.assertEqual("# Test Guidance v2\nNew guidance.\n", guide_file.read_text(encoding="utf-8"))

        # 6. Rollback restores v1 bytes
        dep_v2 = applied_v2["deployment_id"]
        rb = rollback(self.project_a, dep_v2, apply=True)
        self.assertEqual("rolled_back", rb["status"])
        self.assertEqual(v1_skill_bytes, skill_file.read_bytes())
        self.assertEqual(v1_guide_bytes, guide_file.read_bytes())

    def test_d_user_modified_installed_file_detects_conflict(self):
        """Test D: User modified installed file -> Detects conflict -> Not overwritten."""
        context = {
            "name": "project_d", "description": "Conflict project",
            "project_type": "software-project", "lifecycle": "production",
            "technology_tags": ["Python"], "runtime": "Python", "platform": "local",
            "goal": "Test conflicts", "constraints": ["AI coding host is Codex"],
            "collaboration": "solo",
        }
        ctx_file = self.base / "ctx_d.yaml"
        ctx_file.write_text(yaml.safe_dump(context), encoding="utf-8")

        # 1. Install v1
        plan_v1 = build_plan(self.project_a, host="codex", context_path=ctx_file, global_root=self.global_root, root=self.vcbs_root)
        dec_v1 = decide(plan_v1, "approve")
        apply_plan(plan_v1, dec_v1, self.project_a, global_root=self.global_root, root=self.vcbs_root)

        # 2. User modifies installed file
        guide_file = self.project_a / ".vibe-coding-bootstrap/components/test-guidance.md"
        custom_content = "# User custom guidance - DO NOT TOUCH\n"
        guide_file.write_text(custom_content, encoding="utf-8")

        # 3. VCBS source changes to v2
        (self.vcbs_root / "components/project/test-guidance.md").write_text("# Test Guidance v2\n", encoding="utf-8")

        # 4. Plan detects conflict on modified file
        plan_v2 = build_plan(self.project_a, host="codex", context_path=ctx_file, global_root=self.global_root, root=self.vcbs_root)
        conflicts = plan_v2["conflicts"]
        self.assertTrue(any("test-guidance" in c["component_id"] and c["result"] == "conflict" for c in conflicts))
        self.assertFalse(plan_v2["executable"])

        # 5. User file was NOT overwritten
        self.assertEqual(custom_content, guide_file.read_text(encoding="utf-8"))

    def test_e_repeat_run_after_upgrade_is_no_new_changes(self):
        """Test E: Repeat run after upgrade -> NO_NEW_CHANGES (idempotent no-op)."""
        context = {
            "name": "project_e", "description": "Idempotent project",
            "project_type": "software-project", "lifecycle": "production",
            "technology_tags": ["Python"], "runtime": "Python", "platform": "local",
            "goal": "Maintain idempotent updates", "constraints": ["AI coding host is Codex"],
            "collaboration": "solo",
        }
        ctx_file = self.base / "ctx_e.yaml"
        ctx_file.write_text(yaml.safe_dump(context), encoding="utf-8")

        # 1. Install v1
        plan_v1 = build_plan(self.project_a, host="codex", context_path=ctx_file, global_root=self.global_root, root=self.vcbs_root)
        apply_plan(plan_v1, decide(plan_v1, "approve"), self.project_a, global_root=self.global_root, root=self.vcbs_root)

        # 2. Upgrade to v2
        (self.vcbs_root / activation.SKILL_SOURCE).write_text("# Skill v2\n", encoding="utf-8")
        (self.vcbs_root / "components/project/test-guidance.md").write_text("# Guidance v2\n", encoding="utf-8")
        plan_v2 = build_plan(self.project_a, host="codex", context_path=ctx_file, global_root=self.global_root, root=self.vcbs_root)
        apply_plan(plan_v2, decide(plan_v2, "approve"), self.project_a, global_root=self.global_root, root=self.vcbs_root)

        # 3. Repeat plan after upgrade
        plan_repeat = build_plan(self.project_a, host="codex", context_path=ctx_file, global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual(0, plan_repeat["actual_changes"])
        self.assertFalse(plan_repeat["requires_confirmation"])
        self.assertEqual("already_active", plan_repeat["host_activations"][0]["status"])

        # 4. Repeat apply returns no_changes
        result = apply_plan(plan_repeat, None, self.project_a, global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual("no_changes", result["status"])
        self.assertNotIn("deployment_id", result)


if __name__ == "__main__":
    unittest.main()
