# Experience Lifecycle

Experience describes what happened in a particular project. Capability describes an ability intended for reuse. A record can move through review states, but no state transition edits a Capability.

## States

| State | Meaning | Entry evidence |
| --- | --- | --- |
| `raw` | A project observation captured close to its source, without generalization. | Source project and original observation are retained. |
| `candidate` | A person or AI has organized the observation into a possible lesson. | Source facts and the reasoning that connects them to the lesson are retained; evidence and limits are explicit. |
| `verified` | There is a real project-level observation with checked evidence that confirms what happened in that project. | At least one moderate or strong non-AI, non-single-attempt evidence item (such as project result, CI result, reviewer confirmation, or measured benefit) with checked or independently confirmed verification, plus accepted review by a named reviewer and date. This does not prove causality or universal applicability. |
| `rejected` | Review found the proposed lesson incorrect, misleading, or too context-bound to reuse as stated. | Preserve the reason and source record. |
| `archived` | Retained for history but no longer active for new consideration. | Preserve prior state and review history in the record or linked history. |

Records use `schemas/experience.schema.json`. Phase 1 records without lifecycle `status` retain the legacy evidence shape. Any record with a lifecycle status must use `schemas/evidence.schema.json` for each evidence item. Lifecycle records provide `identity`, `source`, `context`, `observation`, `solution`, `scope`, `reasoning`, and `status`. Do not invent missing source facts. `verified` also requires named reviewer and review date, and at least one qualifying checked evidence item; AI judgment and a single attempt alone cannot meet this gate.

`verified` confirms what was observed in one project. It does not establish that a practice caused the outcome, applies to every project, or automatically promotes a Capability.

## Review path

```text
raw → candidate → verified
                 ↘ rejected
any retained state → archived
```

Transitions are human decisions. A candidate may be revised while preserving its source facts and reasoning. Rejection does not delete evidence; it records where a generalization failed. Archiving preserves history and does not imply acceptance.

Evidence is recorded using `schemas/evidence.schema.json`. AI judgment and a single attempt are weak evidence. CI, reviewer confirmation, repeated use across projects, and measured benefit can strengthen a case when their sources and limits are recorded. Schema validity checks shape, not truth, provenance availability, or causal claims.

## Repository layout

Store records in the matching state directory under `experiences/`: `raw/`, `candidate/`, `verified/`, `rejected/`, or `archived/`. Keep source artifacts in their owning project and reference them; do not copy private project contents into this repository. When a record changes state, move the same record and preserve its identity and review history. This folder layout is a curation convention, not an automated state machine.

## Proposals

An Experience Proposal (`schemas/experience-proposal.schema.json`) explains whether one experience may inform one Capability. A Capability Improvement Proposal (`schemas/capability-proposal.schema.json`) turns reviewed evidence into a concrete change request with version, scope, and risk. Acceptance records human authorization only. A maintainer must separately edit and review the Capability; no proposal action performs that edit.
