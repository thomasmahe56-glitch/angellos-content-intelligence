from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from content_agent.intelligence.luna_client import LunaClient

_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "successful_hook_patterns": {"type": "array", "items": {"type": "string"}},
        "successful_formats": {"type": "array", "items": {"type": "string"}},
        "successful_topics": {"type": "array", "items": {"type": "string"}},
        "weak_patterns": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["successful_hook_patterns", "successful_formats", "successful_topics", "weak_patterns", "notes"],
}


async def analyze_performance_signals(client: LunaClient, rows: list[dict]) -> dict:
    """Generate hypotheses, never mandates, from actual published-content metrics."""
    result = await client.json(
        "You are Luna. Analyze only supplied Angellos performance data. Treat correlations as weak historical signals, not causal facts or mandatory rules. Never invent missing metrics.",
        f"Published-content rows: {rows}",
        _SCHEMA,
    )
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "total_reels": len(rows), "historical_signals": result, "patterns": {"hooks_gagnants": result["successful_hook_patterns"], "formats_gagnants": result["successful_formats"], "sujets_performants": result["successful_topics"], "patterns_faibles": result["weak_patterns"]}, "insights": result["notes"]}


def save_patterns(data: dict, destination: Path = Path("performance_patterns.json")) -> None:
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temporary.replace(destination)
