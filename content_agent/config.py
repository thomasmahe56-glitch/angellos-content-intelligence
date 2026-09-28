"""V2 settings. Secrets are deliberately read only from the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    # Reuses The Lazy Company's established Luna identifier while remaining
    # overrideable if the provider/API name changes.
    openai_content_model: str = os.getenv("OPENAI_CONTENT_MODEL", "gpt-5.6-luna")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_video_model: str = os.getenv("GEMINI_VIDEO_MODEL", os.getenv("GEMINI_MODEL_PRIMARY", "gemini-2.0-flash"))
    notion_api_key: str = os.getenv("NOTION_API_KEY", "")
    notion_programme_content_db: str = os.getenv("NOTION_PROGRAMME_CONTENT_DB", "")
    notion_state_page_id: str = os.getenv("NOTION_CONTENT_AGENT_STATE_PAGE_ID", "")
    instagram_session_path: str = os.getenv("INSTAGRAM_SESSION_PATH", "/tmp/instagram-session.json")
    instagram_cookies_file: str = os.getenv("INSTAGRAM_COOKIES_FILE", "/tmp/instagram-cookies.txt")
    instagram_cookies_b64: str = os.getenv("INSTAGRAM_COOKIES_B64", "")
    apify_api_key: str = os.getenv("APIFY_API_KEY", "")
    # Disabled by default: this fallback creates an Apify actor run and may
    # incur usage. Enable only after choosing the desired run budget.
    apify_metrics_fallback: bool = _bool("APIFY_METRICS_FALLBACK", False)
    apify_metrics_max_reels: int = _int("APIFY_METRICS_MAX_REELS", 25)
    viral_ratio_min: float = float(os.getenv("VIRAL_RATIO_MIN", "4.0"))
    discovery_max_reels_per_run: int = _int("DISCOVERY_MAX_REELS_PER_RUN", 100)
    qualified_max_per_run: int = _int("QUALIFIED_MAX_PER_RUN", 5)
    analyze_max_per_run: int = _int("ANALYZE_MAX_PER_RUN", 5)
    scout_scroll_limit: int = _int("SCOUT_SCROLL_LIMIT", 30)
    scout_max_runtime_minutes: int = _int("SCOUT_MAX_RUNTIME_MINUTES", 20)
    content_language: str = os.getenv("CONTENT_LANGUAGE", "EN")
    timezone: str = os.getenv("TIMEZONE", "Europe/Paris")
    content_publish_days: tuple[str, ...] = tuple(x.strip().upper() for x in os.getenv("CONTENT_PUBLISH_DAYS", "").split(",") if x.strip())
    content_items_per_day: int = _int("CONTENT_ITEMS_PER_DAY", 0)
    follower_cache_ttl_hours: int = _int("FOLLOWER_CACHE_TTL_HOURS", 72)
    seed_accounts: tuple[str, ...] = tuple(x.strip().lstrip("@") for x in os.getenv("INSTAGRAM_SEED_ACCOUNTS", "").split(",") if x.strip())
    dream100_accounts: tuple[str, ...] = tuple(x.strip().lstrip("@") for x in os.getenv("DREAM100_ACCOUNTS", "").split(",") if x.strip())
    log_format: str = os.getenv("CONTENT_AGENT_LOG_FORMAT", "text").lower()
    downloads_dir: str = os.getenv("DOWNLOADS_DIR", "./downloads")
    delete_source_video_after_analysis: bool = _bool("DELETE_SOURCE_VIDEO_AFTER_ANALYSIS", True)
    run_timeout_seconds: int = _int("SCOUT_RUN_TIMEOUT_SECONDS", 1200)

    @property
    def local_state_path(self) -> Path:
        return Path(os.getenv("CONTENT_AGENT_LOCAL_STATE_PATH", "/tmp/content-agent-state.json"))


settings = Settings()


def is_configured(value: str) -> bool:
    """Reject explicit deployment placeholders as well as empty values."""
    return bool(value and not value.startswith("__REPLACE_ME__"))
