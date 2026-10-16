"""Revision binding and deterministic Existing Project Adoption Plan tests."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from adoption.planning import build_adoption_plan, plan_digest, validate_adoption_plan
from adoption.revision import bind_target_revision
from adoption.semantic import build_semantic_input
from composer.core import ROOT


class AdoptionPlanningTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.project / "AGENTS.md").write_text("# Rules\nTest changes and review them.\n", encoding="utf-8")
        self.git("add", "AGENTS.md")
        self.git("commit", "-m", "initial")
        self.git("remote", "add", "origin", "https://user:secret@github.com/example/project.git")
        self.package = build_semantic_input(self.project)
        self.result = self.result_with()

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.project), *args], check=True, capture_output=True, text=True).stdout.strip()

    def result_with(self, overrides=None):
        overrides = overrides or {}
        coverage = []
        for record in self.package["capability_catalog"]:
            capability_id = record["id"]
            status, action = overrides.get(capability_id, ("unknown", "no_action"))
            if (status == "satisfied" and action == "keep_existing") or status == "conflict":
                evidence = [{"path": "AGENTS.md", "claim": "Project has rules."}]
            else:
                evidence = []
            coverage.append({"capability_id": capability_id, "status": status, "confidence": "medium",
                             "evidence": evidence, "reason": "Reviewed for this fixture.", "recommended_action": action})
        explicit = self.package["profile_constraints"]["explicit_preferences"]
        preference_ids = (set(explicit) - {"additional"}) | set(explicit.get("additional", {}))
        if {"prefer_simple_solutions", "avoid_unnecessary_abstraction"}.issubset(preference_ids):
            preference_ids.difference_update({"prefer_simple_solutions", "avoid_unnecessary_abstraction"})
            preference_ids.add("solution_complexity")
        preferences = [
            {"preference_id": preference_id, "status": "compatible",
             "evidence": [{"path": "AGENTS.md", "claim": "Project instructions are available."}],
             "reason": "No incompatibility is established."}
            for preference_id in sorted(preference_ids)
        ]
        return {"project_name": "project", "reviewer": "host-ai", "semantic_coverage": coverage,
                "preference_findings": preferences}

    def test_clean_and_dirty_revision_binding_excludes_private_remote_data(self):
        binding = bind_target_revision(self.project)
        self.assertEqual("main", binding["branch"])
        self.assertEqual(self.git("rev-parse", "HEAD"), binding["commit_sha"])
        self.assertFalse(binding["dirty"])
        self.assertEqual("example/project", binding["repository"])
        (self.project / "AGENTS.md").write_text("changed\n", encoding="utf-8")
        self.assertTrue(bind_target_revision(self.project)["dirty"])

    def test_semantic_input_and_plan_bind_exact_revision_without_local_path_or_secret(self):
        self.assertEqual(self.git("rev-parse", "HEAD"), self.package["target"]["commit_sha"])
        plan = build_adoption_plan(self.package, self.result, self.project)
        serialized = json.dumps({"package": self.package, "plan": plan})
        self.assertNotIn(str(self.project), serialized)
        self.assertNotIn("secret", serialized)
        self.assertEqual("example/project", plan["target"]["repository"])

    def test_dirty_target_and_changed_head_reject_stale_review(self):
        (self.project / "AGENTS.md").write_text("uncommitted\n", encoding="utf-8")
        self.assertTrue(bind_target_revision(self.project)["dirty"])
        with self.assertRaisesRegex(ValueError, "STALE_REVIEW"):
            build_adoption_plan(self.package, self.result, self.project)
        self.git("add", "AGENTS.md")
        self.git("commit", "-m", "second")
        with self.assertRaisesRegex(ValueError, "STALE_REVIEW"):
            build_adoption_plan(self.package, self.result, self.project)

    def test_dispositions_noop_review_only_and_applicable_component(self):
        statuses = {
            "engineering-baseline": ("satisfied", "keep_existing"),
            "testing": ("satisfied", "keep_existing"),
            "code-review": ("satisfied", "keep_existing"),
            "documentation": ("satisfied", "keep_existing"),
            "planning": ("satisfied", "keep_existing"),
        }
        no_op = build_adoption_plan(self.package, self.result_with(statuses), self.project)
        self.assertEqual("no_changes", no_op["overall_decision"])
        self.assertEqual(0, no_op["materializable_changes"])

        statuses["debugging"] = ("partial", "review_gap")
        statuses["agent-development"] = ("partial", "review_gap")
        review_only = build_adoption_plan(self.package, self.result_with(statuses), self.project)
        self.assertEqual("review_required", review_only["overall_decision"])
        self.assertEqual(0, review_only["materializable_changes"])

        statuses["testing"] = ("missing", "consider_bootstrap_component")
        component_plan = build_adoption_plan(self.package, self.result_with(statuses), self.project)
        testing = next(item for item in component_plan["items"] if item["capability_id"] == "testing")
        self.assertEqual("consider_component", testing["disposition"])
        self.assertTrue(testing["materialization_eligible"])
        self.assertEqual("changes_available", component_plan["overall_decision"])

    def test_invalid_action_mapping_and_digest_validation(self):
        invalid = self.result_with({"debugging": ("partial", "consider_bootstrap_component")})
        with self.assertRaisesRegex(ValueError, "Partial coverage must use review_gap"):
            build_adoption_plan(self.package, invalid, self.project)
        plan = build_adoption_plan(self.package, self.result, self.project)
        again = build_adoption_plan(self.package, self.result, self.project)
        self.assertEqual(plan["plan_digest"], again["plan_digest"])
        changed = dict(plan)
        changed["overall_decision"] = "review_required"
        self.assertNotEqual(plan["plan_digest"], plan_digest(changed))
        validate_adoption_plan(plan)

    def test_conflict_and_missing_gap_mappings_remain_review_only(self):
        statuses = {"debugging": ("missing", "review_gap"),
                    "agent-development": ("conflict", "review_conflict")}
        plan = build_adoption_plan(self.package, self.result_with(statuses), self.project)
        self.assertEqual("review_gap", next(item for item in plan["items"]
                                             if item["capability_id"] == "debugging")["disposition"])
        self.assertEqual("review_conflict", next(item for item in plan["items"]
                                                  if item["capability_id"] == "agent-development")["disposition"])
        self.assertEqual(0, plan["materializable_changes"])
        self.assertEqual("review_required", plan["overall_decision"])

    def test_missing_component_without_valid_catalog_entry_is_rejected(self):
        invalid = self.result_with({"debugging": ("missing", "consider_bootstrap_component")})
        with self.assertRaisesRegex(ValueError, "No applicable project-scoped Bootstrap component"):
            build_adoption_plan(self.package, invalid, self.project)


if __name__ == "__main__":
    unittest.main()
