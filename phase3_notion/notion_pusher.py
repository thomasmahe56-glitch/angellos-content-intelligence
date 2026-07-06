"""
Push generated Reel analysis + Angellos script to the Programme Content Notion database.

Target DB: "All Content" (Programme Content page)
Schema:
  Name             — title
  Platform         — select    → "Instagram"
  IG Content Type  — select    → determined by Claude
  IG Status        — select    → "Idea"
  Content Type     — select    → determined by Claude
  Date of Publish  — date      → today

Full script, analysis, and metadata are written as page body blocks.
"""
import json
from datetime import date
from typing import Optional
from notion_client import Client
from config import NOTION_API_KEY, NOTION_PROGRAMME_CONTENT_DB
from utils.logger import log_info, log_success, log_error

notion = Client(auth=NOTION_API_KEY)

# ── IG Content Type mapping (fallback if Claude doesn't suggest one) ──────────

_GEMINI_FORMAT_TO_IG_TYPE = {
    "tutorial": "Step by step",
    "before-after": "Histoire",
    "testimonial": "Histoire",
    "talking head": "Tips",
    "voiceover": "Tips",
    "voiceover + text": "Tips",
    "talking head + text": "Tips",
}

_DEFAULT_IG_CONTENT_TYPE = "Tips"
_DEFAULT_CONTENT_TYPE = "Hooks"


def _map_ig_content_type(format_str: str | None, claude_suggested: str | None) -> str:
    if claude_suggested and claude_suggested in (
        "Histoire", "Carousel", "Liste", "Tips", "Mythe",
        "Erreur commune", "Step by step", "Citation", "Humour",
        "Exercices", "offre",
    ):
        return claude_suggested
    if format_str:
        for key, val in _GEMINI_FORMAT_TO_IG_TYPE.items():
            if key in format_str.lower():
                return val
    return _DEFAULT_IG_CONTENT_TYPE


def _map_content_type(claude_suggested: str | None) -> str:
    allowed = (
        "Promotion", "Hooks", "Personnal Branding", "Preuve Social",
        "Objections", "Croyances Limitantes", "Mythes et Croyances",
        "Conseils Pratiques", "Inspiration", "Erreurs", "Présentation",
    )
    if claude_suggested and claude_suggested in allowed:
        return claude_suggested
    return _DEFAULT_CONTENT_TYPE


def push_to_notion(
    reel_data: dict,
    gemini_analysis: dict,
    claude_script: dict,
) -> Optional[str]:
    """Creates a page in the Programme Content Notion database.
    Returns the page URL or None on failure.
    """
    db_id = NOTION_PROGRAMME_CONTENT_DB
    if not db_id:
        log_error("NOTION_PROGRAMME_CONTENT_DB is not set — skipping Notion push")
        return None

    log_info(f"Push Notion (Programme Content) pour {reel_data['shortcode']}...")

    script = claude_script.get("script", {})
    indications = claude_script.get("indications_tournage", {})
    titre = claude_script.get(
        "titre_interne",
        f"Reel @{reel_data.get('account', 'unknown')} — {reel_data['shortcode']}",
    )
    hook = script.get("hook", gemini_analysis.get("hook", ""))
    cta = script.get("cta", gemini_analysis.get("cta", ""))

    # Determine content types
    gemini_format = gemini_analysis.get("format", "")
    ig_content_type = _map_ig_content_type(
        gemini_format,
        claude_script.get("ig_content_type"),
    )
    content_type = _map_content_type(claude_script.get("content_type"))

    try:
        page = notion.pages.create(
            parent={"database_id": db_id},
            properties={
                "Name": {
                    "title": [{"text": {"content": titre[:2000]}}],
                },
                "Platform": {
                    "select": {"name": "Instagram"},
                },
                "IG Content Type": {
                    "select": {"name": ig_content_type},
                },
                "IG Status": {
                    "status": {"name": "Idea"},
                },
                "Content Type": {
                    "select": {"name": content_type},
                },
                "Date of Publish": {
                    "date": {"start": date.today().isoformat()},
                },
            },
            children=_build_body(reel_data, gemini_analysis, claude_script),
        )
        page_url = page["url"]
        log_success(f"Page Notion créée : {page_url}")
        return page_url

    except Exception as e:
        log_error(f"Erreur Notion : {e}")
        return None


def _build_body(reel_data: dict, gemini_analysis: dict, claude_script: dict) -> list:
    script = claude_script.get("script", {})
    indications = claude_script.get("indications_tournage", {})
    blocks = []

    # ── Source Reel ──────────────────────────────────────────────────────────
    blocks += [
        _heading("Source Reel", level=2),
        _paragraph(f"URL: {reel_data.get('url', '')}"),
        _paragraph(f"Compte: @{reel_data.get('account', '')}"),
    ]
    if gemini_analysis.get("caption_originale"):
        blocks += [
            _heading("Caption originale", level=3),
            _paragraph(gemini_analysis["caption_originale"]),
        ]

    blocks.append(_divider())

    # ── Script Angellos ───────────────────────────────────────────────────────
    blocks += [_heading("Script Angellos (Claude)", level=2)]
    if script.get("hook"):
        blocks.append(_callout(f"🎣 Hook (0–3s)\n{script['hook']}"))
    for step in script.get("developpement", []):
        blocks.append(_bullet(step))
    if script.get("cta"):
        blocks.append(_callout(f"📣 CTA\n{script['cta']}"))

    blocks.append(_divider())

    # ── Indications tournage ──────────────────────────────────────────────────
    if indications:
        blocks += [
            _heading("Indications tournage", level=2),
            _bullet(f"Format caméra : {indications.get('format_camera', '')}"),
            _bullet(f"Décor : {indications.get('décor_recommandé', '')}"),
            _bullet(f"Durée cible : {indications.get('durée_cible', '')}s"),
            _bullet(f"Rythme : {indications.get('rythme', '')}"),
        ]
        blocks.append(_divider())

    # ── Caption Angellos ──────────────────────────────────────────────────────
    if claude_script.get("caption_angellos"):
        blocks += [
            _heading("Caption Angellos", level=2),
            _callout(claude_script["caption_angellos"]),
            _divider(),
        ]

    # ── Hashtags + Pourquoi ───────────────────────────────────────────────────
    hashtags = claude_script.get("hashtags_suggeres", [])
    if hashtags:
        blocks += [
            _heading("Hashtags", level=3),
            _paragraph(" ".join(f"#{h.lstrip('#')}" for h in hashtags)),
        ]
    if claude_script.get("pourquoi_ca_marche"):
        blocks += [
            _heading("Pourquoi ça marche", level=3),
            _paragraph(claude_script["pourquoi_ca_marche"]),
        ]

    blocks.append(_divider())

    # ── Analyse Gemini (JSON) ─────────────────────────────────────────────────
    blocks += [_heading("Analyse Gemini", level=2)]
    blocks += _code_blocks(json.dumps(gemini_analysis, ensure_ascii=False, indent=2))

    return blocks


# ── Block helpers ─────────────────────────────────────────────────────────────

def _heading(text: str, level: int = 2) -> dict:
    t = f"heading_{level}"
    return {"object": "block", "type": t, t: {"rich_text": [{"text": {"content": text}}]}}


def _paragraph(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"text": {"content": (text or "")[:2000]}}]},
    }


def _bullet(text: str) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [{"text": {"content": (text or "")[:2000]}}]},
    }


def _callout(text: str) -> dict:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [{"text": {"content": (text or "")[:2000]}}],
            "icon": {"emoji": "💡"},
        },
    }


def _code_blocks(content: str) -> list:
    chunks = [content[i:i+2000] for i in range(0, len(content), 2000)]
    return [
        {
            "object": "block",
            "type": "code",
            "code": {
                "rich_text": [{"text": {"content": chunk}}],
                "language": "json",
            },
        }
        for chunk in chunks
    ]


def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}
