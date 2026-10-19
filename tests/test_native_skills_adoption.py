"""Tests for Claude Code and Antigravity native Skill adoption and activation."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from activation.core import (
    MANIFEST_PATH, apply as activation_apply, build_plan as activation_build_plan,
    deactivate as activation_deactivate, make_decision as activation_make_decision,
)
from bootstrap.core import (
    ProductLoopError, apply_plan, build_plan, decide, inspect_project, render_summary,
)
from bootstrap.lifecycle import get_status, rollback
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
        sem_path = Path(self.temp.name) / "semantic-result.json"
        sem_path.write_text(json.dumps(semantic_result, indent=2), encoding="utf-8")

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

        coverage = []
        for cat in package["capability_catalog"]:
            cid = cat["id"]
            if cid == "vcbs-manager":
                coverage.append({
                    "capability_id": cid, "status": "missing", "confidence": "high",
                    "recommended_action": "consider_bootstrap_component",
                    "reason": "Adopt VCBS Manager.", "evidence": [],
                })
            else:
                coverage.append({
                    "capability_id": cid, "status": "unknown", "confidence": "high",
                    "recommended_action": "no_action", "reason": "Out of scope.", "evidence": [],
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
        sem_path = Path(self.temp.name) / "multi-sem.json"
        sem_path.write_text(json.dumps(semantic_result, indent=2), encoding="utf-8")

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
