# Unified Product Loop v1

The normal user flow is `inspect → plan → confirm once → apply`. Users do not need to call or understand Composer, Assembly, Materializer, Adoption, or Activation directly. Those CLIs remain available for advanced and diagnostic use.

```text
python -m bootstrap inspect --project-root <project> --host codex
python -m bootstrap plan --project-root <project> --host codex --context <context.yaml> --output <plan.json>
python -m bootstrap decide <plan.json> --decision approve --output <decision.json>
python -m bootstrap apply --plan <plan.json> --decision <decision.json> --project-root <project>
```

A mutating apply first creates a local Git snapshot under `refs/vibe-coding-bootstrap/snapshots/<deployment-id>` using a temporary index, then records touched paths and post-apply hashes under Git metadata. It does not move `HEAD`, change the real index, modify a branch, or push the snapshot. Ignored files are not included.

```text
python -m bootstrap rollback --project-root <project> --deployment <deployment-id>
python -m bootstrap rollback --project-root <project> --deployment <deployment-id> --apply
```

Rollback previews by default. `--apply` restores only paths changed by that deployment and refuses all writes if any touched path differs from its recorded post-apply state. Project files are restored from the pre-apply snapshot; global files use a per-file local backup. Rollback does not reset, stage, commit, or push. Snapshots remain local recovery points and are not pruned automatically.

`inspect` is read-only and binds the repository revision. It selects `compose` unless existing project-owned AI/development environment assets are found; Bootstrap-owned, hash-matching assets do not change a Compose target into Adoption. Adoption inspection includes the bounded semantic review package.

For Compose, the Host reads the repository, README, manifest, and the user's request, then supplies the existing Project Context schema. Ask the user only for missing facts that change the plan. Without context, planning returns `PROJECT_CONTEXT_REQUIRED`. For Adoption, the Host reviews the bounded package and writes a semantic result under the existing schema; without it, planning returns `HOST_SEMANTIC_REVIEW_REQUIRED`.

The Host presents `render_summary` output and asks once. It must create an approve Decision only after explicit human confirmation. The Unified Decision is the human authority; exact lower-level decisions are deterministic in-memory projections. Bootstrap has no built-in model/provider.

Plans bind target revision, route, Host, Catalog snapshot, Profile hash, exact materialization actions, activation subplans, and classifications. Apply reruns materialization preflight and requires planned copy actions, classifications, and hashes to remain identical to the approved Unified Plan before snapshot creation. Artifact files are created exclusively outside the target. Apply preflights every action, rejects stale inputs and conflicts without snapshots, applies Codex activation before materialization, and verifies the promised outputs. Global writes require an explicit root at planning and application. Existing files are never force-overwritten. A no-op needs no decision and creates no snapshot or deployment record.

## Managed Updates

Bootstrap manages safe upgrades of its owned assets (both Materializer copy components and Codex-native activation):
- If destination bytes match the prior owned manifest digest and the desired source has updated, the plan classifies the item as an upgrade with old/new versions, digests, and delta shown.
- If destination bytes differ from the manifest digest (user modified the file), Bootstrap detects a conflict and refuses to overwrite.
- Unmanaged files remain project-owned and are never taken over.
- Applying an upgrade takes a pre-apply Git snapshot; rollback restores the exact pre-update bytes.
- Repeating an upgrade run is idempotent and returns `no_changes`.

## Experience Learning Loop v1

```text
python -m bootstrap harvest --project-root <project> --observation <observation.yaml>
python -m bootstrap review-experience <candidate-id> --decision approve --reviewer <name>
```

- `harvest`: Bounded semantic distillation from Host AI is bound with target repository facts, commit revision, and unverified evidence to produce a candidate Experience. Privacy guardrails reject credentials, API keys, host absolute paths, bulk code dumps, and raw chat logs.
- `review-experience`: An explicit human review approves or rejects the candidate. Upon approval by a named reviewer, qualifying evidence is checked, the record is verified, and it is registered in `profiles/personal.yaml#reusable_experience_ids`.
- Cross-project reuse: When Composer plans for another project matching the capability and project context, verified personal experiences are automatically included without editing generic Capability Catalog definitions.

## Standardized Lifecycle Operations (Phase 12)

Phase 12 elevates VCBS into a product with four core lifecycle operations: `status`, `deploy`, `upgrade`, and `uninstall`. Users interact through `./vcbs` or `python -m bootstrap <subcommand>`.

### 1. Status (`vcbs status`)

Read-only inspection of target project management status.

```text
python -m bootstrap status --project-root <project> [--json]
```

- Reports target path, current Git revision, deployment state (`installed`, `partially_installed`, `not_installed`).
- Lists VCBS-managed components with scope (`project`, `global`, `external`), delivery mode, installed version, disk sha256 vs expected sha256, and health status (`healthy`, `modified_conflict`, `missing`).
- Reports Native Activation status (`activated`, `drifted`, `not_activated`) and Host support status.
- Detects unmanaged AI assets (rules, skills, mcp) that are project-owned and protected from takeover.
- Lists available recovery snapshots and recent deployment history.
- Produces clean human-readable summaries or schema-validated JSON conforming to `schemas/bootstrap-status.schema.json`.
- Guaranteed strictly read-only: does not modify files, Git index, HEAD, manifests, or profiles.

### 2. Standardized Deploy (`vcbs deploy`)

Unified first-time deployment into a target project.

```text
python -m bootstrap deploy --project-root <project> [--host <host>] [--context <context.yaml>] [--global-root <dir>] [--apply] [--decision <decision.json>]
```

- Reuses existing inspect → plan → decide → apply pipeline under the hood.
- Distinguishes clean Compose from Existing Adoption.
- Previews the deployment plan by default without writing files.
- Execution requires an approved decision artifact (`--decision <decision.json>`) created through explicit confirmation; silent auto-approval at apply time is strictly prohibited.
- Creates a safety Git snapshot under `refs/vibe-coding-bootstrap/snapshots/<id>` before any disk mutation.
- Verified post-apply state; repeats are fully idempotent (`no_changes`).

### 3. Standardized Upgrade (`vcbs upgrade`)

Managed component upgrade for installed projects.

```text
python -m bootstrap upgrade --project-root <project> [--global-root <dir>] --decision <decision.json> --apply
```

- Reads installed `manifest.yaml` and compares against the current Capability Catalog.
- Categorizes each item: `up_to_date`, `upgrade_available`, `conflict`.
- Shows old vs new version, old vs new SHA-256, and patch preview.
- Modifying an installed file causes a `conflict` block: VCBS refuses to overwrite user modifications.
- Executes safe upgrade only with an approved decision artifact (`--decision <decision.json>`) and pre-apply Git snapshot; silent auto-approval is prohibited. Rollback can restore exact pre-upgrade state.
- Repeating an upgrade run immediately yields `no_changes`.

### 4. Safe Uninstall (`vcbs uninstall`)

Strict, ownership-verified uninstallation of VCBS-deployed assets.

```text
python -m bootstrap uninstall --project-root <project> [--component <id>] [--components <id1,id2>] [--global-root <dir>] --decision <decision.json> --apply
```

- Explicit Approval Requirement: Execution with `--apply` requires an approved decision artifact (`--decision <decision.json>`) created through explicit human confirmation; silent auto-approval is strictly prohibited.
- Strict Ownership Verification: Assets are only uninstalled if recorded in `manifest.yaml` and their on-disk SHA-256 matches the manifest digest.
- Never guesses ownership by file name or directory scanning; never performs recursive `rm -rf`.
- Never touches project source code, unmanaged AI configurations (user rules, skills, MCP configs), or consumer Git state.
- Conflict blocking: If a managed file was edited by the user (`conflict: modified_by_user`), uninstall plan marks it as blocked and refuses removal unless reconciled.
- Scope support: Safely uninstalls project-scope files, global-scope files (with per-file backup), and Codex Native Activation (`.agents/skills/bootstrap-testing/SKILL.md`).
- Strict Pre-apply Verification: `apply_uninstall` re-verifies the exact manifest content, ownership, and remaining component records, as well as the exact global root and path containment. Any drift aborts execution before any disk write.
- Clean pruning: Removes empty parent directories created for VCBS components without affecting non-empty directories.
- Clean manifest lifecycle: Removes `manifest.yaml` if all managed items are uninstalled, or updates it if partial component uninstall is selected.
- Rollback support: Apply takes a pre-uninstall Git snapshot. Exact pre-uninstall files and manifests can be restored via `python -m bootstrap rollback`.

## Supported AI Hosts & Native Activation Contracts

Supported Hosts: `codex`, `claude` (and `claude_code`), `antigravity`, and `none`.
When targeting either host or both (`--host claude,antigravity`), VCBS provides a single bounded consent and plan with per-host digests.

### Host Activation Contracts & Destinations

1. **Codex (`host: codex`)**:
   - Destination: `.agents/skills/bootstrap-testing/SKILL.md`
   - Trigger: Exact trigger appended to `AGENTS.md`: `For implementation or bug-fix tasks that change observable behavior, use the $bootstrap-testing skill.`
   - Contract: Codex CLI requires both the repo-local Skill and the exact `AGENTS.md` trigger.
   - Reference: [Codex Native Activation](codex-native-activation.md).

2. **Claude Code (`host: claude` / `host: claude_code`)**:
   - Destination: `.claude/skills/vcbs-manager/SKILL.md`
   - Contract: Claude Code automatically discovers repo-local skills in `.claude/skills/<name>/SKILL.md` with standard YAML frontmatter (`name`, `description`). No instruction file modification (`CLAUDE.md`) is performed.
   - Capability: `vcbs-manager` (workflow guidance for real read-only VCBS inspection, bounded planning, and human confirmation).

3. **Google Antigravity (`host: antigravity`)**:
   - Destination: `.agents/skills/vcbs-manager/SKILL.md`
   - Contract: Antigravity discovers workspace customizations in `.agents/skills/<name>/SKILL.md` via progressive disclosure (skills are indexed by frontmatter and loaded into context on demand). No instruction file modification (`AGENTS.md` / `GEMINI.md`) is performed.
   - Capability: `vcbs-manager` (workflow guidance for real read-only VCBS inspection, bounded planning, and human confirmation).

4. **None (`host: none`)**:
   - No native host activations or Skill files are installed. Standard managed assets in `manifest.yaml` are materialized as planned.

### Shared `.agents` Ownership Isolation & Discovery Rules

Both Codex and Google Antigravity utilize the repo-local `.agents/skills/` hierarchy:
- Shared Discovery Root: Both Codex and Antigravity inspect `.agents/skills/`. Deploying an Antigravity skill places it on Codex's discovery surface.
- Trigger Isolation: Codex requires an explicit trigger line in `AGENTS.md` to invoke skills. Antigravity activation does not mutate `AGENTS.md`, preventing unintended automatic Codex execution.
- Informed Consent: Planning for Antigravity surfaces an explicit warning alerting the user to the shared discovery root with Codex.
- Subdirectory Isolation: Codex owns `.agents/skills/bootstrap-testing/`; Antigravity owns `.agents/skills/vcbs-manager/`. Paths never collide.
- Ownership tracking: The target manifest `.vibe-coding-bootstrap/activation.json` records individual activations per `(host, capability)` tuple, tracking artifact paths, sources, and SHA-256 digests. Multi-host activation manifests record `host: "multiple"` while preserving per-activation entries.
- Coexistence: Installing, upgrading, or uninstalling an Antigravity skill does not affect Codex's `bootstrap-testing` skill or `AGENTS.md`, and vice versa.

### Host Consumption Semantics vs. File Presence

**Merely writing a Skill file to disk is NOT proof that a host has consumed or used it.**
- File presence in `.claude/skills/` or `.agents/skills/` establishes only that the skill asset has been deployed and is available for host discovery.
- Proving actual host consumption requires revision-bound runtime discovery and invocation evidence (such as a non-interactive CLI execution transcript where the host selects and invokes the skill).
- If a host or its automation interface is unavailable on the target machine, the status MUST be reported as `NOT_TESTED` with the exact reason, rather than inferring or claiming host activation from file existence.

No automatic Git add, commit, or push occurs in a consumer project.
