"""Thin, revision-bound Codex, Claude Code, and Antigravity repo-local Skill activation adapter."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess

from composer.core import ROOT


SKILL_SOURCE = Path("host_adapters/codex/skills/testing/SKILL.md")
SKILL_DESTINATION = ".agents/skills/bootstrap-testing/SKILL.md"
AGENTS_DESTINATION = "AGENTS.md"
AGENTS_TRIGGER = "For implementation or bug-fix tasks that change observable behavior, use the $bootstrap-testing skill.\n"
MANIFEST_PATH = ".vibe-coding-bootstrap/activation.json"

ACTIVATION_TARGETS = {
    ("codex", "testing"): {
        "skill_source": Path("host_adapters/codex/skills/testing/SKILL.md"),
        "skill_destination": ".agents/skills/bootstrap-testing/SKILL.md",
        "agents_destination": "AGENTS.md",
        "agents_trigger": "For implementation or bug-fix tasks that change observable behavior, use the $bootstrap-testing skill.\n",
    },
    ("claude", "vcbs-manager"): {
        "skill_source": Path("host_adapters/claude/skills/vcbs-manager/SKILL.md"),
        "skill_destination": ".claude/skills/vcbs-manager/SKILL.md",
        "agents_destination": None,
        "agents_trigger": None,
    },
    ("claude_code", "vcbs-manager"): {
        "skill_source": Path("host_adapters/claude/skills/vcbs-manager/SKILL.md"),
        "skill_destination": ".claude/skills/vcbs-manager/SKILL.md",
        "agents_destination": None,
        "agents_trigger": None,
    },
    ("antigravity", "vcbs-manager"): {
        "skill_source": Path("host_adapters/antigravity/skills/vcbs-manager/SKILL.md"),
        "skill_destination": ".agents/skills/vcbs-manager/SKILL.md",
        "agents_destination": None,
        "agents_trigger": None,
    },
}


def _get_target_config(host, capability):
    return ACTIVATION_TARGETS.get((host, capability))


def _sha256(value):
    return hashlib.sha256(value).hexdigest()


def _git(project, *args):
    result = subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f"Cannot inspect target Git state: {result.stderr.strip()}")
    return result.stdout.strip()


def _state(project):
    return {"commit_sha": _git(project, "rev-parse", "HEAD"),
            "dirty": bool(_git(project, "status", "--porcelain"))}


def _contained(root, relative):
    if not relative:
        return None
    path = PurePosixPath(relative.replace("\\", "/"))
    if (path.is_absolute() or PureWindowsPath(relative).is_absolute()
            or PureWindowsPath(relative).drive or ".." in path.parts):
        raise ValueError("Activation destination must be a contained relative path")
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Activation path traverses a symlink: {path.as_posix()}")
    resolved = current.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Activation path escapes target root: {path.as_posix()}")
    return resolved


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _owned_artifacts(skill_digest, agents_digest=None, skill_source=None, skill_destination=None, agents_source=None, agents_destination=None):
    s_src = skill_source or SKILL_SOURCE.as_posix()
    s_dst = skill_destination or SKILL_DESTINATION
    artifacts = [{"destination": s_dst, "sha256": skill_digest, "source": s_src}]
    if agents_digest is not None:
        a_src = agents_source or "codex-adapter:trigger"
        a_dst = agents_destination or AGENTS_DESTINATION
        artifacts.append({"destination": a_dst, "sha256": agents_digest, "source": a_src})
    return artifacts


def plan_digest(plan):
    return _sha256(_canonical({key: value for key, value in plan.items()
                               if key not in {"plan_digest", "generated_at"}}))


def build_plan(project_root, capability="testing", host="codex", root=ROOT):
    """Describe the supported host activation without modifying the target."""
    project = Path(project_root).resolve(strict=True)
    if not project.is_dir():
        raise ValueError("Target must be an existing Git repository directory")
    if host not in {"codex", "claude", "claude_code", "antigravity"}:
        raise ValueError(f"Unsupported host: {host}")
    if capability == "testing" and host != "codex":
        raise ValueError(f"Unsupported host: {host}")
    if host == "codex" and capability != "testing":
        raise ValueError(f"Unsupported capability: {capability}")
    target_conf = _get_target_config(host, capability)
    if not target_conf:
        raise ValueError(f"Unsupported capability: {capability}")
    state = _state(project)
    content = (Path(root).resolve() / target_conf["skill_source"]).read_bytes()
    skill_digest = _sha256(content)
    agents_dest = target_conf["agents_destination"]
    if target_conf["agents_trigger"]:
        agents_content = target_conf["agents_trigger"].encode("utf-8")
        agents_digest = _sha256(agents_content)
        agents = _contained(project, agents_dest)
    else:
        agents_content = None
        agents_digest = None
        agents = None
    destination = _contained(project, target_conf["skill_destination"])
    manifest = _contained(project, MANIFEST_PATH)

    manifest_activations = []
    manifest_corrupt = False
    if manifest.exists():
        try:
            installed = json.loads(manifest.read_text(encoding="utf-8"))
            if installed.get("format_version") == 1:
                if "activations" in installed and isinstance(installed["activations"], list):
                    manifest_activations = installed["activations"]
                elif installed.get("host") and installed.get("capability"):
                    manifest_activations = [{
                        "host": installed["host"],
                        "capability": installed["capability"],
                        "artifacts": installed.get("artifacts", [])
                    }]
            else:
                manifest_corrupt = True
        except (OSError, AttributeError, json.JSONDecodeError):
            manifest_corrupt = True

    installed_entry = next((a for a in manifest_activations if a.get("host") == host and a.get("capability") == capability), None)

    status = "ready"
    is_upgrade = False
    old_artifacts = None

    if manifest_corrupt:
        status = "conflict"
    elif destination.exists():
        if destination.is_file() and _sha256(destination.read_bytes()) == skill_digest:
            if manifest.exists():
                expected = _owned_artifacts(skill_digest, agents_digest=agents_digest,
                                           skill_source=target_conf["skill_source"].as_posix(),
                                           skill_destination=target_conf["skill_destination"],
                                           agents_destination=agents_dest)
                if installed_entry and installed_entry.get("artifacts") == expected:
                    if agents_dest is None or (agents and agents.is_file() and _sha256(agents.read_bytes()) == agents_digest):
                        status = "already_active"
                    else:
                        status = "conflict"
                else:
                    status = "conflict"
            else:
                if host == "codex" and agents and agents.is_file() and _sha256(agents.read_bytes()) == agents_digest:
                    status = "already_active"
                else:
                    status = "review_required"
        else:
            if manifest.exists() and installed_entry:
                inst_skill = next((a for a in installed_entry.get("artifacts", []) if a.get("destination") == target_conf["skill_destination"]), None)
                inst_agents = next((a for a in installed_entry.get("artifacts", []) if a.get("destination") == agents_dest), None) if agents_dest else None
                skill_matches_installed = inst_skill and destination.is_file() and _sha256(destination.read_bytes()) == inst_skill.get("sha256")
                agents_matches_installed = True
                if agents_dest:
                    agents_matches_installed = inst_agents and agents and agents.is_file() and _sha256(agents.read_bytes()) == inst_agents.get("sha256")
                if skill_matches_installed and agents_matches_installed:
                    status = "ready"
                    is_upgrade = True
                    old_artifacts = installed_entry["artifacts"]
                else:
                    status = "conflict"
            else:
                status = "conflict"
    else:
        if manifest.exists() and installed_entry:
            status = "conflict"
        elif agents and agents.exists() and not manifest.exists():
            status = "review_required"
        elif manifest.exists() and not installed_entry and not manifest_activations:
            status = "conflict"
        else:
            status = "ready"

    plan = {
        "format_version": 1, "host": host, "target": str(project),
        "target_commit_sha": state["commit_sha"], "target_dirty": state["dirty"],
        "capability_id": capability, "source": target_conf["skill_source"].as_posix(),
        "source_sha256": skill_digest,
        "destination": target_conf["skill_destination"], "agents_destination": agents_dest,
        "status": status,
        "requires_confirmation": True,
    }
    if is_upgrade:
        plan["is_upgrade"] = True
        plan["old_artifacts"] = old_artifacts
        inst_skill = next((a for a in old_artifacts if a.get("destination") == target_conf["skill_destination"]), {})
        plan["old_skill_sha256"] = inst_skill.get("sha256")
        plan["new_skill_sha256"] = skill_digest
    plan["plan_digest"] = plan_digest(plan)
    return plan


def make_decision(plan, decision):
    _validate_plan(plan)
    if decision not in {"approve", "reject"}:
        raise ValueError("Decision must be approve or reject")
    if plan.get("plan_digest") != plan_digest(plan):
        raise ValueError("Activation Plan digest mismatch")
    return {"format_version": 1, "plan_digest": plan["plan_digest"],
            "target_commit_sha": plan["target_commit_sha"], "decision": decision,
            "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}


def _validate_plan(plan):
    if plan.get("plan_digest") != plan_digest(plan):
        raise ValueError("Activation Plan digest mismatch")
    host = plan.get("host")
    cap = plan.get("capability_id")
    target_conf = _get_target_config(host, cap)
    if not target_conf:
        raise ValueError(f"Invalid or unsupported Activation Plan host/capability: {host}/{cap}")
    if (plan.get("format_version") != 1
            or plan.get("source") != target_conf["skill_source"].as_posix()
            or plan.get("destination") != target_conf["skill_destination"]
            or plan.get("agents_destination") != target_conf["agents_destination"]
            or plan.get("status") not in {"ready", "already_active", "conflict", "review_required", "unsupported"}):
        raise ValueError("Invalid or unsupported Activation Plan")
    sha = plan.get("target_commit_sha", "")
    if (len(sha) != 40 or any(character not in "0123456789abcdef" for character in sha)
            or not isinstance(plan.get("target_dirty"), bool)
            or plan.get("requires_confirmation") is not True):
        raise ValueError("Invalid or unsupported Activation Plan")


def _is_dirty_only_by_bootstrap(project):
    result = subprocess.run(["git", "-C", str(project), "status", "--porcelain", "-uall"], capture_output=True, text=True)
    if result.returncode != 0:
        return False
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return True
    allowed = {MANIFEST_PATH, ".vibe-coding-bootstrap/manifest.yaml"}
    manifest_path = _contained(project, MANIFEST_PATH)
    if manifest_path and manifest_path.is_file():
        try:
            mdata = json.loads(manifest_path.read_text(encoding="utf-8"))
            for art in mdata.get("artifacts", []):
                allowed.add(art.get("destination"))
            for act in mdata.get("activations", []):
                for art in act.get("artifacts", []):
                    allowed.add(art.get("destination"))
        except Exception:
            return False
    else:
        return False

    for line in lines:
        raw = line[3:].strip()
        parts = raw.split(" -> ")
        target_rel = parts[-1].strip().strip('"').replace("\\", "/")
        if target_rel not in allowed:
            return False
    return True


def apply(plan, decision, project_root, root=ROOT):
    """Apply only the exact approved plan while its repository state is unchanged."""
    project = Path(project_root).resolve(strict=True)
    _validate_plan(plan)
    if decision.get("format_version") != 1 or decision.get("decision") != "approve":
        raise ValueError("Activation requires an approved decision")
    if (decision.get("plan_digest") != plan["plan_digest"]
            or decision.get("target_commit_sha") != plan["target_commit_sha"]):
        raise ValueError("Decision does not match the exact Activation Plan")
    if project != Path(plan["target"]).resolve():
        raise ValueError("Activation target does not match the plan")
    state = _state(project)
    if state["commit_sha"] != plan["target_commit_sha"]:
        raise ValueError("STALE_ACTIVATION_PLAN: target revision or dirty state changed")
    if state["dirty"] != plan["target_dirty"]:
        if not (plan["target_dirty"] is False and state["dirty"] is True and _is_dirty_only_by_bootstrap(project)):
            raise ValueError("STALE_ACTIVATION_PLAN: target revision or dirty state changed")
    host = plan["host"]
    capability = plan["capability_id"]
    target_conf = _get_target_config(host, capability)
    if not target_conf:
        raise ValueError("Unsupported Activation Plan")
    if plan["status"] not in {"ready", "already_active"}:
        raise ValueError(f"Activation Plan cannot be applied: {plan['status']}")
    destination = _contained(project, plan["destination"])
    agents_dest = plan.get("agents_destination")
    agents = _contained(project, agents_dest) if agents_dest else None
    manifest_path = _contained(project, MANIFEST_PATH)
    content = (Path(root).resolve() / plan["source"]).read_bytes()
    skill_digest = _sha256(content)
    if plan.get("source_sha256") != skill_digest:
        raise ValueError("STALE_ACTIVATION_PLAN: Skill source content changed after planning")
    agents_content = target_conf["agents_trigger"].encode("utf-8") if target_conf["agents_trigger"] else None
    agents_digest = _sha256(agents_content) if agents_content else None
    owned = _owned_artifacts(skill_digest, agents_digest=agents_digest,
                             skill_source=plan["source"], skill_destination=plan["destination"],
                             agents_destination=agents_dest)

    if plan["status"] == "already_active":
        if (not destination.is_file() or _sha256(destination.read_bytes()) != skill_digest
                or (agents and (not agents.is_file() or _sha256(agents.read_bytes()) != agents_digest))):
            raise ValueError(f"{host} activation artifacts changed after planning")
        if not manifest_path.is_file():
            return {"status": "already_active", "sha256": skill_digest}
        try:
            mdata = json.loads(manifest_path.read_text(encoding="utf-8"))
            if mdata.get("format_version") != 1:
                raise ValueError("Activation ownership conflict")
            activations = mdata.get("activations") or ([{"host": mdata.get("host"), "capability": mdata.get("capability"), "artifacts": mdata.get("artifacts", [])}] if mdata.get("host") else [])
            entry = next((a for a in activations if a.get("host") == host and a.get("capability") == capability), None)
            if not entry or entry.get("artifacts") != owned:
                raise ValueError("Activation ownership conflict")
        except Exception as exc:
            raise ValueError("Activation ownership conflict") from exc
        return {"status": "already_active", "sha256": skill_digest}

    if plan.get("is_upgrade"):
        for art in plan.get("old_artifacts", []):
            dest = _contained(project, art["destination"])
            if not dest.is_file() or _sha256(dest.read_bytes()) != art.get("sha256"):
                raise ValueError(f"{host} activation target was modified before upgrade; aborted")
    else:
        if destination.exists() or (agents and agents.exists()):
            raise ValueError("Activation destination conflict; existing content was not overwritten")
        if manifest_path.exists():
            try:
                mdata = json.loads(manifest_path.read_text(encoding="utf-8"))
                if mdata.get("format_version") != 1:
                    raise ValueError("Activation destination conflict; existing content was not overwritten")
                activations = mdata.get("activations") or ([{"host": mdata.get("host"), "capability": mdata.get("capability"), "artifacts": mdata.get("artifacts", [])}] if mdata.get("host") else [])
                if any(a.get("host") == host and a.get("capability") == capability for a in activations):
                    raise ValueError("Activation destination conflict; existing content was not overwritten")
            except Exception:
                raise ValueError("Activation destination conflict; existing content was not overwritten")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    if agents and agents_content:
        agents.parent.mkdir(parents=True, exist_ok=True)
        agents.write_bytes(agents_content)

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    existing_activations = []
    if manifest_path.is_file():
        try:
            mdata = json.loads(manifest_path.read_text(encoding="utf-8"))
            if mdata.get("format_version") == 1:
                existing_activations = mdata.get("activations") or ([{"host": mdata.get("host"), "capability": mdata.get("capability"), "artifacts": mdata.get("artifacts", [])}] if mdata.get("host") else [])
        except Exception:
            pass

    existing_activations = [a for a in existing_activations if not (a.get("host") == host and a.get("capability") == capability)]
    existing_activations.append({
        "host": host,
        "capability": capability,
        "artifacts": owned,
    })

    all_artifacts = []
    seen_dest = set()
    for act in existing_activations:
        for art in act.get("artifacts", []):
            if art["destination"] not in seen_dest:
                seen_dest.add(art["destination"])
                all_artifacts.append(art)

    manifest = {
        "format_version": 1,
        "host": host if len(existing_activations) == 1 else "multiple",
        "capability": capability if len(existing_activations) == 1 else "multiple",
        "artifacts": all_artifacts,
        "activations": existing_activations,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"status": "activated", "sha256": skill_digest}


def deactivate(project_root, host="codex", capability="testing"):
    """Remove only the unchanged Skill named by Bootstrap's ownership manifest."""
    target_conf = _get_target_config(host, capability)
    if not target_conf:
        raise ValueError("Unsupported deactivation target")
    project = Path(project_root).resolve(strict=True)
    manifest_path = _contained(project, MANIFEST_PATH)
    if not manifest_path.is_file():
        return {"status": "not_active"}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "conflict"}
    if manifest.get("format_version") != 1:
        return {"status": "conflict"}
    activations = manifest.get("activations") or ([{"host": manifest.get("host"), "capability": manifest.get("capability"), "artifacts": manifest.get("artifacts", [])}] if manifest.get("host") else [])
    entry = next((a for a in activations if a.get("host") == host and a.get("capability") == capability), None)
    if not entry:
        return {"status": "not_active"}

    expected_dests = {target_conf["skill_destination"]}
    if target_conf["agents_destination"]:
        expected_dests.add(target_conf["agents_destination"])
    entry_dests = {a.get("destination") for a in entry.get("artifacts", [])}
    if entry_dests != expected_dests:
        return {"status": "conflict"}

    files = []
    for art in entry["artifacts"]:
        dest = _contained(project, art["destination"])
        if not dest.is_file() or _sha256(dest.read_bytes()) != art.get("sha256"):
            return {"status": "conflict"}
        files.append(dest)

    for dest in files:
        dest.unlink()

    remaining_activations = [a for a in activations if not (a.get("host") == host and a.get("capability") == capability)]
    if not remaining_activations:
        manifest_path.unlink()
    else:
        all_artifacts = []
        seen = set()
        for act in remaining_activations:
            for art in act.get("artifacts", []):
                if art["destination"] not in seen:
                    seen.add(art["destination"])
                    all_artifacts.append(art)
        updated_manifest = {
            "format_version": 1,
            "host": remaining_activations[0]["host"] if len(remaining_activations) == 1 else "multiple",
            "capability": remaining_activations[0]["capability"] if len(remaining_activations) == 1 else "multiple",
            "artifacts": all_artifacts,
            "activations": remaining_activations,
        }
        manifest_path.write_text(json.dumps(updated_manifest, indent=2) + "\n", encoding="utf-8")
    return {"status": "deactivated"}
