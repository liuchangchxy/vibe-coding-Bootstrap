# Experience Event Capture

`vcbs harvest-event` accepts a normalized, caller-supplied event and a bounded Host observation. It creates one `candidate` Experience only when the Issue and PR are closed, the PR is merged, successful CI targets the PR head, repository URLs agree, and the local project checkout is clean at the exact merge commit.

```bash
vcbs harvest-event --project-root <project> --event <event.json> --observation <observation.json> --host <codex|claude|antigravity|none>
```

The event envelope is defined by [`schemas/experience-event.schema.json`](../schemas/experience-event.schema.json). Capture records the repository, Issue, PR, commit, CI run, review, trigger identity, and deduplication keys. Event facts and Host-generated interpretation are stored as unverified evidence. Caller-supplied fields are not independently authenticated by this command; `event_hook_status: external_delivery_unverified` means delivery authenticity remains to be established by the integration.

Repeated delivery for the same repository, PR, and head returns the existing candidate. A conservative content fingerprint also suppresses a similar candidate for that repository. Open, failed, mismatched-head, and incomplete events create no candidate. Harvest errors do not affect a merge because this command is a separate consumer.

Candidates remain project observations. They cannot be reused by Composer unless evidence is independently verified, a named human accepts the review, and the Experience is explicitly registered in the Personal Profile or an authorized Capability edit. Approval alone does not verify evidence. Keep private source artifacts in their owning project; the privacy filter rejects transcripts, credentials, and bulk content.

## Automatic delivery

`.github/workflows/experience-capture.yml` invokes the consumer from both merged-PR closure and successful CI completion. This covers either event order: a CI event before merge is ignored, while the later merge event retries capture; a merge event before CI is ignored until the successful workflow event. Repeated triggers use the consumer's stable repository/PR/head key. The workflow opens a review PR containing the candidate; it never writes directly to `main` or promotes the candidate. Events from candidate PRs are excluded to prevent recursive captures.

GitHub's `closingIssuesReferences` supplies the real linked Issue; PR number is never substituted for Issue number. The workflow extracts a candidate from the Issue's `Problem`/`Context` section and PR's `Approach`, `Outcome`, and `Lesson` sections (heading aliases are accepted). It skips unlinked PRs and notes without all four sections instead of creating boilerplate. The Issue and PR URLs are retained in candidate provenance and evidence. Extracted lessons remain unverified and require human review before reuse. GitHub Actions provides delivery without polling or a background service.
