# Codex Native Activation

Bootstrap projects the existing `testing` Capability into Codex's repo-local Skill surface at `.agents/skills/bootstrap-testing/SKILL.md`. The Codex CLI 0.162.0 A/B pilot did not select the Skill from a matching task prompt, so activation also creates one short `AGENTS.md` trigger. When a target already has `AGENTS.md`, planning returns `review_required` and leaves it untouched.

Create a revision-bound plan:

```powershell
python -m activation codex-plan --project-root <target> --capability testing --output activation-plan.json
python -m activation decide activation-plan.json --decision approve --output activation-decision.json
python -m activation apply --plan activation-plan.json --decision activation-decision.json --project-root <target>
```

Plan and decision files are created without replacing existing files. Apply checks the exact plan digest, decision, target commit and dirty state again. A conflict is left untouched. The target manifest `.vibe-coding-bootstrap/activation.json` records the owned Skill and trigger with their SHA-256 values. Deactivation removes both only when their current hashes still match the manifest; a changed file is reported as a conflict.

The adapter supports `host=codex` and `capability=testing`. The production Skill template lives under `host_adapters/codex/skills/testing/`; it is kept outside generic project components. Acceptance-only marker text lives in `tests/fixtures/codex_activation/` and is not part of the production Skill.

The repo-local Skill layout follows current Codex conventions documented by OpenAI: [Using skills to accelerate OSS maintenance](https://developers.openai.com/blog/skills-agents-sdk) and [Codex Skills evaluation guidance](https://developers.openai.com/blog/eval-skills).
