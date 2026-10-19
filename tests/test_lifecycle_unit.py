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
from bootstrap import __version__
from bootstrap.__main__ import _parser, main
from bootstrap.core import ProductLoopError, ROOT, decide, apply_plan
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

    def _deploy_initial(self):
        p = deploy(self.project, host="codex", context_path=self.context_file,
                   global_root=self.global_root, apply=False, root=self.vcbs_root)
        return deploy(self.project, host="codex", context_path=self.context_file,
                      global_root=self.global_root, apply=True, decision=decide(p["plan"], "approve"),
                      plan=p["plan"], root=self.vcbs_root)

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

    def test_deploy_preview_and_apply_with_decision(self):
        """Deploy without apply previews; apply without decision raises DECISION_REQUIRED; with decision installs cleanly."""
        # 1. Preview
        prev = deploy(self.project, host="codex", context_path=self.context_file,
                      global_root=self.global_root, apply=False, root=self.vcbs_root)
        self.assertEqual("preview", prev["status"])
        self.assertIn("summary", prev)
        self.assertFalse((self.project / ".vibe-coding-bootstrap/manifest.yaml").exists())

        # 2. Apply without decision fails closed
        with self.assertRaises(ProductLoopError) as ctx:
            deploy(self.project, host="codex", context_path=self.context_file,
                   global_root=self.global_root, apply=True, decision=None, plan=prev["plan"], root=self.vcbs_root)
        self.assertIn("DECISION_REQUIRED", str(ctx.exception))

        # 3. Apply with approved decision succeeds
        dec = decide(prev["plan"], "approve")
        res = deploy(self.project, host="codex", context_path=self.context_file,
                     global_root=self.global_root, apply=True, decision=dec, plan=prev["plan"], root=self.vcbs_root)
        self.assertEqual("applied", res["status"])
        self.assertTrue((self.project / ".vibe-coding-bootstrap/manifest.yaml").is_file())
        self.assertTrue((self.project / activation.MANIFEST_PATH).is_file())
        self.assertTrue((self.project / activation.SKILL_DESTINATION).is_file())

        # 4. Status after deploy
        st = get_status(self.project, host="codex", global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual("installed", st["status"])
        self.assertTrue(st["deployed"])
        self.assertEqual("active", st["codex_activation"]["status"])
        self.assertFalse(st["has_conflicts"])

        # 5. Repeated deploy returns no_changes
        repeat = deploy(self.project, host="codex", context_path=self.context_file,
                        global_root=self.global_root, apply=True, root=self.vcbs_root)
        self.assertEqual("no_changes", repeat["status"])

    def test_upgrade_flow_and_no_changes(self):
        """Deploy v1, update VCBS source to v2, upgrade requires decision, then repeat is no_changes."""
        # Initial deploy
        self._deploy_initial()

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

        # Upgrade without decision fails closed
        with self.assertRaises(ProductLoopError) as ctx:
            upgrade(self.project, host="codex", global_root=self.global_root,
                    apply=True, decision=None, plan=up_prev["plan"], root=self.vcbs_root)
        self.assertIn("DECISION_REQUIRED", str(ctx.exception))

        # Upgrade with decision succeeds
        up_dec = decide(up_prev["plan"], "approve")
        up_res = upgrade(self.project, host="codex", global_root=self.global_root,
                         apply=True, decision=up_dec, plan=up_prev["plan"], root=self.vcbs_root)
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
        self._deploy_initial()

        # Add user file
        user_file = self.project / "my_custom_code.py"
        user_file.write_text("print('user code')\n")

        # Uninstall preview
        u_prev = uninstall(self.project, global_root=self.global_root, apply=False, root=self.vcbs_root)
        self.assertEqual("preview", u_prev["status"])
        self.assertGreater(len(u_prev["plan"]["removals"]), 0)

        # Uninstall without decision fails closed
        with self.assertRaises(ProductLoopError) as ctx:
            uninstall(self.project, global_root=self.global_root, apply=True, decision=None, plan=u_prev["plan"], root=self.vcbs_root)
        self.assertIn("DECISION_REQUIRED", str(ctx.exception))

        # Uninstall apply with decision
        u_dec = decide_uninstall(u_prev["plan"], "approve")
        u_res = uninstall(self.project, global_root=self.global_root, apply=True, decision=u_dec, plan=u_prev["plan"], root=self.vcbs_root)
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
        self._deploy_initial()

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

        # Skill file was NOT deleted
        self.assertTrue(skill.is_file())
        self.assertEqual("# User edited this skill directly\n", skill.read_text())

    def test_corrupted_manifest_fails_safely(self):
        """Corrupted manifest.yaml prevents unauthorized operations."""
        self._deploy_initial()

        # Corrupt the manifest
        (self.project / ".vibe-coding-bootstrap/manifest.yaml").write_text("corrupted: yaml: content: [", encoding="utf-8")

        # Status reports conflict
        st = get_status(self.project, host="codex", global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual("conflict", st["status"])

        # Uninstall is blocked
        plan = build_uninstall_plan(self.project, global_root=self.global_root, root=self.vcbs_root)
        self.assertFalse(plan["executable"])

    def test_manifest_drift_after_approval_blocks_apply_uninstall(self):
        """If manifest.yaml drifts after approval, apply_uninstall rejects execution."""
        self._deploy_initial()

        # Build plan and approve
        plan = build_uninstall_plan(self.project, global_root=self.global_root, root=self.vcbs_root)
        dec = decide_uninstall(plan, "approve")

        # Modify manifest.yaml directly to create drift
        mpath = self.project / ".vibe-coding-bootstrap/manifest.yaml"
        mdata = yaml.safe_load(mpath.read_text(encoding="utf-8"))
        mdata["items"][0]["sha256"] = "0" * 64
        mpath.write_text(yaml.safe_dump(mdata), encoding="utf-8")

        with self.assertRaises(ProductLoopError) as ctx:
            apply_uninstall(plan, dec, self.project, global_root=self.global_root, root=self.vcbs_root)
        self.assertIn("STALE_BOOTSTRAP_PLAN", str(ctx.exception))

    def test_global_root_verification_in_apply_uninstall(self):
        """apply_uninstall validates that actual global root matches planned global root exactly."""
        self._deploy_initial()

        plan = build_uninstall_plan(self.project, global_root=self.global_root, root=self.vcbs_root)
        dec = decide_uninstall(plan, "approve")

        # 1. Missing global root when plan has global components
        with self.assertRaises(ProductLoopError) as ctx:
            apply_uninstall(plan, dec, self.project, global_root=None, root=self.vcbs_root)
        self.assertIn("GLOBAL_ROOT_REQUIRED", str(ctx.exception))

        # 2. Mismatched global root
        other_global = self.base / "other_global"
        other_global.mkdir()
        with self.assertRaises(ProductLoopError) as ctx:
            apply_uninstall(plan, dec, self.project, global_root=other_global, root=self.vcbs_root)
        self.assertIn("GLOBAL_ROOT_MISMATCH", str(ctx.exception))

    def test_partial_uninstall_preserves_remaining_components(self):
        """Partial uninstall of a specific component leaves unselected components and manifest intact."""
        self._deploy_initial()

        # Target only project component project-test-guidance
        plan = build_uninstall_plan(self.project, component_id="project-test-guidance",
                                    global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual(1, len(plan["removals"]))
        self.assertGreater(len(plan["remaining_manifest_items"]), 0)

        dec = decide_uninstall(plan, "approve")
        res = apply_uninstall(plan, dec, self.project, global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual("applied", res["status"])

        # test-guidance removed
        self.assertFalse((self.project / ".vibe-coding-bootstrap/components/test-guidance.md").exists())

        # Codex activation and global components preserved
        self.assertTrue((self.project / activation.SKILL_DESTINATION).is_file())
        self.assertTrue((self.global_root / "components/reviewable-changes.md").is_file())

        # manifest.yaml exists and still records remaining components
        mpath = self.project / ".vibe-coding-bootstrap/manifest.yaml"
        self.assertTrue(mpath.is_file())
        mdata = yaml.safe_load(mpath.read_text(encoding="utf-8"))
        self.assertFalse(any(it["component_id"] == "project-test-guidance" for it in mdata["items"]))
        self.assertTrue(any(it["component_id"] == "reviewable-changes" for it in mdata["items"]))

    def _run_cli(self, *args, cwd=None):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        return subprocess.run(
            [sys.executable, "-m", "bootstrap", *map(str, args)],
            cwd=str(cwd or self.project),
            env=env,
            capture_output=True,
            text=True,
        )

    def _run_vcbs(self, *args, cwd=None):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        return subprocess.run(
            [sys.executable, str(ROOT / "vcbs"), *map(str, args)],
            cwd=str(cwd or self.project),
            env=env,
            capture_output=True,
            text=True,
        )

    def test_cli_version_flag(self):
        """Top-level --version flag displays 0.1.0 and exits 0 via python -m bootstrap and ./vcbs."""
        # 0. Package __version__
        self.assertEqual("0.1.0", __version__)

        # 1. python -m bootstrap --version
        res_m = self._run_cli("--version")
        self.assertEqual(0, res_m.returncode)
        self.assertEqual("0.1.0", res_m.stdout.strip())

        # 2. ./vcbs --version
        res_v = self._run_vcbs("--version")
        self.assertEqual(0, res_v.returncode)
        self.assertEqual("0.1.0", res_v.stdout.strip())

    def test_cli_status_default_project_root(self):
        """python -m bootstrap status without --project-root defaults to CWD and inspects repository."""
        # 1. In uninstalled repository
        res = self._run_cli("status")
        self.assertEqual(0, res.returncode)
        self.assertIn("VCBS Status: NOT_INSTALLED", res.stdout)
        self.assertIn(str(self.project), res.stdout)

        # 2. In uninstalled repository with --json
        res_json = self._run_cli("status", "--json")
        self.assertEqual(0, res_json.returncode)
        data = json.loads(res_json.stdout)
        self.assertEqual("not_installed", data["status"])
        self.assertEqual(str(self.project), data["project_root"])

        # 3. Via ./vcbs wrapper
        res_vcbs = self._run_vcbs("status")
        self.assertEqual(0, res_vcbs.returncode)
        self.assertIn("VCBS Status: NOT_INSTALLED", res_vcbs.stdout)

        # 4. In deployed repository
        self._deploy_initial()
        res_deployed = self._run_cli("status", "--global-root", str(self.global_root))
        self.assertEqual(0, res_deployed.returncode)
        self.assertIn("VCBS Status: INSTALLED", res_deployed.stdout)
        self.assertIn(str(self.project), res_deployed.stdout)

    def test_lifecycle_subcommands_parser_defaults(self):
        """Lifecycle subcommands default --project-root to '.' and diagnostic subcommands require it."""
        parser = _parser()

        # Lifecycle subcommands default to "."
        self.assertEqual(".", parser.parse_args(["status"]).project_root)
        self.assertEqual(".", parser.parse_args(["deploy"]).project_root)
        self.assertEqual(".", parser.parse_args(["upgrade"]).project_root)
        self.assertEqual(".", parser.parse_args(["uninstall"]).project_root)

        # Diagnostic subcommands still require --project-root
        res_inspect = self._run_cli("inspect")
        self.assertEqual(2, res_inspect.returncode)
        self.assertIn("--project-root", res_inspect.stderr)

        res_plan = self._run_cli("plan")
        self.assertEqual(2, res_plan.returncode)
        self.assertIn("--project-root", res_plan.stderr)

