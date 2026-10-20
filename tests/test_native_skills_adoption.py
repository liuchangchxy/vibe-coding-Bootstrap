"""Tests for Claude Code and Antigravity native Skill adoption and activation."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import yaml

from activation.core import (
    MANIFEST_PATH, apply as activation_apply, build_plan as activation_build_plan,
    deactivate as activation_deactivate, make_decision as activation_make_decision,
    plan_digest,
)
from bootstrap.core import (
    ProductLoopError, apply_plan, build_plan, decide, inspect_project, render_summary,
)
from bootstrap.lifecycle import (
    apply_uninstall, build_uninstall_plan, decide_uninstall, deploy,
    get_status, rollback, uninstall, upgrade,
)
from composer.core import ROOT


def git(project, *args):
    return subprocess.run(["git", "-C", str(project), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


class NativeSkillsAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        git(self.project, "init", "-q")
        git(self.project, "config", "user.name", "Native Skill Test")
        git(self.project, "config", "user.email", "native-skill-test@example.invalid")
        (self.project / "README.md").write_text("# Test Repo\n", encoding="utf-8")
        git(self.project, "add", "README.md")
        git(self.project, "commit", "-qm", "initial")

    def tearDown(self):
        self.temp.cleanup()

    def test_claude_skill_direct_activation_lifecycle(self):
        """Test Claude Code native skill activation, idempotent reapply, and clean deactivation."""
        plan = activation_build_plan(self.project, capability="vcbs-manager", host="claude")
        self.assertEqual("ready", plan["status"])
        self.assertEqual("vcbs-manager", plan["capability_id"])
        self.assertEqual("claude", plan["host"])
        self.assertEqual(".claude/skills/vcbs-manager/SKILL.md", plan["destination"])
        self.assertIsNone(plan["agents_destination"])

        # Approve and apply
        approval = activation_make_decision(plan, "approve")
        res = activation_apply(plan, approval, self.project)
        self.assertEqual("activated", res["status"])

        skill_file = self.project / ".claude/skills/vcbs-manager/SKILL.md"
        self.assertTrue(skill_file.is_file())
        content = skill_file.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("---\nname: vcbs-manager\n"))
        self.assertIn("description:", content)

        # Neither CLAUDE.md nor AGENTS.md was created
        self.assertFalse((self.project / "CLAUDE.md").exists())
        self.assertFalse((self.project / "AGENTS.md").exists())

        # Activation manifest verification
        manifest = json.loads((self.project / MANIFEST_PATH).read_text(encoding="utf-8"))
        self.assertEqual(1, manifest["format_version"])
        self.assertEqual("claude", manifest["host"])
        self.assertEqual("vcbs-manager", manifest["capability"])
        self.assertEqual(1, len(manifest["artifacts"]))
        self.assertEqual(".claude/skills/vcbs-manager/SKILL.md", manifest["artifacts"][0]["destination"])

        # Idempotent reapply
        plan2 = activation_build_plan(self.project, capability="vcbs-manager", host="claude")
        self.assertEqual("already_active", plan2["status"])
        res2 = activation_apply(plan2, activation_make_decision(plan2, "approve"), self.project)
        self.assertEqual("already_active", res2["status"])

        # Deactivate
        deact = activation_deactivate(self.project, host="claude", capability="vcbs-manager")
        self.assertEqual("deactivated", deact["status"])
        self.assertFalse(skill_file.exists())
        self.assertFalse((self.project / MANIFEST_PATH).exists())

    def test_antigravity_skill_direct_activation_lifecycle(self):
        """Test Antigravity native skill activation and directory isolation."""
        plan = activation_build_plan(self.project, capability="vcbs-manager", host="antigravity")
        self.assertEqual("ready", plan["status"])
        self.assertEqual(".agents/skills/vcbs-manager/SKILL.md", plan["destination"])
        self.assertIsNone(plan["agents_destination"])

        res = activation_apply(plan, activation_make_decision(plan, "approve"), self.project)
        self.assertEqual("activated", res["status"])

        skill_file = self.project / ".agents/skills/vcbs-manager/SKILL.md"
        self.assertTrue(skill_file.is_file())
        content = skill_file.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("---\nname: vcbs-manager\n"))

        # No AGENTS.md or GEMINI.md created
        self.assertFalse((self.project / "AGENTS.md").exists())
        self.assertFalse((self.project / "GEMINI.md").exists())

        # Deactivate cleanly
        self.assertEqual("deactivated", activation_deactivate(self.project, host="antigravity", capability="vcbs-manager")["status"])
        self.assertFalse(skill_file.exists())
        self.assertFalse((self.project / MANIFEST_PATH).exists())

    def test_coexistence_codex_testing_and_antigravity_vcbs_manager(self):
        """Codex and Antigravity skills can safely coexist under .agents/skills."""
        # 1. Activate Codex testing
        p1 = activation_build_plan(self.project, capability="testing", host="codex")
        activation_apply(p1, activation_make_decision(p1, "approve"), self.project)

        # 2. Activate Antigravity vcbs-manager
        p2 = activation_build_plan(self.project, capability="vcbs-manager", host="antigravity")
        self.assertEqual("ready", p2["status"])
        activation_apply(p2, activation_make_decision(p2, "approve"), self.project)

        # Both skills and AGENTS.md exist
        codex_skill = self.project / ".agents/skills/bootstrap-testing/SKILL.md"
        ag_skill = self.project / ".agents/skills/vcbs-manager/SKILL.md"
        agents_md = self.project / "AGENTS.md"
        self.assertTrue(codex_skill.is_file())
        self.assertTrue(ag_skill.is_file())
        self.assertTrue(agents_md.is_file())

        manifest = json.loads((self.project / MANIFEST_PATH).read_text(encoding="utf-8"))
        self.assertEqual(3, len(manifest["artifacts"]))
        self.assertEqual(2, len(manifest["activations"]))

        # 3. Deactivate Antigravity vcbs-manager
        deact_ag = activation_deactivate(self.project, host="antigravity", capability="vcbs-manager")
        self.assertEqual("deactivated", deact_ag["status"])
        self.assertFalse(ag_skill.exists())
        self.assertTrue(codex_skill.is_file())
        self.assertTrue(agents_md.is_file())
        self.assertTrue((self.project / MANIFEST_PATH).is_file())

        # 4. Deactivate Codex testing
        deact_codex = activation_deactivate(self.project, host="codex", capability="testing")
        self.assertEqual("deactivated", deact_codex["status"])
        self.assertFalse(codex_skill.exists())
        self.assertFalse(agents_md.exists())
        self.assertFalse((self.project / MANIFEST_PATH).exists())

    def test_collision_with_unmanaged_skill_fails_closed(self):
        """Pre-existing unmanaged skill file triggers conflict and is never overwritten."""
        # Unmanaged Claude skill
        claude_target = self.project / ".claude/skills/vcbs-manager/SKILL.md"
        claude_target.parent.mkdir(parents=True)
        claude_target.write_text("user custom claude skill\n", encoding="utf-8")

        plan_claude = activation_build_plan(self.project, capability="vcbs-manager", host="claude")
        self.assertEqual("conflict", plan_claude["status"])
        with self.assertRaisesRegex(ValueError, "cannot be applied: conflict"):
            activation_apply(plan_claude, activation_make_decision(plan_claude, "approve"), self.project)
        self.assertEqual("user custom claude skill\n", claude_target.read_text(encoding="utf-8"))

        # Unmanaged Antigravity skill
        ag_target = self.project / ".agents/skills/vcbs-manager/SKILL.md"
        ag_target.parent.mkdir(parents=True)
        ag_target.write_text("user custom ag skill\n", encoding="utf-8")

        plan_ag = activation_build_plan(self.project, capability="vcbs-manager", host="antigravity")
        self.assertEqual("conflict", plan_ag["status"])
        with self.assertRaisesRegex(ValueError, "cannot be applied: conflict"):
            activation_apply(plan_ag, activation_make_decision(plan_ag, "approve"), self.project)
        self.assertEqual("user custom ag skill\n", ag_target.read_text(encoding="utf-8"))

    def test_tampering_detection_and_deactivation_abort(self):
        """Tampering with an activated skill causes conflict and refuses deactivation."""
        plan = activation_build_plan(self.project, capability="vcbs-manager", host="claude")
        activation_apply(plan, activation_make_decision(plan, "approve"), self.project)

        skill_file = self.project / ".claude/skills/vcbs-manager/SKILL.md"
        skill_file.write_text("tampered content\n", encoding="utf-8")

        # Plan detects conflict
        plan2 = activation_build_plan(self.project, capability="vcbs-manager", host="claude")
        self.assertEqual("conflict", plan2["status"])

        # Deactivation detects conflict and does not touch file
        res = activation_deactivate(self.project, host="claude", capability="vcbs-manager")
        self.assertEqual("conflict", res["status"])
        self.assertEqual("tampered content\n", skill_file.read_text(encoding="utf-8"))
        self.assertTrue((self.project / MANIFEST_PATH).is_file())

    def _make_semantic_result(self, package, sem_path):
        catalog_records = package["capability_catalog"]
        coverage = []
        for cat in catalog_records:
            cid = cat["id"]
            if cid == "vcbs-manager":
                coverage.append({
                    "capability_id": cid,
                    "status": "missing",
                    "confidence": "high",
                    "recommended_action": "consider_bootstrap_component",
                    "reason": "Adopt VCBS Manager for AI coding host guidance.",
                    "evidence": [],
                })
            else:
                coverage.append({
                    "capability_id": cid,
                    "status": "unknown",
                    "confidence": "high",
                    "recommended_action": "no_action",
                    "reason": "Out of scope for this pilot.",
                    "evidence": [],
                })

        pref_findings = [
            {"preference_id": pid, "status": "compatible", "reason": "No conflict.", "evidence": []}
            for pid in package["profile_constraints"]["explicit_preferences"] if pid != "additional"
        ]
        for add_k in package["profile_constraints"]["explicit_preferences"].get("additional", {}):
            if add_k in {"prefer_simple_solutions", "avoid_unnecessary_abstraction"}:
                continue
            pref_findings.append({"preference_id": add_k, "status": "compatible", "reason": "No conflict.", "evidence": []})
        pref_findings.append({"preference_id": "solution_complexity", "status": "compatible", "reason": "No conflict.", "evidence": []})

        semantic_result = {
            "project_name": package["project"]["name"],
            "reviewer": "host-ai",
            "semantic_coverage": coverage,
            "preference_findings": pref_findings,
        }
        sem_path.write_text(json.dumps(semantic_result, indent=2), encoding="utf-8")
        return sem_path

    def test_existing_project_adoption_route_claude(self):
        """Unified product loop Adoption route: adopts vcbs-manager for Claude Code and rolls back."""
        # Add an unmanaged agent instruction file so discover classifies as existing project adoption
        claude_md = self.project / "CLAUDE.md"
        claude_md.write_text("# Project Guidelines\nExisting project instructions.\n", encoding="utf-8")
        git(self.project, "add", ".")
        git(self.project, "commit", "-qm", "add existing instructions")

        # Step 1: inspect
        inspected = inspect_project(self.project, host="claude", root=ROOT)
        self.assertEqual("adoption", inspected["route"])
        package = inspected["semantic_package"]

        # Step 2: build semantic review result with vcbs-manager eligible
        sem_path = Path(self.temp.name) / "semantic-result.json"
        self._make_semantic_result(package, sem_path)

        # Step 3: build unified plan
        uplan = build_plan(self.project, host="claude", semantic_result_path=sem_path, root=ROOT)
        self.assertEqual("adoption", uplan["route"])
        self.assertEqual(["vcbs-manager"], uplan["capabilities"])
        self.assertEqual(1, len(uplan["host_activations"]))
        self.assertEqual("claude", uplan["host_activations"][0]["host"])
        self.assertEqual(".claude/skills/vcbs-manager/SKILL.md", uplan["host_activations"][0]["destination"])
        self.assertEqual(1, uplan["actual_changes"])
        self.assertTrue(uplan["requires_confirmation"])

        # Summary
        summary = render_summary(uplan)
        self.assertIn("Host activation: claude:ready", summary)

        # Step 4: decide & apply
        udec = decide(uplan, "approve")
        res = apply_plan(uplan, udec, self.project, root=ROOT)
        self.assertEqual("applied", res["status"])
        self.assertTrue(res["rollback_available"])

        skill_file = self.project / ".claude/skills/vcbs-manager/SKILL.md"
        self.assertTrue(skill_file.is_file())

        # Step 5: status
        st = get_status(self.project, host="claude", root=ROOT)
        self.assertEqual("installed", st["status"])
        self.assertTrue(st["host_support"]["supported"])
        self.assertEqual("supported", st["host_support"]["native_activation"])

        # Step 6: rollback
        rb = rollback(self.project, res["deployment_id"], apply=True)
        self.assertEqual("rolled_back", rb["status"])
        self.assertFalse(skill_file.exists())
        self.assertFalse((self.project / MANIFEST_PATH).exists())

    def test_multi_host_claude_and_antigravity_plan_and_rollback(self):
        """--host claude,antigravity simultaneously activates both native skills and rolls back."""
        claude_md = self.project / "CLAUDE.md"
        claude_md.write_text("# Project Guidelines\n", encoding="utf-8")
        git(self.project, "add", ".")
        git(self.project, "commit", "-qm", "add existing instructions")

        inspected = inspect_project(self.project, host="claude,antigravity", root=ROOT)
        self.assertEqual("adoption", inspected["route"])
        package = inspected["semantic_package"]

        sem_path = Path(self.temp.name) / "multi-sem.json"
        self._make_semantic_result(package, sem_path)

        uplan = build_plan(self.project, host="claude,antigravity", semantic_result_path=sem_path, root=ROOT)
        self.assertEqual(2, len(uplan["host_activations"]))
        self.assertEqual(2, uplan["actual_changes"])

        res = apply_plan(uplan, decide(uplan, "approve"), self.project, root=ROOT)
        self.assertEqual("applied", res["status"])

        claude_skill = self.project / ".claude/skills/vcbs-manager/SKILL.md"
        ag_skill = self.project / ".agents/skills/vcbs-manager/SKILL.md"
        self.assertTrue(claude_skill.is_file())
        self.assertTrue(ag_skill.is_file())

        # Rollback restores exact pre-state
        rb = rollback(self.project, res["deployment_id"], apply=True)
        self.assertEqual("rolled_back", rb["status"])
        self.assertFalse(claude_skill.exists())
        self.assertFalse(ag_skill.exists())
        self.assertFalse((self.project / MANIFEST_PATH).exists())

    def test_cli_execution_with_native_hosts(self):
        """CLI operations accept --host claude and --host antigravity without syntax errors."""
        res_inspect = subprocess.run([
            "python3", "-m", "bootstrap", "inspect",
            "--project-root", str(self.project),
            "--host", "claude"
        ], capture_output=True, text=True, check=True)
        self.assertIn('"host": "claude"', res_inspect.stdout)

        res_status = subprocess.run([
            "python3", "-m", "bootstrap", "status",
            "--project-root", str(self.project),
            "--host", "antigravity",
            "--json"
        ], capture_output=True, text=True, check=True)
        st_data = json.loads(res_status.stdout)
        self.assertEqual("antigravity", st_data["host_support"]["host"])
        self.assertTrue(st_data["host_support"]["supported"])

    def test_adoption_plan_and_manifest_name_host_specific_component(self):
        """Host-specific component naming: --host antigravity names antigravity-native-vcbs-manager-skill, claude names claude-native-vcbs-manager-skill."""
        (self.project / "CLAUDE.md").write_text("# Claude guidelines\n", encoding="utf-8")
        git(self.project, "add", ".")
        git(self.project, "commit", "-qm", "guidelines")

        inspected = inspect_project(self.project, host="antigravity", root=ROOT)
        sem_path = Path(self.temp.name) / "sem-ag.json"
        self._make_semantic_result(inspected["semantic_package"], sem_path)

        # Plan with antigravity
        ag_plan = build_plan(self.project, host="antigravity", semantic_result_path=sem_path, root=ROOT)
        self.assertIn("antigravity-native-vcbs-manager-skill", ag_plan["references"])
        self.assertNotIn("claude-native-vcbs-manager-skill", ag_plan["references"])
        self.assertEqual(1, len(ag_plan["host_activations"]))
        self.assertEqual("antigravity", ag_plan["host_activations"][0]["host"])

        # Apply antigravity
        res_ag = apply_plan(ag_plan, decide(ag_plan, "approve"), self.project, root=ROOT)
        manifest_data = yaml.safe_load((self.project / ".vibe-coding-bootstrap/manifest.yaml").read_text(encoding="utf-8"))
        comp_ids = [item["component_id"] for item in manifest_data["items"]]
        self.assertIn("antigravity-native-vcbs-manager-skill", comp_ids)
        self.assertNotIn("claude-native-vcbs-manager-skill", comp_ids)

        # Rollback cleanly
        rollback(self.project, res_ag["deployment_id"], apply=True)

        # Plan with claude
        sem_path_cl = Path(self.temp.name) / "sem-cl.json"
        self._make_semantic_result(inspected["semantic_package"], sem_path_cl)
        cl_plan = build_plan(self.project, host="claude", semantic_result_path=sem_path_cl, root=ROOT)
        self.assertIn("claude-native-vcbs-manager-skill", cl_plan["references"])
        self.assertNotIn("antigravity-native-vcbs-manager-skill", cl_plan["references"])
        res_cl = apply_plan(cl_plan, decide(cl_plan, "approve"), self.project, root=ROOT)
        manifest_data_cl = yaml.safe_load((self.project / ".vibe-coding-bootstrap/manifest.yaml").read_text(encoding="utf-8"))
        comp_ids_cl = [item["component_id"] for item in manifest_data_cl["items"]]
        self.assertIn("claude-native-vcbs-manager-skill", comp_ids_cl)
        self.assertNotIn("antigravity-native-vcbs-manager-skill", comp_ids_cl)

    def test_post_rollback_residue_and_status(self):
        """Rollback leaves zero .vibe-coding-bootstrap residue and status returns not_installed."""
        (self.project / "CLAUDE.md").write_text("# Project Guidelines\n", encoding="utf-8")
        git(self.project, "add", ".")
        git(self.project, "commit", "-qm", "guidelines")

        inspected = inspect_project(self.project, host="claude", root=ROOT)
        sem_path = Path(self.temp.name) / "sem-rollback.json"
        self._make_semantic_result(inspected["semantic_package"], sem_path)

        plan = build_plan(self.project, host="claude", semantic_result_path=sem_path, root=ROOT)
        res = apply_plan(plan, decide(plan, "approve"), self.project, root=ROOT)
        self.assertTrue((self.project / ".vibe-coding-bootstrap/manifest.yaml").is_file())
        self.assertTrue((self.project / ".claude/skills/vcbs-manager/SKILL.md").is_file())

        rb = rollback(self.project, res["deployment_id"], apply=True)
        self.assertEqual("rolled_back", rb["status"])

        # Residue checks
        self.assertFalse((self.project / ".vibe-coding-bootstrap").exists())
        self.assertFalse((self.project / ".claude/skills/vcbs-manager/SKILL.md").exists())

        # Status check
        st = get_status(self.project, host="claude", root=ROOT)
        self.assertEqual("uninstalled", st["status"])
        self.assertFalse(st["deployed"])
        self.assertEqual([], st["components"])

    def test_tamper_and_delete_native_skill_causes_status_conflict_and_non_executable_plan(self):
        """Tampering or deleting native skill causes status conflict and non-executable plan."""
        (self.project / "CLAUDE.md").write_text("# Project Guidelines\n", encoding="utf-8")
        git(self.project, "add", ".")
        git(self.project, "commit", "-qm", "guidelines")

        inspected = inspect_project(self.project, host="claude,antigravity", root=ROOT)
        sem_path = Path(self.temp.name) / "sem-tamper.json"
        self._make_semantic_result(inspected["semantic_package"], sem_path)

        plan = build_plan(self.project, host="claude,antigravity", semantic_result_path=sem_path, root=ROOT)
        apply_plan(plan, decide(plan, "approve"), self.project, root=ROOT)

        # 1. Tamper Claude skill
        claude_skill = self.project / ".claude/skills/vcbs-manager/SKILL.md"
        claude_skill.write_text("TAMPERED MANUALLY\n", encoding="utf-8")

        st = get_status(self.project, host="claude,antigravity", root=ROOT)
        self.assertEqual("conflict", st["status"])
        self.assertTrue(st["has_conflicts"])

        # Re-planning with tampered skill produces non-executable plan with unresolved conflict
        p_blocked = build_plan(self.project, host="claude,antigravity", semantic_result_path=sem_path, root=ROOT)
        self.assertFalse(p_blocked["executable"])
        self.assertTrue(any(u.get("kind") == "host_activation" for u in p_blocked["unresolved"]))

        # 2. Delete Antigravity skill
        ag_skill = self.project / ".agents/skills/vcbs-manager/SKILL.md"
        ag_skill.unlink()

        st_ag = get_status(self.project, host="antigravity", root=ROOT)
        self.assertEqual("conflict", st_ag["status"])
        self.assertTrue(st_ag["has_conflicts"])

    def test_uninstall_claude_only_and_antigravity_only(self):
        """Uninstalling Claude or Antigravity native skills removes skill and activation manifest cleanly."""
        (self.project / "CLAUDE.md").write_text("# Guidelines\n", encoding="utf-8")
        git(self.project, "add", ".")
        git(self.project, "commit", "-qm", "guidelines")

        inspected = inspect_project(self.project, host="claude", root=ROOT)
        sem_path = Path(self.temp.name) / "sem-un.json"
        self._make_semantic_result(inspected["semantic_package"], sem_path)

        plan = build_plan(self.project, host="claude", semantic_result_path=sem_path, root=ROOT)
        apply_plan(plan, decide(plan, "approve"), self.project, root=ROOT)

        skill_file = self.project / ".claude/skills/vcbs-manager/SKILL.md"
        manifest_file = self.project / ".vibe-coding-bootstrap/manifest.yaml"
        self.assertTrue(skill_file.is_file())
        self.assertTrue(manifest_file.is_file())
        pre_uninstall_skill_bytes = skill_file.read_bytes()
        pre_uninstall_manifest_bytes = manifest_file.read_bytes()
        pre_uninstall_act_bytes = (self.project / MANIFEST_PATH).read_bytes()

        # Uninstall Claude native skill
        u_plan = build_uninstall_plan(self.project, component_id="claude-native-vcbs-manager-skill", root=ROOT)
        self.assertEqual(1, len(u_plan["removals"]))
        self.assertTrue(u_plan["executable"])
        self.assertIn(".vibe-coding-bootstrap/manifest.yaml", u_plan.get("manifest_updates", []))

        u_res = apply_uninstall(u_plan, decide_uninstall(u_plan, "approve"), self.project, root=ROOT)
        self.assertEqual("applied", u_res["status"])
        self.assertTrue(u_res["rollback_available"])
        self.assertFalse(skill_file.exists())
        self.assertFalse((self.project / MANIFEST_PATH).exists())
        self.assertFalse(manifest_file.exists())

        st = get_status(self.project, host="claude", root=ROOT)
        self.assertEqual("uninstalled", st["status"])

        # Second uninstall is no_changes
        u_plan2 = build_uninstall_plan(self.project, component_id="claude-native-vcbs-manager-skill", root=ROOT)
        self.assertEqual(0, u_plan2["actual_changes"])
        self.assertEqual([], u_plan2["removals"])
        res_no_changes = uninstall(self.project, component_id="claude-native-vcbs-manager-skill", root=ROOT)
        self.assertEqual("no_changes", res_no_changes["status"])

        # Rollback restores manifest.yaml, activation.json, and skill byte-for-byte
        rb = rollback(self.project, u_res["deployment_id"], apply=True)
        self.assertEqual("rolled_back", rb["status"])
        self.assertTrue(skill_file.is_file())
        self.assertTrue(manifest_file.is_file())
        self.assertTrue((self.project / MANIFEST_PATH).is_file())
        self.assertEqual(pre_uninstall_skill_bytes, skill_file.read_bytes())
        self.assertEqual(pre_uninstall_manifest_bytes, manifest_file.read_bytes())
        self.assertEqual(pre_uninstall_act_bytes, (self.project / MANIFEST_PATH).read_bytes())

        # Clean uninstall Claude before Antigravity check
        u_clean = build_uninstall_plan(self.project, component_id="claude-native-vcbs-manager-skill", root=ROOT)
        apply_uninstall(u_clean, decide_uninstall(u_clean, "approve"), self.project, root=ROOT)

        # 2. Antigravity uninstall
        sem_path_ag = Path(self.temp.name) / "sem-un-ag.json"
        self._make_semantic_result(inspected["semantic_package"], sem_path_ag)
        plan_ag = build_plan(self.project, host="antigravity", semantic_result_path=sem_path_ag, root=ROOT)
        apply_plan(plan_ag, decide(plan_ag, "approve"), self.project, root=ROOT)
        ag_skill_file = self.project / ".agents/skills/vcbs-manager/SKILL.md"
        self.assertTrue(ag_skill_file.is_file())
        self.assertTrue((self.project / MANIFEST_PATH).is_file())
        self.assertTrue(manifest_file.is_file())
        pre_ag_skill_bytes = ag_skill_file.read_bytes()
        pre_ag_manifest_bytes = manifest_file.read_bytes()
        pre_ag_act_bytes = (self.project / MANIFEST_PATH).read_bytes()

        u_plan_ag = build_uninstall_plan(self.project, component_id="antigravity-native-vcbs-manager-skill", root=ROOT)
        self.assertEqual(1, len(u_plan_ag["removals"]))
        self.assertIn(".vibe-coding-bootstrap/manifest.yaml", u_plan_ag.get("manifest_updates", []))
        u_res_ag = apply_uninstall(u_plan_ag, decide_uninstall(u_plan_ag, "approve"), self.project, root=ROOT)
        self.assertEqual("applied", u_res_ag["status"])
        self.assertTrue(u_res_ag["rollback_available"])
        self.assertFalse(ag_skill_file.exists())
        self.assertFalse((self.project / MANIFEST_PATH).exists())
        self.assertFalse(manifest_file.exists())

        st_ag = get_status(self.project, host="antigravity", root=ROOT)
        self.assertEqual("uninstalled", st_ag["status"])

        # Second uninstall is no_changes
        u_plan_ag2 = build_uninstall_plan(self.project, component_id="antigravity-native-vcbs-manager-skill", root=ROOT)
        self.assertEqual(0, u_plan_ag2["actual_changes"])
        self.assertEqual([], u_plan_ag2["removals"])
        res_ag_no_changes = uninstall(self.project, component_id="antigravity-native-vcbs-manager-skill", root=ROOT)
        self.assertEqual("no_changes", res_ag_no_changes["status"])

        # Rollback restores manifest.yaml, activation.json, and skill byte-for-byte
        rb_ag = rollback(self.project, u_res_ag["deployment_id"], apply=True)
        self.assertEqual("rolled_back", rb_ag["status"])
        self.assertTrue(ag_skill_file.is_file())
        self.assertTrue(manifest_file.is_file())
        self.assertTrue((self.project / MANIFEST_PATH).is_file())
        self.assertEqual(pre_ag_skill_bytes, ag_skill_file.read_bytes())
        self.assertEqual(pre_ag_manifest_bytes, manifest_file.read_bytes())
        self.assertEqual(pre_ag_act_bytes, (self.project / MANIFEST_PATH).read_bytes())

    def test_uninstall_by_activation_component_id_or_alias(self):
        """Uninstalling by activation ID or host alias cleans both manifest.yaml and activation.json without stale rows."""
        (self.project / "CLAUDE.md").write_text("# Guidelines\n", encoding="utf-8")
        git(self.project, "add", ".")
        git(self.project, "commit", "-qm", "guidelines")

        inspected = inspect_project(self.project, host="claude", root=ROOT)
        sem_path = Path(self.temp.name) / "sem-alias.json"
        self._make_semantic_result(inspected["semantic_package"], sem_path)

        plan = build_plan(self.project, host="claude", semantic_result_path=sem_path, root=ROOT)
        apply_plan(plan, decide(plan, "approve"), self.project, root=ROOT)

        # Verify initial state
        self.assertTrue((self.project / ".claude/skills/vcbs-manager/SKILL.md").is_file())
        m_data = yaml.safe_load((self.project / ".vibe-coding-bootstrap/manifest.yaml").read_text(encoding="utf-8"))
        self.assertEqual(1, len(m_data["items"]))
        self.assertEqual("claude-native-vcbs-manager-skill", m_data["items"][0]["component_id"])

        # Uninstall using the activation ID instead of manifest component ID
        u_plan = build_uninstall_plan(self.project, component_id="claude-vcbs-manager-activation", root=ROOT)
        self.assertTrue(u_plan["executable"])
        self.assertEqual(1, len(u_plan["removals"]))
        self.assertIn(".vibe-coding-bootstrap/manifest.yaml", u_plan.get("manifest_updates", []))

        u_res = apply_uninstall(u_plan, decide_uninstall(u_plan, "approve"), self.project, root=ROOT)
        self.assertEqual("applied", u_res["status"])
        self.assertFalse((self.project / ".claude/skills/vcbs-manager/SKILL.md").exists())
        self.assertFalse((self.project / MANIFEST_PATH).exists())
        # manifest.yaml must have no stale rows and be removed/unlinked
        self.assertFalse((self.project / ".vibe-coding-bootstrap/manifest.yaml").exists())

        st = get_status(self.project, host="claude", root=ROOT)
        self.assertEqual("uninstalled", st["status"])

        # Re-deploy works cleanly
        plan_re = build_plan(self.project, host="claude", semantic_result_path=sem_path, root=ROOT)
        res_re = apply_plan(plan_re, decide(plan_re, "approve"), self.project, root=ROOT)
        self.assertEqual("applied", res_re["status"])
        self.assertTrue((self.project / ".claude/skills/vcbs-manager/SKILL.md").is_file())

        # Test uninstall by host alias "claude"
        u_plan_alias = build_uninstall_plan(self.project, component_id="claude", root=ROOT)
        self.assertTrue(u_plan_alias["executable"])
        u_res_alias = apply_uninstall(u_plan_alias, decide_uninstall(u_plan_alias, "approve"), self.project, root=ROOT)
        self.assertEqual("applied", u_res_alias["status"])
        self.assertFalse((self.project / ".claude/skills/vcbs-manager/SKILL.md").exists())
        self.assertFalse((self.project / MANIFEST_PATH).exists())

    def test_coexistence_codex_and_claude_adoption_uninstall_and_rollback(self):
        """Coexistence: uninstalling adopted Claude leaves Codex intact; rollback restores Claude byte-for-byte."""
        # 1. Direct activation of Codex testing
        p_codex = activation_build_plan(self.project, capability="testing", host="codex")
        activation_apply(p_codex, activation_make_decision(p_codex, "approve"), self.project)

        # 2. Adopt Claude vcbs-manager
        (self.project / "CLAUDE.md").write_text("# Guidelines\n", encoding="utf-8")
        git(self.project, "add", ".")
        git(self.project, "commit", "-qm", "guidelines")

        inspected = inspect_project(self.project, host="claude", root=ROOT)
        sem_path = Path(self.temp.name) / "sem-coexist.json"
        self._make_semantic_result(inspected["semantic_package"], sem_path)

        plan = build_plan(self.project, host="claude", semantic_result_path=sem_path, root=ROOT)
        apply_plan(plan, decide(plan, "approve"), self.project, root=ROOT)

        codex_skill = self.project / ".agents/skills/bootstrap-testing/SKILL.md"
        claude_skill = self.project / ".claude/skills/vcbs-manager/SKILL.md"
        manifest_file = self.project / ".vibe-coding-bootstrap/manifest.yaml"
        self.assertTrue(codex_skill.is_file())
        self.assertTrue(claude_skill.is_file())
        self.assertTrue(manifest_file.is_file())

        pre_codex_skill_bytes = codex_skill.read_bytes()
        pre_claude_skill_bytes = claude_skill.read_bytes()
        pre_manifest_bytes = manifest_file.read_bytes()
        pre_act_bytes = (self.project / MANIFEST_PATH).read_bytes()

        # 3. Uninstall Claude only
        u_plan = build_uninstall_plan(self.project, component_id="claude-native-vcbs-manager-skill", root=ROOT)
        self.assertEqual(1, len(u_plan["removals"]))
        self.assertIn(".vibe-coding-bootstrap/manifest.yaml", u_plan.get("manifest_updates", []))

        u_res = apply_uninstall(u_plan, decide_uninstall(u_plan, "approve"), self.project, root=ROOT)
        self.assertEqual("applied", u_res["status"])

        # Claude skill removed
        self.assertFalse(claude_skill.exists())

        # Codex skill, AGENTS.md, and Codex activation remain intact!
        self.assertTrue(codex_skill.is_file())
        self.assertEqual(pre_codex_skill_bytes, codex_skill.read_bytes())
        self.assertTrue((self.project / "AGENTS.md").is_file())
        self.assertTrue((self.project / MANIFEST_PATH).is_file())
        act_data = json.loads((self.project / MANIFEST_PATH).read_text(encoding="utf-8"))
        self.assertEqual(1, len(act_data["activations"]))
        self.assertEqual("codex", act_data["activations"][0]["host"])

        # Second uninstall is no_changes
        u_plan_repeat = build_uninstall_plan(self.project, component_id="claude-native-vcbs-manager-skill", root=ROOT)
        self.assertEqual(0, u_plan_repeat["actual_changes"])
        self.assertEqual([], u_plan_repeat["removals"])
        res_no_changes = uninstall(self.project, component_id="claude-native-vcbs-manager-skill", root=ROOT)
        self.assertEqual("no_changes", res_no_changes["status"])

        # Rollback restores Claude byte-for-byte alongside Codex
        rb = rollback(self.project, u_res["deployment_id"], apply=True)
        self.assertEqual("rolled_back", rb["status"])
        self.assertTrue(claude_skill.is_file())
        self.assertEqual(pre_claude_skill_bytes, claude_skill.read_bytes())
        self.assertEqual(pre_codex_skill_bytes, codex_skill.read_bytes())
        self.assertEqual(pre_manifest_bytes, manifest_file.read_bytes())
        self.assertEqual(pre_act_bytes, (self.project / MANIFEST_PATH).read_bytes())

    def test_antigravity_codex_shared_surface_warning_and_status(self):
        """Antigravity plan emits shared-surface warning for Codex, rendered in summary, and reported in status."""
        (self.project / "CLAUDE.md").write_text("# Project Guidelines\n", encoding="utf-8")
        git(self.project, "add", ".")
        git(self.project, "commit", "-qm", "guidelines")

        inspected = inspect_project(self.project, host="antigravity", root=ROOT)
        sem_path = Path(self.temp.name) / "sem-ag-warning.json"
        self._make_semantic_result(inspected["semantic_package"], sem_path)

        # Plan with antigravity
        plan = build_plan(self.project, host="antigravity", semantic_result_path=sem_path, root=ROOT)
        self.assertIn("warnings", plan)
        ag_warning = next((w for w in plan["warnings"] if "Codex" in w and ".agents/skills" in w), None)
        self.assertIsNotNone(ag_warning, f"Expected Antigravity shared-surface warning in plan: {plan.get('warnings')}")
        self.assertIn("AGENTS.md", ag_warning)

        # Summary renders warning
        summary = render_summary(plan)
        self.assertIn("Codex", summary)
        self.assertIn(".agents/skills", summary)

        # Apply plan
        res = apply_plan(plan, decide(plan, "approve"), self.project, root=ROOT)
        self.assertEqual("applied", res["status"])

        # Status inspection reports shared surface
        st_ag = get_status(self.project, host="antigravity", root=ROOT)
        self.assertEqual("installed", st_ag["status"])
        self.assertTrue(len(st_ag.get("native_activations", [])) > 0)
        ag_act = next((a for a in st_ag["native_activations"] if a.get("host") == "antigravity"), None)
        self.assertIsNotNone(ag_act)
        self.assertEqual("codex", ag_act.get("shared_surface_with"))
        self.assertIn("Codex", ag_act.get("shared_surface_notice", ""))

        # Codex status inspection on the same repo handles presence cleanly without conflicts
        st_codex = get_status(self.project, host="codex", root=ROOT)
        self.assertEqual("installed", st_codex["status"])
        self.assertFalse(st_codex["has_conflicts"])

    def test_committed_evidence_recomputable_plan_and_skill_digests(self):
        """Committed activation evidence in examples/activation/claude/ is authentic and recomputable."""
        plan_path = ROOT / "examples/activation/claude/activation-plan.json"
        result_path = ROOT / "examples/activation/claude/activated-result.json"

        self.assertTrue(plan_path.is_file())
        self.assertTrue(result_path.is_file())

        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        result = json.loads(result_path.read_text(encoding="utf-8"))

        # Recompute plan_digest directly from disk representation
        recomputed_plan_digest = plan_digest(plan)
        self.assertEqual(plan["plan_digest"], recomputed_plan_digest)
        self.assertEqual("17999427b88c7f8786773a6f1b8dc41e1a97ef58e22862ed4612120b97c43f94", recomputed_plan_digest)

        # Verify exact skill file digest recomputes from template
        skill_template = ROOT / "host_adapters/claude/skills/vcbs-manager/SKILL.md"
        expected_skill_sha = hashlib.sha256(skill_template.read_bytes()).hexdigest()
        self.assertEqual(result["production_skill_sha256"], expected_skill_sha)

        # Verify result status and honest reporting
        self.assertEqual("NOT_TESTED", result["run"])
        self.assertIn("interactive permission approval", result["reason"])
        self.assertTrue(result["unit_tests_passed"])
        self.assertTrue(result["filesystem_isolation_verified"])

    def test_upgrade_native_skill_claude_and_antigravity(self):
        """Upgrades to host_adapters template in VCBS root are detected and applied cleanly."""
        vcbs_root = Path(self.temp.name) / "vcbs"
        vcbs_root.mkdir()
        for name in ["schemas", "capabilities", "composer", "profiles", "experiences", "components", "host_adapters", "assembly", "examples", "adoption"]:
            src = ROOT / name
            if src.is_dir():
                shutil.copytree(src, vcbs_root / name)

        (self.project / "CLAUDE.md").write_text("# Project Guidelines\n", encoding="utf-8")
        git(self.project, "add", ".")
        git(self.project, "commit", "-qm", "guidelines")

        inspected = inspect_project(self.project, host="claude", root=vcbs_root)
        sem_path = Path(self.temp.name) / "sem-up.json"
        self._make_semantic_result(inspected["semantic_package"], sem_path)

        plan = build_plan(self.project, host="claude", semantic_result_path=sem_path, root=vcbs_root)
        apply_plan(plan, decide(plan, "approve"), self.project, root=vcbs_root)

        skill_file = self.project / ".claude/skills/vcbs-manager/SKILL.md"
        self.assertTrue(skill_file.is_file())

        # Mutate VCBS root Claude skill template
        updated_skill = "---\nname: vcbs-manager\ndescription: Manage VCBS v2\n---\n# VCBS Manager v2\nUpdated instructions.\n"
        (vcbs_root / "host_adapters/claude/skills/vcbs-manager/SKILL.md").write_text(updated_skill, encoding="utf-8")

        # Status detects update available
        st = get_status(self.project, host="claude", root=vcbs_root)
        self.assertEqual("update_available", st["status"])
        self.assertTrue(st["update_available"])

        # Preview upgrade
        up_prev = upgrade(self.project, host="claude", apply=False, root=vcbs_root)
        self.assertEqual("preview", up_prev["status"])
        self.assertIn("Upgrades:", up_prev["summary"])

        # Apply upgrade
        up_dec = decide(up_prev["plan"], "approve")
        up_res = upgrade(self.project, host="claude", apply=True, decision=up_dec, plan=up_prev["plan"], root=vcbs_root)
        self.assertEqual("applied", up_res["status"])
        self.assertEqual(updated_skill, skill_file.read_text(encoding="utf-8"))

        # Status after upgrade is installed and up-to-date
        st2 = get_status(self.project, host="claude", root=vcbs_root)
        self.assertEqual("installed", st2["status"])
        self.assertFalse(st2["update_available"])

        # Repeat upgrade is no_changes
        repeat = upgrade(self.project, host="claude", apply=False, root=vcbs_root)
        self.assertEqual("no_changes", repeat["status"])

    def test_rapid_consecutive_deployments_disambiguate_deployment_id(self):
        """Rapid consecutive deployments in the same second disambiguate deployment_id without SNAPSHOT_FAILED."""
        from bootstrap.recovery import prepare_deployment
        plan = {"plan_digest": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890", "project_changes": [], "host_activations": []}
        dep1 = prepare_deployment(plan, self.project)
        dep2 = prepare_deployment(plan, self.project)
        self.assertNotEqual(dep1["deployment_id"], dep2["deployment_id"])
        self.assertTrue(dep2["deployment_id"].startswith(dep1["deployment_id"] + "-"))


