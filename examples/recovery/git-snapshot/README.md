# Git Snapshot and Safe Rollback Evidence

Disposable local Git fixtures verified the recovery contract. Each fixture started from a temporary repository; no consumer project or real global root was used.

- Temporary-index snapshot captured tracked dirty, deleted, and non-ignored untracked worktree bytes.
- The real index and `HEAD` remained unchanged; ignored `secret.tmp` was absent from the snapshot tree.
- Apply returned a local snapshot ref and deployment ID; the record lived under Git metadata.
- Rollback preview and apply restored only Bootstrap-touched paths, including global files and manifests.
- Project or global post-deployment edits blocked the complete rollback before any restore.
- Synthetic materializer failure left a `partial` deployment that could be rolled back.
- No-op and stale apply produced no snapshot/deployment.

See `apply-summary.json` and `rollback-summary.json` for sanitized outcome summaries without local paths or repository content.
