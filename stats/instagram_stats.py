"""
Récupère les stats des Reels TTR publiés via l'Instagram Graph API.
Requiert un compte Creator ou Business avec un token d'accès valide.

Permissions requises sur le token :
  instagram_basic, instagram_manage_insights, pages_show_list
"""
import re
from typing import Optional

import httpx

from config import INSTAGRAM_ACCESS_TOKEN, INSTAGRAM_ACCOUNT_ID
from utils.logger import log_info, log_success, log_error

IG_BASE = "https://graph.instagram.com/v21.0"
_MEDIA_FIELDS = "id,caption,media_type,timestamp,permalink,like_count,comments_count"
# Essaye plusieurs combinaisons de métriques — certaines varient selon le type de post
_METRICS_REEL = "plays,reach,saved,shares,total_interactions"
_METRICS_FALLBACK = "video_views,reach,saved,impressions"


async def fetch_my_reels(limit: int = 50) -> list:
    """
    Récupère tous les Reels du compte Instagram authentifié avec leurs stats.
    Retourne une liste de dicts avec vues, saves, reach, likes, commentaires.
    """
    if not INSTAGRAM_ACCESS_TOKEN:
        raise ValueError(
            "INSTAGRAM_ACCESS_TOKEN manquant dans .env — "
            "génère un token depuis developers.facebook.com"
        )

    user_node = INSTAGRAM_ACCOUNT_ID if INSTAGRAM_ACCOUNT_ID and INSTAGRAM_ACCOUNT_ID != "me" else "me"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{IG_BASE}/{user_node}/media",
            params={
                "fields": _MEDIA_FIELDS,
                "limit": limit,
                "access_token": INSTAGRAM_ACCESS_TOKEN,
            },
        )
        if resp.status_code >= 400 and user_node != "me":
            # Fallback sur /me/media
            resp = await client.get(
                f"{IG_BASE}/me/media",
                params={
                    "fields": _MEDIA_FIELDS,
                    "limit": limit,
                    "access_token": INSTAGRAM_ACCESS_TOKEN,
                },
            )
        resp.raise_for_status()
        media_list = resp.json().get("data", [])

    posts = []
    for media in media_list:
        if media.get("media_type") not in ("VIDEO", "REEL"):
            continue
        insights = await _fetch_insights(media["id"])
        posts.append({
            "ig_id": media["id"],
            "url": media.get("permalink", ""),
            "shortcode": _shortcode(media.get("permalink", "")),
            "caption": (media.get("caption") or "")[:300],
            "timestamp": media.get("timestamp", ""),
            "date": media.get("timestamp", "")[:10],
            "likes": media.get("like_count") or 0,
            "comments": media.get("comments_count") or 0,
            "views": insights.get("plays") or insights.get("video_views") or 0,
            "reach": insights.get("reach") or 0,
            "saves": insights.get("saved") or insights.get("saves") or 0,
            "shares": insights.get("shares") or 0,
        })

    log_success(f"{len(posts)} Reels TTR récupérés depuis Instagram Graph API")
    return posts


async def _fetch_insights(media_id: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        for metrics in (_METRICS_REEL, _METRICS_FALLBACK):
            try:
                r = await client.get(
                    f"{IG_BASE}/{media_id}/insights",
                    params={
                        "metric": metrics,
                        "access_token": INSTAGRAM_ACCESS_TOKEN,
                    },
                )
                if r.status_code == 200:
                    data = r.json().get("data", [])
                    return {
                        item["name"]: (item.get("values") or [{}])[0].get("value", 0)
                        for item in data
                    }
            except Exception:
                pass
    return {}


def _shortcode(url: str) -> Optional[str]:
    m = re.search(r"/(p|reel)/([A-Za-z0-9_-]+)", url)
    return m.group(2) if m else None
