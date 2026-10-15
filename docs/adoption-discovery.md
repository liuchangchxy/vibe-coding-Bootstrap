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
