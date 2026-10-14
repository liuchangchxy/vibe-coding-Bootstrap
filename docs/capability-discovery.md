# Capability Discovery Foundation

## Purpose and boundary

Discovery makes available abilities and their constraints understandable. Phase 2 supplies the catalog vocabulary and a future input/output contract only. It does not analyze projects, run matching code, rank records, recommend combinations, select components, create agents, or change an environment. Phase 3 can build a Composer on this foundation after Controller acceptance.

## Future inputs

| Input | Meaning | Authority |
| --- | --- | --- |
| Project context | Project goal, work type, technologies, constraints, evidence needs, and permitted tools | Explicit project information; private facts stay with the project |
| Profile | Stated preferences, default and excluded Capability IDs | The user's Profile; a default is a preference, not permission to install |
| Experience | Human-curated outcomes with context, evidence, confidence, and limitations | Experience records; absence of evidence remains unknown |
| Catalog | Available records, component provenance, applicability, review state, and relationships | Only the canonical registry and its referenced records |

Missing inputs remain explicitly unknown. Do not fill gaps by inventing preferences, Experience, applicability, or runtime availability. The Profile and Experience schemas remain the input vocabulary for those models; this phase introduces no duplicate store for them.

## Matching vocabulary, without an algorithm

`purpose`, `category`, and `applicability.use_when` describe relevance to the goal. `applicability.not_for`, technology/component scope, and `selection_hints.constraints` expose limits. `selection_hints.signals` supplies plain-language cues. Tags and technology names supplement purpose classification; matching a technology name alone does not establish fitness.

`requires` exposes prerequisites, `related_capabilities` exposes optional complements, and `conflicts_with` exposes incompatible combinations. Profile exclusions and project tool restrictions must be surfaced, including when they conflict with a default or prerequisite. Do not silently override them. Experience can inform a future explanation only with its original context and evidence limits; it is not universal proof.

These are fields a future implementation may inspect, not a scoring formula, inference policy, traversal implementation, or automatic decision procedure. No field has a hidden numeric weight or priority.

## Future output: candidate capabilities

A candidate set is an explainable discovery result for human consideration. It should reference canonical Capability IDs, cite the particular input facts and catalog fields relevant to inclusion, describe applicable component limits, and expose prerequisites, conflicts, exclusions, and missing information. It must distinguish provenance, review status, and project-use evidence. A candidate does not authorize installation or modification.

For example, a future discovery interaction might receive an explicit request to develop an agent, a Python runtime, and a need for behavioral checks. It could present `agent-development` for consideration, show its declared prerequisite `testing`, point to testing's Python-scoped reference component, and disclose both records' draft status and pending evidence. This is an illustrative human reading of the data, not the output of an implemented matcher or an instruction to select those abilities.

No recommendation order, score, mandatory combination, approval, or installation plan is specified here. A later Composer must preserve the link from any candidate to its canonical record and the catalog revision used. How that Composer is implemented and how choices are confirmed belong to a later phase.

## Separation of concerns

- Capability describes an ability; a Skill or MCP may be a referenced component. A collection of Skill files is not a capability catalog.
- Project Knowledge contains project facts, architecture, and private constraints. Catalog descriptions may state reusable applicability but never absorb those private facts.
- Experience records real observations and supporting evidence. A catalog's pending evidence placeholder is neither an observation nor an Experience record.
- Discovery describes candidates. Composition, resource resolution, installation, execution, and feedback capture remain separate future responsibilities.
