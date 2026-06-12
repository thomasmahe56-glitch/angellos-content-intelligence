"""
Scraping des Reels Instagram via Apify (apify/instagram-scraper).
Retourne le top N reels d'un compte trié par vues décroissantes.
"""
import asyncio
from datetime import datetime
from typing import Optional

import httpx

APIFY_BASE = "https://api.apify.com/v2"
ACTOR_ID = "apify~instagram-scraper"


def _normalize_url(item: dict) -> str:
    sc = item.get("shortCode") or item.get("id", "")
    if sc:
        return f"https://www.instagram.com/reel/{sc}/"
    return item.get("url", "")


def _fmt_date(ts: Optional[str]) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        months = ["jan", "fév", "mar", "avr", "mai", "juin",
                  "juil", "août", "sep", "oct", "nov", "déc"]
        return f"{dt.day} {months[dt.month - 1]} {dt.year}"
    except Exception:
        return ts[:10]


async def scrape_account_reels(account: str, api_key: str, top: int = 20) -> list:
    """
    Lance un run Apify Instagram Scraper pour le compte donné.
    Attend la complétion (polling), récupère les items, filtre les vidéos,
    retourne le top N (non trié — le tri est fait côté frontend).
    """
    profile_url = f"https://www.instagram.com/{account}/reels/"

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{APIFY_BASE}/acts/{ACTOR_ID}/runs",
            params={"token": api_key},
            json={
                "directUrls": [profile_url],
                "resultsType": "posts",
                "resultsLimit": 60,
            },
        )
        resp.raise_for_status()
        run = resp.json()["data"]
        run_id = run["id"]
        dataset_id = run["defaultDatasetId"]

    # Poll jusqu'à SUCCEEDED (max 5 min, tick 5 s)
    async with httpx.AsyncClient(timeout=30) as client:
        for _ in range(60):
            await asyncio.sleep(5)
            r = await client.get(
                f"{APIFY_BASE}/actor-runs/{run_id}",
                params={"token": api_key},
            )
            status = r.json()["data"]["status"]
            if status == "SUCCEEDED":
                break
            if status in ("FAILED", "TIMED-OUT", "ABORTED"):
                raise RuntimeError(f"Apify run {status} pour @{account}")
        else:
            raise RuntimeError("Timeout Apify (5 min dépassées)")

    # Récupère les items du dataset
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{APIFY_BASE}/datasets/{dataset_id}/items",
            params={"token": api_key, "format": "json"},
        )
        items = r.json()

    # Filtre les vidéos (Reels = isVideo ou type Video)
    reels = []
    for item in items:
        is_video = item.get("isVideo") or item.get("type") in ("Video", "video")
        if not is_video:
            continue
        caption_raw = item.get("caption") or item.get("text") or ""
        raw_ts = item.get("timestamp") or ""
        reels.append({
            "url": _normalize_url(item),
            "thumbnail": item.get("displayUrl") or item.get("thumbnailUrl") or "",
            "views": item.get("videoViewCount") or item.get("videoPlayCount") or 0,
            "comments": item.get("commentsCount") or item.get("videoCommentCount") or 0,
            "likes": item.get("likesCount") or 0,
            "date": _fmt_date(raw_ts),
            "timestamp_raw": raw_ts[:10],
            "caption": caption_raw[:300],
            "account": item.get("ownerUsername") or account,
        })

    # Pas de tri ici — le frontend trie par l'onglet actif
    return reels[:top]
