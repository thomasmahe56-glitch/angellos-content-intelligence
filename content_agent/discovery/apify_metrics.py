"""Optional, bounded metric enrichment for public Instagram Reels via Apify."""
from __future__ import annotations

import asyncio
import re
from typing import Iterable

import httpx

from content_agent.models.schemas import Candidate

APIFY_BASE = "https://api.apify.com/v2"
ACTOR_ID = "apify~instagram-scraper"


def _shortcode(value: str) -> str:
    match = re.search(r"/(?:reel|p)/([A-Za-z0-9_-]+)", value or "")
    if match:
        return match.group(1)
    return value if re.fullmatch(r"[A-Za-z0-9_-]+", value or "") else ""


def _number(item: dict, *names: str):
    for name in names:
        value = item.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return None


async def enrich_reel_metrics(candidates: Iterable[Candidate], api_key: str, limit: int) -> int:
    """Fill views/followers returned by one capped Apify run; never invent data.

    The actor is used only when the browser cannot expose public view counts.
    Callers must opt in through configuration because an Apify run can incur cost.
    """
    selected = [candidate for candidate in candidates if "/reel/" in candidate.source_url][:max(0, limit)]
    if not selected or not api_key:
        return 0
    wanted = {candidate.shortcode: candidate for candidate in selected}
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{APIFY_BASE}/acts/{ACTOR_ID}/runs",
            params={"token": api_key},
            json={"directUrls": [candidate.source_url for candidate in selected], "resultsType": "posts", "resultsLimit": len(selected)},
        )
        response.raise_for_status()
        run = response.json()["data"]
        run_id, dataset_id = run["id"], run["defaultDatasetId"]
        for _ in range(60):
            await asyncio.sleep(5)
            status_response = await client.get(f"{APIFY_BASE}/actor-runs/{run_id}", params={"token": api_key})
            status = status_response.json()["data"]["status"]
            if status == "SUCCEEDED":
                break
            if status in {"FAILED", "TIMED-OUT", "ABORTED"}:
                raise RuntimeError(f"Apify run {status}")
        else:
            raise RuntimeError("Apify metrics timeout")
        result_response = await client.get(f"{APIFY_BASE}/datasets/{dataset_id}/items", params={"token": api_key, "format": "json"})
        result_response.raise_for_status()
        items = result_response.json()
    enriched = 0
    for item in items:
        candidate = wanted.get(_shortcode(item.get("url") or item.get("shortCode") or item.get("id") or ""))
        if not candidate:
            continue
        views = _number(item, "videoViewCount", "videoPlayCount", "playCount", "viewsCount")
        owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
        followers = _number(item, "ownerFollowersCount", "ownerFollowers") or _number(owner, "followersCount", "followers")
        if views is not None:
            candidate.views = views
            enriched += 1
        if followers is not None:
            candidate.followers = followers
        candidate.likes = candidate.likes if candidate.likes is not None else _number(item, "likesCount")
        candidate.comments = candidate.comments if candidate.comments is not None else _number(item, "commentsCount", "videoCommentCount")
    return enriched
