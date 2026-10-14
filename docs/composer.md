# Capability Composer MVP — Phase 3

Composer reads explicit project context, the canonical Capability Catalog, a personal
Profile, and human-curated Experience. It produces a recommendation plan for human
review. This is a local rule-based prototype, with no installation or assembly step.

## Run the prototype

Use Python 3.10+ and install the development dependencies in `requirements.txt`.
From the repository root:

```console
python -m pip install -r requirements.txt
python -m composer examples/long-lived-web/project-context.yaml --output recommendation.yaml
python -m unittest discover -s tests -v
```

The output path must not already exist. The only write is exclusive creation of
that explicitly selected YAML report. Composer never edits a target project,
installs components, downloads MCPs, syncs resources, or starts an agent.

## Inputs and transparent rules

`schemas/project-context.schema.json` defines identity, lifecycle, technology tags,
runtime, platform, goal, constraints, and collaboration. `unspecified` may be used
explicitly for unknown runtime/platform; no project scanning or inference occurs.
Project type and technology tags are open strings, independent of any language.

`composer/rules.yaml` is the editable matching policy. Conditions use exact,
case-sensitive values; all condition fields must match (AND), any listed value
within a field may match (OR). A technology-tags condition matches any shared tag.
Free-text goals and constraints remain visible for human review; the MVP does not
pretend to interpret arbitrary natural-language restrictions.

- `long_lived` or `production`: engineering-baseline, testing, documentation.
- `agent`: agent-development, with testing brought in by the catalog prerequisite.
- `research`: research-workflow.
- No match and no personal default: an empty recommendation is valid.

`profiles/personal.yaml` conforms to the existing Profile schema. Its initial empty
lists contain no inferred user preferences. Explicit defaults add candidates;
exclusions take precedence, including over defaults and prerequisites. A candidate
whose prerequisite is excluded is itself excluded with an explanation. Other
Profile preference fields are preserved in the input file but are not matching
rules in this MVP.

Composer reads immediate `experiences/*.yaml` files using the existing schema.
There are no real Experience records yet. For matching project types and Capability
IDs it includes the complete original Experience, including failures, context,
evidence, confidence and limits. This coarse match is advisory; it never changes
rule confidence, manufactures observations, or promotes catalog evidence status.

## Output and confirmation

`schemas/recommendation.schema.json` defines the plan. Each recommendation has a
canonical ID, reason, confidence, source references, record path, review status,
maturity, catalog evidence, exclusions of applicability, component constraints,
prerequisites, and matching Experiences. The plan also preserves the complete
project input, profile/Experience source paths, explicit and propagated exclusions,
and selected conflict pairs. SHA-256 over registry bytes and ordered record paths
and bytes identifies the catalog snapshot independently of a Git checkout.

Confidence measures relevance to explicit rules or preferences, not demonstrated
effectiveness. All initial catalog entries remain draft/experimental with pending
evidence. The Python-specific testing component is disclosed even for a Node.js
project: recommending a general capability does not resolve an implementation.
Optional related capabilities are never automatically included. Conflict edges
are checked in both directions, without choosing a winner.

`requires_confirmation` is always true, including for an empty plan. Users review
scope, unresolved free-text restrictions, component fit, draft status, prerequisites,
conflicts, and evidence before deciding. This prototype has no approve flag or
confirmation persistence; record that decision outside Composer for a future phase.

Rules keep the MVP deterministic, inspectable, and modifiable. A black-box AI
matcher would obscure reasons and introduce unnecessary runtime infrastructure.
No database, server, API, Web UI, Memory, automatic Experience extraction, VCCP
integration, installer, downloader, or agent loop is part of this phase.

## Examples and validation

Each directory in `examples/` contains a project input and a generated plan:
`long-lived-web` recommends three maintenance capabilities, `agent` recommends two,
and `one-off-experiment` recommends zero. These are illustrative project contexts,
not historical evidence. Reproduce a plan by choosing a fresh output path.

Tests cover all JSON Schemas, exact example sets, deterministic output, draft
evidence preservation, defaults/exclusions, prerequisite blocking, one-sided
conflicts, duplicate YAML keys, and faithful advisory Experience handling.
Malformed input fails with the validation error; there are no silent fallbacks,
automatic repair paths, retries, or runtime availability claims.

Phase 3 stops at a reviewable recommendation. Phase 4 awaits Controller acceptance.
