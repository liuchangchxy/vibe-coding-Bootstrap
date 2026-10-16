# Codex Native Activation Acceptance

Date: 2026-10-16. Host: Codex CLI `0.162.0-alpha.17.2`.

The initial skill-only Activated run did not emit the acceptance marker, while its Control run also omitted it. The repo-local `.agents/skills/` location and front matter match current OpenAI documentation, so the pilot added the permitted one-line `AGENTS.md` trigger and repeated the A/B comparison from the same initial commit.

Final result: Control marker absent; Activated marker present. Both final runs started at `aa6921294fb1e8a4e0ebe412d55818435a8e8478` and used the identical prompt. The Activated transcript records Codex reading the repo-local Skill, a pre-fix run with two failing tests, then a fixed run with all three tests passing. `AGENTS.md` contains only the trigger line. The marker was appended only to the temporary pilot copy after Bootstrap apply; it is absent from the production Skill.

Evidence files contain sanitized summaries, not raw CLI transcripts or local paths. The disposable repositories and full transcripts are kept outside this repository and are not part of the deliverable.
