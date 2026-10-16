"""Deterministic read-only discovery with a replaceable semantic mapping boundary."""

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import re

from composer.core import ROOT, load_yaml, validate
from .mapping import map_capabilities


ROOT_FILES = ("AGENTS.md", "CLAUDE.md", ".cursorrules", "README.md", "SPEC.md", "DECISIONS.md", "ROADMAP.md")
SKILL_ROOTS = (".claude/skills", ".agents/skills", ".opencode/skills")
DOC_TERMS = (
    "rule", "constraint", "test", "review", "workflow", "onboard", "start_here",
    "continuation", "mcp", "development", "process", "spec", "decision", "roadmap",
    "plan", "architecture", "contribut", "debug", "release", "research", "traceability",
    "guardrail", "collaboration", "automation",
)


def _read_text(path):
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def _asset_type(path, relative):
    name = path.name.lower()
    rel = relative.lower()
    if name in {"agents.md", "claude.md"}:
        return "agent_instruction"
    if name == "skill.md" or "/skills/" in f"/{rel}":
        return "skill"
    if ".github/workflows/" in f"/{rel}":
        return "workflow"
    if ".github/vccp/" in f"/{rel}":
        if name.endswith((".md", ".txt")):
            return "prompt"
        return "workflow" if name.endswith((".yaml", ".yml")) else "other"
    if name in {"mcp.json", "mcp_config.json"} or "mcp" in name and name.endswith((".json", ".yaml", ".yml")):
        return "mcp_configuration"
    if any(term in rel for term in ("testing", "test-policy", "test_policy")):
        return "test_policy"
    if any(term in rel for term in ("rules", "constraints")):
        return "development_rule"
    if name.endswith((".md", ".rst", ".txt")):
        return "documentation"
    return "other"


def _summary(text, fallback):
    for line in text.splitlines():
        title = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if title:
            return title.group(1).strip()[:160]
    return fallback


def _discover_paths(project):
    selected = {}

    def add(path, kind=None):
        if path.is_file():
            relative = path.relative_to(project).as_posix()
            selected[relative] = kind or _asset_type(path, relative)

    for name in ROOT_FILES:
        add(project / name)

    for base_name in (".github/workflows", ".github/vccp", ".cursor/rules"):
        base = project / base_name
        if base.is_dir():
            for path in sorted(base.rglob("*")):
                if path.is_file() and path.suffix.lower() in {".md", ".txt", ".yaml", ".yml", ".json"}:
                    add(path)

    for base_name in SKILL_ROOTS:
        base = project / base_name
        if base.is_dir():
            for path in sorted(base.rglob("SKILL.md")):
                add(path, "skill")

    docs = project / "docs"
    if docs.is_dir():
        for path in sorted(docs.rglob("*.md")):
            relative = path.relative_to(project).as_posix().lower()
            text = _read_text(path)
            title = _summary(text, "").lower()
            if any(term in relative or term in title for term in DOC_TERMS):
                add(path)

    # Discover MCP setup guidance wherever it lives without inventorying every document.
    for path in (project / "docs").glob("*.md") if (project / "docs").is_dir() else ():
        if "mcp" in path.name.lower() and path.relative_to(project).as_posix() not in selected:
            add(path)
    return selected


def _project_context(project):
    files = {path.name.lower() for path in project.iterdir()}
    technologies, platforms, signals = set(), set(), set()
    if "pubspec.yaml" in files:
        technologies.update(("Flutter", "Dart"))
        platforms.update(("Android", "iOS", "Web", "Desktop"))
    if (project / "server").is_dir() or (project / "backend").is_dir():
        signals.add("server component")
    if (project / "test").is_dir() or (project / "tests").is_dir():
        signals.add("tests")
    if (project / ".github/workflows").is_dir():
        signals.add("GitHub Actions")
    if any((project / name).is_dir() for name in (".claude", ".opencode")):
        signals.add("AI development tooling")
    if (project / "docs/mcp-setup.md").is_file() or (project / ".mcp").is_dir():
        signals.add("MCP")
    readme = _read_text(project / "README.md")
    description = next((line.strip(" *\t") for line in readme.splitlines()
                        if line.strip() and not line.lstrip().startswith(("#", "[", "---", "*"))), project.name)
    description = description[:240]
    if "cross-platform" in readme.lower() or "flutter" in readme.lower():
        project_type = "cross-platform application"
    else:
        project_type = "software project"
    return {
        "name": project.name,
        "project_type": project_type,
        "description": description,
        "technologies": sorted(technologies),
        "platforms": sorted(platforms),
        "development_signals": sorted(signals),
    }


def discover(project_root, root=ROOT):
    """Inspect development-environment assets and return a validated report."""
    project = Path(project_root).resolve(strict=True)
    if not project.is_dir():
        raise ValueError(f"Project root is not a directory: {project_root}")

    assets = []
    for index, (relative, asset_type) in enumerate(sorted(_discover_paths(project).items()), start=1):
        path = project / relative
        text = _read_text(path)
        assets.append({
            "id": f"asset-{index:03d}",
            "path": relative,
            "asset_type": asset_type,
            "ownership": "project_owned",
            "summary": _summary(text, path.name),
            "detected_signals": [],
            "candidate_capabilities": [],
            "_text": text,
        })

    catalog = load_yaml(root / "capabilities/catalog.yaml")
    records = {}
    for catalog_path in catalog["capabilities"]:
        record = load_yaml(root / "capabilities" / catalog_path)
        records[record["id"]] = record

    context = _project_context(project)
    mapping = map_capabilities(assets, records)
    for asset in assets:
        asset["detected_signals"] = mapping["asset_signals"][asset["path"]]
        asset["candidate_capabilities"] = mapping["asset_candidates"][asset["path"]]
        del asset["_text"]

    coverage = mapping["coverage"]
    counter = Counter(asset["asset_type"] for asset in assets)
    gaps = [item["capability_id"] for item in coverage if item["status"] in {"partial", "missing"}]
    conflicts = [item["capability_id"] for item in coverage if item["status"] == "conflict"]
    recommendations = []
    for item in coverage:
        if item["status"] == "missing":
            recommendations.append({
                "capability_id": item["capability_id"],
                "recommendation": "Potential gap detected; review with the project owner before considering Bootstrap resources.",
                "requires_confirmation": True,
            })
        elif item["status"] == "partial":
            recommendations.append({
                "capability_id": item["capability_id"],
                "recommendation": "Review the existing partial coverage before considering any addition.",
                "requires_confirmation": True,
            })

    report = {
        "project": context,
        "discovered_assets": assets,
        "asset_type_counts": dict(sorted(counter.items())),
        "capability_coverage": coverage,
        "gaps": gaps,
        "conflicts": conflicts,
        "recommendations": recommendations,
        "requires_confirmation": True,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    validate(report, "adoption-report.schema.json", root)
    return report
