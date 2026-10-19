"""Tests for P0-C (Managed Updates for Materializer assets and Codex native activation)."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

from activation import core as activation
from assembly.core import assemble
from bootstrap.core import apply_plan, build_plan, decide, inspect_project, load_json, render_summary
from bootstrap.recovery import load_record, rollback
from composer.core import ROOT, compose, load_yaml, validate
from materializer.core import materialize


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class ManagedUpdatesTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.vcbs_root = self.base / "vcbs"
        for name in ["schemas", "capabilities", "composer", "profiles", "experiences", "components", "host_adapters", "assembly", "examples"]:
            shutil.copytree(ROOT / name, self.vcbs_root / name)
        # Create disposable project
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self._git("init", "-q")
        self._git("config", "user.email", "dev@example.invalid")
        self._git("config", "user.name", "Dev")
        (self.repo / "README.md").write_text("# Project\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-qm", "initial")
        self.context = {
            "name": "pilot", "description": "Managed update test",
            "project_type": "software-project", "lifecycle": "production",
            "technology_tags": ["Python"], "runtime": "Python", "platform": "local",
            "goal": "Test managed upgrades", "constraints": ["AI coding host is Codex"],
            "collaboration": "solo",
        }
        self.context_file = self.base / "context.yaml"
        self.context_file.write_text(yaml.safe_dump(self.context), encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    def test_materializer_upgrade_unmodified_managed_component(self):
        # 1. Materialize v1
        item = {
            "component_id": "test-guidance", "component_type": "document", "source_capability": "testing",
            "capability_version": "1.0", "reason": "Guidance", "scope": "project",
            "source": "components/project/test-guidance.md", "delivery": "copy",
            "destination": ".vibe-coding-bootstrap/components/test-guidance.md", "action": "include",
            "configuration_preview": {}, "dependencies": [], "conflicts": [],
        }
        plan_v1 = assemble(load_yaml(self.vcbs_root / "examples/one-off-experiment/recommendation.yaml"), root=self.vcbs_root)
        plan_v1["components"] = [item]
        applied_v1 = materialize(plan_v1, self.repo, apply=True, root=self.vcbs_root)
        self.assertEqual("copied", applied_v1["items"][0]["result"])
        target = self.repo / ".vibe-coding-bootstrap/components/test-guidance.md"
        v1_content = target.read_bytes()
        v1_sha = _sha(v1_content)

        # 2. Update source in VCBS to v2
        source_path = self.vcbs_root / "components/project/test-guidance.md"
        source_path.write_text("# Updated v2 Guidance\nNew testing recommendations.\n", encoding="utf-8")
        v2_sha = _sha(source_path.read_bytes())
        self.assertNotEqual(v1_sha, v2_sha)

        # 3. Dry run with v2 assembly plan
        item_v2 = dict(item, capability_version="2.0")
        plan_v2 = dict(plan_v1, components=[item_v2])
        dry_v2 = materialize(plan_v2, self.repo, apply=False, root=self.vcbs_root)
        result_item = dry_v2["items"][0]
        self.assertEqual("upgraded", result_item["result"])
        self.assertEqual(v1_sha, result_item["old_sha256"])
        self.assertEqual(v2_sha, result_item["sha256"])
        self.assertEqual("1.0", result_item["old_version"])
        self.assertEqual("2.0", result_item["new_version"])

        # 4. Apply v2 upgrade
        applied_v2 = materialize(plan_v2, self.repo, apply=True, root=self.vcbs_root)
        self.assertEqual("upgraded", applied_v2["items"][0]["result"])
        self.assertEqual(_sha(target.read_bytes()), v2_sha)

        # 5. Repeat apply is idempotent unchanged
        repeat = materialize(plan_v2, self.repo, apply=True, root=self.vcbs_root)
        self.assertEqual("unchanged", repeat["items"][0]["result"])

    def test_materializer_modified_managed_component_is_conflict(self):
        # 1. Materialize v1
        item = {
            "component_id": "test-guidance", "component_type": "document", "source_capability": "testing",
            "capability_version": "1.0", "reason": "Guidance", "scope": "project",
            "source": "components/project/test-guidance.md", "delivery": "copy",
            "destination": ".vibe-coding-bootstrap/components/test-guidance.md", "action": "include",
            "configuration_preview": {}, "dependencies": [], "conflicts": [],
        }
        plan = assemble(load_yaml(self.vcbs_root / "examples/one-off-experiment/recommendation.yaml"), root=self.vcbs_root)
        plan["components"] = [item]
        materialize(plan, self.repo, apply=True, root=self.vcbs_root)

        # 2. User modifies the file
        target = self.repo / ".vibe-coding-bootstrap/components/test-guidance.md"
        target.write_text("# User customized guidance\nDo not overwrite me!\n", encoding="utf-8")

        # 3. Source in VCBS updates to v2
        source_path = self.vcbs_root / "components/project/test-guidance.md"
        source_path.write_text("# Updated v2 Guidance\n", encoding="utf-8")

        # 4. Materialize detects conflict
        res = materialize(plan, self.repo, apply=True, root=self.vcbs_root)
        self.assertEqual("conflict", res["items"][0]["result"])
        self.assertIn("was modified", res["items"][0]["reason"])
        self.assertIn("User customized", target.read_text(encoding="utf-8"))

    def test_codex_activation_upgrade_and_conflict(self):
        # 1. Fresh install of Codex activation
        plan = activation.build_plan(self.repo, "testing", "codex", root=self.vcbs_root)
        self.assertEqual("ready", plan["status"])
        dec = activation.make_decision(plan, "approve")
        res = activation.apply(plan, dec, self.repo, root=self.vcbs_root)
        self.assertEqual("activated", res["status"])

        # 2. Update skill source in VCBS
        skill_src = self.vcbs_root / activation.SKILL_SOURCE
        skill_src.write_text("# V2 Codex Testing Skill\nUpdated instructions.\n", encoding="utf-8")
        new_sha = _sha(skill_src.read_bytes())

        # 3. Build plan recognizes upgrade
        plan_v2 = activation.build_plan(self.repo, "testing", "codex", root=self.vcbs_root)
        self.assertEqual("ready", plan_v2["status"])
        self.assertTrue(plan_v2.get("is_upgrade"))
        self.assertEqual(new_sha, plan_v2.get("new_skill_sha256"))

        # 4. Apply upgrade
        dec_v2 = activation.make_decision(plan_v2, "approve")
        applied_v2 = activation.apply(plan_v2, dec_v2, self.repo, root=self.vcbs_root)
        self.assertEqual("activated", applied_v2["status"])
        dest = self.repo / activation.SKILL_DESTINATION
        self.assertEqual(_sha(dest.read_bytes()), new_sha)

        # 5. Repeat is already_active
        repeat = activation.build_plan(self.repo, "testing", "codex", root=self.vcbs_root)
        self.assertEqual("already_active", repeat["status"])

        # 6. User modifies skill -> conflict
        dest.write_text("User edits skill", encoding="utf-8")
        skill_src.write_text("# V3 Skill", encoding="utf-8")
        plan_v3 = activation.build_plan(self.repo, "testing", "codex", root=self.vcbs_root)
        self.assertEqual("conflict", plan_v3["status"])

    def test_product_loop_full_upgrade_and_rollback(self):
        global_root = self.base / "global"
        # Step 1: Initial deployment (v1)
        plan1 = build_plan(self.repo, host="codex", context_path=self.context_file, global_root=global_root, root=self.vcbs_root)
        dec1 = decide(plan1, "approve")
        applied1 = apply_plan(plan1, dec1, self.repo, global_root=global_root, root=self.vcbs_root)
        self.assertEqual("applied", applied1["status"])
        self.assertTrue((self.repo / activation.SKILL_DESTINATION).is_file())
        v1_skill_content = (self.repo / activation.SKILL_DESTINATION).read_bytes()
        v1_guide_content = (self.repo / ".vibe-coding-bootstrap/components/test-guidance.md").read_bytes()

        # Step 2: VCBS changes component and skill sources to v2
        (self.vcbs_root / "components/project/test-guidance.md").write_text("# Test Guidance v2\n", encoding="utf-8")
        (self.vcbs_root / activation.SKILL_SOURCE).write_text("# Codex Skill v2\n", encoding="utf-8")

        # Step 3: Inspect recognizes compose
        inspected = inspect_project(self.repo, host="codex", root=self.vcbs_root)
        self.assertEqual("compose", inspected["route"])

        # Step 4: Plan displays upgrade plan
        plan2 = build_plan(self.repo, host="codex", context_path=self.context_file, global_root=global_root, root=self.vcbs_root)
        summary = render_summary(plan2)
        self.assertIn("Upgrades:", summary)
        self.assertGreater(plan2["actual_changes"], 0)
        self.assertTrue(plan2["requires_confirmation"])

        # Step 5: User approves upgrade
        dec2 = decide(plan2, "approve")
        applied2 = apply_plan(plan2, dec2, self.repo, global_root=global_root, root=self.vcbs_root)
        self.assertEqual("applied", applied2["status"])
        self.assertEqual((self.repo / activation.SKILL_DESTINATION).read_text(encoding="utf-8"), "# Codex Skill v2\n")
        self.assertEqual((self.repo / ".vibe-coding-bootstrap/components/test-guidance.md").read_text(encoding="utf-8"), "# Test Guidance v2\n")

        # Step 6: Repeat run is no-op
        plan3 = build_plan(self.repo, host="codex", context_path=self.context_file, global_root=global_root, root=self.vcbs_root)
        self.assertEqual(0, plan3["actual_changes"])
        self.assertFalse(plan3["requires_confirmation"])
        noop = apply_plan(plan3, None, self.repo, global_root=global_root, root=self.vcbs_root)
        self.assertEqual("no_changes", noop["status"])

        # Step 7: Rollback restored v1
        dep_id = applied2["deployment_id"]
        rb = rollback(self.repo, dep_id, apply=True)
        self.assertEqual("rolled_back", rb["status"])
        self.assertEqual(v1_skill_content, (self.repo / activation.SKILL_DESTINATION).read_bytes())
        self.assertEqual(v1_guide_content, (self.repo / ".vibe-coding-bootstrap/components/test-guidance.md").read_bytes())


if __name__ == "__main__":
    unittest.main()
