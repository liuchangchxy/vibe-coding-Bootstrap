# Experience Event Capture

`vcbs harvest-event` accepts a normalized, caller-supplied event and a bounded Host observation. It creates one `candidate` Experience only when the Issue and PR are closed, the PR is merged, successful CI targets the PR head, repository URLs agree, and the local project checkout is clean at the exact merge commit.

```bash
vcbs harvest-event --project-root <project> --event <event.json> --observation <observation.json> --host <codex|claude|antigravity|none>
```

The event envelope is defined by [`schemas/experience-event.schema.json`](../schemas/experience-event.schema.json). Capture records the repository, Issue, PR, commit, CI run, review, trigger identity, and deduplication keys. Event facts and Host-generated interpretation are stored as unverified evidence. Caller-supplied fields are not independently authenticated by this command; `event_hook_status: external_delivery_unverified` means delivery authenticity remains to be established by the integration.

Repeated delivery for the same repository, PR, and head returns the existing candidate. A conservative content fingerprint also suppresses a similar candidate for that repository. Open, failed, mismatched-head, and incomplete events create no candidate. Harvest errors do not affect a merge because this command is a separate consumer.

Candidates remain project observations. They cannot be reused by Composer unless evidence is independently verified, a named human accepts the review, and the Experience is explicitly registered in the Personal Profile or an authorized Capability edit. Approval alone does not verify evidence. Keep private source artifacts in their owning project; the privacy filter rejects transcripts, credentials, and bulk content.

## Delivery status

The repository's pinned VCCP configuration (`.github/control-plane.yml`) handles PR opened, ready-for-review, and synchronize events for its Reviewer Task. It does not configure a successful post-merge delivery to this command. At the pinned VCCP contract revision, no generic check-completion/post-merge task trigger or native webhook receiver is available. Therefore this release provides a callable event-consumer boundary and a manual/Host invocation path; automatic delivery remains unavailable until VCCP exposes a supported extension point. It uses no polling, daemon, paid service, or background monitor.
