---
name: vcbs-manager
description: Use when inspecting, planning, adopting, deploying, upgrading, or rolling back Vibe Coding Bootstrap (VCBS) components and capabilities in an existing project.
---

# VCBS Manager

Manage Vibe Coding Bootstrap (VCBS) capabilities, deployment lifecycles, and host activations safely in this repository using verified CLI operations.

## Core Rules and Guardrails

1. **Only Real Commands**: Use only documented `python -m bootstrap` or `./vcbs` CLI commands. Never invent subcommands, parameters, or flags.
2. **Explicit Human Consent**: Never approve plans automatically or infer consent from gaps. Always display generated plan summaries to the human operator and require explicit approval before running `decide approve` or `apply`.
3. **No Dispatch Authority**: This skill grants no GitHub Issue dispatch authority. Automation or VCCP dispatch operates strictly through authorized external consumer runners and reviewers.
4. **Preserve Project Assets**: Respect existing project-owned instruction files (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`), existing skills, rules, and configurations. Never overwrite unmanaged files.
5. **Fail-Closed on Stale State**: All plans bind to exact target git revisions and catalog digests. If the repository head or dirty state changes, planning must be regenerated.

## Standard Workflows

### 1. Inspect Project State

Query current VCBS status, route, and managed components inventory:

```bash
# Check status and component health
python -m bootstrap status --project-root .

# Structured JSON inspection
python -m bootstrap inspect --project-root . --output /tmp/vcbs-inspect.json
```

- If `route == "adoption"`: the project has existing environment assets. VCBS operates in Existing Project Adoption mode.
- If `route == "compose"`: VCBS operates in Project Context Compose mode.

### 2. Generate Bounded Plan

For existing projects (Adoption route):
1. Review the generated `semantic_package` from inspect output.
2. Produce a valid `semantic-review-result.json` assessing catalog capabilities.
3. Generate the bounded plan:
```bash
python -m bootstrap plan --project-root . --host antigravity --semantic-result /tmp/semantic-result.json --output /tmp/vcbs-plan.json
```
Supported hosts: `claude`, `antigravity`, `codex`, `none`, or `claude,antigravity` for both.

For new projects (Compose route):
```bash
python -m bootstrap plan --project-root . --host antigravity --context <context.yaml> --output /tmp/vcbs-plan.json
```

### 3. Request Explicit Human Approval

Present the plan summary to the human operator:
- Review planned project changes, host activations, and potential conflicts.
- Ask for explicit approval. Do NOT proceed without human confirmation.

When approved by the human operator, record the decision:
```bash
python -m bootstrap decide /tmp/vcbs-plan.json --decision approve --output /tmp/vcbs-decision.json
```

If rejected:
```bash
python -m bootstrap decide /tmp/vcbs-plan.json --decision reject --output /tmp/vcbs-decision.json
```

### 4. Apply Approved Plan

Execute the exact approved plan:
```bash
python -m bootstrap apply --plan /tmp/vcbs-plan.json --decision /tmp/vcbs-decision.json --project-root . --output /tmp/vcbs-apply-result.json
```

Verify that:
- Return code is 0.
- `status` is `"applied"`.
- `verification.passed` is `true`.

### 5. Preview or Execute Rollback

To preview a rollback without making changes:
```bash
python -m bootstrap rollback --project-root . --deployment <deployment-id>
```

To execute the rollback with explicit user confirmation:
```bash
python -m bootstrap rollback --project-root . --deployment <deployment-id> --apply
```
