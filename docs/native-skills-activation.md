# Native Skills Activation for AI Coding Hosts

Bootstrap extends the Unified Product Loop to support safe adoption, activation, verification, and lifecycle management of native Skills for modern AI coding hosts: **Claude Code** and **Google Antigravity**, while preserving existing **Codex** and **None** behavior.

---

## 1. Supported Hosts and Destinations

Host | Capability | Repo-Local Skill Destination | Invocation / Recognition Trigger | Trigger File Mutation
:--- | :--- | :--- | :--- | :---
**Codex** | `testing` | `.agents/skills/bootstrap-testing/SKILL.md` | Exact prompt trigger: `For implementation or bug-fix tasks that change observable behavior, use the $bootstrap-testing skill.` | `AGENTS.md` (appended)
**Claude Code** (`claude` / `claude_code`) | `vcbs-manager` | `.claude/skills/vcbs-manager/SKILL.md` | Automatic discovery via YAML frontmatter (`name: vcbs-manager`) | *None* (`CLAUDE.md` untouched)
**Antigravity** | `vcbs-manager` | `.agents/skills/vcbs-manager/SKILL.md` | Workspace progressive disclosure via YAML frontmatter (`name: vcbs-manager`) | *None* (`AGENTS.md` / `GEMINI.md` untouched)
**None** | *N/A* | *None* | *None* | *None*

---

## 2. Authoritative Host Documentation & Recognition Contracts

### Claude Code
- **Authoritative Reference**: Anthropic Claude Code documentation ([Claude Code Skills](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview)).
- **Discovery Mechanism**: Claude Code inspects the workspace directory for `.claude/skills/<skill-name>/SKILL.md` (or `.claude/skills/<skill-name>.md`). Skills declare a YAML frontmatter header containing at least `name` and `description`.
- **Invocation Semantics**: When a user task or workflow mentions capability management or matching topics, Claude Code loads the skill into context on demand. No modification to `CLAUDE.md` is needed or permitted.

### Google Antigravity
- **Authoritative Reference**: Antigravity Customization System Guide ([Antigravity Customizations / Workspace Skills Guide](https://cloud.google.com/gemini/docs/antigravity)).
- **Discovery Mechanism**: Antigravity discovers workspace customizations in `.agents/` (or `.agent/`, `_agents/`, `_agent/`) at the repository root. Skills are located in `.agents/skills/<name>/SKILL.md`.
- **Invocation Semantics**: Antigravity applies **progressive disclosure**: skill names and descriptions are indexed from frontmatter without filling the context window. The body is loaded into context only when the agent or user activates the skill. No modification to `AGENTS.md` or `GEMINI.md` is needed or permitted.

### OpenAI Codex
- **Authoritative Reference**: [Codex Native Activation](codex-native-activation.md), citing OpenAI Developer documentation on Codex Skills.
- **Discovery Mechanism**: Requires both `.agents/skills/bootstrap-testing/SKILL.md` and the explicit trigger in `AGENTS.md`.

---

## 3. Shared `.agents/skills` Ownership Isolation & Cross-Host Discovery Rules

Both OpenAI Codex and Google Antigravity utilize the repo-local `.agents/skills/` tree as their standard workspace skill discovery surface:

1. **Discovery Surfaces & Cross-Host Overlap**:
   - **Codex**: Discovers repo-local skills in `.agents/skills/<name>/SKILL.md`.
   - **Antigravity**: Discovers workspace skills in `.agents/skills/<name>/SKILL.md` (as documented in the Antigravity Customization System Guide).
   - **Consequence**: Deploying an Antigravity skill to `.agents/skills/vcbs-manager/SKILL.md` physically places that skill file inside Codex's repo-local discovery hierarchy.

2. **Trigger Isolation & Codex Inactivity**:
   - Under the authoritative Codex Native Activation contract (`docs/codex-native-activation.md`), Codex v1 requires an explicit trigger phrase appended to `AGENTS.md` (`For implementation or bug-fix tasks that change observable behavior, use the $bootstrap-testing skill.`) in order to activate or invoke a skill.
   - Antigravity activation **never touches** `AGENTS.md` or `GEMINI.md`.
   - Consequently, while `.agents/skills/vcbs-manager/SKILL.md` is visible within the `.agents/skills/` tree, Codex will not automatically invoke it without an `AGENTS.md` trigger line.

3. **Explicit Plan Surfacing & Human Consent**:
   - Because `.agents/skills` is shared, cross-host exposure is **never silent**.
   - During planning (`vcbs plan --host antigravity`), VCBS explicitly surfaces a dedicated warning:
     `"Antigravity skill destination '.agents/skills/vcbs-manager/SKILL.md' shares the '.agents/skills' root with Codex. Codex discovers this directory, but will not invoke the skill without an AGENTS.md trigger."`
   - This warning is rendered in the human-facing plan summary before approval, ensuring explicit informed consent prior to running `vcbs apply`.

4. **Status Transparency**:
   - In `vcbs status`, each entry in `native_activations` reports:
     - `shared_surface_with: "codex"` (for Antigravity activations)
     - `shared_surface_notice: "Shares .agents/skills discovery root with Codex; unmanaged in Codex without AGENTS.md trigger."`
   - This ensures continuous observability over the shared discovery root.

5. **Subdirectory & Manifest Isolation**:
   - **Path Boundaries**: Codex owns only `.agents/skills/bootstrap-testing/SKILL.md`, while Antigravity owns only `.agents/skills/vcbs-manager/SKILL.md`. Each skill resides in its own isolated subfolder; paths never collide.
   - **Manifest Ownership**: The ownership manifest `.vibe-coding-bootstrap/activation.json` records distinct entries in its `activations` array for each `(host, capability)` pair.
   - **Independent Lifecycle**: Deploying, upgrading, uninstalling, or rolling back an Antigravity activation preserves existing Codex activations and `AGENTS.md`, and vice versa.

---

## 4. Host Activation Semantics vs. File Presence

**Merely writing a Skill file to disk is NOT proof that a host has consumed or used it.**
- **File presence**: Demonstrates only that the asset was deployed according to the host's required directory layout.
- **Consumption proof**: Claiming that a host consumed the skill requires revision-bound runtime discovery and invocation evidence (such as a CLI execution trace where the host inspects the repository and invokes real VCBS commands).
- **Unavailable hosts**: If an installed host or its headless automation interface is unavailable in the environment, the outcome must be explicitly reported as `NOT_TESTED` with the exact reason (e.g. absence of a non-interactive CLI runner for desktop apps), rather than promoting file presence to full activation.

---

## 5. Lifecycle Management

Native skills participate fully in all standard VCBS lifecycle operations:
- **Status (`vcbs status`)**: Verifies on-disk SHA-256 for all active host skills. Tampering or deletion immediately reports `status: conflict`.
- **Deploy (`vcbs deploy --host <host>`)**: Installs the native skill, records ownership in `.vibe-coding-bootstrap/activation.json`, and records reference delivery in `manifest.yaml`.
- **Upgrade (`vcbs upgrade --host <host>`)**: Checks for updated template digests and applies upgrades with target revision binding.
- **Uninstall (`vcbs uninstall [--component <id>]`)**: Safely removes specified or all native skills, preserves sibling activations in `activation.json`, and prunes empty directories.
- **Rollback (`vcbs rollback --apply`)**: Restores exact pre-apply state and leaves no residue in `.vibe-coding-bootstrap/`.
