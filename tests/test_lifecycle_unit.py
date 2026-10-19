"""Unit tests for VCBS Phase 12 standardized lifecycle operations."""

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
from bootstrap.core import ProductLoopError, ROOT
from bootstrap.lifecycle import (
    apply_uninstall, build_uninstall_plan, decide_uninstall, deploy,
    get_status, render_status_summary, render_uninstall_summary,
    uninstall, upgrade
)
from bootstrap.recovery import list_deployments, rollback
from composer.core import load_yaml, validate


class LifecycleUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name).resolve()

        # Isolated VCBS root copy
        self.vcbs_root = self.base / "vcbs"
        self.vcbs_root.mkdir()
        for name in ["schemas", "capabilities", "composer", "profiles", "experiences", "components", "host_adapters", "assembly", "examples", "adoption"]:
            src = ROOT / name
            if src.is_dir():
                shutil.copytree(src, self.vcbs_root / name)

        # Create disposable consumer project
        self.project = self.base / "consumer"
        self.project.mkdir()
        self._git(self.project, "init", "-q")
        self._git(self.project, "config", "user.email", "dev@example.invalid")
        self._git(self.project, "config", "user.name", "Dev")
        (self.project / "README.md").write_text("# Consumer Project\n")
        (self.project / "app.py").write_text("print('hello')\n")
        self._git(self.project, "add", ".")
        self._git(self.project, "commit", "-qm", "Initial commit")

        self.global_root = self.base / "global"
        self.global_root.mkdir()

        self.context = {
            "name": "consumer",
            "description": "Disposable test project",
            "project_type": "software-project",
            "lifecycle": "production",
            "technology_tags": ["Python"],
            "runtime": "Python",
            "platform": "local",
            "goal": "Test lifecycle",
            "constraints": ["AI coding host is Codex"],
            "collaboration": "solo",
        }
        self.context_file = self.base / "context.yaml"
        self.context_file.write_text(yaml.safe_dump(self.context), encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _git(self, project, *args):
        return subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True, text=True).stdout.strip()

    def test_status_uninstalled_repo(self):
        """Status on a fresh repo returns not_installed with accurate git and route info."""
        status = get_status(self.project, host="codex", root=self.vcbs_root)
        self.assertEqual("not_installed", status["status"])
        self.assertFalse(status["deployed"])
        self.assertEqual([], status["components"])
        self.assertEqual("not_active", status["codex_activation"]["status"])
        self.assertFalse(status["has_conflicts"])
        self.assertEqual("compose", status["route_prediction"])
        self.assertFalse(status["target"]["dirty"])
        summary = render_status_summary(status)
        self.assertIn("VCBS Status: NOT_INSTALLED", summary)
        self.assertIn("Managed Components: none", summary)

    def test_status_detects_unmanaged_ai_assets(self):
        """Pre-existing AI configs are detected as unmanaged assets and predict adoption route."""
        (self.project / "CLAUDE.md").write_text("# Claude instructions\n")
        status = get_status(self.project, host="codex", root=self.vcbs_root)
        self.assertIn("CLAUDE.md", status["unmanaged_assets"])
        self.assertEqual("adoption", status["route_prediction"])
        summary = render_status_summary(status)
        self.assertIn("Unmanaged Pre-existing Environment Assets:", summary)
        self.assertIn("CLAUDE.md", summary)

    def test_deploy_preview_and_apply(self):
        """Deploy without apply previews; with apply installs components and activation."""
        # 1. Preview
        prev = deploy(self.project, host="codex", context_path=self.context_file,
                      global_root=self.global_root, apply=False, root=self.vcbs_root)
        self.assertEqual("preview", prev["status"])
        self.assertIn("summary", prev)
        self.assertFalse((self.project / ".vibe-coding-bootstrap/manifest.yaml").exists())

        # 2. Apply
        res = deploy(self.project, host="codex", context_path=self.context_file,
                     global_root=self.global_root, apply=True, root=self.vcbs_root)
        self.assertEqual("applied", res["status"])
        self.assertTrue((self.project / ".vibe-coding-bootstrap/manifest.yaml").is_file())
        self.assertTrue((self.project / activation.MANIFEST_PATH).is_file())
        self.assertTrue((self.project / activation.SKILL_DESTINATION).is_file())

        # 3. Status after deploy
        st = get_status(self.project, host="codex", global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual("installed", st["status"])
        self.assertTrue(st["deployed"])
        self.assertEqual("active", st["codex_activation"]["status"])
        self.assertFalse(st["has_conflicts"])

        # 4. Repeated deploy returns no_changes
        repeat = deploy(self.project, host="codex", context_path=self.context_file,
                        global_root=self.global_root, apply=True, root=self.vcbs_root)
        self.assertEqual("no_changes", repeat["status"])

    def test_upgrade_flow_and_no_changes(self):
        """Deploy v1, update VCBS source to v2, upgrade applies cleanly, then repeat is no_changes."""
        deploy(self.project, host="codex", context_path=self.context_file,
               global_root=self.global_root, apply=True, root=self.vcbs_root)

        # Mutate VCBS testing capability to v2
        (self.vcbs_root / activation.SKILL_SOURCE).write_text("# Skill v2\nUpdated.\n", encoding="utf-8")
        guide_src = self.vcbs_root / "components/project/test-guidance.md"
        guide_src.write_text("# Guidance v2\nUpdated.\n", encoding="utf-8")

        # Status detects update available
        st = get_status(self.project, host="codex", global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual("update_available", st["status"])
        self.assertTrue(st["update_available"])

        # Upgrade preview
        up_prev = upgrade(self.project, host="codex", global_root=self.global_root,
                          apply=False, root=self.vcbs_root)
        self.assertEqual("preview", up_prev["status"])
        self.assertIn("Upgrades:", up_prev["summary"])

        # Upgrade apply
        up_res = upgrade(self.project, host="codex", global_root=self.global_root,
                         apply=True, root=self.vcbs_root)
        self.assertEqual("applied", up_res["status"])
        self.assertEqual("# Skill v2\nUpdated.\n", (self.project / activation.SKILL_DESTINATION).read_text(encoding="utf-8"))

        # Status is now installed and up to date
        st2 = get_status(self.project, host="codex", global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual("installed", st2["status"])
        self.assertFalse(st2["update_available"])

        # Repeat upgrade is no_changes
        up_repeat = upgrade(self.project, host="codex", global_root=self.global_root,
                            apply=True, root=self.vcbs_root)
        self.assertEqual("no_changes", up_repeat["status"])

    def test_uninstall_flow_removes_assets_and_preserves_user_files(self):
        """Uninstall cleanly removes VCBS assets and manifests without touching user files."""
        # Deploy
        deploy(self.project, host="codex", context_path=self.context_file,
               global_root=self.global_root, apply=True, root=self.vcbs_root)

        # Add user file
        user_file = self.project / "my_custom_code.py"
        user_file.write_text("print('user code')\n")

        # Uninstall preview
        u_prev = uninstall(self.project, global_root=self.global_root, apply=False, root=self.vcbs_root)
        self.assertEqual("preview", u_prev["status"])
        self.assertGreater(len(u_prev["plan"]["removals"]), 0)

        # Uninstall apply
        u_res = uninstall(self.project, global_root=self.global_root, apply=True, root=self.vcbs_root)
        self.assertEqual("applied", u_res["status"])

        # Verification: VCBS files removed
        self.assertFalse((self.project / ".vibe-coding-bootstrap/manifest.yaml").exists())
        self.assertFalse((self.project / activation.MANIFEST_PATH).exists())
        self.assertFalse((self.project / activation.SKILL_DESTINATION).exists())
        self.assertFalse((self.project / ".vibe-coding-bootstrap").exists())

        # User files and git history preserved
        self.assertTrue(user_file.is_file())
        self.assertEqual("print('user code')\n", user_file.read_text())
        self.assertTrue((self.project / "README.md").is_file())
        self.assertTrue((self.project / "app.py").is_file())

        # Status shows uninstalled
        st = get_status(self.project, host="codex", global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual("uninstalled", st["status"])
        self.assertFalse(st["deployed"])

        # Repeat uninstall is no_changes
        u_repeat = uninstall(self.project, global_root=self.global_root, apply=True, root=self.vcbs_root)
        self.assertEqual("no_changes", u_repeat["status"])

    def test_user_modified_file_blocks_uninstall(self):
        """If user modified an installed managed file, uninstall detects conflict and refuses to delete."""
        deploy(self.project, host="codex", context_path=self.context_file,
               global_root=self.global_root, apply=True, root=self.vcbs_root)

        # User modifies skill file
        skill = self.project / activation.SKILL_DESTINATION
        skill.write_text("# User edited this skill directly\n")

        # Status detects conflict
        st = get_status(self.project, host="codex", global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual("conflict", st["status"])
        self.assertTrue(st["has_conflicts"])

        # Uninstall preview flags conflict and marks executable False
        u_prev = uninstall(self.project, global_root=self.global_root, apply=False, root=self.vcbs_root)
        self.assertFalse(u_prev["plan"]["executable"])
        self.assertGreater(len(u_prev["plan"]["conflicts"]), 0)

        # Applying uninstall raises ProductLoopError
        with self.assertRaises(ProductLoopError) as ctx:
            uninstall(self.project, global_root=self.global_root, apply=True, root=self.vcbs_root)
        self.assertIn("PLAN_BLOCKED", str(ctx.exception))

        # Skill file was NOT deleted
        self.assertTrue(skill.is_file())
        self.assertEqual("# User edited this skill directly\n", skill.read_text())

    def test_corrupted_manifest_fails_safely(self):
        """Corrupted manifest.yaml prevents unauthorized operations."""
        deploy(self.project, host="codex", context_path=self.context_file,
               global_root=self.global_root, apply=True, root=self.vcbs_root)

        # Corrupt the manifest
        (self.project / ".vibe-coding-bootstrap/manifest.yaml").write_text("corrupted: yaml: content: [", encoding="utf-8")

        # Status reports conflict
        st = get_status(self.project, host="codex", global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual("conflict", st["status"])

        # Uninstall is blocked
        plan = build_uninstall_plan(self.project, global_root=self.global_root, root=self.vcbs_root)
        self.assertFalse(plan["executable"])
