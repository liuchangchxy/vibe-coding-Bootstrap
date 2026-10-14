# Experience Governance

## Experience does not automatically become Capability

An Experience is local to a project and describes an observed event and its limits. A Capability is a reusable description intended to guide future work. One experience, even a successful one, may depend on project constraints, team choices, or coincidence. The distinction prevents a local observation from silently becoming a global rule.

## Evidence precedes a proposal

Keep source facts, interpretation, evidence, confidence, scope, and risk visible. AI-generated summaries or suggestions can help form a candidate, but they are not proof. Evidence should identify its source and verification method. Stronger evidence can support review; it does not guarantee causality or transfer to every project.

## Incorrect experiences remain useful records

The lifecycle allows `raw`, `candidate`, `verified`, `rejected`, and `archived` records. Retaining the original account and review outcome makes the path of a claim inspectable. Rejected records help prevent a known overgeneralization from resurfacing as if it were confirmed. Rejection does not erase what happened in the project.

## Human-controlled capability evolution

The path is:

```text
Project → Experience → Evidence → Experience Proposal
        → Capability Improvement Proposal → Human Review
        → separately authorized Capability edit
```

Experience Proposals may recommend `add_component`, `modify_guidance`, `change_applicability`, or `no_action`. A separate Capability Improvement Proposal names the current version, requested change, evidence, affected scope, risks, and approval state. Approval does not itself update a record. There is no automatic Capability edit, stable promotion, model training, project scanning, or background summarization in this phase.

## Composer relationship

The Composer may read curated Experience as contextual support for a recommendation. Its existing immediate `experiences/*.yaml` input convention remains unchanged. Lifecycle records placed in the state subdirectories are governance records and are not automatically ingested by the Composer. Only deliberate curation into its existing input path can make an Experience available there; a proposal or `verified` status alone does not alter Composer rules or Capability data. See [Composer](composer.md) for its current input boundary.
