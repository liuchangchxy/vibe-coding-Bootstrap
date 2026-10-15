# vibe-coding-bootstrap

A personalizable foundation for assembling an AI development environment around a software project. It models project context, user preferences, reusable capabilities, and validated experience so future phases can help users make informed choices.

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

## Integrity guarantees

Repository tests validate Catalog paths and references, lifecycle Experience records use structured Evidence, and `verified` requires reviewed checked evidence. Capability proposals bind to the target's current version. GitHub Actions runs the repository integrity, Composer, and Assembly tests.
