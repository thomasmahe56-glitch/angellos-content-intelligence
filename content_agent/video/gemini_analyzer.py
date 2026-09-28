from __future__ import annotations

import asyncio
from typing import Any


class GeminiAnalysisError(RuntimeError):
    pass


def parse_gemini_json(data: Any) -> dict:
    if not isinstance(data, dict) or data.get("raw_response"):
        raise GeminiAnalysisError("Gemini returned invalid JSON")
    # The legacy analyzer is retained, but V2 presents a stable observation contract.
    hook = data.get("hook", "")
    return {
        "duration_seconds": _duration(data.get("duree_estimee")),
        "transcript": data.get("transcript", ""),
        "hook": {"spoken": hook, "on_screen_text": "", "visual": "", "start_seconds": 0, "end_seconds": 3},
        "timeline": data.get("timeline", []),
        "narrative_structure": data.get("narrative_structure", data.get("structure_narrative", "")),
        "format": data.get("format", ""),
        "camera_style": data.get("camera_style", ""),
        "cuts": data.get("cuts", {"estimated_count": None, "average_frequency_seconds": None}),
        "b_roll": data.get("b_roll", []), "screen_recordings": data.get("screen_recordings", []),
        "pattern_interrupts": data.get("pattern_interrupts", []), "music": data.get("musique", ""),
        "sound_effects": data.get("sound_effects", []),
        "subtitles": {"present": bool(data.get("sous_titres", False)), "style": ""},
        "cta": data.get("cta", ""), "payoff": data.get("payoff", ""),
        "retention_mechanisms": data.get("retention_mechanisms", data.get("points_forts", [])),
        "emotion_or_tension": data.get("emotion_or_tension", ""),
        "replicable_creative_pattern": data.get("pattern_replicable", ""),
        "notable_details": data.get("elements_visuels_cles", []),
        "_gemini_model_used": data.get("_gemini_model_used", ""),
        "_gemini_usage": data.get("_gemini_usage", {}),
    }


async def analyze_video(local_path: str, caption: str = "") -> dict:
    try:
        from phase2_analysis.gemini_analyzer import analyze_reel
        raw = await asyncio.to_thread(analyze_reel, local_path, caption)
        return parse_gemini_json(raw)
    except Exception as exc:
        raise GeminiAnalysisError(str(exc)) from exc


def _duration(value: object):
    try:
        return int(str(value).split()[0])
    except (ValueError, IndexError):
        return None
