# Experience Governance

## Experience does not automatically become Capability

An Experience is local to a project and describes an observed event and its limits. A Capability is a reusable description intended to guide future work. One experience, even a successful one, may depend on project constraints, team choices, or coincidence. The distinction prevents a local observation from silently becoming a global rule.

## Evidence precedes a proposal

Keep source facts, interpretation, evidence, confidence, scope, and risk visible. AI-generated summaries or suggestions can help form a candidate, but they are not proof. Evidence should identify its source and verification method. Stronger evidence can support review; it does not guarantee causality or transfer to every project.

Lifecycle Experience records use the structured Evidence schema. A `verified` record requires a named reviewer, review date, and at least one moderate or strong non-AI, non-single-attempt evidence item whose source was checked or independently confirmed. Project results, CI results, reviewer confirmation, and measured benefit are suitable types. This means the project observation was checked; it does not prove causality or that every project should use the same practice.

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

The Composer continues to support legacy immediate `experiences/*.yaml` records. Lifecycle records in state subdirectories are not automatically ingested: only an Experience ID explicitly listed in a Capability's `experience_ids` field is eligible, and the link must resolve to a verified record with an accepted review and matching Capability ID. Verified status alone does not make an Experience reusable. The Composer includes linked evidence only when project type and Capability ID match; Experience never creates a recommendation candidate or changes rule confidence. See [Composer](composer.md) for the input contract.

The Phase 10 Codex activation Experience supports only the Codex-native testing projection observed with Codex CLI 0.162.0-alpha.17.2. It does not establish general testing effectiveness, behavior on other Hosts, or a permanent requirement for an AGENTS trigger.
