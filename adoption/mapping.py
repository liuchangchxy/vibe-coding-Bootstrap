"""Semantic mapping boundary; deterministic baseline is intentionally conservative."""

# These broad evidence phrases complement Capability metadata without building a
# repository-specific rules engine. A host agent can replace this mapping later.
SIGNALS = {
    "engineering-baseline": ("agents.md", "claude.md", "engineering baseline", "development rules", "spec-first", "reviewable changes", "change policy", "delivery process", "execution.md", "hard constraints"),
    "testing": ("testing.md", "test policy", "flutter test", "test suite", "regression test", "automated test", "test command", "test process", ".github/workflows/ci."),
    "code-review": ("code review", "code-review", "reviewing.md", "reviewer-task-prompt"),
    "documentation": ("documentation", "docs structure", "project knowledge", "readme.md", "start_here", "decisions.md", "constraints.md", "roadmap.md", "spec.md"),
    "planning": ("planning", "roadmap.md", "roadmap", "plans/", "acceptance criteria", "scope", "spec-first", "milestone", "start_here"),
    "debugging": ("debugging", "troubleshooting", "reproduce a failure", "debug workflow", "incident investigation"),
    "research-workflow": ("research workflow", "experiment", "reproducibility", "research methods", "research evidence"),
    # Evidence must describe a product that develops agent behavior, not AI tooling use.
    "agent-development": ("agent product", "agent system", "developing an agent", "agent behavior acceptance", "agent evaluation cases"),
}


def map_capabilities(assets, records):
    asset_signals, asset_candidates = {}, {}
    evidence_by_capability = {capability_id: [] for capability_id in records}
    for asset in assets:
        path = asset["path"].lower()
        title = asset["summary"].lower()
        headings = "\n".join(line.strip().lower() for line in asset["_text"].splitlines()
                               if line.lstrip().startswith("#"))
        found = []
        candidates = []
        for capability_id in records:
            phrases = SIGNALS.get(capability_id, ())
            hits = [phrase for phrase in phrases if phrase in f"{path}\n{title}\n{headings}"]
            # Explicit commands and rules in instructions/Skills are semantic evidence;
            # incidental mentions elsewhere do not turn every document into coverage.
            if asset["asset_type"] in {"agent_instruction", "skill", "prompt"}:
                body = asset["_text"].lower()
                direct = {
                    "engineering-baseline": ("spec-first", "reviewable changes", "development rules", "delivery process"),
                    "testing": ("flutter test", "testing policy", "test command", "test process"),
                    "code-review": ("code review process", "reviewing changes", "review process"),
                    "planning": ("spec-first", "acceptance criteria", "planning workflow"),
                }.get(capability_id, ())
                hits.extend(phrase for phrase in direct if phrase in body and phrase not in hits)
            if capability_id == "documentation" and asset["asset_type"] == "documentation":
                hits.append("documentation asset")
            if hits:
                found.extend(hits)
                candidates.append(capability_id)
                evidence_by_capability[capability_id].append(asset["path"])
        if "mcp" in path or "mcp" in title:
            found.append("MCP setup")
        asset_signals[asset["path"]] = sorted(set(found))
        asset_candidates[asset["path"]] = sorted(candidates)

    coverage = []
    for capability_id, record in records.items():
        paths = evidence_by_capability[capability_id]
        unique_paths = list(dict.fromkeys(paths))
        if capability_id == "agent-development":
            status = "satisfied" if len(unique_paths) >= 2 else "unknown"
            reason = ("Multiple project assets describe agent behavior as a product capability."
                      if status == "satisfied" else
                      f"AI or MCP tooling alone does not show that the project develops an agent product. "
                      f"The catalog applies when {record['applicability']['use_when'][0].lower()}")
        elif capability_id in {"engineering-baseline", "testing", "code-review", "documentation", "planning"}:
            status = "satisfied" if len(unique_paths) >= 2 else "partial" if unique_paths else "unknown"
            reason = (f"Multiple project-owned assets provide evidence relevant to the catalog purpose, '{record['purpose']}'; paths are listed below."
                      if status == "satisfied" else
                      "Some relevant project-owned evidence exists, but its coverage is limited." if status == "partial" else
                      "No relevant project-owned evidence was identified; available evidence is insufficient to claim a gap.")
        else:
            status = "partial" if len(unique_paths) >= 2 else "unknown"
            reason = ("Several project-owned assets contain relevant signals, but complete coverage is not established."
                      if status == "partial" else
                      f"No sufficiently specific evidence for catalog signals: "
                      f"{'; '.join(record['selection_hints']['signals'])}. "
                      f"Applicable when {record['applicability']['use_when'][0].lower()}")
        evidence = [{"path": path, "ownership": "project_owned"} for path in unique_paths]
        coverage.append({
            "capability_id": capability_id,
            "status": status,
            "evidence": evidence,
            "reason": reason,
        })
    coverage.sort(key=lambda item: item["capability_id"])
    return {"asset_signals": asset_signals, "asset_candidates": asset_candidates, "coverage": coverage}
