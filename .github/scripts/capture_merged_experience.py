"""Normalize successful merged-PR events and create an unverified VCBS candidate."""

import json
import os
from pathlib import Path
import subprocess
import sys


def gh_json(endpoint):
    return json.loads(subprocess.check_output(["gh", "api", endpoint], text=True))


def main():
    payload = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    repo = os.environ["GITHUB_REPOSITORY"]
    trigger = os.environ["GITHUB_EVENT_NAME"]
    if trigger == "workflow_run":
        runs = payload.get("pull_requests", [])
        if not runs:
            return 0
        number = runs[0].get("number")
        run_sha = payload["workflow_run"]["head_sha"]
        if payload["workflow_run"].get("conclusion") != "success":
            return 0
    else:
        number = payload.get("number")
        run_sha = None
    if not number:
        return 0

    pr = gh_json(f"repos/{repo}/pulls/{number}")
    if (pr.get("state") != "closed" or not pr.get("merged")
            or pr.get("head", {}).get("ref", "").startswith("experience-candidate-")):
        return 0
    head_sha = pr["head"]["sha"]
    if run_sha and run_sha != head_sha:
        return 0

    if trigger == "workflow_run":
        run = payload["workflow_run"]
        ci_url, conclusion, run_id = run["html_url"], run["conclusion"], run["id"]
        occurred_at = run["updated_at"]
    else:
        runs = gh_json(f"repos/{repo}/actions/runs?head_sha={head_sha}&event=pull_request&per_page=100")["workflow_runs"]
        matches = [item for item in runs if item.get("name") == "CI" and item.get("conclusion") == "success"]
        if not matches:
            return 0
        run = sorted(matches, key=lambda item: item["run_number"])[-1]
        ci_url, conclusion, run_id = run["html_url"], run["conclusion"], run["id"]
        occurred_at = pr["closed_at"]
        ci_completed_at = run["updated_at"]

    if trigger == "workflow_run":
        ci_completed_at = occurred_at

    issue = gh_json(f"repos/{repo}/issues/{number}")
    repository_url = f"https://github.com/{repo}"
    event = {
        "format_version": 1,
        "event_source": "github",
        "trigger": {"event_type": "workflow_run.completed" if trigger == "workflow_run" else "pull_request.closed",
                    "event_id": f"github:{repo}:{run_id}",
                    "identity": "github-actions:merged-experience-capture",
                    "occurred_at": occurred_at},
        "repository": {"full_name": repo, "url": repository_url},
        "issue": {"number": number, "url": issue["html_url"], "state": issue["state"]},
        "pull_request": {"number": number, "url": pr["html_url"], "state": pr["state"],
                         "merged": pr["merged"], "head_sha": head_sha,
                         "merge_commit_sha": pr["merge_commit_sha"], "merged_at": pr["merged_at"]},
        "ci": {"name": "CI", "run_id": run_id, "url": ci_url, "head_sha": head_sha,
               "conclusion": conclusion, "completed_at": ci_completed_at},
    }
    observation = {
        "title": pr["title"],
        "approach": "A pull request was merged and the repository CI workflow succeeded.",
        "capability_ids": [],
        "outcome": {"summary": f"CI succeeded for merged pull request #{number}.", "result": "success",
                    "measures": []},
        "observation": f"Pull request #{number} merged at {pr['merge_commit_sha']} after CI succeeded for its exact head.",
        "solution": "The repository recorded the merged change and its successful CI run as source evidence.",
        "lesson": "This event confirms a merge and CI result only; it does not establish causality or a generally reusable practice.",
        "reasoning": {"interpretation": "Repository metadata records a successful merge and exact-head CI run.",
                      "limits": ["No causal or cross-project claim is inferred." ]},
        "scope": ["repository-event"],
        "confidence": "low",
        "evidence": [{"type": "project_result", "source": pr["html_url"],
                      "description": f"Pull request #{number} merged as {pr['merge_commit_sha']}.",
                      "strength": "moderate"}],
        "project_context": {"project_type": "software-project", "goal": "Record a successful repository change.",
                            "technologies": [], "constraints": ["event-derived; causality unverified"]},
    }
    worktree = Path(os.environ["RUNNER_TEMP"]) / f"vcbs-merged-{number}-{head_sha[:8]}"
    subprocess.run(["git", "fetch", "origin", pr["merge_commit_sha"]], check=True)
    subprocess.run(["git", "worktree", "add", "--detach", str(worktree), pr["merge_commit_sha"]], check=True)
    event_path = Path(os.environ["RUNNER_TEMP"]) / "vcbs-event.json"
    observation_path = Path(os.environ["RUNNER_TEMP"]) / "vcbs-observation.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    observation_path.write_text(json.dumps(observation), encoding="utf-8")
    output = subprocess.check_output([
        sys.executable, "-m", "bootstrap", "harvest-event", "--project-root", str(worktree),
        "--event", str(event_path), "--observation", str(observation_path),
    ], text=True)
    result = json.loads(output)
    if result["status"] != "created":
        print("Candidate already captured; no follow-up PR needed.")
        return 0

    branch = f"experience-candidate-{number}-{head_sha[:8]}"
    subprocess.run(["git", "checkout", "-b", branch], check=True)
    candidate_path = Path("experiences/candidate") / f"{result['experience_id']}.yaml"
    subprocess.run(["git", "add", str(candidate_path)], check=True)
    subprocess.run(["git", "-c", "user.name=github-actions[bot]", "-c",
                    "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                    "commit", "-m", f"experience: capture merged PR #{number}"], check=True)
    subprocess.run(["git", "push", "origin", branch], check=True)
    subprocess.run(["gh", "pr", "create", "--base", "main", "--head", branch,
                    "--title", f"experience: capture merged PR #{number}",
                    "--body", f"Auto-captured unverified event candidate for #{number}. Verify evidence and review it manually before reuse.\n\nGenerated by workflow run {run_id}."] , check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
