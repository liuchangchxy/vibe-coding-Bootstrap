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

The original Capability schema remains compatible with Phase 1 records. The catalog-entry schema adds admission requirements, and the catalog schema describes the registry. Profile and Experience schemas are unchanged.

Phase 2 delivers data and documentation only. Composer, recommendation algorithms, installation, CLI, Web UI, agents, services, Memory, automatic Experience extraction, and VCCP integration are deferred. Implementation is ready for Controller acceptance; Phase 3 has not started.
