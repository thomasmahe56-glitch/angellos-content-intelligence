from __future__ import annotations

from content_agent.intelligence.luna_client import LunaClient
from content_agent.models.schemas import Candidate

_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {"decision": {"type": "string", "enum": ["keep", "reject"]}, "confidence": {"type": "number"}, "reason": {"type": "string"}, "likely_transferable_pattern": {"type": "string"}, "potential_angellos_angle": {"type": "string"}, "expected_content_category": {"type": "string"}}, "required": ["decision", "confidence", "reason", "likely_transferable_pattern", "potential_angellos_angle", "expected_content_category"]}


async def qualify_candidate(client: LunaClient, candidate: Candidate, context: str, recent_content: list[dict], historical_signals: dict) -> dict:
    return await client.json(
        "You are Luna, the Angellos marketing strategist. Filter for transferable creative mechanisms, never for literal copying. Reject celebrity-only, dance-only, non-replicable and irrelevant entertainment. Do not invent product facts.",
        f"Candidate: {candidate.to_dict()}\nCanonical Angellos context: {context}\nRecent content: {recent_content[:20]}\nHistorical signals (weak evidence, not rules): {historical_signals}",
        _SCHEMA,
    )
