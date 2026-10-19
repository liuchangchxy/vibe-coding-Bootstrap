# Claude Code Native Activation Acceptance

Date: 2026-10-20. Host: Claude Code CLI `2.1.303`.

The acceptance test evaluated Claude Code recognizing and consuming the repo-local native skill located at `.claude/skills/vcbs-manager/SKILL.md`.

## A/B Comparison Summary

- **Control Run**: Executed in a clean Git repository without `.claude/skills/`. Prompt asked to inspect `.claude/skills` and explain what `vcbs-manager` does. Claude reported that `.claude/skills` does not exist and no `vcbs-manager` skill exists.
- **Activated Run**: Executed in the repository with `.claude/skills/vcbs-manager/SKILL.md` activated by VCBS. Under the exact same prompt, Claude discovered the repo-local skill and correctly summarized its purpose: guiding safe inspection, planning, human approval, application, and rollback of VCBS component deployments using `python -m bootstrap` / `./vcbs`.
- **Isolation Verification**: No `CLAUDE.md`, `AGENTS.md`, or extraneous trigger files were created. Claude Code natively discovers skills placed under `.claude/skills/<skill-name>/SKILL.md`.

Evidence files contain sanitized summaries, not raw local paths or session tokens.
