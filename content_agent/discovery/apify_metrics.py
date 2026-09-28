"""Optional, bounded metric enrichment for public Instagram Reels via Apify."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Iterable, Optional

import httpx

from content_agent.models.schemas import Candidate
from content_agent.discovery.metrics import parse_compact_number

APIFY_BASE = "https://api.apify.com/v2"
ACTOR_ID = "apify~instagram-scraper"


@dataclass(frozen=True)
class ApifyMetricsResult:
    enriched: int = 0
    cost_usd: Optional[float] = None
    run_id: str = ""


@dataclass(frozen=True)
class ApifyAccountResult:
    reels: list[dict]
    cost_usd: Optional[float] = None
    run_id: str = ""


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
        if isinstance(value, str) and re.fullmatch(r"\s*[0-9]+(?:[.,][0-9]+)?\s*[KMkm]?\s*", value):
            return parse_compact_number(value)
    return None


def _creator_username(item: dict) -> str:
    owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
    for value in (item.get("ownerUsername"), item.get("owner_username"), owner.get("username"), owner.get("userName")):
        if isinstance(value, str) and value.strip():
            return value.strip().lstrip("@")
    return ""


def _cost_usd(run: dict) -> Optional[float]:
    """Read a charge reported by Apify; never infer a price from item count."""
    for field in ("usageTotalUsd", "totalCostUsd", "chargedAmount"):
        value = run.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


async def enrich_reel_metrics(candidates: Iterable[Candidate], api_key: str, limit: int) -> ApifyMetricsResult:
    """Fill views/followers returned by one capped Apify run; never invent data.

    The actor is used only when the browser cannot expose public view counts.
    Callers must opt in through configuration because an Apify run can incur cost.
    """
    selected = [candidate for candidate in candidates if "/reel/" in candidate.source_url][:max(0, limit)]
    if not selected or not api_key:
        return ApifyMetricsResult()
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
        completed_run = run
        for _ in range(60):
            await asyncio.sleep(5)
            status_response = await client.get(f"{APIFY_BASE}/actor-runs/{run_id}", params={"token": api_key})
            completed_run = status_response.json()["data"]
            status = completed_run["status"]
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
        followers = _number(item, "ownerFollowersCount", "ownerFollowers", "ownerFollowersCount") or _number(owner, "followersCount", "followers", "follower_count", "followedByCount")
        if views is not None:
            candidate.views = views
            enriched += 1
        if followers is not None:
            candidate.followers = followers
        candidate.creator_username = _creator_username(item) or candidate.creator_username
        candidate.likes = candidate.likes if candidate.likes is not None else _number(item, "likesCount")
        candidate.comments = candidate.comments if candidate.comments is not None else _number(item, "commentsCount", "videoCommentCount")
    return ApifyMetricsResult(enriched=enriched, cost_usd=_cost_usd(completed_run), run_id=run_id)


async def fetch_account_reels(account: str, api_key: str, limit: int = 50) -> ApifyAccountResult:
    """Fetch observed own-account Reel metrics with the same bounded actor.

    This is a public-data fallback when the official Graph token is absent. It
    returns only fields actually present in the actor result and exposes the
    actor-reported cost rather than estimating it.
    """
    account = account.strip().lstrip("@")
    if not account or not api_key or limit <= 0:
        return ApifyAccountResult([])
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{APIFY_BASE}/acts/{ACTOR_ID}/runs",
            params={"token": api_key},
            json={"directUrls": [f"https://www.instagram.com/{account}/reels/"], "resultsType": "posts", "resultsLimit": limit},
        )
        response.raise_for_status()
        run = response.json()["data"]
        run_id, dataset_id = run["id"], run["defaultDatasetId"]
        completed_run = run
        for _ in range(60):
            await asyncio.sleep(5)
            status_response = await client.get(f"{APIFY_BASE}/actor-runs/{run_id}", params={"token": api_key})
            completed_run = status_response.json()["data"]
            status = completed_run["status"]
            if status == "SUCCEEDED":
                break
            if status in {"FAILED", "TIMED-OUT", "ABORTED"}:
                raise RuntimeError(f"Apify run {status}")
        else:
            raise RuntimeError("Apify account metrics timeout")
        result_response = await client.get(f"{APIFY_BASE}/datasets/{dataset_id}/items", params={"token": api_key, "format": "json"})
        result_response.raise_for_status()
        items = result_response.json()
    reels = []
    for item in items:
        if item.get("isVideo") is False:
            continue
        shortcode = _shortcode(item.get("url") or item.get("shortCode") or item.get("id") or "")
        if not shortcode:
            continue
        reels.append({
            "url": f"https://www.instagram.com/reel/{shortcode}/",
            "timestamp": item.get("timestamp") or item.get("takenAt") or "",
            "views": _number(item, "videoViewCount", "videoPlayCount", "playCount", "viewsCount"),
            "likes": _number(item, "likesCount"),
            "comments": _number(item, "commentsCount", "videoCommentCount"),
        })
    return ApifyAccountResult(reels=reels[:limit], cost_usd=_cost_usd(completed_run), run_id=run_id)
