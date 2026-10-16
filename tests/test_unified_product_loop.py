"""Phase 11 routing, safety, and product-loop acceptance tests."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

from bootstrap.core import ProductLoopError, apply_plan, build_plan, decide, inspect_project
from composer.core import ROOT, load_yaml


CONTEXT = {
    "name": "pilot", "description": "Maintained Python project for product-loop acceptance.",
    "project_type": "software-project", "lifecycle": "production", "technology_tags": ["Python"],
    "runtime": "Python", "platform": "local", "goal": "Maintain behavior with focused verification.",
    "constraints": ["AI coding host is Codex"], "collaboration": "solo",
}


class UnifiedLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.repo = self.base / "target"
        self.repo.mkdir()
        self._git("init", "-q")
        self._git("config", "user.email", "bootstrap-test@example.invalid")
        self._git("config", "user.name", "Bootstrap Test")
        (self.repo / "README.md").write_text("# Ordinary project\n", encoding="utf-8")
        (self.repo / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (self.repo / "test_calculator.py").write_text("def test_add():\n    assert 1 + 1 == 2\n", encoding="utf-8")
        self._git("add", "README.md", "calculator.py", "test_calculator.py")
        self._git("commit", "-qm", "initial")
        self.context = self.base / "context.yaml"
        self.context.write_text(yaml.safe_dump(CONTEXT, sort_keys=False), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def _git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    def _run(self, *args, ok=True):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        result = subprocess.run([sys.executable, "-m", "bootstrap", *map(str, args)], cwd=ROOT,
                                env=env, capture_output=True, text=True)
        if ok and result.returncode:
            self.fail(f"CLI failed: {result.stdout}\n{result.stderr}")
        return result

    def test_cli_full_loop_and_second_run(self):
        before = {name: (self.repo / name).read_bytes() for name in ("README.md", "calculator.py", "test_calculator.py")}
        inspect_path = self.base / "inspect.json"
        self._run("inspect", "--project-root", self.repo, "--host", "codex", "--output", inspect_path)
        inspected = json.loads(inspect_path.read_text(encoding="utf-8"))
        self.assertEqual(inspected["route"], "compose")
        plan_path = self.base / "plan.json"
        self._run("plan", "--project-root", self.repo, "--host", "codex", "--context", self.context, "--global-root", self.base / "global", "--output", plan_path)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertGreater(plan["actual_changes"], 0)
        decision_path = self.base / "decision.json"
        self._run("decide", plan_path, "--decision", "approve", "--output", decision_path)
        result_path = self.base / "apply.json"
        self._run("apply", "--plan", plan_path, "--decision", decision_path, "--project-root", self.repo,
                  "--global-root", self.base / "global", "--output", result_path)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "applied")
        self.assertTrue((self.repo / ".agents/skills/bootstrap-testing/SKILL.md").is_file())
        self.assertTrue((self.repo / ".vibe-coding-bootstrap/activation.json").is_file())
        self.assertTrue((self.repo / ".vibe-coding-bootstrap/manifest.yaml").is_file())
        self.assertEqual(before, {name: (self.repo / name).read_bytes() for name in before})
        self._run("inspect", "--project-root", self.repo, "--host", "codex")
        second_path = self.base / "second.json"
        self._run("plan", "--project-root", self.repo, "--host", "codex", "--context", self.context,
                  "--global-root", self.base / "global", "--output", second_path)
        second = json.loads(second_path.read_text(encoding="utf-8"))
        self.assertEqual(second["route"], "compose")
        self.assertEqual(second["actual_changes"], 0)
        self.assertFalse(second["requires_confirmation"])
        self.assertEqual(second["host_activations"][0]["status"], "already_active")
        self.assertTrue(all(item["result"] == "unchanged" for item in second["project_changes"] + second["global_changes"] if item["delivery"] == "copy"))

    def test_routes_and_read_only_adoption(self):
        self.assertEqual(inspect_project(self.repo)["route"], "compose")
        (self.repo / "AGENTS.md").write_text("# Local rules\n", encoding="utf-8")
        self.assertEqual(inspect_project(self.repo)["route"], "adoption")
        self.assertFalse((self.repo / ".vibe-coding-bootstrap").exists())
        self.assertIn("semantic_package", inspect_project(self.repo))
        skill = self.repo / ".agents/skills/example/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("# Existing skill\n", encoding="utf-8")
        self.assertEqual(inspect_project(self.repo)["route"], "adoption")

    def test_missing_context_and_output_containment(self):
        with self.assertRaisesRegex(ProductLoopError, "PROJECT_CONTEXT_REQUIRED"):
            build_plan(self.repo)
        result = self._run("inspect", "--project-root", self.repo, "--output", self.repo / "inspection.json", ok=False)
        self.assertIn("OUTPUT_INSIDE_TARGET", result.stderr)
        self.assertFalse((self.repo / "inspection.json").exists())
        plan = build_plan(self.repo, context_path=self.context, global_root=self.base / "global")
        plan_path = self.base / "inner-output-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        decision_path = self.base / "inner-output-decision.json"
        decision_path.write_text(json.dumps(decide(plan, "approve")), encoding="utf-8")
        result = self._run("apply", "--plan", plan_path, "--decision", decision_path,
                           "--project-root", self.repo, "--global-root", self.base / "global",
                           "--output", self.repo / "apply-result.json", ok=False)
        self.assertIn("OUTPUT_INSIDE_TARGET", result.stderr)
        self.assertFalse((self.repo / ".agents").exists())

    def test_host_none_omits_activation(self):
        plan = build_plan(self.repo, host="none", context_path=self.context, global_root=self.base / "global")
        self.assertEqual(plan["host_activations"], [])

    def test_global_root_is_required_during_planning(self):
        with self.assertRaisesRegex(ProductLoopError, "GLOBAL_ROOT_REQUIRED"):
            build_plan(self.repo, context_path=self.context)

    def test_decision_exact_binding_and_stale_target(self):
        plan = build_plan(self.repo, context_path=self.context, global_root=self.base / "global")
        approved = decide(plan, "approve")
        wrong = dict(approved, plan_digest="0" * 64)
        with self.assertRaisesRegex(ProductLoopError, "DECISION_MISMATCH"):
            apply_plan(plan, wrong, self.repo, self.base / "global")
        (self.repo / "user-change.txt").write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(ProductLoopError, "STALE_BOOTSTRAP_PLAN"):
            apply_plan(plan, approved, self.repo, self.base / "global")
        self.assertFalse((self.repo / ".agents").exists())

    def test_existing_destination_conflict_blocks_plan(self):
        destination = self.repo / ".vibe-coding-bootstrap/components/test-guidance.md"
        destination.parent.mkdir(parents=True)
        destination.write_text("user content\n", encoding="utf-8")
        plan = build_plan(self.repo, context_path=self.context, global_root=self.base / "global")
        self.assertFalse(plan["executable"])
        self.assertTrue(plan["conflicts"])
        with self.assertRaisesRegex(ProductLoopError, "PLAN_BLOCKED"):
            decide(plan, "approve")
        self.assertEqual(destination.read_text(encoding="utf-8"), "user content\n")

    def _adoption_result(self, project, selected=None):
        from adoption.semantic import build_semantic_input
        package = build_semantic_input(project)
        selected = selected or set()
        semantic = []
        for record in package["capability_catalog"]:
            cid = record["id"]
            status = "missing" if cid in selected else "satisfied"
            semantic.append({"capability_id": cid, "status": status, "confidence": "high", "evidence": [],
                             "reason": "Synthetic bounded-orchestration fixture review.",
                             "recommended_action": "consider_bootstrap_component" if cid in selected else "keep_existing"})
        explicit = package["profile_constraints"]["explicit_preferences"]
        preference_ids = (set(explicit) - {"additional"}) | set(explicit.get("additional", {}))
        if {"prefer_simple_solutions", "avoid_unnecessary_abstraction"}.issubset(preference_ids):
            preference_ids.difference_update({"prefer_simple_solutions", "avoid_unnecessary_abstraction"})
            preference_ids.add("solution_complexity")
        preferences = [{"preference_id": preference_id, "status": "unknown", "evidence": [],
                        "reason": "Synthetic bounded-orchestration fixture review."}
                       for preference_id in sorted(preference_ids)]
        return package, {"project_name": package["project"]["name"], "reviewer": "host-ai",
                         "semantic_coverage": semantic, "preference_findings": preferences}

    def test_adoption_semantic_gate_review_only_and_exact_eligible_apply(self):
        agents = self.repo / "AGENTS.md"
        agents.write_text("# Existing development rules\n", encoding="utf-8")
        with self.assertRaisesRegex(ProductLoopError, "HOST_SEMANTIC_REVIEW_REQUIRED"):
            build_plan(self.repo)
        package, result = self._adoption_result(self.repo)
        semantic_path = self.base / "semantic.json"
        semantic_path.write_text(json.dumps(result), encoding="utf-8")
        no_op = build_plan(self.repo, semantic_result_path=semantic_path)
        self.assertEqual(no_op["route"], "adoption")
        self.assertEqual(no_op["actual_changes"], 0)
        self.assertFalse(no_op["requires_confirmation"])
        self.assertEqual(apply_plan(no_op, None, self.repo)["status"], "no_changes")
        self.assertFalse((self.repo / ".vibe-coding-bootstrap").exists())
        package, result = self._adoption_result(self.repo, {"testing"})
        semantic_path.write_text(json.dumps(result), encoding="utf-8")
        eligible = build_plan(self.repo, semantic_result_path=semantic_path)
        self.assertEqual(eligible["capabilities"], ["testing"])
        self.assertEqual([item["component_id"] for item in eligible["project_changes"] if item["result"] == "copied"], ["project-test-guidance"])
        approval = decide(eligible, "approve")
        applied = apply_plan(eligible, approval, self.repo)
        self.assertEqual(applied["status"], "applied")
        self.assertTrue((self.repo / ".vibe-coding-bootstrap/components/test-guidance.md").is_file())
        self.assertFalse((self.repo / ".agents/skills/bootstrap-testing/SKILL.md").exists())

    def test_bootstrap_owned_route_and_unrelated_skill(self):
        project = self.repo
        project.mkdir(exist_ok=True)
        activation_manifest = project / ".vibe-coding-bootstrap/activation.json"
        activation_manifest.parent.mkdir(parents=True)
        skill_source = ROOT / "host_adapters/codex/skills/testing/SKILL.md"
        skill_dest = project / ".agents/skills/bootstrap-testing/SKILL.md"
        skill_dest.parent.mkdir(parents=True)
        skill_dest.write_bytes(skill_source.read_bytes())
        from activation.core import AGENTS_TRIGGER, _owned_artifacts, _sha256
        agents_bytes = AGENTS_TRIGGER.encode("utf-8")
        (project / "AGENTS.md").write_bytes(agents_bytes)
        activation_manifest.write_text(json.dumps({"format_version": 1, "host": "codex", "capability": "testing",
            "artifacts": _owned_artifacts(_sha256(skill_source.read_bytes()), _sha256(agents_bytes))}), encoding="utf-8")
        self.assertEqual(inspect_project(project)["route"], "compose")
        custom = project / ".agents/skills/custom/SKILL.md"
        custom.parent.mkdir(parents=True)
        custom.write_text("# User skill\n", encoding="utf-8")
        self.assertEqual(inspect_project(project)["route"], "adoption")

    def test_stale_profile_and_catalog_rejected(self):
        plan = build_plan(self.repo, context_path=self.context, global_root=self.base / "global")
        approved = decide(plan, "approve")
        profile = ROOT / "profiles/personal.yaml"
        original = profile.read_bytes()
        try:
            profile.write_bytes(original + b"\n")
            with self.assertRaisesRegex(ProductLoopError, "STALE_BOOTSTRAP_PROFILE"):
                apply_plan(plan, approved, self.repo, self.base / "global")
        finally:
            profile.write_bytes(original)
        plan = build_plan(self.repo, context_path=self.context, global_root=self.base / "global")
        approved = decide(plan, "approve")
        catalog = ROOT / "capabilities/catalog.yaml"
        original_catalog = catalog.read_bytes()
        try:
            catalog.write_bytes(original_catalog + b"\n")
            with self.assertRaisesRegex(ProductLoopError, "STALE_BOOTSTRAP_CATALOG"):
                apply_plan(plan, approved, self.repo, self.base / "global")
        finally:
            catalog.write_bytes(original_catalog)

    def _isolated_bootstrap_root(self, label):
        root = self.base / f"bootstrap-{label}"
        shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        return root

    def _approve_isolated_plan(self, root, global_root):
        plan = build_plan(self.repo, context_path=self.context, global_root=global_root, root=root)
        return plan, decide(plan, "approve")

    def test_changed_project_component_source_rejects_before_write(self):
        root = self._isolated_bootstrap_root("project-source")
        global_root = self.base / "global-project-source"
        plan, approval = self._approve_isolated_plan(root, global_root)
        self.assertTrue(any(item["component_id"] == "project-test-guidance" and item["result"] == "copied"
                            for item in plan["project_changes"]))
        source = root / "components/project/test-guidance.md"
        source.write_bytes(source.read_bytes() + b"\nchanged after approval\n")
        with self.assertRaisesRegex(ProductLoopError, "STALE_BOOTSTRAP_PLAN"):
            apply_plan(plan, approval, self.repo, global_root, root)
        self.assertFalse((self.repo / "AGENTS.md").exists())
        self.assertFalse((self.repo / ".agents").exists())
        self.assertFalse((self.repo / ".vibe-coding-bootstrap").exists())
        self.assertFalse(global_root.exists())

    def test_changed_global_component_source_rejects_before_write(self):
        root = self._isolated_bootstrap_root("global-source")
        global_root = self.base / "global-global-source"
        plan, approval = self._approve_isolated_plan(root, global_root)
        self.assertTrue(any(item["component_id"] == "reviewable-changes" and item["result"] == "copied"
                            for item in plan["global_changes"]))
        source = root / "components/global/reviewable-changes.md"
        source.write_bytes(source.read_bytes() + b"\nchanged after approval\n")
        with self.assertRaisesRegex(ProductLoopError, "STALE_BOOTSTRAP_PLAN"):
            apply_plan(plan, approval, self.repo, global_root, root)
        self.assertFalse((self.repo / "AGENTS.md").exists())
        self.assertFalse((self.repo / ".agents").exists())
        self.assertFalse((self.repo / ".vibe-coding-bootstrap").exists())
        self.assertFalse(global_root.exists())

    def test_global_copy_classification_change_rejects_before_write(self):
        root = self._isolated_bootstrap_root("global-classification")
        global_root = self.base / "global-classification"
        plan, approval = self._approve_isolated_plan(root, global_root)
        component = next(item for item in plan["global_changes"] if item["component_id"] == "reviewable-changes")
        destination = global_root / component["destination"]
        destination.parent.mkdir(parents=True)
        source = root / "components/global/reviewable-changes.md"
        destination.write_bytes(source.read_bytes())
        with self.assertRaisesRegex(ProductLoopError, "STALE_BOOTSTRAP_PLAN"):
            apply_plan(plan, approval, self.repo, global_root, root)
        self.assertFalse((self.repo / "AGENTS.md").exists())
        self.assertFalse((self.repo / ".agents").exists())
        self.assertFalse((self.repo / ".vibe-coding-bootstrap").exists())
        self.assertEqual(destination.read_bytes(), source.read_bytes())


if __name__ == "__main__":
    unittest.main()
