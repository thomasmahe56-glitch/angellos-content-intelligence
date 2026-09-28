from __future__ import annotations

from content_agent.models.schemas import Candidate


def is_duplicate(candidate: Candidate, state: dict, *, retry_insufficient_metrics: bool = False) -> bool:
    records = state.get("reels", {})
    record = records.get(candidate.key)
    if record:
        return not (retry_insufficient_metrics and record.get("status") == "insufficient_metrics")
    url = candidate.source_url.rstrip("/")
    matching = next((r for r in records.values() if (r.get("source_url") or "").rstrip("/") == url), None)
    return bool(matching and not (retry_insufficient_metrics and matching.get("status") == "insufficient_metrics"))
