"""Read-only binding between an Existing Project and its Git revision."""

import re
import subprocess


def _git(project, *args, allow_missing=False):
    result = subprocess.run(
        ["git", "-C", str(project), *args],
        check=False, capture_output=True, text=True,
    )
    if result.returncode:
        if allow_missing:
            return None
        detail = result.stderr.strip() or result.stdout.strip()
        raise ValueError(f"Cannot bind target revision (git {' '.join(args)}): {detail}")
    return result.stdout.strip()


def _repository_from_remote(url):
    if not url:
        return None
    match = re.search(r"(?:github\.com[:/])([^/\s:]+/[^/\s]+?)(?:\.git)?$", url, re.IGNORECASE)
    return match.group(1).lower() if match else None


def bind_target_revision(project):
    """Return safe repository metadata; never include URL credentials or local paths."""
    branch = _git(project, "branch", "--show-current") or None
    commit = _git(project, "rev-parse", "HEAD")
    status = _git(project, "status", "--porcelain")
    remote = "origin"
    remote_url = _git(project, "remote", "get-url", remote, allow_missing=True)
    remote_commit = _git(project, "rev-parse", f"refs/remotes/{remote}/{branch}", allow_missing=True) if branch else None
    return {
        "repository": _repository_from_remote(remote_url),
        "branch": branch,
        "commit_sha": commit,
        "remote": remote if remote_url else None,
        "remote_commit_sha": remote_commit,
        "dirty": bool(status),
    }
