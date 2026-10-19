"""End-to-end acceptance tests for VCBS Phase 12 standardized lifecycle operations.

Covers:
- Acceptance A: Standardized First-Time Deployment and Idempotence
- Acceptance B: Standardized Safe Upgrade and Content Diff
- Acceptance C: Complete Safe Uninstall and Residual Protection
- Acceptance D: Exact-Byte Rollback for Deploy, Upgrade, and Uninstall
- Acceptance E: User-Modified Conflict Protections and Refusal to Overwrite/Delete
- Acceptance F: Approval Binding and Manifest/Global Root Drift Protections
- Acceptance G: Existing Project Adoption Preservation (Non-destructive)
- Acceptance H: Project and Global Scope Materialization & Safe Removal
- Acceptance I: CLI Subcommands (python -m bootstrap) and ./vcbs wrapper
"""

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
from bootstrap.core import ProductLoopError, ROOT, decide, apply_plan
from bootstrap.lifecycle import (
    apply_uninstall, build_uninstall_plan, decide_uninstall, deploy,
    get_status, render_status_summary, render_uninstall_summary,
    uninstall, upgrade
)
from bootstrap.recovery import list_deployments, rollback
from composer.core import load_yaml, validate


class Phase12E2EAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name).resolve()

        # Copy VCBS codebase to isolated test root
        self.vcbs_root = self.base / "vcbs"
        self.vcbs_root.mkdir()
        for name in ["schemas", "capabilities", "composer", "profiles", "experiences",
                     "components", "host_adapters", "assembly", "examples", "adoption", "bootstrap",
                     "materializer", "activation"]:
            src = ROOT / name
            if src.is_dir():
                shutil.copytree(src, self.vcbs_root / name)
        # Copy root wrapper
        shutil.copy2(ROOT / "vcbs", self.vcbs_root / "vcbs")
        os.chmod(self.vcbs_root / "vcbs", 0o755)

        # Create disposable consumer project
        self.project = self.base / "consumer"
        self.project.mkdir()
        self._git(self.project, "init", "-q")
        self._git(self.project, "config", "user.email", "e2e@example.invalid")
        self._git(self.project, "config", "user.name", "E2E User")
        (self.project / "README.md").write_text("# Test Consumer Repository\n")
        (self.project / "main.py").write_text("def run():\n    return 42\n")
        self._git(self.project, "add", ".")
        self._git(self.project, "commit", "-qm", "Initial commit")

        self.global_root = self.base / "global"
        self.global_root.mkdir()

        self.context = {
            "name": "consumer",
            "description": "Disposable test project for Phase 12",
            "project_type": "software-project",
            "lifecycle": "production",
            "technology_tags": ["Python"],
            "runtime": "Python",
            "platform": "local",
            "goal": "Verify Phase 12 standardized lifecycle",
            "constraints": ["AI coding host is Codex"],
            "collaboration": "solo",
        }
        self.context_file = self.base / "context.yaml"
        self.context_file.write_text(yaml.safe_dump(self.context), encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _git(self, project, *args):
        return subprocess.run(
            ["git", "-C", str(project), *args],
            check=True, capture_output=True, text=True
        ).stdout.strip()

    def _run_cli(self, *args, check=True):
        env = dict(os.environ, PYTHONPATH=str(self.vcbs_root))
        result = subprocess.run(
            [sys.executable, "-m", "bootstrap", *map(str, args)],
            cwd=self.vcbs_root, env=env, capture_output=True, text=True
        )
        if check and result.returncode != 0:
            self.fail(f"CLI command failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}")
        return result

    def _run_vcbs_wrapper(self, *args, check=True):
        env = dict(os.environ, PYTHONPATH=str(self.vcbs_root))
        result = subprocess.run(
            [str(self.vcbs_root / "vcbs"), *map(str, args)],
            cwd=self.vcbs_root, env=env, capture_output=True, text=True
        )
        if check and result.returncode != 0:
            self.fail(f"vcbs wrapper failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}")
        return result

    def _approve_and_deploy(self, **kwargs):
        prev = deploy(apply=False, **kwargs)
        if prev.get("status") == "no_changes":
            return prev
        dec = decide(prev["plan"], "approve")
        return deploy(apply=True, decision=dec, plan=prev["plan"], **kwargs)

    def _approve_and_upgrade(self, **kwargs):
        prev = upgrade(apply=False, **kwargs)
        if prev.get("status") == "no_changes":
            return prev
        dec = decide(prev["plan"], "approve")
        return upgrade(apply=True, decision=dec, plan=prev["plan"], **kwargs)

    def _approve_and_uninstall(self, **kwargs):
        prev = uninstall(apply=False, **kwargs)
        if prev.get("status") == "no_changes":
            return prev
        dec = decide_uninstall(prev["plan"], "approve")
        return uninstall(apply=True, decision=dec, plan=prev["plan"], **kwargs)

    def test_acceptance_a_deploy_and_idempotence(self):
        """Acceptance A: Clean git project -> status not_installed -> deploy preview -> apply -> installed -> repeat deploy no_changes."""
        # 1. Initial status is not_installed
        st = get_status(self.project, host="codex", global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual("not_installed", st["status"])
        self.assertFalse(st["deployed"])

        # 2. Deploy preview does not write
        prev = deploy(self.project, host="codex", context_path=self.context_file,
                      global_root=self.global_root, apply=False, root=self.vcbs_root)
        self.assertEqual("preview", prev["status"])
        self.assertFalse((self.project / ".vibe-coding-bootstrap").exists())

        # 3. Apply deployment requires explicit decision
        with self.assertRaises(ProductLoopError) as ctx:
            deploy(self.project, host="codex", context_path=self.context_file,
                   global_root=self.global_root, apply=True, decision=None, plan=prev["plan"], root=self.vcbs_root)
        self.assertIn("DECISION_REQUIRED", str(ctx.exception))

        dec = decide(prev["plan"], "approve")
        res = deploy(self.project, host="codex", context_path=self.context_file,
                     global_root=self.global_root, apply=True, decision=dec, plan=prev["plan"], root=self.vcbs_root)
        self.assertEqual("applied", res["status"])
        self.assertTrue((self.project / ".vibe-coding-bootstrap/manifest.yaml").is_file())
        self.assertTrue((self.project / activation.MANIFEST_PATH).is_file())
        self.assertTrue((self.project / activation.SKILL_DESTINATION).is_file())
        self.assertTrue((self.global_root / "components/reviewable-changes.md").is_file())

        # 4. Status reflects installed state
        st2 = get_status(self.project, host="codex", global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual("installed", st2["status"])
        self.assertTrue(st2["deployed"])
        self.assertFalse(st2["update_available"])
        self.assertFalse(st2["has_conflicts"])

        # 5. Repeated deploy returns no_changes
        repeat = deploy(self.project, host="codex", context_path=self.context_file,
                        global_root=self.global_root, apply=True, root=self.vcbs_root)
        self.assertEqual("no_changes", repeat["status"])

    def test_acceptance_b_safe_upgrade_and_idempotence(self):
        """Acceptance B: Deploy v1 -> mutate to v2 -> upgrade preview shows old/new -> apply v2 -> installed -> repeat no_changes."""
        self._approve_and_deploy(project_root=self.project, host="codex", context_path=self.context_file,
                                 global_root=self.global_root, root=self.vcbs_root)

        skill_dest = self.project / activation.SKILL_DESTINATION
        v1_skill_bytes = skill_dest.read_bytes()

        # Update VCBS source for Skill and test guidance to v2
        (self.vcbs_root / activation.SKILL_SOURCE).write_text("# Skill v2\nAcceptance B.\n", encoding="utf-8")
        guide_src = self.vcbs_root / "components/project/test-guidance.md"
        guide_src.write_text("# Guidance v2\nAcceptance B.\n", encoding="utf-8")

        # Status detects update available
        st = get_status(self.project, host="codex", global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual("update_available", st["status"])
        self.assertTrue(st["update_available"])

        # Upgrade preview shows old and new
        up_prev = upgrade(self.project, host="codex", global_root=self.global_root,
                          apply=False, root=self.vcbs_root)
        self.assertEqual("preview", up_prev["status"])
        self.assertIn("Upgrades:", up_prev["summary"])

        # Execute upgrade requires decision
        with self.assertRaises(ProductLoopError) as ctx:
            upgrade(self.project, host="codex", global_root=self.global_root,
                    apply=True, decision=None, plan=up_prev["plan"], root=self.vcbs_root)
        self.assertIn("DECISION_REQUIRED", str(ctx.exception))

        up_dec = decide(up_prev["plan"], "approve")
        up_res = upgrade(self.project, host="codex", global_root=self.global_root,
                         apply=True, decision=up_dec, plan=up_prev["plan"], root=self.vcbs_root)
        self.assertEqual("applied", up_res["status"])
        self.assertEqual("# Skill v2\nAcceptance B.\n", skill_dest.read_text(encoding="utf-8"))

        # Status after upgrade is installed and up-to-date
        st2 = get_status(self.project, host="codex", global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual("installed", st2["status"])
        self.assertFalse(st2["update_available"])

        # Repeat upgrade is no_changes
        repeat = upgrade(self.project, host="codex", global_root=self.global_root,
                         apply=True, root=self.vcbs_root)
        self.assertEqual("no_changes", repeat["status"])

    def test_acceptance_c_complete_safe_uninstall(self):
        """Acceptance C: Deploy -> uninstall preview -> apply -> all VCBS components removed -> user files untouched -> uninstalled -> repeat no_changes."""
        self._approve_and_deploy(project_root=self.project, host="codex", context_path=self.context_file,
                                 global_root=self.global_root, root=self.vcbs_root)

        # Verify deployment present
        skill_dest = self.project / activation.SKILL_DESTINATION
        guide_dest = self.project / ".vibe-coding-bootstrap/components/test-guidance.md"
        global_dest = self.global_root / "components/reviewable-changes.md"
        self.assertTrue(skill_dest.is_file())
        self.assertTrue(guide_dest.is_file())
        self.assertTrue(global_dest.is_file())

        # Add user files
        user_file = self.project / "user_service.py"
        user_file.write_text("class UserService: pass\n")

        # Uninstall preview
        u_prev = uninstall(self.project, global_root=self.global_root, apply=False, root=self.vcbs_root)
        self.assertEqual("preview", u_prev["status"])
        self.assertTrue(u_prev["plan"]["executable"])
        self.assertIn(str(skill_dest), u_prev["plan"]["removals"])
        self.assertIn(str(guide_dest), u_prev["plan"]["removals"])
        self.assertIn(str(global_dest), u_prev["plan"]["removals"])

        # Apply without decision fails closed
        with self.assertRaises(ProductLoopError) as ctx:
            uninstall(self.project, global_root=self.global_root, apply=True, decision=None, plan=u_prev["plan"], root=self.vcbs_root)
        self.assertIn("DECISION_REQUIRED", str(ctx.exception))

        # Apply uninstall with decision
        u_dec = decide_uninstall(u_prev["plan"], "approve")
        u_res = uninstall(self.project, global_root=self.global_root, apply=True, decision=u_dec, plan=u_prev["plan"], root=self.vcbs_root)
        self.assertEqual("applied", u_res["status"])

        # Managed assets cleanly removed
        self.assertFalse(skill_dest.exists())
        self.assertFalse(guide_dest.exists())
        self.assertFalse(global_dest.exists())
        self.assertFalse((self.project / ".vibe-coding-bootstrap").exists())

        # User assets, original code, README completely preserved
        self.assertTrue(user_file.is_file())
        self.assertEqual("class UserService: pass\n", user_file.read_text())
        self.assertTrue((self.project / "README.md").is_file())
        self.assertTrue((self.project / "main.py").is_file())

        # Status shows uninstalled
        st = get_status(self.project, host="codex", global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual("uninstalled", st["status"])
        self.assertFalse(st["deployed"])

        # Repeat uninstall is no_changes
        repeat = uninstall(self.project, global_root=self.global_root, apply=True, root=self.vcbs_root)
        self.assertEqual("no_changes", repeat["status"])

    def test_acceptance_d_exact_byte_rollback_for_deploy_upgrade_and_uninstall(self):
        """Acceptance D: Verify exact-byte rollback for deploy -> rollback, upgrade -> rollback, and uninstall -> rollback."""
        # --- Case D1: Deploy -> Rollback ---
        readme_before = (self.project / "README.md").read_bytes()
        main_before = (self.project / "main.py").read_bytes()

        res_deploy = self._approve_and_deploy(project_root=self.project, host="codex", context_path=self.context_file,
                                             global_root=self.global_root, root=self.vcbs_root)
        dep_id_deploy = res_deploy["deployment_id"]

        # Rollback deploy
        rb_deploy = rollback(self.project, dep_id_deploy, apply=True)
        self.assertEqual("rolled_back", rb_deploy["status"])

        # Verify target is clean of VCBS files and original files untouched
        self.assertFalse((self.project / ".vibe-coding-bootstrap").exists())
        self.assertFalse((self.project / activation.SKILL_DESTINATION).exists())
        self.assertEqual(readme_before, (self.project / "README.md").read_bytes())
        self.assertEqual(main_before, (self.project / "main.py").read_bytes())

        # --- Case D2: Deploy v1 -> Upgrade to v2 -> Rollback restores v1 ---
        res_v1 = self._approve_and_deploy(project_root=self.project, host="codex", context_path=self.context_file,
                                          global_root=self.global_root, root=self.vcbs_root)
        skill_dest = self.project / activation.SKILL_DESTINATION
        guide_dest = self.project / ".vibe-coding-bootstrap/components/test-guidance.md"
        v1_skill_bytes = skill_dest.read_bytes()
        v1_guide_bytes = guide_dest.read_bytes()
        v1_manifest_bytes = (self.project / ".vibe-coding-bootstrap/manifest.yaml").read_bytes()

        # Update source to v2 and apply upgrade
        (self.vcbs_root / activation.SKILL_SOURCE).write_text("# Skill v2\nRollback test.\n", encoding="utf-8")
        guide_src = self.vcbs_root / "components/project/test-guidance.md"
        guide_src.write_text("# Guidance v2\nRollback test.\n", encoding="utf-8")

        res_v2 = self._approve_and_upgrade(project_root=self.project, host="codex", global_root=self.global_root, root=self.vcbs_root)
        dep_id_v2 = res_v2["deployment_id"]
        self.assertNotEqual(v1_skill_bytes, skill_dest.read_bytes())

        # Rollback upgrade
        rb_upgrade = rollback(self.project, dep_id_v2, apply=True)
        self.assertEqual("rolled_back", rb_upgrade["status"])

        # Exact v1 bytes restored
        self.assertEqual(v1_skill_bytes, skill_dest.read_bytes())
        self.assertEqual(v1_guide_bytes, guide_dest.read_bytes())
        self.assertEqual(v1_manifest_bytes, (self.project / ".vibe-coding-bootstrap/manifest.yaml").read_bytes())

        # --- Case D3: Uninstall -> Rollback restores uninstalled files ---
        res_uninst = self._approve_and_uninstall(project_root=self.project, global_root=self.global_root, root=self.vcbs_root)
        dep_id_uninst = res_uninst["deployment_id"]
        self.assertFalse(skill_dest.exists())
        self.assertFalse(guide_dest.exists())
        self.assertFalse((self.project / ".vibe-coding-bootstrap").exists())

        # Rollback uninstall
        rb_uninst = rollback(self.project, dep_id_uninst, apply=True)
        self.assertEqual("rolled_back", rb_uninst["status"])

        # Exact pre-uninstall files and manifests restored
        self.assertTrue(skill_dest.is_file())
        self.assertTrue(guide_dest.is_file())
        self.assertEqual(v1_skill_bytes, skill_dest.read_bytes())
        self.assertEqual(v1_guide_bytes, guide_dest.read_bytes())
        self.assertEqual(v1_manifest_bytes, (self.project / ".vibe-coding-bootstrap/manifest.yaml").read_bytes())

        # Status after rollback is back to installed (with update_available because vcbs_root has v2)
        st_after = get_status(self.project, host="codex", global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual("update_available", st_after["status"])
        self.assertTrue(st_after["deployed"])

        # Revert vcbs_root source back to v1
        shutil.copy2(ROOT / activation.SKILL_SOURCE, self.vcbs_root / activation.SKILL_SOURCE)
        shutil.copy2(ROOT / "components/project/test-guidance.md", self.vcbs_root / "components/project/test-guidance.md")
        st_v1 = get_status(self.project, host="codex", global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual("installed", st_v1["status"])
        self.assertFalse(st_v1["update_available"])

    def test_acceptance_e_conflict_protections(self):
        """Acceptance E: User modification protects files from overwrite during upgrade and deletion during uninstall; pre-existing file blocks takeover."""
        # 1. Preexisting destination file blocks deploy takeover
        dest_conflict = self.project / ".vibe-coding-bootstrap/components/test-guidance.md"
        dest_conflict.parent.mkdir(parents=True, exist_ok=True)
        dest_conflict.write_text("# Pre-existing unmanaged file\n")

        with self.assertRaises(ProductLoopError) as ctx:
            self._approve_and_deploy(project_root=self.project, host="codex", context_path=self.context_file,
                                     global_root=self.global_root, root=self.vcbs_root)
        self.assertTrue("PLAN_BLOCKED" in str(ctx.exception) or "STALE_BOOTSTRAP_PLAN" in str(ctx.exception))
        dest_conflict.unlink()
        shutil.rmtree(self.project / ".vibe-coding-bootstrap")

        # Deploy clean v1
        self._approve_and_deploy(project_root=self.project, host="codex", context_path=self.context_file,
                                 global_root=self.global_root, root=self.vcbs_root)

        # 2. Modify an installed file: upgrade must detect conflict and refuse to overwrite
        guide_file = self.project / ".vibe-coding-bootstrap/components/test-guidance.md"
        guide_file.write_text("# User customized guidance\n")

        # Mutate VCBS source to v2
        guide_src = self.vcbs_root / "components/project/test-guidance.md"
        guide_src.write_text("# Guidance v2\nConflict test.\n", encoding="utf-8")

        # Upgrade must fail closed
        with self.assertRaises(ProductLoopError) as ctx:
            self._approve_and_upgrade(project_root=self.project, host="codex", global_root=self.global_root, root=self.vcbs_root)
        self.assertIn("UPGRADE_CONFLICT", str(ctx.exception))
        # File must not be overwritten
        self.assertEqual("# User customized guidance\n", guide_file.read_text())

        # 3. Uninstall must also refuse to delete user-modified file
        with self.assertRaises(ProductLoopError) as ctx:
            self._approve_and_uninstall(project_root=self.project, global_root=self.global_root, root=self.vcbs_root)
        self.assertIn("PLAN_BLOCKED", str(ctx.exception))
        self.assertTrue(guide_file.is_file())

    def test_acceptance_f_approval_and_drift_protections(self):
        """Acceptance F: Plan approved -> commit changed or file changed before apply -> apply must fail; manifest drift blocks uninstall."""
        # Preview deploy to generate plan
        prev = deploy(self.project, host="codex", context_path=self.context_file,
                      global_root=self.global_root, apply=False, root=self.vcbs_root)
        plan = prev["plan"]
        decision = decide(plan, "approve")

        # Drift 1: commit change in consumer repository
        (self.project / "extra.py").write_text("x = 1\n")
        self._git(self.project, "add", ".")
        self._git(self.project, "commit", "-qm", "Drift commit")

        with self.assertRaises(ProductLoopError) as ctx:
            apply_plan(plan, decision, self.project, global_root=self.global_root, root=self.vcbs_root)
        self.assertIn("STALE_BOOTSTRAP_PLAN", str(ctx.exception))

        # Re-deploy cleanly
        self._approve_and_deploy(project_root=self.project, host="codex", context_path=self.context_file,
                                 global_root=self.global_root, root=self.vcbs_root)

        # Drift 2: manifest drift before apply_uninstall
        un_plan = build_uninstall_plan(self.project, global_root=self.global_root, root=self.vcbs_root)
        un_dec = decide_uninstall(un_plan, "approve")

        # Modify manifest.yaml
        mpath = self.project / ".vibe-coding-bootstrap/manifest.yaml"
        mdata = yaml.safe_load(mpath.read_text(encoding="utf-8"))
        mdata["items"][0]["sha256"] = "1" * 64
        mpath.write_text(yaml.safe_dump(mdata), encoding="utf-8")

        with self.assertRaises(ProductLoopError) as ctx:
            apply_uninstall(un_plan, un_dec, self.project, global_root=self.global_root, root=self.vcbs_root)
        self.assertIn("STALE_BOOTSTRAP_PLAN", str(ctx.exception))

    def test_acceptance_g_existing_adoption_preservation(self):
        """Acceptance G: Target has existing AI configs -> status detects unmanaged -> adoption route used -> user AI configs never deleted or overwritten."""
        # Pre-existing project-owned AI configurations
        agents_file = self.project / "AGENTS.md"
        agents_file.write_text("# Project Specific Agent Rules\nDo not delete me.\n")
        claude_file = self.project / "CLAUDE.md"
        claude_file.write_text("# Claude guidelines\n")

        # Status inventories unmanaged assets
        st = get_status(self.project, host="codex", root=self.vcbs_root)
        self.assertIn("AGENTS.md", st["unmanaged_assets"])
        self.assertIn("CLAUDE.md", st["unmanaged_assets"])
        self.assertEqual("adoption", st["route_prediction"])

        # Running deploy requires semantic review
        with self.assertRaises(ProductLoopError) as ctx:
            self._approve_and_deploy(project_root=self.project, host="codex", root=self.vcbs_root)
        self.assertIn("HOST_SEMANTIC_REVIEW_REQUIRED", str(ctx.exception))

        # Files remain untouched
        self.assertEqual("# Project Specific Agent Rules\nDo not delete me.\n", agents_file.read_text())
        self.assertEqual("# Claude guidelines\n", claude_file.read_text())

    def test_acceptance_h_project_and_global_scopes(self):
        """Acceptance H: Project and global scopes materialized, isolated, and safely uninstalled."""
        self._approve_and_deploy(project_root=self.project, host="codex", context_path=self.context_file,
                                 global_root=self.global_root, root=self.vcbs_root)

        # Status correctly separates project and global scopes
        st = get_status(self.project, host="codex", global_root=self.global_root, root=self.vcbs_root)
        self.assertGreater(len(st["scope_breakdown"]["project_components"]), 0)
        self.assertGreater(len(st["scope_breakdown"]["global_components"]), 0)

        # Global file exists inside global_root
        global_file = self.global_root / "components/reviewable-changes.md"
        self.assertTrue(global_file.is_file())

        # Uninstalling without global-root when global components exist is blocked
        plan_no_global = build_uninstall_plan(self.project, global_root=None, root=self.vcbs_root)
        self.assertFalse(plan_no_global["executable"])
        self.assertTrue(any(u.get("kind") == "global_root_required" for u in plan_no_global["unresolved"]))

        # Uninstalling with global-root removes both safely
        u_res = self._approve_and_uninstall(project_root=self.project, global_root=self.global_root, root=self.vcbs_root)
        self.assertEqual("applied", u_res["status"])
        self.assertFalse(global_file.exists())

    def test_acceptance_i_cli_and_wrapper(self):
        """Acceptance I: Test full lifecycle via CLI (python -m bootstrap) and ./vcbs wrapper with explicit approval."""
        plan_file = self.base / "deploy_plan.json"
        dec_file = self.base / "deploy_dec.json"

        # 1. vcbs status --json
        res_st = self._run_vcbs_wrapper("status", "--project-root", str(self.project), "--json")
        st_data = json.loads(res_st.stdout)
        self.assertEqual("not_installed", st_data["status"])

        # 2a. Deploy preview saving plan
        self._run_cli(
            "deploy", "--project-root", str(self.project),
            "--context", str(self.context_file),
            "--global-root", str(self.global_root),
            "--output", str(plan_file)
        )
        self.assertTrue(plan_file.is_file())

        # 2b. Attempting deploy --apply without --decision fails closed
        fail_res = self._run_cli(
            "deploy", "--project-root", str(self.project),
            "--context", str(self.context_file),
            "--global-root", str(self.global_root),
            "--apply", check=False
        )
        self.assertNotEqual(0, fail_res.returncode)
        self.assertIn("DECISION_REQUIRED", fail_res.stderr)

        # 2c. Decide plan
        self._run_cli("decide", str(plan_file), "--decision", "approve", "--output", str(dec_file))
        self.assertTrue(dec_file.is_file())

        # 2d. Apply deploy with plan and decision
        res_dep = self._run_cli(
            "deploy", "--project-root", str(self.project),
            "--plan", str(plan_file),
            "--decision", str(dec_file),
            "--global-root", str(self.global_root),
            "--apply", "--json"
        )
        dep_data = json.loads(res_dep.stdout)
        self.assertEqual("applied", dep_data["status"])

        # 3. vcbs status shows installed
        res_st2 = self._run_vcbs_wrapper("status", "--project-root", str(self.project), "--global-root", str(self.global_root))
        self.assertIn("VCBS Status: INSTALLED", res_st2.stdout)

        # 4. Mutate to v2 and run upgrade via CLI
        (self.vcbs_root / activation.SKILL_SOURCE).write_text("# Skill v2\nCLI test.\n", encoding="utf-8")
        guide_src = self.vcbs_root / "components/project/test-guidance.md"
        guide_src.write_text("# Guidance v2\nCLI test.\n", encoding="utf-8")

        up_plan_file = self.base / "up_plan.json"
        up_dec_file = self.base / "up_dec.json"

        # 4a. Upgrade preview
        self._run_vcbs_wrapper(
            "upgrade", "--project-root", str(self.project),
            "--global-root", str(self.global_root),
            "--output", str(up_plan_file)
        )
        # 4b. Decide upgrade
        self._run_cli("decide", str(up_plan_file), "--decision", "approve", "--output", str(up_dec_file))
        # 4c. Apply upgrade
        res_up = self._run_vcbs_wrapper(
            "upgrade", "--project-root", str(self.project),
            "--plan", str(up_plan_file),
            "--decision", str(up_dec_file),
            "--global-root", str(self.global_root),
            "--apply", "--json"
        )
        up_data = json.loads(res_up.stdout)
        self.assertEqual("applied", up_data["status"])

        # 5. Uninstall via CLI with explicit decision
        un_plan_file = self.base / "un_plan.json"
        un_dec_file = self.base / "un_dec.json"

        # 5a. Preview uninstall
        self._run_cli(
            "uninstall", "--project-root", str(self.project),
            "--global-root", str(self.global_root),
            "--output", str(un_plan_file)
        )
        # 5b. Decide uninstall
        self._run_cli("decide", str(un_plan_file), "--decision", "approve", "--output", str(un_dec_file))
        # 5c. Apply uninstall
        res_un = self._run_cli(
            "uninstall", "--project-root", str(self.project),
            "--plan", str(un_plan_file),
            "--decision", str(un_dec_file),
            "--global-root", str(self.global_root),
            "--apply", "--json"
        )
        un_data = json.loads(res_un.stdout)
        self.assertEqual("applied", un_data["status"])

        # 6. Status is uninstalled
        res_st3 = self._run_vcbs_wrapper("status", "--project-root", str(self.project), "--json")
        st3_data = json.loads(res_st3.stdout)
        self.assertEqual("uninstalled", st3_data["status"])
