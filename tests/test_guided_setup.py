"""Acceptance tests for the user-facing guided setup flow."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from bootstrap.core import ProductLoopError, build_plan, decide
from bootstrap.guided import build_context, inspect_setup
from bootstrap.lifecycle import get_status
from bootstrap.core import apply_plan
from composer.core import ROOT


class GuidedSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / "consumer"
        self.project.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Guided Setup Test")
        self.git("config", "user.email", "guided@example.invalid")
        self.put("pyproject.toml", "[project]\nname = 'consumer'\nversion = '0.1.0'\n")
        self.put("README.md", "# Consumer\nA small Python application.\n")
        self.git("add", ".")
        self.git("commit", "-m", "Initial project")

    def tearDown(self):
        self.temp.cleanup()

    def put(self, relative, content):
        target = self.project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.project), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def create_repo(self, name):
        project = self.base / name
        project.mkdir()
        subprocess.run(["git", "-C", str(project), "init", "-b", "main"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(project), "config", "user.name", "Guided Setup Test"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(project), "config", "user.email", "guided@example.invalid"], check=True, capture_output=True)
        workflow = project / ".github/workflows/ci.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text("name: CI\non: [push]\njobs: {}\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(project), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(project), "commit", "-m", "Initial project"], check=True, capture_output=True)
        return project

    def semantic_result(self, package, capability_id):
        preference_ids = package["profile_constraints"]["explicit_preferences"]
        preference_names = (set(preference_ids) - {"additional"}) | set(preference_ids.get("additional", {}))
        if {"prefer_simple_solutions", "avoid_unnecessary_abstraction"}.issubset(preference_names):
            preference_names.difference_update({"prefer_simple_solutions", "avoid_unnecessary_abstraction"})
            preference_names.add("solution_complexity")
        evidence_path = package["discovered_assets"][0]["path"]
        result = {
            "project_name": package["project"]["name"], "reviewer": "host-ai",
            "preference_findings": [{"preference_id": item, "status": "compatible",
                "evidence": [{"path": evidence_path, "claim": "This active project asset is part of the current setup."}],
                "reason": "No preference conflict is established."} for item in sorted(preference_names)],
            "semantic_coverage": [],
        }
        for record in package["capability_catalog"]:
            missing = record["id"] == capability_id
            result["semantic_coverage"].append({"capability_id": record["id"],
                "status": "missing" if missing else "unknown", "confidence": "medium" if missing else "low",
                "evidence": [], "reason": f"The project has no {capability_id} capability." if missing else "Applicability is unknown.",
                "recommended_action": "consider_bootstrap_component" if missing else "no_action"})
        return result

    def cli(self, *args):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        return subprocess.run([sys.executable, "-m", "bootstrap", *map(str, args)],
                              cwd=ROOT, env=env, capture_output=True, text=True)

    def test_greenfield_cli_plan_explicit_approval_apply_and_status(self):
        inspected = inspect_setup(self.project, "none")
        self.assertEqual("compose", inspected["route"])
        self.assertEqual(["Python"], inspected["project"]["technologies"])
        self.assertTrue(any(option["capability_id"] == "testing" for option in inspected["capability_options"]))
        self.assertFalse((self.project / ".vibe-coding-bootstrap").exists())

        plan_path = self.base / "setup-plan.json"
        result = self.cli("setup", "plan", "--project-root", self.project, "--host", "none",
                          "--goal", "Keep regression checks repeatable", "--capability", "testing",
                          "--exclude-component", "codex-native-testing-skill", "--scope", "project",
                          "--output", plan_path)
        self.assertEqual(0, result.returncode, result.stderr)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertTrue(plan["requires_confirmation"])
        self.assertEqual(["testing"], plan["capabilities"])
        self.assertEqual([], plan["global_changes"])
        self.assertIn("project-test-guidance", [item["component_id"] for item in plan["project_changes"]])
        self.assertFalse((self.project / ".vibe-coding-bootstrap").exists())

        decision = self.cli("setup", "approve", "--plan", plan_path)
        self.assertEqual(0, decision.returncode, decision.stderr)
        decision_path = Path(f"{plan_path}.decision.json")
        applied = self.cli("setup", "apply", "--project-root", self.project,
                           "--plan", plan_path, "--decision", decision_path)
        self.assertEqual(0, applied.returncode, applied.stderr)
        self.assertTrue((self.project / ".vibe-coding-bootstrap/components/test-guidance.md").is_file())
        status = get_status(self.project, host="none")
        self.assertEqual("installed", status["status"])
        self.assertFalse(status["has_conflicts"])

    def test_existing_project_uses_host_review_and_native_skill_activation(self):
        cases = [
            ("codex", "testing", ".agents/skills/bootstrap-testing/SKILL.md"),
            ("claude", "vcbs-manager", ".claude/skills/vcbs-manager/SKILL.md"),
            ("antigravity", "vcbs-manager", ".agents/skills/vcbs-manager/SKILL.md"),
        ]
        for index, (host, capability, destination) in enumerate(cases):
            with self.subTest(host=host):
                project = self.create_repo(f"{host}-{index}")
                inspected = inspect_setup(project, host)
                self.assertEqual("adoption", inspected["route"])
                self.assertTrue(inspected["semantic_review_required"])
                package = inspected["semantic_package"]
                semantic_path = self.base / f"{host}-{index}-semantic.json"
                semantic_path.write_text(json.dumps(self.semantic_result(package, capability)), encoding="utf-8")
                if host == "claude":
                    plan_path = self.base / "claude-plan.json"
                    planned = self.cli("setup", "plan", "--project-root", project, "--host", host,
                        "--semantic-result", semantic_path, "--capability", capability,
                        "--scope", "project", "--output", plan_path)
                    self.assertEqual(0, planned.returncode, planned.stderr)
                    plan = json.loads(plan_path.read_text(encoding="utf-8"))
                else:
                    plan = build_plan(project, host=host, semantic_result_path=semantic_path,
                        selected_capability_ids=[capability], scope="project")
                self.assertEqual("adoption", plan["route"])
                self.assertEqual("ready", plan["host_activations"][0]["status"])
                self.assertTrue(plan["requires_confirmation"])
                with self.assertRaisesRegex(ProductLoopError, "DECISION_REQUIRED"):
                    apply_plan(plan, None, project)
                applied = apply_plan(plan, decide(plan, "approve"), project)
                self.assertEqual("applied", applied["status"])
                self.assertTrue((project / destination).is_file())
                status = get_status(project, host=host)
                matching = next(item for item in status["native_activations"] if item["host"] == host)
                self.assertEqual("active", matching["status"])

    def test_web_project_detection_and_selection(self):
        self.put("package.json", '{"name":"consumer-web","scripts":{"test":"node --test"}}\n')
        (self.project / "pyproject.toml").unlink()
        self.git("add", ".")
        self.git("add", "-u")
        self.git("commit", "-m", "Add web runtime marker")
        inspected = inspect_setup(self.project, "none")
        self.assertEqual(["JavaScript"], inspected["project"]["technologies"])
        context = build_context(self.project, goal="Document project interfaces")
        plan = build_plan(self.project, host="none", context_value=context,
                          selected_capability_ids=["documentation"], excluded_component_ids=["reference"],
                          scope="project")
        self.assertEqual(["documentation"], plan["capabilities"])
        self.assertIn("project-documentation-outline", [item["component_id"] for item in plan["project_changes"]])

        global_root = self.base / "global"
        global_root.mkdir()
        global_plan = build_plan(self.project, host="none", context_value=context,
            selected_capability_ids=["engineering-baseline"], global_root=global_root, scope="global")
        self.assertEqual([], global_plan["project_changes"])
        self.assertIn("reviewable-changes", [item["component_id"] for item in global_plan["global_changes"]])
        applied = apply_plan(global_plan, decide(global_plan, "approve"), self.project, global_root)
        self.assertEqual("applied", applied["status"])
        self.assertTrue((global_root / "components/reviewable-changes.md").is_file())

        plan = build_plan(self.project, host="none", context_value=context,
                          selected_capability_ids=["documentation"], excluded_component_ids=["reference"],
                          scope="project")
        applied = apply_plan(plan, decide(plan, "approve"), self.project)
        self.assertEqual("applied", applied["status"])
        self.assertTrue((self.project / ".vibe-coding-bootstrap/components/documentation-outline.md").is_file())
        status = get_status(self.project, host="none", global_root=global_root)
        self.assertEqual("installed", status["status"])
        self.assertFalse(status["has_conflicts"])


if __name__ == "__main__":
    unittest.main()
