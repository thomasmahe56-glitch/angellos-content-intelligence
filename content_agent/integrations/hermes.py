"""Hermes-safe contract for one bounded Content Intelligence run."""
from __future__ import annotations

from typing import Any


def event_from_report(report: dict[str, Any]) -> dict[str, Any]:
    """Convert the scout report into a compact, secret-free Hermes event.

    Hermes owns scheduling and notification policy.  This module deliberately
    has no dependency on a particular Hermes transport or job runner.
    """
    status = report.get("status", "partial_failure")
    needs_attention = status in {"configuration_error", "partial_failure"} or bool(report.get("human_action_required"))
    action = None
    if report.get("human_action_required"):
        action = {"owner": "human", "action": "refresh_instagram_session", "reason": report.get("reason", "instagram_authentication_challenge")}
    elif status == "configuration_error":
        action = {"owner": "operator", "action": "repair_content_agent_configuration"}
    elif status == "partial_failure":
        action = {"owner": "operator", "action": "review_content_agent_errors"}
    return {
        "event_type": "angellos.content_intelligence.run",
        "status": status,
        "requires_attention": needs_attention,
        "next_action": action,
        "summary": {
            key: report.get(key, 0)
            for key in (
                "scanned", "new_reels", "duplicates", "viral_candidates",
                "relevance_qualified", "videos_analyzed", "content_ideas_created",
                "rejected_ratio", "rejected_relevance", "failed",
            )
        },
        "created_items": report.get("created_items", []),
        "errors": report.get("errors", []),
    }
