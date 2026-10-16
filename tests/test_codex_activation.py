import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from activation.core import (
    AGENTS_DESTINATION, AGENTS_TRIGGER, MANIFEST_PATH, SKILL_DESTINATION, apply, build_plan, deactivate,
    make_decision, plan_digest, _contained,
)


ROOT = Path(__file__).resolve().parents[1]


def git(project, *args):
    return subprocess.run(["git", "-C", str(project), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


class CodexActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        git(self.project, "init", "-q")
        git(self.project, "config", "user.name", "Activation Test")
        git(self.project, "config", "user.email", "activation-test@example.invalid")
        (self.project / "README.md").write_text("pilot\n", encoding="utf-8")
        git(self.project, "add", "README.md")
        git(self.project, "commit", "-qm", "initial")

    def tearDown(self):
        self.temp.cleanup()

    def test_plan_approval_apply_and_idempotent_reapply(self):
        plan = build_plan(self.project)
        self.assertEqual("ready", plan["status"])
        self.assertEqual("testing", plan["capability_id"])
        approval = make_decision(plan, "approve")
        self.assertEqual("activated", apply(plan, approval, self.project)["status"])
        self.assertTrue((self.project / SKILL_DESTINATION).is_file())
        self.assertEqual(AGENTS_TRIGGER, (self.project / AGENTS_DESTINATION).read_text(encoding="utf-8"))
        self.assertEqual("already_active", build_plan(self.project)["status"])
        self.assertEqual("already_active", apply(build_plan(self.project),
                         make_decision(build_plan(self.project), "approve"), self.project)["status"])

    def test_reject_wrong_digest_and_stale_target_write_nothing(self):
        plan = build_plan(self.project)
        with self.assertRaisesRegex(ValueError, "approved"):
            apply(plan, make_decision(plan, "reject"), self.project)
        bad_decision = make_decision(plan, "approve")
        bad_decision["plan_digest"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "exact Activation Plan"):
            apply(plan, bad_decision, self.project)
        (self.project / "later.txt").write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "STALE_ACTIVATION_PLAN"):
            apply(plan, make_decision(plan, "approve"), self.project)
        self.assertFalse((self.project / SKILL_DESTINATION).exists())
        self.assertFalse((self.project / MANIFEST_PATH).exists())
        self.assertFalse((self.project / AGENTS_DESTINATION).exists())

    def test_conflicts_never_overwrite_existing_skill_or_manifest(self):
        target = self.project / SKILL_DESTINATION
        target.parent.mkdir(parents=True)
        target.write_text("user skill\n", encoding="utf-8")
        plan = build_plan(self.project)
        self.assertEqual("conflict", plan["status"])
        with self.assertRaisesRegex(ValueError, "cannot be applied"):
            apply(plan, make_decision(plan, "approve"), self.project)
        self.assertEqual("user skill\n", target.read_text(encoding="utf-8"))

        manifest = self.project / MANIFEST_PATH
        manifest.parent.mkdir(parents=True)
        manifest.write_text('{"owner":"user"}\n', encoding="utf-8")
        plan = build_plan(self.project)
        self.assertEqual("conflict", plan["status"])
        self.assertEqual('{"owner":"user"}\n', manifest.read_text(encoding="utf-8"))

    def _write_production_skill(self):
        skill = self.project / SKILL_DESTINATION
        skill.parent.mkdir(parents=True)
        expected = (ROOT / "host_adapters/codex/skills/testing/SKILL.md").read_bytes()
        skill.write_bytes(expected)

    def test_skill_only_unmanaged_is_review_required(self):
        self._write_production_skill()
        skill = self.project / SKILL_DESTINATION
        before = skill.read_bytes()
        plan = build_plan(self.project)
        self.assertEqual("review_required", plan["status"])
        with self.assertRaisesRegex(ValueError, "cannot be applied"):
            apply(plan, make_decision(plan, "approve"), self.project)
        self.assertEqual(before, skill.read_bytes())
        self.assertFalse((self.project / AGENTS_DESTINATION).exists())
        self.assertFalse((self.project / MANIFEST_PATH).exists())

    def test_complete_unmanaged_activation_is_already_active(self):
        self._write_production_skill()
        agents = self.project / AGENTS_DESTINATION
        agents.write_bytes(AGENTS_TRIGGER.encode("utf-8"))
        plan = build_plan(self.project)
        self.assertEqual("already_active", plan["status"])
        self.assertEqual("already_active", apply(plan, make_decision(plan, "approve"), self.project)["status"])
        self.assertFalse((self.project / MANIFEST_PATH).exists())

    def test_complete_unmanaged_activation_is_not_claimed_or_removed(self):
        self._write_production_skill()
        agents = self.project / AGENTS_DESTINATION
        agents.write_bytes(AGENTS_TRIGGER.encode("utf-8"))
        self.assertEqual("not_active", deactivate(self.project)["status"])
        self.assertTrue((self.project / SKILL_DESTINATION).is_file())
        self.assertEqual(AGENTS_TRIGGER, agents.read_text(encoding="utf-8"))
        self.assertFalse((self.project / MANIFEST_PATH).exists())

    def test_complete_unmanaged_apply_rechecks_artifacts_without_claiming_them(self):
        self._write_production_skill()
        agents = self.project / AGENTS_DESTINATION
        agents.write_bytes(AGENTS_TRIGGER.encode("utf-8"))
        plan = build_plan(self.project)
        decision = make_decision(plan, "approve")
        agents.write_text("Changed after planning\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "artifacts changed"):
            apply(plan, decision, self.project)
        self.assertFalse((self.project / MANIFEST_PATH).exists())

    def test_managed_agents_drift_is_conflict_and_deactivate_preserves_files(self):
        plan = build_plan(self.project)
        apply(plan, make_decision(plan, "approve"), self.project)
        agents = self.project / AGENTS_DESTINATION
        agents.write_text("User edit\n", encoding="utf-8")
        self.assertEqual("conflict", build_plan(self.project)["status"])
        self.assertEqual("conflict", deactivate(self.project)["status"])
        self.assertTrue((self.project / SKILL_DESTINATION).is_file())
        self.assertEqual("User edit\n", agents.read_text(encoding="utf-8"))
        self.assertTrue((self.project / MANIFEST_PATH).is_file())

    def test_already_active_requires_complete_observed_codex_contract(self):
        self._write_production_skill()
        self.assertNotEqual("already_active", build_plan(self.project)["status"])
        (self.project / AGENTS_DESTINATION).write_bytes(AGENTS_TRIGGER.encode("utf-8"))
        self.assertEqual("already_active", build_plan(self.project)["status"])

    def test_existing_agents_is_review_required_and_never_changed(self):
        agents = self.project / AGENTS_DESTINATION
        agents.write_text("User instructions\n", encoding="utf-8")
        plan = build_plan(self.project)
        self.assertEqual("review_required", plan["status"])
        with self.assertRaisesRegex(ValueError, "cannot be applied"):
            apply(plan, make_decision(plan, "approve"), self.project)
        self.assertEqual("User instructions\n", agents.read_text(encoding="utf-8"))
        self.assertFalse((self.project / SKILL_DESTINATION).exists())

    def test_existing_different_agents_with_exact_skill_requires_review(self):
        self._write_production_skill()
        agents = self.project / AGENTS_DESTINATION
        agents.write_text("User instructions\n", encoding="utf-8")
        plan = build_plan(self.project)
        self.assertEqual("review_required", plan["status"])
        with self.assertRaisesRegex(ValueError, "cannot be applied"):
            apply(plan, make_decision(plan, "approve"), self.project)
        self.assertEqual("User instructions\n", agents.read_text(encoding="utf-8"))
        self.assertFalse((self.project / MANIFEST_PATH).exists())

    def test_existing_different_skill_is_conflict(self):
        skill = self.project / SKILL_DESTINATION
        skill.parent.mkdir(parents=True)
        skill.write_text("Other skill\n", encoding="utf-8")
        self.assertEqual("conflict", build_plan(self.project)["status"])

    def test_unrelated_skills_survive_activation_and_deactivation(self):
        other = self.project / ".agents/skills/other/SKILL.md"
        other.parent.mkdir(parents=True)
        other.write_text("Other skill\n", encoding="utf-8")
        plan = build_plan(self.project)
        apply(plan, make_decision(plan, "approve"), self.project)
        self.assertEqual("deactivated", deactivate(self.project)["status"])
        self.assertEqual("Other skill\n", other.read_text(encoding="utf-8"))

    def test_unknown_capability_and_host_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported capability"):
            build_plan(self.project, "planning")
        with self.assertRaisesRegex(ValueError, "Unsupported host"):
            build_plan(self.project, host="claude")

    def test_skill_symlink_escape_is_rejected(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (self.project / ".agents").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            build_plan(self.project)
        self.assertEqual([], list(outside.iterdir()))

    def test_path_containment_rejects_traversal_and_absolute_paths(self):
        for unsafe in ("../escape", "C:/outside/SKILL.md", "/outside/SKILL.md"):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                _contained(self.project.resolve(), unsafe)

    def test_changed_head_rejects_plan_without_writing(self):
        plan = build_plan(self.project)
        (self.project / "later.txt").write_text("commit\n", encoding="utf-8")
        git(self.project, "add", "later.txt")
        git(self.project, "commit", "-qm", "advance target")
        with self.assertRaisesRegex(ValueError, "STALE_ACTIVATION_PLAN"):
            apply(plan, make_decision(plan, "approve"), self.project)
        self.assertFalse((self.project / SKILL_DESTINATION).exists())

    def test_safe_deactivation_and_modified_skill_conflict(self):
        plan = build_plan(self.project)
        apply(plan, make_decision(plan, "approve"), self.project)
        skill = self.project / SKILL_DESTINATION
        skill.write_bytes(skill.read_bytes() + b"\nuser edit\n")
        self.assertEqual("conflict", deactivate(self.project)["status"])
        self.assertTrue(skill.exists())
        skill.write_bytes((ROOT / plan["source"]).read_bytes())
        self.assertEqual("deactivated", deactivate(self.project)["status"])
        self.assertFalse(skill.exists())
        self.assertFalse((self.project / MANIFEST_PATH).exists())

    def test_plan_digest_is_deterministic_and_production_skill_has_no_probe(self):
        plan = build_plan(self.project)
        self.assertEqual(plan["plan_digest"], plan_digest(plan))
        production = (ROOT / "host_adapters/codex/skills/testing/SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("BOOTSTRAP_CODEX_TESTING_SKILL_ACTIVE", production)
        self.assertIn("BOOTSTRAP_CODEX_TESTING_SKILL_ACTIVE",
                      (ROOT / "tests/fixtures/codex_activation/pilot-instruction.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
