"""Copy Bootstrap-owned Assembly items into explicit managed roots."""

from datetime import datetime, timezone
import hashlib
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit

import yaml

from composer.core import ROOT, validate


def _relative_path(value, label):
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (not value or path.is_absolute() or PureWindowsPath(value).is_absolute()
            or PureWindowsPath(value).drive or ".." in path.parts):
        raise ValueError(f"{label} must be a relative path without '..': {value}")
    return path


def _contained(root, relative, label):
    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError(f"{label} cannot traverse a symlink: {candidate}")
    target = candidate.resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"{label} escapes its root: {relative.as_posix()}")
    return target


def _sha256(content):
    return hashlib.sha256(content).hexdigest()


def materialize(assembly_plan, project_root, global_root=None, apply=False,
                root=ROOT, assembly_source="assembly plan (in-memory input)"):
    """Preview or copy an Assembly Plan; writes only when apply is explicitly true."""
    validate(assembly_plan, "assembly-plan.schema.json", root)
    repository_root = Path(root).resolve()
    project = Path(project_root).resolve()
    global_path = Path(global_root).resolve() if global_root is not None else None
    if not project.is_dir():
        raise ValueError(f"Project root must be an existing directory: {project}")
    manifest_relative = PurePosixPath(".vibe-coding-bootstrap/manifest.yaml")
    manifest = _contained(project, manifest_relative, "Manifest")
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    items = []
    pending = []
    previous = None
    valid_manifest = True
    if manifest.exists():
        loaded = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or loaded.get("format_version") != 1 or not isinstance(loaded.get("items"), list):
            valid_manifest = False
        else:
            previous = loaded

    for item in [*assembly_plan["components"], *assembly_plan["skipped_components"]]:
        result = {key: item[key] for key in ("component_id", "source_capability", "scope", "delivery", "source")}
        result["capability_version"] = item.get("capability_version", "unspecified")
        reason = None
        if item["action"] == "skip":
            result["result"], reason = "skipped", "Assembly Plan marks this component skipped."
        elif item["scope"] == "external" or item["delivery"] == "reference":
            result["result"] = "reference_only"
        elif item["delivery"] != "copy":
            result["result"], reason = "unsupported", "Phase 6 Installer MVP only supports copy/reference."
        elif item["action"] == "review_required":
            result["result"], reason = "skipped", "Assembly Plan requires review before materialization."
        elif item["scope"] == "global" and global_path is None:
            result["result"], reason = "skipped", "Global copy requires an explicit --global-root."
        else:
            source_relative = _relative_path(item["source"], "Source")
            source = _contained(repository_root, source_relative, "Source")
            if urlsplit(item["source"]).scheme or not source.is_file():
                raise ValueError(f"Copy source must be a repository-local file: {item['source']}")
            destination_relative = _relative_path(item["destination"], "Destination")
            target_root = project if item["scope"] == "project" else global_path
            target = _contained(target_root, destination_relative, "Destination")
            if target == manifest:
                raise ValueError("Component destination cannot replace the materialization manifest.")
            content = source.read_bytes()
            digest = _sha256(content)
            result.update(destination=str(target), sha256=digest)
            state = "copied"
            if target.exists():
                existing_digest = _sha256(target.read_bytes()) if target.is_file() else None
                old_entry = next((entry for entry in (previous or {}).get("items", [])
                                  if entry.get("scope") == item["scope"]
                                  and entry.get("destination") == destination_relative.as_posix()), None)
                if old_entry and existing_digest != old_entry.get("sha256"):
                    state = "conflict"
                    reason = "Bootstrap-managed destination was modified; it was not overwritten."
                elif existing_digest == digest:
                    state = "unchanged"
                else:
                    state = "conflict"
                    reason = "Destination already exists with different content; it was not overwritten."
            result["result"] = state
            pending.append((target, content, result))
        if reason:
            result["reason"] = reason
        items.append(result)

    conflicts = [item for item in items if item["result"] == "conflict"]
    if not valid_manifest:
        for item in items:
            if item["result"] in {"copied", "unchanged", "reference_only"}:
                item["result"] = "conflict"
                item["reason"] = "Existing manifest is not a Bootstrap materialization manifest; it was not overwritten."
        conflicts = [item for item in items if item["result"] == "conflict"]
    manifest_path = str(manifest) if apply and not conflicts else None
    if apply and not conflicts:
        for target, content, result in pending:
            if result["result"] == "copied":
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        manifest_items = []
        if previous:
            manifest_items = previous.get("items", [])
        keyed = {(entry["scope"], entry["component_id"], entry["destination"]): entry
                 for entry in manifest_items}
        for item in items:
            if item["result"] in {"copied", "unchanged", "reference_only"}:
                entry = {
                    "component_id": item["component_id"],
                    "source_capability": item["source_capability"],
                    "capability_version": item["capability_version"],
                    "scope": item["scope"], "source": item["source"],
                    "destination": item.get("destination", ""),
                    "state": "reference_only" if item["result"] == "reference_only" else "materialized",
                }
                if "sha256" in item:
                    entry["sha256"] = item["sha256"]
                keyed[(entry["scope"], entry["component_id"], entry["destination"])] = entry
        manifest_data = {
            "format_version": 1, "assembly_source": assembly_source,
            "installed_at": timestamp, "items": list(keyed.values()),
        }
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(yaml.safe_dump(manifest_data, sort_keys=False, allow_unicode=True), encoding="utf-8")

    result = {
        "assembly_source": assembly_source,
        "project_root": str(project),
        "global_root": str(global_path) if global_path else None,
        "applied_at": timestamp,
        "mode": "applied" if apply else "dry_run",
        "items": items,
        "manifest_path": manifest_path,
    }
    validate(result, "materialization-result.schema.json", root)
    return result
