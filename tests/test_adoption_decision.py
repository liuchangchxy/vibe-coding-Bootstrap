"""Explicit Adoption Plan decision binding tests."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from adoption.decision import build_decision, validate_decision
from adoption.planning import plan_digest
from composer.core import ROOT, validate


class AdoptionDecisionTests(unittest.TestCase):
    def setUp(self):
        plan_path = ROOT / "examples/adoption/dayspark/adoption-plan.json"
        self.plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.plan["target"]["repository"] = "synthetic/decision-fixture"
        self.plan["target"]["commit_sha"] = "a" * 40
        self.plan["target"]["remote_commit_sha"] = "a" * 40
        self.plan["plan_digest"] = plan_digest(self.plan)
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.plan_path = self.directory / "plan.json"
        self.plan_path.write_text(json.dumps(self.plan, indent=2) + "\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_approve_decision_binds_exact_plan(self):
        decision = build_decision(self.plan, "approve")
        self.assertEqual("approve", validate_decision(decision, self.plan)["decision"])
        self.assertEqual(self.plan["plan_digest"], decision["plan_digest"])
        self.assertEqual(self.plan["target"]["commit_sha"], decision["target_commit_sha"])
        self.assertEqual(0, self.plan["materializable_changes"])

    def test_reject_decision_binds_exact_plan(self):
        decision = build_decision(self.plan, "reject")
        self.assertEqual("reject", validate_decision(decision, self.plan)["decision"])

    def test_decision_digest_mismatch_rejected(self):
        decision = build_decision(self.plan, "approve")
        decision["plan_digest"] = ("0" if decision["plan_digest"][0] != "0" else "1") + decision["plan_digest"][1:]
        with self.assertRaisesRegex(ValueError, "plan_digest"):
            validate_decision(decision, self.plan)

    def test_decision_target_sha_mismatch_rejected(self):
        decision = build_decision(self.plan, "approve")
        decision["target_commit_sha"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "target_commit_sha"):
            validate_decision(decision, self.plan)

    def test_modified_plan_invalidates_existing_decision(self):
        decision = build_decision(self.plan, "approve")
        changed_plan = deepcopy(self.plan)
        changed_plan["items"][0]["reason"] += " changed"
        changed_plan["plan_digest"] = plan_digest(changed_plan)
        with self.assertRaisesRegex(ValueError, "plan_digest"):
            validate_decision(decision, changed_plan)

    def test_generated_at_change_keeps_decision_valid(self):
        decision = build_decision(self.plan, "approve")
        changed_plan = deepcopy(self.plan)
        changed_plan["generated_at"] = "2026-01-01T00:00:00Z"
        self.assertEqual(self.plan["plan_digest"], plan_digest(changed_plan))
        self.assertEqual("approve", validate_decision(decision, changed_plan)["decision"])

    def test_decision_schema(self):
        decision = build_decision(self.plan, "approve")
        validate(decision, "adoption-decision.schema.json")
        self.assertNotIn("user_id", decision)
        self.assertNotIn("signature", decision)
        with self.assertRaises(Exception):
            build_decision(self.plan, "pending")

    def test_plan_digest_mismatch_rejected_before_decision_creation(self):
        changed_plan = deepcopy(self.plan)
        changed_plan["plan_digest"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            build_decision(changed_plan, "approve")

    def test_cli_decide_validate_and_refuse_overwrite(self):
        decision_path = self.directory / "decision.json"
        command = [sys.executable, "-m", "adoption", "decide", str(self.plan_path),
                   "--decision", "approve", "--output", str(decision_path)]
        created = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(0, created.returncode, created.stderr)
        validate_command = [sys.executable, "-m", "adoption", "validate-decision",
                            str(decision_path), "--plan", str(self.plan_path)]
        checked = subprocess.run(validate_command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(0, checked.returncode, checked.stderr)
        original = decision_path.read_bytes()
        duplicate = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(0, duplicate.returncode)
        self.assertEqual(original, decision_path.read_bytes())
        artifact = json.loads(original)
        encoded = json.dumps(artifact)
        self.assertNotIn("C:\\Users\\", encoded)
        self.assertNotIn("https://", encoded)
        self.assertNotIn("username", encoded)
        self.assertNotIn("secret", encoded)
        self.assertNotIn("github.com", encoded)
        self.assertNotIn("synthetic/decision-fixture", encoded)


if __name__ == "__main__":
    unittest.main()
