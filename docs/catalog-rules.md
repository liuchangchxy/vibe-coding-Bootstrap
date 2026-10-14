# Catalog rules

## Authority and admission

`capabilities/catalog.yaml` determines catalog membership. Each listed path resolves relative to `capabilities/` and contains one canonical record, validated against `schemas/catalog-entry.schema.json`. That schema extends `capability.schema.json` without changing its Phase 1 required fields. `schemas/catalog.schema.json` describes the index. Profile and Experience records keep their own schemas and owning directories.

A repository maintainer reviews each proposed addition in a normal version-controlled change. Admission requires:

1. A unique stable ID, a clear purpose, and concrete applicability including exclusions.
2. A purpose category, explicit maturity and review status, and descriptive selection hints.
3. At least one meaningful component with an explicit role and a resolvable source ID. References must be sufficient to understand the ability; a name-only empty template is not admissible.
4. Traceable provenance for the authored description (`source`) and every external component (`component_sources`). Use the upstream publisher's location; check it when reviewing. The source title is attribution, not a copied third-party instruction set.
5. An accountable `maintainer` and `last_reviewed` date. The maintainer is responsible for source drift, semantic accuracy, and relationship updates; external publishers do not maintain our descriptions.
6. An honest evidence state. `pending` is an explicit placeholder, requires draft or deprecated status, and is not an Experience. `supported` requires actual evidence references and a summary of their limits. Only maintainers may mark an entry validated after reviewing use evidence. Evidence presence alone does not establish benefit or causality.
7. Valid references and no duplicate capability definition. A dependency does not justify copying another record or a third-party asset.

Do not admit an AI-generated temporary rule directly. An AI-authored proposal must pass the same human review, attribution, scope, and evidence requirements. Do not copy Skills without provenance, vendor external resources into records, record secrets, or promote private project knowledge into a Global Capability. Generalizing a project lesson requires deliberate human curation and evidence review; no automatic promotion mechanism exists.

## Relationship semantics

Use the existing `requires` and `conflicts_with` fields rather than adding a competing `dependencies` representation. Add `related_capabilities` for optional associations. Every value is a catalog Capability ID, never a path, component ID, tool name, or proposed future entry.

| Field on A referencing B | Meaning | Maintenance rule |
| --- | --- | --- |
| `requires` | B is a prerequisite for using A as described | Directed; resolve transitively; no self-reference or cycles |
| `related_capabilities` | B is a potentially useful complement | Advisory and directed; no automatic inclusion or transitive meaning |
| `conflicts_with` | A and B cannot be used together as described | Semantically symmetric; store the edge on one endpoint only; no automatic winner |

The location of a conflict edge has no priority meaning. A reader must inspect both incoming and outgoing conflict edges. Store each unordered conflict pair once to avoid mirrored facts drifting apart. A pair cannot simultaneously be required/related and conflicting in either direction. An entry's transitive prerequisite closure, including itself, must not contain a conflicting pair. Drafts may reference drafts but cannot be treated as validated through dependency inheritance. Validated entries require validated prerequisites; new dependencies require review of the affected entries.

In the initial data, `agent-development.requires: [testing]` means behavioral checking is part of this catalog's definition of agent development. `testing.related_capabilities: [debugging, code-review]` is optional and grants no runtime authority. The initial records declare no known conflicts; empty lists do not prove compatibility in all projects.

For a future genuine conflict between two catalog entries, put `conflicts_with: [other-id]` on just one record and explain the incompatibility in `applicability.not_for`. Do not invent a conflicting initial entry just to populate the field. YAML expresses the graph; no graph database or resolver is introduced.

## Local validation and review

Before committing a catalog change:

1. Parse every JSON Schema and YAML file; reject duplicate YAML mapping keys rather than allowing silent overwrites. Validate the schemas themselves against Draft 2020-12, the registry against its schema, and each listed record against the catalog-entry schema with format checking enabled.
2. Check that registry paths exist, match category/ID filenames, and list all Capability YAML records exactly once; check global ID uniqueness.
3. Resolve every prerequisite, related, conflict, and Experience ID. Component IDs and source IDs must be locally unique; every component must resolve a source, and each declared source must be used. Relative authored-source links must resolve locally. Inspect external source URLs during human review; parsing a URI does not prove availability.
4. Check the relationship invariants above and validation-state prerequisite rule. Schema validation alone cannot check a graph.
5. Confirm legacy compatibility: the original required fields and every original property definition remain unchanged except explanatory descriptions; Profile and Experience schemas remain unchanged. Validate a minimal Phase 1 record against the extended schema.
6. Review the diff for private data, copied third-party content, unsupported evidence claims, or executable behavior. No validator result replaces maintainer review.

These are authoring checks, not a shipped CLI, service, runtime validator, or automatic admission system. Updates to identity, purpose, component origin, or relationships require deliberate review; deprecate obsolete entries while preserving referenced IDs until consumers are deliberately migrated.

## Initial catalog

The repository maintainers own the eight initial descriptors, authored for Phase 2. Their `source` points to this section's document; external documents are separately attributed in each record. They are concrete reference-backed workflow descriptions, not packaged Skills. All eight are `draft` / `experimental` with `evidence.state: pending`. Sources were inspected on 2026-10-15; this confirms reference provenance, not runtime acceptance or beneficial project outcomes.

Testing and debugging deliberately include Python-specific example components. Their capability purposes remain general, while selection constraints expose that the referenced component does not cover other runtimes. Any future additional component should describe its scope just as explicitly.
