# vibe-coding-bootstrap

A personalizable foundation for assembling an AI development environment around a software project. It models project context, user preferences, reusable capabilities, and validated experience so future phases can help users make informed choices.

## Unified Product Loop & Lifecycle

VCBS provides four standardized lifecycle operations for end users:

```bash
vcbs status --project-root <project> [--host <codex|claude|claude_code|antigravity|none>] [--json]
vcbs deploy --project-root <project> [--host <codex|claude|claude_code|antigravity|none>] [--context <context.yaml>] [--decision <decision.json>] [--apply]
vcbs upgrade --project-root <project> [--host <codex|claude|claude_code|antigravity|none>] [--decision <decision.json>] [--apply]
vcbs uninstall --project-root <project> [--component <id>] [--decision <decision.json>] [--apply]
```
*(also available via `python -m bootstrap <deploy|upgrade|uninstall|status>` or `./vcbs`)*

The underlying inspect → plan → confirm once → apply loop coordinates with AI Hosts for context and semantic review while preserving strict safety invariants, snapshot/rollback guarantees, and ownership boundaries. Advanced diagnostic CLIs remain available: `composer`, `assembly`, `materializer`, `adoption`, and `activation`.
See [Unified Product Loop](docs/unified-product-loop.md) for details.

## Phase 1: Capability Foundation

Phase 1 defines three data models only:

- **Capability** — a reusable development ability described through metadata and references to assets such as Skills, MCPs, Rules, Prompts, Templates, Documentation, or Workflows.
- **Profile** — a user's development preferences and defaults for shaping project environments.
- **Experience** — a recorded, evidence-backed result from using an approach in a project, suitable for future human-guided reuse.

The JSON Schemas in [`schemas/`](schemas/) are the normative structural definitions. See [`docs/foundation-model.md`](docs/foundation-model.md) for the concepts, relationships, and scope.

Phase 1 does not analyze projects, recommend or install capabilities, manage Skills, provide a CLI or MCP downloader, implement Memory, or automatically extract Experience.

## Phase 2: Catalog and Discovery Foundation

The [registry](capabilities/catalog.yaml) indexes eight authored capability descriptions. External components are referenced, never copied. All initial records are drafts with pending project-use evidence.

- [Catalog layout and taxonomy](capabilities/README.md)
- [Admission and relationship rules](docs/catalog-rules.md)
- [Discovery input/output contract](docs/capability-discovery.md)
- [Profile boundary](profiles/README.md) and [Experience boundary](experiences/README.md)

The original Capability schema remains compatible with Phase 1 records. The catalog-entry schema adds admission requirements, and the catalog schema describes the registry. Profile schema remains unchanged; Experience retains the Phase 1 shape for records without lifecycle status and requires structured Evidence for lifecycle records.

Phase 2 delivered data and documentation only; its historical discovery contract remains documented above.

## Phase 3: Capability Composer MVP

The [Composer](docs/composer.md) reads a [project context](schemas/project-context.schema.json), the Catalog, an explicit [personal Profile](profiles/personal.yaml), and curated Experience to produce an explainable [recommendation plan](schemas/recommendation.schema.json). Editable [rules](composer/rules.yaml) preserve human control. Plans always require confirmation.

Three [examples](examples/) cover a maintained web project, an agent project, and a one-off experiment. Run the validation prototype with `python -m composer examples/agent/project-context.yaml --output recommendation.yaml` after installing `requirements.txt`.

Phase 3 performs no installation, target-project modification, synchronization, agent execution, or automatic approval.

## Phase 4: Experience Feedback Loop Foundation

Phase 4 adds a human-governed [Experience lifecycle](docs/experience-lifecycle.md), traceable [Evidence](schemas/evidence.schema.json), and separate Experience and Capability proposals. Lifecycle records are grouped by state under [`experiences/`](experiences/README.md). Proposals document review; they never edit or promote Capabilities automatically. See [Experience governance](docs/experience-governance.md) for the boundary with Composer and Capability changes.

## Phase 5: Environment Assembly Foundation

The [Assembly planner](assembly/) resolves a Recommendation Plan through the selected Capabilities into registered components and produces an inspectable [Assembly Plan](schemas/assembly-plan.schema.json). Components retain explicit Global, Project, or External scope; external resources remain references. See [Environment Assembly](docs/environment-assembly.md) and the [scope model](docs/scope-model.md).

Preview without writing a file with `python -m assembly examples/assembly/agent-project/recommendation.yaml --preview`, or write a plan with `--output assembly-plan.yaml`. Assembly is local-only and never installs resources or modifies a target project. All plans require human confirmation.

## Phase 6: Safe Materialization / Installer MVP

Confirmed Assembly Plans can now materialize Bootstrap-owned local components into explicit Project/Global roots with dry-run, conflict detection, idempotence and manifests. External references are not downloaded and materialization does not imply native agent activation. See [Materialization](docs/materialization.md).

## Phase 7A: Existing Project Adoption Discovery

The [adoption discovery command](docs/adoption-discovery.md) inventories existing development-environment assets, identifies project context, and maps evidence to the current Capability Catalog. It produces a read-only Adoption Report; it does not install or modify target files.

Phase 7C binds semantic review artifacts to the target repository revision and builds a deterministic Adoption Plan. A stale review is rejected, each Catalog Capability receives one disposition, and a no-op or review-only outcome is valid. `requires_confirmation: true` means the plan is not approval: an explicit approve/reject Decision Artifact binds the choice to the exact plan digest and target SHA. Execution remains deferred.

## Phase 9: Codex Native Activation

The Codex adapter projects the existing `testing` Capability to a repo-local Skill at `.agents/skills/bootstrap-testing/SKILL.md`. It uses a revision-bound Activation Plan, an explicit approve/reject decision, and a target manifest that records ownership for safe deactivation. See [Codex Native Activation](docs/codex-native-activation.md). Host consumption is established separately by the sanitized Control/Activated acceptance evidence in [`examples/activation/codex/`](examples/activation/codex/).

## Phase 10: Experience Promotion and Cross-Project Reuse

Verified Experience does not auto-promote. Only Experience IDs explicitly linked through an authorized Capability edit are eligible for Composer reuse, and matching remains limited by project type and Capability ID. The Codex activation Experience supports only the observed Codex CLI 0.162.0-alpha.17.2 testing projection; it does not establish general testing effectiveness or rules for other Hosts. See [Experience governance](docs/experience-governance.md) and the [before/after reuse pilot](examples/reuse/codex-maintained-project/README.md).

## Phase 12: Standardized Lifecycle Management

Phase 12 standardizes VCBS deployment, upgrade, uninstallation, and status reporting into four unified operations with strict ownership, pre-apply snapshot creation, and exact-byte rollback:
- **`status`**: Read-only query reporting current git revision, deployment state (`installed`/`partially_installed`/`not_installed`), managed components (version, expected vs disk sha256, health), native activation, unmanaged assets, and recovery points. Validated against `schemas/bootstrap-status.schema.json`.
- **`deploy`**: Standardized deployment pipeline distinguishing clean Compose from Existing Adoption, previewing planned changes, requiring explicit approval, creating Git snapshots, and guaranteeing idempotence (`no_changes`).
- **`upgrade`**: Managed component upgrade detecting outdated vs conflict states, diffing hashes and content, and preventing overwrites of user-modified files.
- **`uninstall`**: Safe ownership-verified removal of VCBS-deployed files, directories, and activation skills. User-modified files block removal; unmanaged files and project code are never touched. Complete or partial uninstalls update manifests cleanly, and snapshots ensure full rollback.

## Integrity guarantees

Repository tests validate Catalog paths and references, lifecycle Experience records use structured Evidence, and `verified` requires reviewed checked evidence. Capability proposals bind to the target's current version. GitHub Actions runs repository integrity, Composer, Assembly, and Materializer tests.
