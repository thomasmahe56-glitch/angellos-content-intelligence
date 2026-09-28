import asyncio

from content_agent.config import Settings
from content_agent.discovery.instagram_browser import InstagramHumanActionRequired
from content_agent.models.schemas import Candidate
from content_agent import runner


def test_challenge_during_enrichment_stops_before_luna(monkeypatch, tmp_path):
    class Browser:
        async def check_authentication(self): pass
        async def discover(self, _): return [Candidate(source_url="https://www.instagram.com/reel/abc/", shortcode="abc")]
        async def enrich(self, *_): raise InstagramHumanActionRequired("instagram_authentication_challenge")
    class Calendar:
        def __init__(self, *_): pass
        def recent_content(self): return []
    monkeypatch.setattr(runner, "InstagramBrowser", lambda _: Browser())
    monkeypatch.setattr(runner, "NotionEditorialCalendar", Calendar)
    monkeypatch.setattr(runner, "fetch_angellos_context", lambda: "context")
    monkeypatch.setenv("CONTENT_AGENT_LOCAL_STATE_PATH", str(tmp_path / "state.json"))
    cfg = Settings()
    result = asyncio.run(runner.run_daily_scout(cfg=cfg))
    assert result["status"] == "human_action_required"
    assert result["ai_calls"] == {"luna": 0, "gemini": 0}
