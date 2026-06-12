"""
Synchronise les stats Instagram dans la base Notion 'Contenu TTR'.

Stratégie de matching (dans l'ordre) :
  1. Shortcode exact dans 'URL Reel' (peu probable — c'est l'URL source analysée)
  2. Proximité temporelle : post IG publié entre J-3 et J+30 de la Date de génération
     → Si un seul candidat : match certain
     → Si plusieurs : match sur le plus proche temporellement
"""
from datetime import datetime, timezone, timedelta
from typing import Optional

from notion_client import Client

from config import NOTION_API_KEY, NOTION_DATABASE_ID
from stats.instagram_stats import fetch_my_reels
from utils.logger import log_info, log_success, log_error

notion = Client(auth=NOTION_API_KEY)

_STAT_PROPS = {
    "Vues IG": "number",
    "Saves IG": "number",
    "Reach IG": "number",
    "Likes IG": "number",
    "Commentaires IG": "number",
    "Partages IG": "number",
}

_props_ready = False


def _ensure_stat_properties() -> None:
    global _props_ready
    if _props_ready:
        return
    try:
        db = notion.databases.retrieve(database_id=NOTION_DATABASE_ID)
        existing = db.get("properties", {})
        missing = {k: {v: {}} for k, v in _STAT_PROPS.items() if k not in existing}
        if missing:
            notion.databases.update(database_id=NOTION_DATABASE_ID, properties=missing)
            log_info(f"Propriétés stats créées : {list(missing.keys())}")
        _props_ready = True
    except Exception as e:
        log_error(f"Impossible de créer les propriétés stats : {e}")


def _fetch_all_notion_pages() -> list:
    pages = []
    cursor = None
    while True:
        kwargs: dict = {"database_id": NOTION_DATABASE_ID, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = notion.databases.query(**kwargs)
        pages.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return pages


def _url_reel_of(page: dict) -> str:
    prop = page.get("properties", {}).get("URL Reel", {})
    return prop.get("url") or ""


def _date_gen_of(page: dict) -> Optional[datetime]:
    # Préfère 'Date de génération' sinon created_time
    date_prop = page.get("properties", {}).get("Date de génération", {})
    start = (date_prop.get("date") or {}).get("start") or page.get("created_time", "")[:10]
    if not start:
        return None
    try:
        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _match(ig_post: dict, pages: list) -> Optional[dict]:
    sc = ig_post.get("shortcode")

    # 1. Shortcode dans URL Reel
    if sc:
        for page in pages:
            if sc in _url_reel_of(page):
                return page

    # 2. Proximité temporelle
    ts = ig_post.get("timestamp", "")[:10]
    if not ts:
        return None
    try:
        ig_dt = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    except Exception:
        return None

    candidates = []
    for page in pages:
        pg_dt = _date_gen_of(page)
        if pg_dt is None:
            continue
        delta = (ig_dt - pg_dt).days   # positif = IG publié après la génération
        if -3 <= delta <= 30:
            candidates.append((abs(delta), page))

    if not candidates:
        return None
    return sorted(candidates, key=lambda x: x[0])[0][1]


def _update_stats(page_id: str, post: dict) -> None:
    notion.pages.update(
        page_id=page_id,
        properties={
            "Vues IG":        {"number": post.get("views", 0) or 0},
            "Saves IG":       {"number": post.get("saves", 0) or 0},
            "Reach IG":       {"number": post.get("reach", 0) or 0},
            "Likes IG":       {"number": post.get("likes", 0) or 0},
            "Commentaires IG":{"number": post.get("comments", 0) or 0},
            "Partages IG":    {"number": post.get("shares", 0) or 0},
        },
    )


async def sync_instagram_stats() -> dict:
    """
    Synchronise les stats IG → Notion. Retourne un dict de résumé.
    """
    _ensure_stat_properties()

    log_info("Récupération des Reels depuis Instagram Graph API...")
    ig_posts = await fetch_my_reels(limit=50)

    log_info("Récupération des pages Notion Contenu TTR...")
    notion_pages = _fetch_all_notion_pages()

    updated = skipped = errors = 0
    for post in ig_posts:
        page = _match(post, notion_pages)
        if page is None:
            skipped += 1
            log_info(f"Pas de match Notion pour {post.get('url', '')}")
            continue
        try:
            _update_stats(page["id"], post)
            updated += 1
            log_success(f"Stats mises à jour → {post.get('url', '')} ({post['views']} vues)")
        except Exception as e:
            errors += 1
            log_error(f"Erreur update Notion : {e}")

    return {
        "ig_posts": len(ig_posts),
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }
