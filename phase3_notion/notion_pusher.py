"""
Push generated Reel analysis + Angellos script to the Notion database.

Target DB schema (Angellos — Results DB):
  Name     — title
  Content  — rich_text   (hook + script summary)
  Type     — select      (uses "Content Topic Used")
  Account  — rich_text
  Date     — date
  Active   — checkbox

Full script, analysis, hashtags, and caption are written as page body blocks.
"""
import json
from datetime import date
from typing import Optional
from notion_client import Client
from config import NOTION_API_KEY, NOTION_DATABASE_ID
from utils.logger import log_info, log_success, log_error

notion = Client(auth=NOTION_API_KEY)


def push_to_notion(reel_data: dict, gemini_analysis: dict, claude_script: dict) -> Optional[str]:
    """Creates a page in the Notion database. Returns the page URL or None on failure."""
    log_info(f"Push Notion pour {reel_data['shortcode']}...")

    script = claude_script.get("script", {})
    indications = claude_script.get("indications_tournage", {})
    titre = claude_script.get(
        "titre_interne",
        f"Reel @{reel_data.get('account', 'unknown')} — {reel_data['shortcode']}",
    )
    hook = script.get("hook", gemini_analysis.get("hook", ""))
    cta = script.get("cta", gemini_analysis.get("cta", ""))
    content_summary = f"🎣 {hook}\n\n📣 {cta}"

    try:
        page = notion.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties={
                "Name": {
                    "title": [{"text": {"content": titre[:2000]}}]
                },
                "Content": {
                    "rich_text": [{"text": {"content": content_summary[:2000]}}]
                },
                "Type": {
                    "select": {"name": "Content Topic Used"}
                },
                "Account": {
                    "rich_text": [{"text": {"content": f"@{reel_data.get('account', '')}"}}]
                },
                "Date": {
                    "date": {"start": date.today().isoformat()}
                },
                "Active": {
                    "checkbox": True
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
