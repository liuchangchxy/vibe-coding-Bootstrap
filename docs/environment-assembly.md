# Environment Assembly Foundation

## Recommendation and Assembly

Recommendation selects and explains Capabilities using project context, catalog metadata, Profile, and curated Experience. Assembly takes that reviewed plan and resolves registered Capability components into explicit items with source, scope, delivery hint, dependencies, and conflicts.

## Capability and Component

A Capability is the only high-level entry point: it describes a reusable ability and its applicability. A Component is a specific resource that contributes to that ability, such as a Skill, MCP, rule, prompt, template, document, or workflow reference. A documentation-only Capability does not imply an installable implementation.

## Assembly and Installation

Assembly is deterministic, local-only planning. It reads inputs and emits an inspectable Assembly Plan. It does not download, copy, link, install, execute, or modify a target project. Delivery describes a possible future method only.

## Scope

See [scope-model.md](scope-model.md) for Global, Project, and External. Global discovery does not activate a component. Project content cannot be promoted automatically. External resources remain references so this project does not become a stale fork of upstream material.

## Dependencies and conflicts

Capability prerequisites must be selected in Recommendation. An explicitly excluded prerequisite stays excluded and creates an unresolved issue; Assembly never re-adds it. Capability conflicts, component conflicts, incompatible scope combinations, and competing delivery choices are recorded and require human review. Assembly does not select a winner.

## Confirmation and later work

Every Assembly Plan requires confirmation, including empty plans. The preview makes the choices and unresolved issues understandable before anyone proceeds. Phase 5 does not install. A later Phase 6 may discuss an installation design, with its own explicit scope and approval.
