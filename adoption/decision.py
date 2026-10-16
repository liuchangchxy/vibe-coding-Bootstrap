"""Explicit user decisions bound to one exact Adoption Plan."""

from datetime import datetime, timezone

from composer.core import ROOT, validate
from .planning import plan_digest, validate_adoption_plan


def build_decision(plan, decision, root=ROOT):
    """Build an approve/reject artifact after validating and rehashing its Plan."""
    validate_adoption_plan(plan, root)
    if decision not in ("approve", "reject"):
        raise ValueError("Decision must be approve or reject")
    digest = plan_digest(plan)
    if plan["plan_digest"] != digest:
        raise ValueError("Adoption Plan digest mismatch")
    artifact = {
        "format_version": 1,
        "plan_digest": digest,
        "target_commit_sha": plan["target"]["commit_sha"],
        "decision": decision,
        "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    validate(artifact, "adoption-decision.schema.json", root)
    return artifact


def validate_decision(decision_artifact, plan, root=ROOT):
    """Validate an explicit decision against a freshly validated exact Plan."""
    validate_adoption_plan(plan, root)
    validate(decision_artifact, "adoption-decision.schema.json", root)
    digest = plan_digest(plan)
    if plan["plan_digest"] != digest:
        raise ValueError("Adoption Plan digest mismatch")
    if decision_artifact["plan_digest"] != digest:
        raise ValueError("Decision plan_digest does not match the validated Adoption Plan")
    if decision_artifact["target_commit_sha"] != plan["target"]["commit_sha"]:
        raise ValueError("Decision target_commit_sha does not match the Adoption Plan")
    return decision_artifact
