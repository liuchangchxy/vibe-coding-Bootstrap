"""Thin, revision-bound Codex repo-local Skill activation adapter."""

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


def _owned_artifacts(skill_digest, agents_digest):
    return [
        {"destination": SKILL_DESTINATION, "sha256": skill_digest, "source": SKILL_SOURCE.as_posix()},
        {"destination": AGENTS_DESTINATION, "sha256": agents_digest, "source": "codex-adapter:trigger"},
    ]


def plan_digest(plan):
    return _sha256(_canonical({key: value for key, value in plan.items()
                               if key not in {"plan_digest", "generated_at"}}))


def build_plan(project_root, capability="testing", host="codex", root=ROOT):
    """Describe the one supported projection without modifying the target."""
    project = Path(project_root).resolve(strict=True)
    if not project.is_dir():
        raise ValueError("Target must be an existing Git repository directory")
    if host != "codex":
        raise ValueError(f"Unsupported host: {host}")
    if capability != "testing":
        raise ValueError(f"Unsupported capability: {capability}")
    state = _state(project)
    content = (Path(root).resolve() / SKILL_SOURCE).read_bytes()
    skill_digest = _sha256(content)
    agents_digest = _sha256(AGENTS_TRIGGER.encode("utf-8"))
    destination = _contained(project, SKILL_DESTINATION)
    agents = _contained(project, AGENTS_DESTINATION)
    manifest = _contained(project, MANIFEST_PATH)
    status = "ready"
    is_upgrade = False
    old_artifacts = None
    if destination.exists():
        if destination.is_file() and _sha256(destination.read_bytes()) == skill_digest:
            if manifest.exists():
                try:
                    installed = json.loads(manifest.read_text(encoding="utf-8"))
                    expected = _owned_artifacts(skill_digest, agents_digest)
                    status = "already_active" if (
                        installed.get("format_version") == 1
                        and installed.get("host") == "codex"
                        and installed.get("capability") == "testing"
                        and installed.get("artifacts") == expected
                        and agents.is_file()
                        and _sha256(agents.read_bytes()) == agents_digest
                    ) else "conflict"
                except (OSError, AttributeError, json.JSONDecodeError):
                    status = "conflict"
            else:
                status = ("already_active" if agents.is_file()
                          and _sha256(agents.read_bytes()) == agents_digest
                          else "review_required")
        else:
            if manifest.exists():
                try:
                    installed = json.loads(manifest.read_text(encoding="utf-8"))
                    if (installed.get("format_version") == 1 and installed.get("host") == "codex"
                            and installed.get("capability") == "testing"
                            and isinstance(installed.get("artifacts"), list)):
                        inst_skill = next((a for a in installed["artifacts"] if a.get("destination") == SKILL_DESTINATION), None)
                        inst_agents = next((a for a in installed["artifacts"] if a.get("destination") == AGENTS_DESTINATION), None)
                        if (inst_skill and inst_agents
                                and destination.is_file() and _sha256(destination.read_bytes()) == inst_skill.get("sha256")
                                and agents.is_file() and _sha256(agents.read_bytes()) == inst_agents.get("sha256")):
                            status = "ready"
                            is_upgrade = True
                            old_artifacts = installed["artifacts"]
                        else:
                            status = "conflict"
                    else:
                        status = "conflict"
                except (OSError, AttributeError, json.JSONDecodeError):
                    status = "conflict"
            else:
                status = "conflict"
    if agents.exists() and not manifest.exists():
        if not (destination.is_file() and _sha256(destination.read_bytes()) == skill_digest
                and agents.is_file() and _sha256(agents.read_bytes()) == agents_digest):
            status = "review_required"
    elif manifest.exists() and status != "already_active" and not is_upgrade:
        status = "conflict"
    plan = {
        "format_version": 1, "host": host, "target": str(project),
        "target_commit_sha": state["commit_sha"], "target_dirty": state["dirty"],
        "capability_id": capability, "source": SKILL_SOURCE.as_posix(),
        "destination": SKILL_DESTINATION, "agents_destination": AGENTS_DESTINATION,
        "status": status,
        "requires_confirmation": True,
    }
    if is_upgrade:
        plan["is_upgrade"] = True
        plan["old_artifacts"] = old_artifacts
        inst_skill = next((a for a in old_artifacts if a.get("destination") == SKILL_DESTINATION), {})
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
    if (plan.get("format_version") != 1 or plan.get("host") != "codex"
            or plan.get("capability_id") != "testing"
            or plan.get("source") != SKILL_SOURCE.as_posix()
            or plan.get("destination") != SKILL_DESTINATION
            or plan.get("agents_destination") != AGENTS_DESTINATION
            or plan.get("status") not in {"ready", "already_active", "conflict", "review_required", "unsupported"}):
        raise ValueError("Invalid or unsupported Activation Plan")
    sha = plan.get("target_commit_sha", "")
    if (len(sha) != 40 or any(character not in "0123456789abcdef" for character in sha)
            or not isinstance(plan.get("target_dirty"), bool)
            or plan.get("requires_confirmation") is not True):
        raise ValueError("Invalid or unsupported Activation Plan")


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
    if state["commit_sha"] != plan["target_commit_sha"] or state["dirty"] != plan["target_dirty"]:
        raise ValueError("STALE_ACTIVATION_PLAN: target revision or dirty state changed")
    if plan["host"] != "codex" or plan["capability_id"] != "testing":
        raise ValueError("Unsupported Activation Plan")
    if plan["status"] not in {"ready", "already_active"}:
        raise ValueError(f"Activation Plan cannot be applied: {plan['status']}")
    destination = _contained(project, plan["destination"])
    agents = _contained(project, plan["agents_destination"])
    manifest_path = _contained(project, MANIFEST_PATH)
    content = (Path(root).resolve() / plan["source"]).read_bytes()
    skill_digest = _sha256(content)
    agents_content = AGENTS_TRIGGER.encode("utf-8")
    agents_digest = _sha256(agents_content)
    if plan["status"] == "already_active":
        if (not destination.is_file() or _sha256(destination.read_bytes()) != skill_digest
                or not agents.is_file() or _sha256(agents.read_bytes()) != agents_digest):
            raise ValueError("Codex activation artifacts changed after planning")
        if not manifest_path.is_file():
            return {"status": "already_active", "sha256": skill_digest}
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (manifest.get("format_version") != 1
                or manifest.get("host") != "codex" or manifest.get("capability") != "testing"
                or manifest.get("artifacts") != _owned_artifacts(skill_digest, agents_digest)):
            raise ValueError("Activation ownership conflict")
        return {"status": "already_active", "sha256": skill_digest}
    if plan.get("is_upgrade"):
        for art in plan.get("old_artifacts", []):
            dest = _contained(project, art["destination"])
            if not dest.is_file() or _sha256(dest.read_bytes()) != art.get("sha256"):
                raise ValueError("Codex activation target was modified before upgrade; aborted")
    elif destination.exists() or agents.exists() or manifest_path.exists():
        raise ValueError("Activation destination conflict; existing content was not overwritten")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    agents.write_bytes(agents_content)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "format_version": 1, "host": "codex", "capability": "testing",
        "artifacts": _owned_artifacts(skill_digest, agents_digest),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"status": "activated", "sha256": skill_digest}


def deactivate(project_root, host="codex", capability="testing"):
    """Remove only the unchanged Skill named by Bootstrap's ownership manifest."""
    if host != "codex" or capability != "testing":
        raise ValueError("Unsupported deactivation target")
    project = Path(project_root).resolve(strict=True)
    manifest_path = _contained(project, MANIFEST_PATH)
    if not manifest_path.is_file():
        return {"status": "not_active"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("format_version") != 1 or manifest.get("host") != host
            or manifest.get("capability") != capability or not isinstance(manifest.get("artifacts"), list)):
        return {"status": "conflict"}
    destinations = {entry.get("destination") for entry in manifest["artifacts"]}
    if destinations != {SKILL_DESTINATION, AGENTS_DESTINATION}:
        return {"status": "conflict"}
    files = []
    for entry in manifest["artifacts"]:
        destination = _contained(project, entry["destination"])
        if not destination.is_file() or _sha256(destination.read_bytes()) != entry.get("sha256"):
            return {"status": "conflict"}
        files.append(destination)
    for destination in files:
        destination.unlink()
    manifest_path.unlink()
    return {"status": "deactivated"}
