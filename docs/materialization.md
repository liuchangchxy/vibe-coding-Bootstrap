# Materialization

Assembly decides which components belong in a proposed environment. Materialization copies eligible local resources from a confirmed Assembly Plan into explicit managed roots.

Materialization means files exist. Activation means Codex, Claude, an IDE, or an MCP runtime loads and uses them. Phase 6 does not claim activation.

Project copies go below `.vibe-coding-bootstrap/` in the chosen project root. Global copies require an explicit `--global-root`. External components remain references. Phase 6 never downloads or installs from the network.

The CLI previews by default. Use `--apply` to write. Existing different files and unknown manifests are reported as conflicts and are not overwritten. Reapplying identical content is idempotent. A small `manifest.yaml` records source capability, version, scope, destination, and content hash.

Run `python -m materializer assembly-plan.yaml --project-root <path>` to preview. Add `--global-root <path>` for Global copies and `--apply` to materialize.

Native host adapters, MCP or Skill installation, and update or sync belong to later work.
