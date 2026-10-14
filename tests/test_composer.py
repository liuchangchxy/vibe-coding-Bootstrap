import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator
import yaml

from composer.core import ROOT, compose, load_yaml, validate


class ComposerTests(unittest.TestCase):
    def test_examples(self):
        expected = {
            "long-lived-web": {"engineering-baseline", "testing", "documentation"},
            "agent": {"agent-development", "testing"},
            "one-off-experiment": set(),
        }
        for name, ids in expected.items():
            context = load_yaml(ROOT / "examples" / name / "project-context.yaml")
            plan = compose(context)
            self.assertEqual(ids, {r["capability_id"] for r in plan["recommendations"]})
            self.assertTrue(plan["requires_confirmation"])
            self.assertEqual(plan, compose(context))
            self.assertTrue(all(r["evidence"]["state"] == "pending" for r in plan["recommendations"]))

    def test_all_schemas(self):
        for path in (ROOT / "schemas").glob("*.json"):
            Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))
        validate(load_yaml(ROOT / "profiles/personal.yaml"), "profile.schema.json")

    def test_profile_exclusions_dependencies_and_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ["schemas", "capabilities", "composer", "profiles", "experiences"]:
                shutil.copytree(ROOT / name, root / name)
            context = load_yaml(ROOT / "examples/agent/project-context.yaml")
            profile_path = root / "profiles/personal.yaml"
            profile = load_yaml(profile_path)
            profile["excluded_capability_ids"] = ["testing"]
            profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
            plan = compose(context, root)
            self.assertEqual([], plan["recommendations"])
            self.assertEqual({"testing", "agent-development"}, {e["capability_id"] for e in plan["excluded"]})
            profile["excluded_capability_ids"] = []
            profile["default_capability_ids"] = ["documentation"]
            profile_path.write_text(yaml.safe_dump(profile), encoding="utf-8")
            record_path = root / "capabilities/workflow/documentation.yaml"
            record = load_yaml(record_path)
            record["conflicts_with"] = ["agent-development"]
            record_path.write_text(yaml.safe_dump(record), encoding="utf-8")
            plan = compose(context, root)
            self.assertEqual([["agent-development", "documentation"]], plan["conflicts"])
            self.assertEqual(3, len(plan["recommendations"]))

    def test_experience_keeps_outcome_and_limits(self):
        # Synthetic test fixture only; never installed as real Experience.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ["schemas", "capabilities", "composer", "profiles", "experiences"]:
                shutil.copytree(ROOT / name, root / name)
            experience = {
                "id": "test-only", "title": "Synthetic failure fixture",
                "project_context": {"project_type": "agent", "goal": "Test matching"},
                "approach": "Fixture", "capability_ids": ["testing"],
                "outcome": {"result": "failure", "summary": "Synthetic failure"},
                "evidence": [{"description": "Test fixture, no real observation"}],
                "confidence": "low", "lesson": "Must not imply effectiveness",
                "recorded_at": "2026-10-15T00:00:00+08:00",
            }
            (root / "experiences/test.yaml").write_text(yaml.safe_dump(experience), encoding="utf-8")
            context = load_yaml(ROOT / "examples/agent/project-context.yaml")
            plan = compose(context, root)
            testing = next(r for r in plan["recommendations"] if r["capability_id"] == "testing")
            self.assertEqual(experience, testing["experiences"][0]["record"])
            self.assertEqual("pending", testing["evidence"]["state"])
            context = copy.deepcopy(context)
            context["project_type"] = "web"
            context["lifecycle"] = "long_lived"
            self.assertTrue(all(not r["experiences"] for r in compose(context, root)["recommendations"]))

    def test_duplicate_yaml_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.yaml"
            path.write_text("name: first\nname: second\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate YAML key"):
                load_yaml(path)

    def test_cli_writes_only_report_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context_path = root / "project-context.yaml"
            shutil.copyfile(ROOT / "examples/agent/project-context.yaml", context_path)
            report = root / "recommendation.yaml"
            original = context_path.read_bytes()
            command = [sys.executable, "-m", "composer", str(context_path), "--output", str(report)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            validate(load_yaml(report), "recommendation.schema.json")
            before = report.read_bytes()
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(0, result.returncode)
            self.assertEqual(before, report.read_bytes())
            self.assertEqual(original, context_path.read_bytes())
            self.assertEqual({"project-context.yaml", "recommendation.yaml"}, {p.name for p in root.iterdir()})


if __name__ == "__main__":
    unittest.main()
