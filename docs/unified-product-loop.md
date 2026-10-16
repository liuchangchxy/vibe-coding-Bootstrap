# Unified Product Loop v1

The normal user flow is `inspect → plan → confirm once → apply`. Users do not need to call or understand Composer, Assembly, Materializer, Adoption, or Activation directly. Those CLIs remain available for advanced and diagnostic use.

```text
python -m bootstrap inspect --project-root <project> --host codex
python -m bootstrap plan --project-root <project> --host codex --context <context.yaml> --output <plan.json>
python -m bootstrap decide <plan.json> --decision approve --output <decision.json>
python -m bootstrap apply --plan <plan.json> --decision <decision.json> --project-root <project>
```

`inspect` is read-only and binds the repository revision. It selects `compose` unless existing project-owned AI/development environment assets are found; Bootstrap-owned, hash-matching assets do not change a Compose target into Adoption. Adoption inspection includes the bounded semantic review package.

For Compose, the Host reads the repository, README, manifest, and the user's request, then supplies the existing Project Context schema. Ask the user only for missing facts that change the plan. Without context, planning returns `PROJECT_CONTEXT_REQUIRED`. For Adoption, the Host reviews the bounded package and writes a semantic result under the existing schema; without it, planning returns `HOST_SEMANTIC_REVIEW_REQUIRED`.

The Host presents `render_summary` output and asks once. It must create an approve Decision only after explicit human confirmation. The Unified Decision is the human authority; exact lower-level decisions are deterministic in-memory projections. Bootstrap has no built-in model/provider.

Plans bind target revision, route, Host, Catalog snapshot, Profile hash, exact materialization actions, activation subplans, and classifications. Artifact files are created exclusively outside the target. Apply preflights every action, rejects stale inputs and conflicts without writes, applies Codex activation before materialization, and verifies the promised outputs. Global writes require an explicit root at planning and application. Existing files are never force-overwritten. A no-op needs no decision and creates no target Bootstrap artifacts.

Supported Hosts: `codex` and `none`. Native activation is Codex-only and Compose-only when testing is selected. Adoption remains conservative and does not add Host activation. No automatic Git add, commit, or push occurs in a consumer project.
