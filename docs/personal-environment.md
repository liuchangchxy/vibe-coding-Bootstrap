# Personal AI Development-Environment Manager

VCBS turns from a safe installation framework into a usable personal AI development-environment manager. It decouples long-lived personal preferences and reusable capabilities from both the VCBS program repository and target projects, while enabling AI Hosts (Claude Code, Antigravity, Codex) to drive semantic environment recommendations.

## Four Fixed Responsibilities and Boundaries

The VCBS model clearly distinguishes four boundaries:

```text
┌─────────────────────────────────────────────────────────────┐
│ 1. VCBS Built-in Program & Catalog Data                     │
│    - Capabilities Catalog (capabilities/catalog.yaml)       │
│    - JSON Schemas (schemas/*.json)                          │
│    - Static fallback rules (composer/rules.yaml)            │
│    - Safety engine (ownership, conflict, snapshot/rollback) │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│ 2. User-owned VCBS Home (~/.vcbs/)                          │
│    - Personal Profile & Preferences (profile.yaml)          │
│    - Reusable User Capabilities (capabilities/*.yaml)       │
│    - Verified Reusable Experience (experiences/verified/)   │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│ 3. Target-Project State (<project>/)                        │
│    - Target git revision & technology facts                 │
│    - Existing project assets & Host configurations          │
│    - VCBS manifest (.vibe-coding-bootstrap/manifest.yaml)   │
│    - Component deployments & recovery snapshots             │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│ 4. Current Host AI Semantic Judgment                        │
│    - Reads bounded recommendation input (fact package)      │
│    - Evaluates what is useful, unnecessary, or redundant    │
│    - Emits structured recommendation (or "install nothing") │
│    - VCBS validates IDs, scope, conflicts, and revisions    │
│    - User approves once; deployment applies safely          │
└─────────────────────────────────────────────────────────────┘
```

### 1. VCBS Built-in Data
- **Location:** The VCBS source repository or packaged distribution.
- **Responsibility:** Fact collection, schema validation, Catalog definitions, ownership tracking, conflict checking, materialization, snapshotting, upgrade, uninstall, and rollback.
- **Invariance:** Installing or upgrading VCBS never touches personal state or target-project state.

### 2. User-Owned VCBS Home (`~/.vcbs/`)
- **Location:** Defaults to `~/.vcbs/` (overridable via `$VCBS_HOME` or `--vcbs-home <path>`).
- **Layout:**
  ```text
  ~/.vcbs/
  ├── profile.yaml             # Personal preferences and tool defaults
  ├── capabilities/            # Reusable user capability & component definitions
  └── experiences/             # Personal verified and candidate Experience records
      ├── verified/
      ├── candidate/
      └── rejected/
  ```
- **Sync Boundary:** Ordinary user-owned files. Can be versioned or synced using standard user tools (Git, OneDrive, Syncthing, iCloud, etc.). VCBS does not require or run any cloud synchronization service.
- **Isolation:** Reusable assets stored in VCBS Home are never silently copied into unrelated projects without explicit recommendation and user confirmation.

### 3. Target-Project State
- **Location:** Within the target project directory.
- **Responsibility:** Project source code, project-owned instructions (`AGENTS.md`, `CLAUDE.md`, etc.), target git revisions, and VCBS-managed installations tracked strictly in `.vibe-coding-bootstrap/manifest.yaml`.
- **Isolation:** Project-specific assets stay in the project; they do not leak into other projects or overwrite user home definitions.

### 4. Host Semantic Judgment
- **Location:** Executed by the active AI Host (e.g. Claude Code, Antigravity, Codex) through guided CLI interactions.
- **Responsibility:** Semantic reasoning about project relevance. The Host AI analyzes the bounded fact package (`recommendation_input`), evaluates project goals, existing assets, and personal preferences, and outputs a structured recommendation matching `schemas/host-recommendation.schema.json`.
- **Constraint:** The Host AI may only recommend registered Catalog IDs or user capability IDs. It cannot invent arbitrary targets or bypass existing ownership/conflict rules. A valid recommendation may be **install nothing**.

---

## Simplified Normal Setup Flow

The normal setup flow requires only a single user confirmation:

```text
inspect project facts
        ↓
collect personal context (~/.vcbs)
        ↓
Host AI reasons over fact package
        ↓
show concise "what / why / scope"
        ↓
one user approval (--confirm)
        ↓
apply through existing deployment engine
```

### Step 1: Inspect Project and Context
```bash
vcbs setup inspect --project-root . --host claude --recommendation-input /tmp/facts.json --json
```
Returns a bounded, machine-readable fact package containing:
- Project technologies and target revision (`commit_sha`, dirty status)
- Existing relevant project assets and Host command tools
- Currently VCBS-managed components
- Selectable Catalog capabilities and user-defined capabilities
- Detected conflict/redundancy signals
- Personal preferences loaded from `~/.vcbs/profile.yaml`
- Matching verified experiences from `~/.vcbs/experiences/verified/`

### Step 2: Host Semantic Recommendation
The Host AI reasons about the project facts and writes a structured recommendation (`/tmp/rec.json`) conforming to `schemas/host-recommendation.schema.json`:

```json
{
  "format_version": 1,
  "target": {
    "commit_sha": "abc1234...",
    "dirty": false,
    "path": "/path/to/project"
  },
  "host": "claude",
  "recommendations": [
    {
      "capability_id": "testing",
      "reason": "Python project detected without test guidance.",
      "scope": "project"
    }
  ],
  "not_recommended": [
    {
      "capability_id": "documentation",
      "reason": "Existing README.md already provides project documentation."
    }
  ],
  "conflicts": [],
  "install_nothing": false,
  "reason": "Add testing capability"
}
```

If the project already has all necessary components, the Host outputs an **"install nothing"** recommendation:
```json
{
  "format_version": 1,
  "target": { "commit_sha": "abc1234...", "dirty": false, "path": "/path/to/project" },
  "host": "claude",
  "recommendations": [],
  "not_recommended": [],
  "conflicts": [],
  "install_nothing": true,
  "reason": "Project is already fully configured; no changes needed."
}
```

### Step 3: Plan and Concise Summary
```bash
vcbs setup plan --project-root . --host claude --recommendation /tmp/rec.json --output /tmp/plan.json
```
VCBS validates the recommendation against known capability IDs, component IDs, target revision, and conflict rules. It displays a concise "what / why / scope" summary:

```text
Route: compose
Target: my-app @ 8f3a1c2
Host: claude
Recommendation Source: Host AI semantic judgment
What:
  - testing (project): project-test-guidance
    Why: Python project detected without test guidance.
Not recommended:
  - documentation: Existing README.md already provides project documentation.
Scope: project
Changes: 1 project writes, 0 global writes, 0 conflicts
Plan: /tmp/plan.json
Next: review the plan, then run setup apply --plan <plan> --confirm.
```

### Step 4: One User Approval
```bash
vcbs setup apply --project-root . --plan /tmp/plan.json --confirm
```
The user confirms once. The existing deployment and safety engine verifies the target revision, checks ownership, creates recovery snapshots, and applies the changes.

If "install nothing" was planned, `apply` cleanly reports `no_changes` with zero mutations.

### Fallback to Static Rules
If `--recommendation` is not provided, `vcbs setup plan` falls back to the static rule engine or explicit user flags (`--capability`, `--goal`, `--semantic-result`), ensuring diagnostics and offline scripting remain fully functional.
