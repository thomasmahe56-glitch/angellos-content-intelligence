from __future__ import annotations

from content_agent.models.schemas import Candidate


def is_duplicate(candidate: Candidate, state: dict) -> bool:
    records = state.get("reels", {})
    if candidate.key in records:
        return True
    url = candidate.source_url.rstrip("/")
    return any((r.get("source_url") or "").rstrip("/") == url for r in records.values())
