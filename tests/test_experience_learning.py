"""Tests for P0-A (Harvest) and P0-B (Personal Reusable Experience)."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

from bootstrap.experience import ExperienceError, harvest_experience, review_experience, verify_evidence
from composer.core import ROOT, compose, load_yaml, validate


class ExperienceLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        # Create a mock VCBS root
        self.vcbs_root = self.base / "vcbs"
        for name in ["schemas", "capabilities", "composer", "profiles", "experiences"]:
            shutil.copytree(ROOT / name, self.vcbs_root / name)
        # Create a disposable target repository (Project A)
        self.project_a = self.base / "project_a"
        self.project_a.mkdir()
        self._git(self.project_a, "init", "-q")
        self._git(self.project_a, "config", "user.email", "dev@example.invalid")
        self._git(self.project_a, "config", "user.name", "Developer")
        (self.project_a / "calculator.py").write_text("def add(a, b): return a + b\n")
        (self.project_a / "test_calculator.py").write_text("def test_add(): assert add(1, 2) == 3\n")
        self._git(self.project_a, "add", ".")
        self._git(self.project_a, "commit", "-qm", "initial commit")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _git(self, project, *args):
        return subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True, text=True).stdout.strip()

    def _valid_observation(self):
        return {
            "title": "Use targeted pytest fixtures for state isolation",
            "approach": "Replaced monkeypatch fixtures with scoped pytest-mock helpers.",
            "capability_ids": ["testing"],
            "outcome": {
                "summary": "State isolation issues resolved in calculator tests.",
                "result": "success",
                "measures": [{"name": "tests_passed", "value": True}],
            },
            "observation": "Global state leaks caused intermittent test failures.",
            "solution": "Define scoped fixtures and verify teardown behavior.",
            "lesson": "Targeted fixtures prevent test order dependency in unit suites.",
            "reasoning": {
                "interpretation": "Scoped isolation fixed test flake without changing business logic.",
                "limits": ["Observed in Python pytest test suites only."],
            },
            "scope": ["testing", "python", "fixtures"],
            "confidence": "high",
            "evidence": [
                {
                    "type": "project_result",
                    "source": "test_calculator.py",
                    "description": "Deterministic test pass across 5 repeated runs.",
                    "strength": "moderate",
                }
            ],
            "project_context": {
                "project_type": "software-project",
                "goal": "Ensure reliable test execution.",
                "technologies": ["Python", "pytest"],
                "constraints": ["solo-dev"],
            },
        }

    def test_p0_a_harvest_creates_candidate_with_provenance_and_unverified_evidence(self):
        obs = self._valid_observation()
        candidate = harvest_experience(self.project_a, obs, host="codex", root=self.vcbs_root)
        self.assertEqual("candidate", candidate["status"])
        self.assertEqual("pending", candidate["review"]["review_status"])
        self.assertEqual("unverified", candidate["evidence"][0]["verification"]["status"])
        head_sha = self._git(self.project_a, "rev-parse", "HEAD")
        self.assertEqual(f"commit:{head_sha}", candidate["source"]["record_uri"])
        self.assertEqual("project_a", candidate["source"]["project"])
        self.assertTrue((self.vcbs_root / "experiences/candidate" / f"{candidate['id']}.yaml").is_file())
        validate(candidate, "experience.schema.json", self.vcbs_root)

    def test_p0_a_harvest_rejects_credentials_and_secrets(self):
        obs = self._valid_observation()
        obs["observation"] = "Found key sk-123456789012345678901234 in configuration"
        with self.assertRaises(ExperienceError) as ctx:
            harvest_experience(self.project_a, obs, root=self.vcbs_root)
        self.assertIn("HARVEST_PRIVACY_VIOLATION", str(ctx.exception))

    def test_p0_a_harvest_rejects_private_ssh_keys(self):
        obs = self._valid_observation()
        obs["approach"] = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0...\n-----END RSA PRIVATE KEY-----"
        with self.assertRaises(ExperienceError) as ctx:
            harvest_experience(self.project_a, obs, root=self.vcbs_root)
        self.assertIn("HARVEST_PRIVACY_VIOLATION", str(ctx.exception))

    def test_p0_a_harvest_rejects_host_escaping_absolute_paths(self):
        obs = self._valid_observation()
        obs["solution"] = "Load file from /Users/alice/projects/secret.conf"
        with self.assertRaises(ExperienceError) as ctx:
            harvest_experience(self.project_a, obs, root=self.vcbs_root)
        self.assertIn("HARVEST_PRIVACY_VIOLATION", str(ctx.exception))

    def test_p0_a_harvest_rejects_bulk_content_dump(self):
        obs = self._valid_observation()
        obs["observation"] = "A" * 6000
        with self.assertRaises(ExperienceError) as ctx:
            harvest_experience(self.project_a, obs, root=self.vcbs_root)
        self.assertIn("HARVEST_PRIVACY_VIOLATION", str(ctx.exception))

    def test_p0_a_harvest_rejects_chat_transcripts(self):
        obs = self._valid_observation()
        obs["transcript"] = [{"role": "user", "text": "hello"}]
        with self.assertRaises(ExperienceError) as ctx:
            harvest_experience(self.project_a, obs, root=self.vcbs_root)
        self.assertIn("HARVEST_PRIVACY_VIOLATION", str(ctx.exception))

    def test_p0_b_human_approval_promotes_to_verified_personal_experience(self):
        obs = self._valid_observation()
        candidate = harvest_experience(self.project_a, obs, root=self.vcbs_root)
        cid = candidate["id"]
        candidate_file = self.vcbs_root / "experiences/candidate" / f"{cid}.yaml"
        self.assertTrue(candidate_file.is_file())

        # 1. Attempting to approve unverified candidate directly must fail
        with self.assertRaises(ExperienceError) as ctx:
            review_experience(candidate_file, "approve", reviewer="Reviewer Bob",
                              notes="Premature approval", root=self.vcbs_root)
        self.assertIn("UNVERIFIED_EVIDENCE", str(ctx.exception))

        # 2. Fact-checking: verify evidence independently
        verified_ev = verify_evidence(candidate_file, evidence_index=0, verifier="QA Alice",
                                      method="Automated test suite passed 5 repeated runs",
                                      status="checked", root=self.vcbs_root)
        self.assertEqual("checked", verified_ev["evidence"][0]["verification"]["status"])
        self.assertEqual("QA Alice", verified_ev["evidence"][0]["verification"]["verified_by"])

        # 3. Human reviewer approves verified candidate
        verified = review_experience(candidate_file, "approve", reviewer="Reviewer Bob",
                                     notes="Verified test run evidence", root=self.vcbs_root)
        self.assertEqual("verified", verified["status"])
        self.assertEqual("accepted", verified["review"]["review_status"])
        self.assertEqual("Reviewer Bob", verified["review"]["reviewer"])
        self.assertEqual("checked", verified["evidence"][0]["verification"]["status"])
        self.assertEqual("QA Alice", verified["evidence"][0]["verification"]["verified_by"])
        self.assertFalse(candidate_file.exists())
        self.assertTrue((self.vcbs_root / "experiences/verified" / f"{cid}.yaml").is_file())

        # Verify added to personal profile
        profile = load_yaml(self.vcbs_root / "profiles/personal.yaml")
        self.assertIn(cid, profile.get("reusable_experience_ids", []))

    def test_p0_b_unapproved_candidate_cannot_be_reused_by_composer(self):
        obs = self._valid_observation()
        candidate = harvest_experience(self.project_a, obs, root=self.vcbs_root)
        cid = candidate["id"]

        # Consumer B context (same project type)
        context_b = {
            "name": "consumer_b", "description": "Second project",
            "project_type": "software-project", "lifecycle": "production",
            "technology_tags": ["Python"], "runtime": "Python", "platform": "local",
            "goal": "Build robust features", "constraints": [], "collaboration": "solo",
        }
        rec = compose(context_b, self.vcbs_root)
        testing_rec = next(r for r in rec["recommendations"] if r["capability_id"] == "testing")
        reused_ids = [e["record"]["id"] for e in testing_rec["experiences"]]
        self.assertNotIn(cid, reused_ids)

    def test_p0_b_rejected_experience_cannot_be_reused(self):
        obs = self._valid_observation()
        candidate = harvest_experience(self.project_a, obs, root=self.vcbs_root)
        cid = candidate["id"]
        review_experience(cid, "reject", reviewer="Reviewer Bob", notes="Too context specific", root=self.vcbs_root)
        self.assertTrue((self.vcbs_root / "experiences/rejected" / f"{cid}.yaml").is_file())

        context_b = {
            "name": "consumer_b", "description": "Second project",
            "project_type": "software-project", "lifecycle": "production",
            "technology_tags": ["Python"], "runtime": "Python", "platform": "local",
            "goal": "Build robust features", "constraints": [], "collaboration": "solo",
        }
        rec = compose(context_b, self.vcbs_root)
        testing_rec = next(r for r in rec["recommendations"] if r["capability_id"] == "testing")
        self.assertNotIn(cid, [e["record"]["id"] for e in testing_rec["experiences"]])

    def test_p0_b_approved_experience_is_automatically_reused_without_catalog_edit(self):
        # Initial catalog testing capability does NOT have our new experience
        catalog_testing = load_yaml(self.vcbs_root / "capabilities/quality/testing.yaml")
        initial_experience_ids = list(catalog_testing.get("experience_ids", []))

        # Harvest from Project A, verify evidence, and approve
        obs = self._valid_observation()
        candidate = harvest_experience(self.project_a, obs, root=self.vcbs_root)
        cid = candidate["id"]
        verify_evidence(cid, verifier="QA Lead", method="pytest passed 5x", root=self.vcbs_root)
        review_experience(cid, "approve", reviewer="Reviewer Bob", root=self.vcbs_root)

        # Catalog testing capability was NOT touched
        catalog_testing_after = load_yaml(self.vcbs_root / "capabilities/quality/testing.yaml")
        self.assertEqual(initial_experience_ids, catalog_testing_after.get("experience_ids", []))

        # Consumer B with matching project_type (software-project) automatically sees it!
        context_b = {
            "name": "consumer_b", "description": "Second project",
            "project_type": "software-project", "lifecycle": "production",
            "technology_tags": ["Python"], "runtime": "Python", "platform": "local",
            "goal": "Build robust features", "constraints": [], "collaboration": "solo",
        }
        rec = compose(context_b, self.vcbs_root)
        testing_rec = next(r for r in rec["recommendations"] if r["capability_id"] == "testing")
        reused_ids = [e["record"]["id"] for e in testing_rec["experiences"]]
        self.assertIn(cid, reused_ids)

        # Consumer C with different project_type does NOT match
        context_c = dict(context_b, project_type="research", lifecycle="short_term")
        rec_c = compose(context_c, self.vcbs_root)
        research_rec = next(r for r in rec_c["recommendations"] if r["capability_id"] == "research-workflow")
        self.assertNotIn(cid, [e["record"]["id"] for e in research_rec["experiences"]])

    def test_verify_evidence_parameters_and_failures(self):
        obs = self._valid_observation()
        candidate = harvest_experience(self.project_a, obs, root=self.vcbs_root)
        cid = candidate["id"]

        # Missing verifier
        with self.assertRaises(ExperienceError) as ctx:
            verify_evidence(cid, verifier="", method="Method", root=self.vcbs_root)
        self.assertIn("VERIFIER_REQUIRED", str(ctx.exception))

        # Missing method
        with self.assertRaises(ExperienceError) as ctx:
            verify_evidence(cid, verifier="Tester", method="", root=self.vcbs_root)
        self.assertIn("METHOD_REQUIRED", str(ctx.exception))

        # Invalid status
        with self.assertRaises(ExperienceError) as ctx:
            verify_evidence(cid, verifier="Tester", method="Method", status="invalid_status", root=self.vcbs_root)
        self.assertIn("INVALID_VERIFICATION_STATUS", str(ctx.exception))

        # Invalid index
        with self.assertRaises(ExperienceError) as ctx:
            verify_evidence(cid, evidence_index=99, verifier="Tester", method="Method", root=self.vcbs_root)
        self.assertIn("INVALID_EVIDENCE_INDEX", str(ctx.exception))

    def test_unverified_evidence_cannot_enter_personal_reusable(self):
        """Negative test: composer rejects reusable experiences if any evidence is unverified."""
        obs = self._valid_observation()
        candidate = harvest_experience(self.project_a, obs, root=self.vcbs_root)
        cid = candidate["id"]

        # Manually move to verified with unverified evidence
        cand_path = self.vcbs_root / "experiences/candidate" / f"{cid}.yaml"
        verified_path = self.vcbs_root / "experiences/verified" / f"{cid}.yaml"
        cand_data = load_yaml(cand_path)
        cand_data["status"] = "verified"
        cand_data["review"] = {"review_status": "accepted", "reviewer": "Test", "reviewed_at": "2026-10-19T00:00:00Z"}
        verified_path.write_text(yaml.safe_dump(cand_data), encoding="utf-8")
        cand_path.unlink()

        profile_path = self.vcbs_root / "profiles/personal.yaml"
        profile = load_yaml(profile_path)
        profile["reusable_experience_ids"] = [cid]
        profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")

        context_b = {
            "name": "consumer_b", "description": "Second project",
            "project_type": "software-project", "lifecycle": "production",
            "technology_tags": ["Python"], "runtime": "Python", "platform": "local",
            "goal": "Build robust features", "constraints": [], "collaboration": "solo",
        }
        with self.assertRaises(Exception) as ctx:
            compose(context_b, self.vcbs_root)
        self.assertTrue(
            "contains unverified evidence" in str(ctx.exception)
            or "does not contain items matching the given schema" in str(ctx.exception)
        )


if __name__ == "__main__":
    unittest.main()
