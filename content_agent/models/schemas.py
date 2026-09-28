from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class Candidate:
    source_url: str
    shortcode: str = ""
    creator_username: str = ""
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    followers: Optional[int] = None
    source_published_at: str = ""
    caption_preview: str = ""
    discovered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    discovery_method: str = ""
    # One identifier is generated for each supervised or production cycle.
    # It lets the scheduler prove that it is only handing that cycle's new
    # ideas to ARIA, never the historical editorial backlog.
    run_id: str = ""
    thumbnail_url: str = ""
    media_is_video: Optional[bool] = None
    status: str = "discovered"
    viral_ratio: Optional[float] = None
    outlier_score: Optional[float] = None
    view_outlier_ratio: Optional[float] = None
    velocity_outlier_ratio: Optional[float] = None
    like_rate_outlier_ratio: Optional[float] = None
    comment_rate_outlier_ratio: Optional[float] = None
    outlier_level: str = ""

    @property
    def key(self) -> str:
        return self.shortcode or self.source_url.rstrip("/")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunReport:
    status: str = "completed"
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ended_at: str = ""
    scanned: int = 0
    new_reels: int = 0
    duplicates: int = 0
    viral_candidates: int = 0
    relevance_qualified: int = 0
    videos_analyzed: int = 0
    content_ideas_created: int = 0
    rejected_ratio: int = 0
    rejected_relevance: int = 0
    failed: int = 0
    human_action_required: bool = False
    human_action_reason: str = ""
    created_items: list[dict[str, Any]] = field(default_factory=list)
    ai_calls: dict[str, int] = field(default_factory=lambda: {"luna": 0, "gemini": 0})
    videos_downloaded: int = 0
    gemini_input_tokens: int = 0
    gemini_output_tokens: int = 0
    errors: list[str] = field(default_factory=list)
    costs: dict[str, Any] = field(default_factory=dict)

    def finish(self, status: Optional[str] = None) -> dict[str, Any]:
        if status:
            self.status = status
        self.ended_at = datetime.now(timezone.utc).isoformat()
        data = asdict(self)
        data.pop("human_action_reason", None)
        if self.human_action_required:
            data["human_action_required"] = True
            data["reason"] = self.human_action_reason or "human_action_required"
        return data
