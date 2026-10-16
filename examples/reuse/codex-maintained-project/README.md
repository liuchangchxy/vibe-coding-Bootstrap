# Curated Codex Testing Experience Reuse

This fixture uses one maintained software project context to compare the actual Phase 9 Base with the Phase 10 Catalog after the authorized promotion.

Before promotion, Composer recommended the existing `testing` Capability with pending evidence, no linked Experience, and no Codex-native testing component in Assembly. After promotion, the same context receives the accepted `exp-codex-testing-activation-v1` as supporting context, sees narrowly scoped supported evidence, and Assembly exposes `codex-native-testing-skill` as a project-scoped `reference`.

The difference comes from the explicit Capability `experience_ids` link, matching project type, and the new Capability component. Experience does not create a recommendation candidate or change rule confidence. The reference remains optional and Codex-specific; no files were activated or copied into a consumer project.

Composer and Assembly outputs are generated from `project-context.yaml`. The JSON summaries record the before/after facts without duplicating complete generated plans. No automatic Host activation occurred.
