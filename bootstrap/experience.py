"""Experience harvest and human review for personal reuse."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlparse

import yaml

from composer.core import ROOT, load_yaml, validate


class ExperienceError(ValueError):
    """User-facing experience workflow error."""


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _git(project, *args):
    result = subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True)
    if result.returncode:
        raise ExperienceError(f"CANNOT_INSPECT_GIT: {result.stderr.strip() or 'Git command failed'}")
    return result.stdout.strip()


# Regex checks for secrets and leaked tokens
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{20,}|bearer\s+[a-zA-Z0-9_\-\.]+)\b", re.IGNORECASE),
    re.compile(r"(?i)\b(password|secret|api_key|token)\s*[:=]\s*[\"']?[^\s\"']{8,}"),
]

LEAKED_PATH_PATTERNS = [
    re.compile(r"/Users/[a-zA-Z0-9_\.\-]+/"),
    re.compile(r"/home/[a-zA-Z0-9_\.\-]+/"),
    re.compile(r"[a-zA-Z]:\\[a-zA-Z0-9_\.\-]+\\"),
]


def _check_privacy(value, path=""):
    """Enforce strict guardrails against secrets, transcripts, and host absolute paths."""
    if isinstance(value, dict):
        for k, v in value.items():
            if str(k).lower() in {"messages", "transcript", "chat_history", "raw_conversation"}:
                raise ExperienceError(f"HARVEST_PRIVACY_VIOLATION: unrestricted chat logs not allowed ({k})")
            _check_privacy(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _check_privacy(item, f"{path}[{i}]")
    elif isinstance(value, str):
        if len(value) > 5000:
            raise ExperienceError(f"HARVEST_PRIVACY_VIOLATION: bulk content dump exceeding size limit at {path}")
        for pattern in SECRET_PATTERNS:
            if pattern.search(value):
                raise ExperienceError(f"HARVEST_PRIVACY_VIOLATION: potential credential or secret detected at {path}")
        for pattern in LEAKED_PATH_PATTERNS:
            if pattern.search(value):
                raise ExperienceError(f"HARVEST_PRIVACY_VIOLATION: host absolute path escaping project detected at {path}")


def _load_mapping(value, label):
    if isinstance(value, (str, Path)):
        source = Path(value)
        if not source.is_file():
            raise ExperienceError(f"{label.upper()}_FILE_NOT_FOUND: {value}")
        raw_text = source.read_text(encoding="utf-8")
        try:
            value = yaml.safe_load(raw_text)
        except yaml.YAMLError:
            value = json.loads(raw_text)
    if not isinstance(value, dict):
        raise ExperienceError(f"INVALID_{label.upper()}: expected a structured mapping")
    return value


def _repository_name_from_remote(remote):
    if "://" in remote:
        path = urlparse(remote).path
    elif "@" in remote and ":" in remote:
        path = remote.split(":", 1)[1]
    else:
        path = remote
    return path.strip("/").removesuffix(".git").casefold()


def _repository_name_from_url(value):
    return urlparse(value).path.strip("/").split("/")[:2]


def _event_similarity_key(repository, observation):
    normalize = lambda value: " ".join(str(value).casefold().split())
    payload = {
        "repository": repository.casefold(),
        "title": normalize(observation["title"]),
        "capability_ids": sorted(observation["capability_ids"]),
        "lesson": normalize(observation["lesson"]),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _existing_event_candidate(root, deduplication_key, similarity_key):
    for state in ("candidate", "verified", "rejected", "raw", "archived"):
        for path in sorted((Path(root) / "experiences" / state).glob("*.yaml")):
            try:
                record = load_yaml(path)
            except (OSError, yaml.YAMLError) as exc:
                raise ExperienceError(f"EXISTING_EXPERIENCE_UNREADABLE: {path.name}: {exc}") from exc
            trigger = record.get("trigger", {}) if isinstance(record, dict) else {}
            if not isinstance(trigger, dict):
                raise ExperienceError(f"EXISTING_EXPERIENCE_UNREADABLE: invalid trigger in {path.name}")
            if trigger.get("deduplication_key") == deduplication_key:
                return record, "repeated_event"
            if trigger.get("similarity_key") == similarity_key:
                return record, "similar_candidate"
    return None, None


def _validate_success_event(project, event, root):
    validate(event, "experience-event.schema.json", root)
    if event["trigger"]["event_type"] == "pull_request.opened":
        raise ExperienceError("EVENT_NOT_SUCCESSFUL: opened pull requests do not produce Experience candidates")
    pr, issue, ci = event["pull_request"], event["issue"], event["ci"]
    if pr["state"] != "closed" or not pr["merged"] or issue["state"] != "closed":
        raise ExperienceError("EVENT_NOT_SUCCESSFUL: linked Issue and pull request must be closed, with the PR merged")
    if ci["conclusion"] != "success":
        raise ExperienceError(f"EVENT_NOT_SUCCESSFUL: CI conclusion is {ci['conclusion']}, not success")
    if ci["head_sha"] != pr["head_sha"]:
        raise ExperienceError("EVENT_HEAD_MISMATCH: successful CI must belong to the merged pull request head SHA")

    expected_repo = event["repository"]["full_name"].casefold()
    remote = _git(project, "remote", "get-url", "origin")
    if _repository_name_from_remote(remote) != expected_repo:
        raise ExperienceError("EVENT_REPOSITORY_MISMATCH: event repository does not match the local origin remote")
    for label, value in (("repository", event["repository"]["url"]), ("issue", issue["url"]),
                         ("pull request", pr["url"]), ("CI run", ci["url"])):
        path_parts = _repository_name_from_url(value)
        if len(path_parts) < 2 or "/".join(path_parts).casefold() != expected_repo:
            raise ExperienceError(f"EVENT_PROVENANCE_MISMATCH: {label} URL does not belong to {expected_repo}")
    if not issue["url"].rstrip("/").endswith(f"/issues/{issue['number']}"):
        raise ExperienceError("EVENT_PROVENANCE_MISMATCH: Issue URL does not match its number")
    if not pr["url"].rstrip("/").endswith(f"/pull/{pr['number']}"):
        raise ExperienceError("EVENT_PROVENANCE_MISMATCH: pull request URL does not match its number")
    if not ci["url"].rstrip("/").endswith(f"/runs/{ci['run_id']}"):
        raise ExperienceError("EVENT_PROVENANCE_MISMATCH: CI URL does not match its run ID")

    if _git(project, "rev-parse", "HEAD") != pr["merge_commit_sha"]:
        raise ExperienceError("EVENT_CHECKOUT_MISMATCH: checkout must be the exact merged commit before harvesting")
    if _git(project, "status", "--porcelain"):
        raise ExperienceError("EVENT_CHECKOUT_DIRTY: event harvesting requires a clean checkout")


def harvest_successful_event(project_root, event, observation, host="codex", root=ROOT):
    """Create one unverified candidate from exact-head merged PR and successful CI facts."""
    root = Path(root).resolve()
    project = Path(project_root).resolve(strict=True)
    if not project.is_dir():
        raise ExperienceError(f"INVALID_PROJECT_ROOT: {project_root} is not a directory")
    event_data = _load_mapping(event, "event")
    observation_data = _load_mapping(observation, "observation")
    _check_privacy(event_data)
    _check_privacy(observation_data)
    _validate_success_event(project, event_data, root)

    pr, issue, ci = event_data["pull_request"], event_data["issue"], event_data["ci"]
    repository = event_data["repository"]["full_name"]
    dedupe_payload = f"{repository.casefold()}#{pr['number']}@{pr['head_sha']}"
    deduplication_key = hashlib.sha256(dedupe_payload.encode("utf-8")).hexdigest()
    similarity_key = _event_similarity_key(repository, observation_data)
    existing, reason = _existing_event_candidate(root, deduplication_key, similarity_key)
    if existing:
        return {"created": False, "reason": reason, "candidate": existing,
                "event_hook_status": "external_delivery_unverified"}

    observation_data = dict(observation_data)
    observation_data["evidence"] = list(observation_data["evidence"])
    observation_data["evidence"].extend([
        {"type": "project_result", "source": issue["url"],
         "description": f"Linked Issue #{issue['number']} was closed.", "strength": "moderate"},
        {"type": "project_result", "source": pr["url"],
         "description": f"Pull request #{pr['number']} was merged at commit {pr['merge_commit_sha']}.",
         "strength": "moderate"},
        {"type": "ci_result", "source": ci["url"],
         "description": f"{ci['name']} completed successfully for exact PR head {pr['head_sha']}.",
         "strength": "moderate"},
    ])
    for review in event_data.get("reviews", []):
        if review["state"] == "approved" and review["commit_sha"] == pr["head_sha"]:
            observation_data["evidence"].append({
                "type": "reviewer_confirmation", "source": review["url"],
                "description": f"{review['reviewer']} approved exact PR head {pr['head_sha']}.",
                "strength": "moderate",
            })

    metadata = {
        "event_source": event_data["event_source"],
        "event_type": event_data["trigger"]["event_type"],
        "event_id": event_data["trigger"]["event_id"],
        "identity": event_data["trigger"]["identity"],
        "deduplication_key": deduplication_key,
        "similarity_key": similarity_key,
        "occurred_at": event_data["trigger"]["occurred_at"],
        "repository": repository,
        "issue_number": issue["number"],
        "issue_url": issue["url"],
        "pull_request_number": pr["number"],
        "pull_request_url": pr["url"],
        "commit_sha": pr["head_sha"],
        "merge_commit_sha": pr["merge_commit_sha"],
        "ci_run_url": ci["url"],
    }
    event_id = f"exp-event-{deduplication_key[:16]}"
    try:
        candidate = harvest_experience(project, observation_data, host=host, root=root,
                                       event_metadata=metadata, event_candidate_id=event_id)
    except FileExistsError:
        existing, reason = _existing_event_candidate(root, deduplication_key, similarity_key)
        if existing:
            return {"created": False, "reason": reason or "repeated_event", "candidate": existing,
                    "event_hook_status": "external_delivery_unverified"}
        raise
    return {"created": True, "reason": "successful_event", "candidate": candidate,
            "event_hook_status": "external_delivery_unverified"}


def harvest_experience(project_root, observation, host="codex", output_path=None, root=ROOT,
                       event_metadata=None, event_candidate_id=None):
    """Harvest bounded project observation into a candidate Experience."""
    root = Path(root).resolve()
    project = Path(project_root).resolve(strict=True)
    if not project.is_dir():
        raise ExperienceError(f"INVALID_PROJECT_ROOT: {project_root} is not a directory")

    obs = _load_mapping(observation, "observation")

    # Enforce privacy and leak prevention
    _check_privacy(obs)

    # Required semantic fields from Host AI
    for req in ("title", "approach", "outcome", "observation", "solution", "lesson", "reasoning", "capability_ids", "evidence"):
        if req not in obs:
            raise ExperienceError(f"MISSING_OBSERVATION_FIELD: observation requires '{req}'")

    if not isinstance(obs["evidence"], list) or not obs["evidence"]:
        raise ExperienceError("MISSING_EVIDENCE: candidate requires at least one evidence item")

    commit_sha = _git(project, "rev-parse", "HEAD")
    project_name = project.name
    recorded_at = _utc()

    title_slug = re.sub(r"[^a-z0-9]+", "-", obs["title"].lower()).strip("-")[:32]
    content_hash = hashlib.sha256(f"{project_name}:{commit_sha}:{title_slug}".encode()).hexdigest()[:8]
    exp_id = event_candidate_id or f"exp-{title_slug}-{content_hash}"

    # Build evidence with unverified status on harvest
    evidence_items = []
    for item in obs["evidence"]:
        if not isinstance(item, dict):
            raise ExperienceError("INVALID_EVIDENCE: evidence item must be a mapping")
        evidence_items.append({
            "type": item.get("type", "project_result"),
            "source": item.get("source", f"git:{commit_sha[:7]}"),
            "description": item.get("description", "Observed outcome in project"),
            "strength": item.get("strength", "moderate"),
            "verification": {"status": "unverified"},
            "observed_at": item.get("observed_at", recorded_at),
            "limits": item.get("limits", []),
        })

    project_context = obs.get("project_context", {
        "project_type": obs.get("project_type", "software-project"),
        "goal": obs.get("goal", f"Maintain behavior in {project_name}"),
        "technologies": obs.get("technologies", []),
        "constraints": obs.get("constraints", []),
    })

    candidate = {
        "id": exp_id,
        "identity": exp_id,
        "title": obs["title"],
        "project_context": project_context,
        "approach": obs["approach"],
        "capability_ids": obs["capability_ids"],
        "outcome": obs["outcome"],
        "evidence": evidence_items,
        "confidence": obs.get("confidence", "medium"),
        "lesson": obs["lesson"],
        "recorded_by": obs.get("recorded_by", f"host-{host}-harvest"),
        "recorded_at": recorded_at,
        "source": {
            "project": project_name,
            "recorded_by": obs.get("recorded_by", f"host-{host}-harvest"),
            "record_uri": f"commit:{commit_sha}",
            "recorded_at": recorded_at,
        },
        "context": obs.get("context", f"Observed during work on {project_name} at commit {commit_sha[:7]}"),
        "observation": obs["observation"],
        "solution": obs["solution"],
        "scope": obs.get("scope", [project_name]),
        "reasoning": obs["reasoning"],
        "status": "candidate",
        "capture_method": "github_structured_extraction_after_successful_event" if event_metadata and event_metadata.get("event_source") == "github" else "host_semantic_inference_after_successful_event" if event_metadata else "host_semantic_harvest",
        "review": {
            "review_status": "pending",
        },
    }
    if event_metadata:
        candidate["source"].update({
            key: event_metadata[key] for key in (
                "repository", "issue_number", "issue_url", "pull_request_number", "pull_request_url",
                "commit_sha", "merge_commit_sha", "ci_run_url",
            )
        })
        candidate["source"]["record_uri"] = event_metadata["pull_request_url"]
        candidate["trigger"] = {key: event_metadata[key] for key in (
            "event_source", "event_type", "event_id", "identity", "deduplication_key",
            "similarity_key", "occurred_at",
        )}

    validate(candidate, "experience.schema.json", root)

    dest = Path(output_path).resolve() if output_path else root / "experiences/candidate" / f"{exp_id}.yaml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    serialized = yaml.safe_dump(candidate, sort_keys=False, allow_unicode=True)
    if event_metadata:
        with dest.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(serialized)
    else:
        dest.write_text(serialized, encoding="utf-8")
    return candidate


def _find_experience(ref, root=ROOT):
    root = Path(root).resolve()
    target_path = Path(ref)
    if target_path.is_file():
        return target_path, load_yaml(target_path)
    for state in ("candidate", "verified", "rejected", "raw", "archived"):
        candidate_path = root / "experiences" / state / f"{ref}.yaml"
        if candidate_path.is_file():
            return candidate_path, load_yaml(candidate_path)
    raise ExperienceError(f"EXPERIENCE_NOT_FOUND: cannot locate experience {ref}")


def verify_evidence(experience_ref, evidence_index=0, verifier=None, method=None, status="checked", root=ROOT):
    """Verify one or all evidence items in an experience candidate prior to approval."""
    root = Path(root).resolve()
    if status not in {"checked", "independently_confirmed"}:
        raise ExperienceError(f"INVALID_VERIFICATION_STATUS: status must be 'checked' or 'independently_confirmed' (found {status})")
    if not verifier or not str(verifier).strip():
        raise ExperienceError("VERIFIER_REQUIRED: verify_evidence requires a named verifier")
    if not method or not str(method).strip():
        raise ExperienceError("METHOD_REQUIRED: verify_evidence requires a verification method description")

    path, record = _find_experience(experience_ref, root)
    evidence_list = record.get("evidence", [])
    if not evidence_list:
        raise ExperienceError("MISSING_EVIDENCE: experience candidate has no evidence items to verify")

    now = _utc()
    if evidence_index == "all":
        targets = list(range(len(evidence_list)))
    else:
        try:
            idx = int(evidence_index)
        except (ValueError, TypeError):
            raise ExperienceError(f"INVALID_EVIDENCE_INDEX: evidence index must be an integer or 'all' (found {evidence_index})")
        if idx < 0 or idx >= len(evidence_list):
            raise ExperienceError(f"INVALID_EVIDENCE_INDEX: index {idx} out of range (0..{len(evidence_list)-1})")
        targets = [idx]

    for idx in targets:
        evidence_list[idx]["verification"] = {
            "status": status,
            "method": str(method).strip(),
            "verified_by": str(verifier).strip(),
            "verified_at": now,
        }

    validate(record, "experience.schema.json", root)
    path.write_text(yaml.safe_dump(record, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return record


def review_experience(experience_ref, decision, reviewer=None, notes=None, root=ROOT):
    """Review and approve or reject an Experience candidate for personal reuse."""
    root = Path(root).resolve()
    if decision not in {"approve", "reject"}:
        raise ExperienceError("INVALID_DECISION: review decision must be approve or reject")

    path, record = _find_experience(experience_ref, root)

    if decision == "approve":
        if not reviewer or not str(reviewer).strip():
            raise ExperienceError("REVIEWER_REQUIRED: approve decision requires a named reviewer")
        if record.get("status") != "candidate":
            raise ExperienceError(f"INVALID_STATE_TRANSITION: only candidate experiences can be approved (found {record.get('status')})")

        # Disallow approving if candidate has unverified evidence
        evidence_list = record.get("evidence", [])
        if not evidence_list:
            raise ExperienceError("MISSING_EVIDENCE: candidate has no evidence items")

        unverified = [
            i for i, item in enumerate(evidence_list)
            if item.get("verification", {}).get("status") not in {"checked", "independently_confirmed"}
        ]
        if unverified:
            raise ExperienceError(
                f"UNVERIFIED_EVIDENCE: cannot approve candidate with unverified evidence (indices: {unverified}); "
                "verify evidence before human approval"
            )

        # Must have at least one qualifying non-AI moderate/strong evidence item
        qualifying = False
        for item in evidence_list:
            v = item.get("verification", {})
            if (item.get("type") not in {"ai_judgment", "single_attempt"}
                    and item.get("strength") in {"moderate", "strong"}
                    and v.get("status") in {"checked", "independently_confirmed"}
                    and v.get("verified_by")
                    and v.get("method")):
                qualifying = True
                break
        if not qualifying:
            raise ExperienceError(
                "INSUFFICIENT_EVIDENCE: verified status requires at least one moderate or strong "
                "non-AI checked evidence item with verifier and method"
            )

        now = _utc()
        record["status"] = "verified"
        record["review"] = {
            "review_status": "accepted",
            "reviewer": str(reviewer).strip(),
            "reviewed_at": now,
            "notes": notes or f"Approved for personal reuse by {reviewer}",
        }

        validate(record, "experience.schema.json", root)

        target_path = root / "experiences/verified" / f"{record['id']}.yaml"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(yaml.safe_dump(record, sort_keys=False, allow_unicode=True), encoding="utf-8")
        if path != target_path and path.is_file():
            path.unlink()

        # Update Personal Profile with reusable_experience_ids
        profile_path = root / "profiles/personal.yaml"
        profile = load_yaml(profile_path)
        reusable = profile.setdefault("reusable_experience_ids", [])
        if record["id"] not in reusable:
            reusable.append(record["id"])
        profile["updated_at"] = now
        validate(profile, "profile.schema.json", root)
        profile_path.write_text(yaml.safe_dump(profile, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return record

    else:
        # Rejection
        now = _utc()
        record["status"] = "rejected"
        record["review"] = {
            "review_status": "rejected",
            "reviewer": str(reviewer).strip() if reviewer else "reviewer",
            "reviewed_at": now,
            "notes": notes or "Rejected during human review",
        }
        validate(record, "experience.schema.json", root)

        target_path = root / "experiences/rejected" / f"{record['id']}.yaml"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(yaml.safe_dump(record, sort_keys=False, allow_unicode=True), encoding="utf-8")
        if path != target_path and path.is_file():
            path.unlink()

        # Remove from personal profile if present
        profile_path = root / "profiles/personal.yaml"
        profile = load_yaml(profile_path)
        reusable = profile.get("reusable_experience_ids", [])
        if record["id"] in reusable:
            reusable.remove(record["id"])
            profile["updated_at"] = now
            validate(profile, "profile.schema.json", root)
            profile_path.write_text(yaml.safe_dump(profile, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return record
