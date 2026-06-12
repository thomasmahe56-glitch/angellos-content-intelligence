import json
from datetime import date
from typing import Optional
from notion_client import Client
from config import NOTION_API_KEY, NOTION_DATABASE_ID
from utils.logger import log_info, log_success, log_error

notion = Client(auth=NOTION_API_KEY)

_DATE_PROP = "Date de génération"
_date_prop_ready = False


def _ensure_date_property() -> None:
    """Crée la propriété 'Date de génération' (type date) dans la DB si absente."""
    global _date_prop_ready
    if _date_prop_ready:
        return
    try:
        db = notion.databases.retrieve(database_id=NOTION_DATABASE_ID)
        if _DATE_PROP not in db.get("properties", {}):
            notion.databases.update(
                database_id=NOTION_DATABASE_ID,
                properties={_DATE_PROP: {"date": {}}},
            )
            log_info(f"Propriété '{_DATE_PROP}' ajoutée à la base Notion")
        _date_prop_ready = True
    except Exception as e:
        log_error(f"Impossible de vérifier/créer la propriété date : {e}")


def push_to_notion(reel_data: dict, gemini_analysis: dict, claude_script: dict) -> Optional[str]:
    """
    Creates a page in the Notion "Contenu Angellos" database.
    Retourne l'URL de la page créée.

    Structure attendue de la DB Notion :
    - Titre (title) : titre interne du script
    - Compte source (rich_text)
    - URL Reel (url)
    - Statut (select) : "À tourner"
    - Hook (rich_text)
    - Format (select)
    - Durée cible (number)
    - Analyse Gemini (rich_text) — JSON complet
    - Script Claude (rich_text) — JSON complet
    """
    _ensure_date_property()
    log_info(f"Push Notion pour {reel_data['shortcode']}...")

    titre = claude_script.get("titre_interne", f"Reel @{reel_data['account']} - {reel_data['shortcode']}")
    script = claude_script.get("script", {})
    indications = claude_script.get("indications_tournage", {})
    hook_text = f"{gemini_analysis.get('hook', '')} [{gemini_analysis.get('hook_type', '')}]"
    script_summary = f"Hook: {script.get('hook', '')}\nCTA: {script.get('cta', '')}"

    try:
        page = notion.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties={
                "Titre": {
                    "title": [{"text": {"content": titre}}]
                },
                "Compte source": {
                    "rich_text": [{"text": {"content": f"@{reel_data['account']}"}}]
                },
                "URL Reel": {
                    "url": reel_data["url"]
                },
                "Statut": {
                    "select": {"name": "À tourner"}
                },
                "Hook analysé": {
                    "rich_text": [{"text": {"content": hook_text[:2000]}}]
                },
                "Script Angellos": {
                    "rich_text": [{"text": {"content": script_summary[:2000]}}]
                },
                _DATE_PROP: {
                    "date": {"start": date.today().isoformat()}
                },
            },
            children=[
                _heading("Analyse Gemini"),
                *_code_blocks(json.dumps(gemini_analysis, ensure_ascii=False, indent=2)),
                _divider(),
                _heading("Script Angellos (Claude)"),
                _callout(f"🎣 Hook: {script.get('hook', '')}"),
                *[_bullet(step) for step in script.get("developpement", [])],
                _callout(f"📣 CTA: {script.get('cta', '')}"),
                _divider(),
                _heading("Indications tournage"),
                _bullet(f"Format caméra : {indications.get('format_camera', '')}"),
                _bullet(f"Décor : {indications.get('décor_recommandé', '')}"),
                _bullet(f"Durée cible : {indications.get('durée_cible', '')}s"),
                _bullet(f"Rythme : {indications.get('rythme', '')}"),
                _divider(),
                _heading("Hashtags"),
                _paragraph(" ".join(claude_script.get("hashtags_suggeres", []))),
                _heading("Pourquoi ça marche"),
                _paragraph(claude_script.get("pourquoi_ca_marche", "")),
                *([
                    _divider(),
                    _heading("Caption originale"),
                    _paragraph(gemini_analysis.get("caption_originale", "")),
                ] if gemini_analysis.get("caption_originale") else []),
                *([
                    _divider(),
                    _heading("Caption Angellos"),
                    _callout(claude_script.get("caption_angellos", "")),
                ] if claude_script.get("caption_angellos") else []),
            ],
        )
        page_url = page["url"]
        log_success(f"Page Notion créée : {page_url}")
        return page_url

    except Exception as e:
        log_error(f"Erreur Notion : {e}")
        return None


# --- Helpers blocs Notion ---

def _heading(text: str, level: int = 2) -> dict:
    return {
        "object": "block",
        "type": f"heading_{level}",
        f"heading_{level}": {"rich_text": [{"text": {"content": text}}]},
    }

def _paragraph(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"text": {"content": text[:2000]}}]},
    }

def _bullet(text: str) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [{"text": {"content": text[:2000]}}]},
    }

def _callout(text: str) -> dict:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [{"text": {"content": text[:2000]}}],
            "icon": {"emoji": "💡"},
        },
    }

def _code_blocks(content: str) -> list:
    """Découpe un long JSON en plusieurs blocs code de 2000 chars max."""
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
