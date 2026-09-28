"""Transparent, creator-relative Reel outlier scoring."""
from __future__ import annotations

from datetime import date, datetime, timezone
from statistics import median
from typing import Optional

from content_agent.models.schemas import Candidate


def _median(values: list[float]) -> Optional[float]:
    usable = [value for value in values if value is not None and value > 0]
    return float(median(usable)) if usable else None


def _age_hours(value: str, now: Optional[datetime] = None) -> Optional[float]:
    if not value:
        return None
    try:
        published = datetime.combine(date.fromisoformat(value[:10]), datetime.min.time(), tzinfo=timezone.utc)
    except ValueError:
        return None
    hours = ((now or datetime.now(timezone.utc)) - published).total_seconds() / 3600
    return hours if hours > 0 else None


def build_baseline(reels: list[dict]) -> dict:
    """Compact per-creator medians from observed, non-candidate Reels."""
    views = [reel.get("views") for reel in reels]
    like_rates = [reel["likes"] / reel["views"] for reel in reels if reel.get("likes") is not None and reel.get("views")]
    comment_rates = [reel["comments"] / reel["views"] for reel in reels if reel.get("comments") is not None and reel.get("views")]
    velocities = []
    for reel in reels:
        age = _age_hours(reel.get("source_published_at", ""))
        if reel.get("views") and age:
            velocities.append(reel["views"] / age)
    return {
        "sample_size": len([value for value in views if value]),
        "median_views": _median(views),
        "median_like_rate": _median(like_rates),
        "median_comment_rate": _median(comment_rates),
        "median_velocity": _median(velocities),
    }


def score_candidate(candidate: Candidate, baseline: dict, now: Optional[datetime] = None) -> dict:
    """Return inspectable components; no hidden learned or arbitrary model score."""
    views = candidate.views or 0
    view_ratio = views / baseline["median_views"] if views and baseline.get("median_views") else None
    age = _age_hours(candidate.source_published_at, now)
    velocity_ratio = (views / age) / baseline["median_velocity"] if views and age and baseline.get("median_velocity") else None
    like_rate = candidate.likes / views if candidate.likes is not None and views else None
    comment_rate = candidate.comments / views if candidate.comments is not None and views else None
    like_ratio = like_rate / baseline["median_like_rate"] if like_rate is not None and baseline.get("median_like_rate") else None
    comment_ratio = comment_rate / baseline["median_comment_rate"] if comment_rate is not None and baseline.get("median_comment_rate") else None
    follower_ratio = candidate.viral_ratio
    score = 0.0
    if view_ratio is not None:
        score += min(40.0, max(0.0, (view_ratio - 1.0) * 40.0))
    if velocity_ratio is not None:
        score += min(20.0, max(0.0, (velocity_ratio - 1.0) * 20.0))
    if like_ratio is not None:
        score += min(15.0, max(0.0, (like_ratio - 1.0) * 15.0))
    if comment_ratio is not None:
        score += min(10.0, max(0.0, (comment_ratio - 1.0) * 10.0))
    if follower_ratio is not None:
        score += min(10.0, max(0.0, follower_ratio / 4.0 * 10.0))
    if age is not None and age <= 72:
        score += 5.0
    level = "normal" if score < 40 else "interesting" if score < 55 else "candidate" if score < 70 else "strong_outlier" if score < 85 else "priority"
    return {
        "outlier_score": round(score, 1), "view_outlier_ratio": round(view_ratio, 3) if view_ratio else None,
        "velocity_outlier_ratio": round(velocity_ratio, 3) if velocity_ratio else None,
        "like_rate_outlier_ratio": round(like_ratio, 3) if like_ratio else None,
        "comment_rate_outlier_ratio": round(comment_ratio, 3) if comment_ratio else None,
        "outlier_level": level,
    }
