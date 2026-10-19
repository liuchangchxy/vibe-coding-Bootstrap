"""Local Git-native deployment snapshots and conflict-safe recovery."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess
import tempfile


class RecoveryError(ValueError):
    pass


def _git(project, *args, env=None, input=None):
    result = subprocess.run(["git", "-C", str(project), *map(str, args)], input=input,
                            capture_output=True, env=env, check=False)
    if result.returncode:
        raise RecoveryError(result.stderr.decode("utf-8", "replace").strip() or "Git recovery command failed")
    return result.stdout.decode("utf-8", "replace").strip()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _git_path(project, relative):
    value = Path(_git(project, "rev-parse", "--git-path", relative))
    return value if value.is_absolute() else (Path(project) / value).resolve()


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_rel(value):
    raw = str(value)
    windows = PureWindowsPath(raw)
    if windows.is_absolute() or windows.drive:
        raise RecoveryError("INVALID_DEPLOYMENT_RECORD: absolute path is not allowed")
    path = PurePosixPath(raw.replace("\\", "/"))
    if not value or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise RecoveryError("INVALID_DEPLOYMENT_RECORD: unsafe relative path")
    return path.as_posix()


def _contained(project, relative):
    rel = _safe_rel(relative)
    target = Path(project).joinpath(*PurePosixPath(rel).parts)
    current = Path(project)
    for part in PurePosixPath(rel).parts:
        current = current / part
        if current.is_symlink():
            raise RecoveryError("INVALID_DEPLOYMENT_RECORD: path traverses a symlink")
    try:
        target.resolve().relative_to(Path(project).resolve())
    except ValueError as exc:
        raise RecoveryError("INVALID_DEPLOYMENT_RECORD: path escapes project") from exc
    return target


def _state(path):
    if not path.exists() and not path.is_symlink():
        return {"exists": False, "sha256": None}
    if path.is_symlink() or not path.is_file():
        raise RecoveryError(f"SNAPSHOT_UNSAFE: unsupported mutation target {path}")
    return {"exists": True, "sha256": _sha(path.read_bytes())}


def _metadata_dir(project, deployment_id):
    if not deployment_id or any(c not in "0123456789tz-abcdef" for c in deployment_id.lower()):
        raise RecoveryError("INVALID_DEPLOYMENT_RECORD: invalid deployment id")
    return _git_path(project, f"vibe-coding-bootstrap/deployments/{deployment_id}")


def _deployment_paths(plan, project):
    paths = {}
    for item in plan.get("project_changes", []):
        if item.get("result") in {"copied", "upgraded", "removed", "uninstalled"}:
            destination = Path(item["destination"]).resolve()
            try:
                rel = destination.relative_to(Path(project).resolve()).as_posix()
            except ValueError as exc:
                raise RecoveryError("SNAPSHOT_UNSAFE: project destination escapes target") from exc
            rel = _safe_rel(rel)
            paths[rel] = {"path": rel, "scope": "project", "source": "materializer", "existed_before": None}
    if any(item.get("result") in {"copied", "upgraded", "removed", "uninstalled", "reference_only"} for item in plan.get("project_changes", [])):
        rel = ".vibe-coding-bootstrap/manifest.yaml"
        paths[rel] = {"path": rel, "scope": "project", "source": "materializer_manifest", "existed_before": None}
    for subplan in plan.get("host_activations", []):
        if subplan.get("status") in {"ready", "deactivating", "removed", "uninstalled"}:
            dest_list = [subplan["destination"], ".vibe-coding-bootstrap/activation.json"]
            if subplan.get("agents_destination"):
                dest_list.append(subplan["agents_destination"])
            for rel in dest_list:
                rel = _safe_rel(rel)
                paths[rel] = {"path": rel, "scope": "project", "source": f"{subplan.get('host', 'codex')}_activation", "existed_before": None}
    for item in plan.get("global_changes", []):
        if item.get("result") in {"copied", "upgraded", "removed", "uninstalled"}:
            if not plan.get("global_root"):
                raise RecoveryError("SNAPSHOT_UNSAFE: global destination requires planned global_root")
            destination = Path(item["destination"]).resolve()
            global_base = Path(plan["global_root"]).resolve()
            try:
                rel = destination.relative_to(global_base).as_posix()
            except ValueError as exc:
                raise RecoveryError("SNAPSHOT_UNSAFE: global destination escapes the planned root") from exc
            rel = _safe_rel(rel)
            paths[f"global:{rel}"] = {"path": rel, "scope": "global", "source": "materializer", "existed_before": None}
    return list(paths.values())


def _snapshot(project, head, deployment_id):
    fd, index = tempfile.mkstemp(prefix="vcc-bootstrap-index-")
    os.close(fd)
    env = dict(os.environ, GIT_INDEX_FILE=index,
               GIT_AUTHOR_NAME="Vibe Coding Bootstrap", GIT_AUTHOR_EMAIL="bootstrap@localhost",
               GIT_COMMITTER_NAME="Vibe Coding Bootstrap", GIT_COMMITTER_EMAIL="bootstrap@localhost")
    try:
        _git(project, "read-tree", head, env=env)
        _git(project, "add", "-A", "--", ".", env=env)
        tree = _git(project, "write-tree", env=env)
        commit = _git(project, "commit-tree", tree, "-p", head,
                      input=f"vibe-coding-bootstrap safety snapshot {deployment_id}\n".encode(), env=env)
        ref = f"refs/vibe-coding-bootstrap/snapshots/{deployment_id}"
        _git(project, "update-ref", ref, commit)
        return ref, commit
    finally:
        try:
            os.unlink(index)
        except FileNotFoundError:
            pass


def prepare_deployment(plan, project, global_root=None):
    deployment_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + plan["plan_digest"][:12]
    metadata = _metadata_dir(project, deployment_id)
    if metadata.exists():
        raise RecoveryError("SNAPSHOT_FAILED: deployment metadata already exists")
    head = _git(project, "rev-parse", "HEAD")
    actions = _deployment_paths(plan, project)
    try:
        for action in actions:
            base = Path(project) if action["scope"] == "project" else Path(global_root)
            target = _contained(base, action["path"])
            state = _state(target)
            action["existed_before"] = state["exists"]
            action["pre_sha256"] = state["sha256"]
            action["pre_state"] = state
            if action["scope"] == "project" and state["exists"]:
                tracked = subprocess.run(["git", "-C", str(project), "ls-files", "--error-unmatch", "--", action["path"]], capture_output=True)
                ignored = subprocess.run(["git", "-C", str(project), "check-ignore", "-q", "--", action["path"]], capture_output=True)
                if tracked.returncode and ignored.returncode == 0:
                    raise RecoveryError("SNAPSHOT_UNSAFE: existing ignored mutation target cannot be recovered")
        ref, commit = _snapshot(project, head, deployment_id)
        metadata.mkdir(parents=True, exist_ok=False)
        (metadata / "global-backups").mkdir()
        record = {"format_version": 1, "deployment_id": deployment_id, "status": "prepared",
                  "plan_digest": plan["plan_digest"], "target_head": head, "snapshot_ref": ref,
                  "snapshot_commit": commit, "created_at": _utc(), "applied_at": None,
                  "rolled_back_at": None, "project_root": str(Path(project).resolve()),
                  "global_root": str(Path(global_root).resolve()) if global_root else None,
                  "actions": actions, "verification": {}}
        for index, action in enumerate(actions):
            if action["scope"] == "global" and action["existed_before"]:
                target = _contained(global_root, action["path"])
                (metadata / "global-backups" / f"{index:04d}.bin").write_bytes(target.read_bytes())
                action["backup"] = f"global-backups/{index:04d}.bin"
        (metadata / "record.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        return record
    except Exception as exc:
        raise RecoveryError(f"SNAPSHOT_FAILED: {exc}") from exc


def update_record(project, record, status):
    record["status"] = status
    if status == "applied":
        record["applied_at"] = _utc()
    for action in record["actions"]:
        base = Path(project) if action["scope"] == "project" else Path(record["global_root"])
        try:
            action["post_state"] = _state(_contained(base, action["path"]))
        except (OSError, RecoveryError):
            action["post_state"] = {"exists": False, "sha256": None}
    metadata = _metadata_dir(project, record["deployment_id"])
    (metadata / "record.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def update_record_after_failure(project, record):
    """Persist observed post-state and classify from mutations to deployment paths."""
    mutated = False
    for action in record["actions"]:
        base = Path(project) if action["scope"] == "project" else Path(record["global_root"])
        try:
            state = _state(_contained(base, action["path"]))
        except (OSError, RecoveryError):
            state = {"exists": False, "sha256": None}
        action["post_state"] = state
        if state != action.get("pre_state"):
            mutated = True
    record["status"] = "partial" if mutated else "aborted"
    metadata = _metadata_dir(project, record["deployment_id"])
    (metadata / "record.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return mutated


def load_record(project, deployment_id):
    path = _metadata_dir(project, deployment_id) / "record.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if (record.get("format_version") != 1 or record.get("deployment_id") != deployment_id
                or record.get("status") not in {"prepared", "applied", "partial", "rolled_back", "aborted"}
                or not isinstance(record.get("actions"), list)):
            raise ValueError("invalid structure")
        project_root = Path(project).resolve()
        if Path(record.get("project_root", "")).resolve() != project_root:
            raise ValueError("project root mismatch")
        expected = f"refs/vibe-coding-bootstrap/snapshots/{deployment_id}"
        if record.get("snapshot_ref") != expected or _git(project, "rev-parse", expected) != record.get("snapshot_commit"):
            raise ValueError("snapshot ref mismatch")
        for action in record["actions"]:
            if action.get("scope") not in {"project", "global"}:
                raise ValueError("invalid scope")
            rel = _safe_rel(action.get("path"))
            base = project_root if action["scope"] == "project" else Path(record.get("global_root") or "")
            _contained(base, rel)
        return record
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, RecoveryError) as exc:
        raise RecoveryError(f"INVALID_DEPLOYMENT_RECORD: {exc}") from exc


def list_deployments(project):
    """List deployment records for a project, sorted newest first."""
    try:
        base = _git_path(project, "vibe-coding-bootstrap/deployments")
    except Exception:
        return []
    if not base.is_dir():
        return []
    records = []
    for item in sorted(base.iterdir(), reverse=True):
        if item.is_dir() and (item / "record.json").is_file():
            try:
                records.append(load_record(project, item.name))
            except Exception:
                pass
    return records


def rollback(project, deployment_id, apply=False):
    project = Path(project).resolve(strict=True)
    record = load_record(project, deployment_id)
    if record["status"] == "rolled_back":
        return {"status": "no_changes", "deployment_id": deployment_id, "actions": []}
    if record["status"] not in {"applied", "partial"}:
        raise RecoveryError("ROLLBACK_UNAVAILABLE: deployment is not applied or partial")
    conflicts, actions = [], []
    for action in record["actions"]:
        base = project if action["scope"] == "project" else Path(record["global_root"])
        target = _contained(base, action["path"])
        expected = action.get("post_state")
        current = _state(target)
        if expected is None or current != expected:
            conflicts.append(action["path"])
        actions.append((action, target))
    preview = {"status": "blocked" if conflicts else "ready", "deployment_id": deployment_id,
               "conflicts": conflicts, "actions": [a["path"] for a, _ in actions], "applied": bool(apply)}
    if conflicts:
        if apply:
            raise RecoveryError("ROLLBACK_CONFLICT: " + ", ".join(conflicts))
        return preview
    if not apply:
        return preview
    global_dirs = {target.parent for action, target in actions if action["scope"] == "global"}
    snapshot = record["snapshot_commit"]
    for action, target in actions:
        if action["scope"] == "global":
            if action["existed_before"]:
                backup = _metadata_dir(project, deployment_id) / action["backup"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(backup.read_bytes())
            elif target.exists():
                target.unlink()
        else:
            rel = action["path"]
            exists = subprocess.run(["git", "-C", str(project), "cat-file", "-e", f"{snapshot}:{rel}"], capture_output=True).returncode == 0
            if exists:
                data = subprocess.run(["git", "-C", str(project), "show", f"{snapshot}:{rel}"], capture_output=True, check=True).stdout
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            elif target.exists():
                target.unlink()
    for directory in sorted({target.parent for action, target in actions if action["scope"] == "project"}, key=lambda p: len(p.parts), reverse=True):
        current = directory
        while current != project:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent
    for directory in sorted(global_dirs, key=lambda p: len(p.parts), reverse=True):
        root = Path(record["global_root"])
        current = directory
        while current != root and current.is_relative_to(root):
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent
    for action, target in actions:
        wanted = action["pre_state"]
        if _state(target) != wanted:
            raise RecoveryError(f"ROLLBACK_VERIFICATION_FAILED: {action['path']}")
    record["status"] = "rolled_back"
    record["rolled_back_at"] = _utc()
    record["verification"] = {"passed": True}
    ( _metadata_dir(project, deployment_id) / "record.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return {"status": "rolled_back", "deployment_id": deployment_id, "conflicts": [],
            "actions": [a["path"] for a, _ in actions], "verification": {"passed": True}}
