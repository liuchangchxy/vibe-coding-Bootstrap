# vibe-coding-bootstrap

An personalizable foundation for assembling an AI development environment around a software project. It models project context, user preferences, reusable capabilities, and validated experience so future phases can help users make informed choices.

## Phase 1: Capability Foundation

Phase 1 defines three data models only:

- **Capability** — a reusable asset such as a Skill, MCP, Rule, Prompt, Template, Documentation, or Workflow.
- **Profile** — a user's development preferences and defaults for shaping project environments.
- **Experience** — a recorded, evidence-backed result from using an approach in a project, suitable for future human-guided reuse.

The JSON Schemas in [`schemas/`](schemas/) are the normative structural definitions. See [`docs/foundation-model.md`](docs/foundation-model.md) for the concepts, relationships, and scope.

Phase 1 does not analyze projects, recommend or install capabilities, manage Skills, provide a CLI or MCP downloader, implement Memory, or automatically extract Experience.

## Status

Phase 1 — Capability Foundation.
