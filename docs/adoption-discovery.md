# Existing Project Adoption Discovery

Phase 7A inspects development-environment assets in an existing project and produces an Adoption Report. It does not install, activate, or modify anything in the target.

Run:

```text
python -m adoption discover <project-root>
```

The report is written to standard output. `--output <path>` can write a new report file outside the target project; it refuses to overwrite an existing file. There is no apply mode.

Discovery checks known instruction files, Skills, GitHub workflows and VCCP files, selected editor rules, and documentation whose path or title signals development process, constraints, testing, reviewing, planning, onboarding, AI setup, or MCP setup. It reads project manifests and a few known directory names for lightweight context signals. It does not inventory ordinary source files or all Markdown documents.

Assets found in the project are classified as `project_owned`, including resources that may have originated elsewhere. Capability candidates use the existing Catalog IDs. Coverage is reported as status, supporting project-relative paths, and a short reason. `unknown` means evidence is insufficient; absence alone is not a gap. Mapping logic is an explicit replaceable boundary for later host-agent semantic review, not a claim that local phrase matching understands every project rule.

The command only reads the target. Unit tests compare a synthetic target's file hashes before and after discovery; a real-project pilot is run locally because CI does not have the sibling checkout.

## Revision-bound semantic review and plan

`python -m adoption semantic-package <project-root> --output <file>` records the target repository identity, branch, exact HEAD, remote-tracking revision when available, and dirty state without recording a local path or remote credentials. The package and semantic adoption report retain this binding.

After Host AI semantic review, validate it with `python -m adoption validate-semantic-review <result> --input <file>`, then build a plan with `python -m adoption plan --input <file> --result <result> --project-root <project-root> --output <plan>`. Planning rereads HEAD and dirty state and rejects a stale review. The plan gives every Catalog Capability one disposition: `keep_existing`, `no_action`, `review_gap`, `review_conflict`, `consider_component`, or `blocked`. A gap alone never makes a component eligible. `consider_component` requires an explicitly recommended missing capability and a real project-scoped copy component in the Catalog.

Plans require explicit confirmation and do not execute materialization. `no_changes` and `review_required` with zero materializable changes are valid outcomes. The plan digest covers the canonical plan content except its digest and generation timestamp.

## Explicit decision

`requires_confirmation: true` is not itself approval. Record an explicit decision with `python -m adoption decide <plan> --decision approve|reject --output <decision-file>`, then verify it with `python -m adoption validate-decision <decision-file> --plan <plan>`. The Decision Artifact binds the choice to the recomputed plan digest and exact target commit SHA. It contains no user identity or signature and does not execute adoption. Any future execution must require a valid matching `approve` decision, a current target revision matching the plan, and must act only on `materialization_eligible` items; that execution path is deferred.
