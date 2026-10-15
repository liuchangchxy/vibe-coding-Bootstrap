# Capability Catalog

`catalog.yaml` is the sole membership registry. Each entry is a path relative to this directory, pointing to exactly one canonical Capability record. The record holds its ID and all metadata; the index does not repeat descriptions, categories, sources, or relationship edges. All Capability YAML records in this tree must be indexed exactly once. Adding an unlisted file does not admit a capability.

Records live at `<category>/<id>.yaml`. IDs are globally unique within this catalog and remain stable when a record moves category. Update the registry path on a move; references continue to use the ID. Never copy a record to give it a second category. Tags can describe cross-cutting concerns.

## Purpose taxonomy

The `category` values in the Capability schema are the structural vocabulary. These meanings guide human classification:

| Category | Purpose | Initial examples |
| --- | --- | --- |
| engineering | Develop and investigate software changes | engineering-baseline, debugging |
| workflow | Organize work and communicate knowledge | planning, documentation |
| quality | Check behavior and assess changes | testing, code-review |
| research | Examine claims with traceable methods and evidence | research-workflow |
| agent | Develop systems whose product includes agent behavior | agent-development |
| automation | Describe repeatable operational tasks | No entry yet |
| delivery | Release and distribute software | No entry yet |
| domain | Apply reusable subject-specific expertise | No entry yet |

Classify by the principal outcome, not by Flutter, Python, React, vendor, or packaging. A Python debugger is a component of debugging, not a top-level category. `experimental` is a maturity value, not a parallel folder: experimental testing still belongs under quality. Directories are created when they contain records; no empty categories or speculative capabilities are required.

## Record interpretation

- `purpose` states the reusable outcome; `description` is a short discovery summary.
- `applicability` states use cases and exclusions. It does not assert universal suitability.
- A Capability is the highest-level reusable development ability; one or more components express it. Top-level `kind` is retained only as a Phase 1 legacy primary-representation hint, not a taxonomy or component constraint. Composite Capabilities may use `workflow`; actual component types come from `components[].kind` and assembly metadata.
- `component_sources` holds provenance once per source within a record; `components[].source_id` joins to it. `reference` means conceptual guidance, while `upstream` means a component supplied by that publisher. Neither means installed, executable, or vendored.
- Source and component IDs are record-local; Capability IDs are catalog-wide. Shared external resources may be referenced by multiple abilities without duplicating their content.
- `source` describes the origin of the authored capability definition. Local relative references resolve from the record's directory; source URIs are locators, not fetch instructions.
- `status` tracks catalog review (`draft`, `validated`, `deprecated`); `maturity` tracks definition stability (`experimental`, `established`). Neither implies a measured benefit. Evidence records the separate project-use state.
- `selection_hints` gives descriptive signals and constraints for later human or Composer consideration; it contains no weights, priorities, scores, or execution instructions.

See [admission and relationship rules](../docs/catalog-rules.md) for cross-record requirements and [Discovery](../docs/capability-discovery.md) for future use. JSON Schema checks structure; human review checks meaning and provenance.
