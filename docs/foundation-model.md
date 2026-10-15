# Phase 1 Foundation Model

## Purpose

The long-term product is a personalizable AI project initialization system. For a new software project, it should eventually help a user assemble an AI development environment suited to the project goal, project type, technology context, the user's preferences, and validated experience from prior work.

The long-term learning loop is: initialize a project, select and assemble capabilities, develop with AI assistance, observe real outcomes, record useful experience, and reuse that experience in future projects. Phase 1 supplies the shared data foundation for that direction; it does not implement the loop.

## Models

### Capability

A Capability is a reusable development ability and the highest-level reuse abstraction. One or more components express it, such as Skills, MCPs, Rules, Prompts, Templates, Documents, or Workflow references. Components are the concrete development resources a Capability uses or references. The top-level `kind` remains for Phase 1 backward compatibility as a legacy primary-representation hint: it is not a Capability taxonomy, is not the only component type, and does not constrain composite Capabilities (which may use `workflow`). Component types come from `components` and their assembly metadata; new logic must not infer them from `kind`. Capability records describe resources; they do not download, install, execute, or manage them.

Only the catalog owner can mark a Capability `validated`. That status indicates the asset has been reviewed for use; it does not promise success in every project.

### Profile

A Profile records a person's stated development preferences, default capabilities, and excluded capabilities. It describes preferences rather than inferring them. A project may use a Profile as input in a later phase; Phase 1 does not select capabilities or resolve preferences automatically.

### Experience

An Experience records an approach in a specific project context, the observed outcome, evidence supporting that outcome, a confidence level, and a possible reusable lesson. Evidence is required so an Experience can be distinguished from an unsupported preference or claim. The outcome may be success, mixed, failure, or inconclusive; negative and uncertain outcomes are useful records too.

Experience records are entered by a person. Their presence does not establish causation or guarantee that the same approach will work elsewhere. The context and evidence should travel with any lesson reused in another project.

## Relationships

- A Profile may refer to Capabilities through `default_capability_ids` and `excluded_capability_ids`.
- An Experience may refer to Capabilities used through `capability_ids`.
- A Capability may point to supporting Experience records through `experience_ids`.
- These are identifier references; Phase 1 does not enforce catalog-wide referential integrity.
- Phase 2 adds a unique [registry](../capabilities/catalog.yaml), [admission rules](catalog-rules.md), and a [discovery contract](capability-discovery.md). Its repository validation checks these references for catalog entries; no runtime resolver is provided.

## Data and maintenance conventions

- IDs are stable, human-readable or generated strings within the owning catalog; schemas do not prescribe a global ID service.
- Timestamps use RFC 3339 date-time strings. Capability review dates use ISO 8601 calendar dates.
- Omit unknown optional fields rather than inventing values.
- Keep measured outcomes separate from interpretation in Experience. Record the source of evidence when one is available and state limits in the lesson.
- Schema validation checks structure only. It does not verify that a source exists, a capability works, a claim is true, or referenced IDs resolve.

## Phase boundary

Phase 1 delivered the three JSON Schemas and their shared conceptual documentation. Phase 2 extends the Capability schema with optional metadata and applies stricter admission constraints only to catalog entries. It does not implement project analysis, automatic recommendations or installation, CLI, MCP download, Skill management, Memory, or automatic Experience extraction.
