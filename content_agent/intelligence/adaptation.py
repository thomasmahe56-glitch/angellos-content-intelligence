from __future__ import annotations

from content_agent.intelligence.luna_client import LunaClient
from content_agent.models.schemas import Candidate

_FILMING = {"type": "object", "additionalProperties": False, "properties": {"format": {"type": "string"}, "camera": {"type": "string"}, "location": {"type": "string"}, "lighting": {"type": "string"}, "props": {"type": "array", "items": {"type": "string"}}, "screen_recordings_needed": {"type": "array", "items": {"type": "string"}}}, "required": ["format", "camera", "location", "lighting", "props", "screen_recordings_needed"]}
_EDITING = {"type": "object", "additionalProperties": False, "properties": {"pace": {"type": "string"}, "cuts": {"type": "string"}, "zoom": {"type": "string"}, "captions": {"type": "string"}, "music": {"type": "string"}, "sound_effects": {"type": "array", "items": {"type": "string"}}}, "required": ["pace", "cuts", "zoom", "captions", "music", "sound_effects"]}
_SOURCE = {"type": "object", "additionalProperties": False, "properties": {"url": {"type": "string"}, "creator": {"type": "string"}, "views": {"type": ["integer", "null"]}, "followers": {"type": ["integer", "null"]}, "viral_ratio": {"type": ["number", "null"]}}, "required": ["url", "creator", "views", "followers", "viral_ratio"]}
_ADAPTATION_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {"internal_title": {"type": "string"}, "source": _SOURCE, "why_selected": {"type": "string"}, "source_pattern": {"type": "string"}, "angellos_angle": {"type": "string"}, "content_goal": {"type": "string"}, "hook": {"type": "string"}, "script": {"type": "object", "additionalProperties": False, "properties": {"hook": {"type": "string"}, "development": {"type": "array", "items": {"type": "string"}}, "cta": {"type": "string"}}, "required": ["hook", "development", "cta"]}, "shot_list": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {"start": {"type": "number"}, "end": {"type": "number"}, "shot": {"type": "string"}, "spoken": {"type": "string"}, "on_screen_text": {"type": "string"}, "b_roll": {"type": "string"}, "editing": {"type": "string"}}, "required": ["start", "end", "shot", "spoken", "on_screen_text", "b_roll", "editing"]}}, "filming": _FILMING, "editing": _EDITING, "target_duration_seconds": {"type": "integer"}, "caption": {"type": "string"}, "cta": {"type": "string"}, "content_type": {"type": "string"}, "ig_content_type": {"type": "string"}, "why_test_this": {"type": "string"}, "facts_that_must_be_verified_before_filming": {"type": "array", "items": {"type": "string"}}}, "required": ["internal_title", "source", "why_selected", "source_pattern", "angellos_angle", "content_goal", "hook", "script", "shot_list", "filming", "editing", "target_duration_seconds", "caption", "cta", "content_type", "ig_content_type", "why_test_this", "facts_that_must_be_verified_before_filming"]}
_QUALITY_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {"approved": {"type": "boolean"}, "issues": {"type": "array", "items": {"type": "string"}}}, "required": ["approved", "issues"]}

# This is intentionally a low-risk CTA: it neither promises a download or a
# reply nor asserts an unverified beta capability.  Product-specific CTAs are
# allowed only when the canonical context explicitly substantiates them.
_SAFE_CTA = "Follow @angellos.ai for practical qualification systems."


async def adapt_to_angellos(client: LunaClient, candidate: Candidate, gemini: dict, context: str, recent_content: list[dict], historical_signals: dict, language: str) -> dict:
    prompt = f"""Create an original, production-ready Angellos Reel brief in {language}.
Source metadata: {candidate.to_dict()}
Gemini observations: {gemini}
Canonical context: {context}
Recent content: {recent_content[:20]}
Historical signals: {historical_signals}

Abstract the source mechanism; never translate/copy the source script, caption,
anecdote, visual identity, testimonial, feature, outcome, or statistic. Use
only facts explicitly present in Canonical context. Do not invent a lead magnet,
checklist, demo, beta access, integration, workflow, result, customer, screen
recording, booking link, sales page, or a human follow-up process. If a fact is
not established, omit it rather than placing it in the brief.

The founder must be able to film this now: target 20–30 seconds, keep all spoken
text (hook + development + CTA) to 65 English words or fewer, and use no product
UI/screen recording unless that exact screen is confirmed in Canonical context.
Use this exact low-risk CTA in both `script.cta` and `cta` unless Canonical
context explicitly proves a more specific CTA and its fulfilment path:
{_SAFE_CTA}
"""
    adaptation = await client.json("You are Luna. Create only factually grounded original marketing concepts that a founder can film today.", prompt, _ADAPTATION_SCHEMA)
    # Source evidence is deterministic input, not model-generated marketing copy.
    adaptation["source"] = {"url": candidate.source_url, "creator": candidate.creator_username, "views": candidate.views, "followers": candidate.followers, "viral_ratio": candidate.viral_ratio}
    return adaptation


async def quality_check(client: LunaClient, adaptation: dict, context: str, recent_content: list[dict]) -> dict:
    return await client.json(
        "You are Luna's final quality gate. Approve only if original, factually safe, non-repetitive, clear, filmable and CTA-coherent. "
        "The exact generic CTA 'Follow @angellos.ai for practical qualification systems.' is coherent and does not require a promised reply, lead magnet, or beta fulfilment. "
        "Do not reject an adaptation merely because it avoids unverified product details; approve it when it is useful and filmable without them.",
        f"Adaptation: {adaptation}\nContext: {context}\nRecent: {recent_content[:20]}",
        _QUALITY_SCHEMA,
    )
