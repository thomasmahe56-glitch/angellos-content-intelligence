import asyncio
import json
import sys
import types

from content_agent import runner
from content_agent.config import Settings
from content_agent.models.schemas import Candidate


def test_scout_orchestrates_one_content_idea_and_cleans_video(monkeypatch, tmp_path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")

    class Browser:
        async def check_authentication(self): pass
        async def discover(self, _):
            return [Candidate(source_url="https://www.instagram.com/reel/abc/", shortcode="abc", discovery_method="explore")]
        async def enrich(self, candidate, _):
            candidate.creator_username = "creator"
            candidate.views, candidate.followers = 100_000, 10_000
            return candidate

    created = []
    class Calendar:
        def __init__(self, *_): pass
        def recent_content(self): return []
        def source_page_url(self, _): return None
        def create(self, candidate, gemini, adaptation, dry_run=False):
            created.append((candidate, gemini, adaptation, dry_run))
            return "https://www.notion.so/idea"

    class Luna:
        def __init__(self, *_): self.calls = 0

    async def qualify(client, *_):
        client.calls += 1
        return {"decision": "keep", "confidence": 1, "reason": "fit", "likely_transferable_pattern": "contrast", "potential_angellos_angle": "DMs", "expected_content_category": "Tips"}

    async def adapt(client, candidate, *_):
        client.calls += 1
        return {"internal_title": "Original Angellos idea", "source": {}, "why_selected": "fit", "source_pattern": "contrast", "angellos_angle": "DMs", "content_goal": "awareness", "hook": "Stop", "script": {"hook": "Stop", "development": ["Proof"], "cta": "Comment"}, "shot_list": [], "filming": {"format": "vertical", "camera": "phone", "location": "desk", "lighting": "window", "props": [], "screen_recordings_needed": []}, "editing": {"pace": "fast", "cuts": "cuts", "zoom": "none", "captions": "on", "music": "none", "sound_effects": []}, "target_duration_seconds": 20, "caption": "caption", "cta": "Comment", "content_type": "Hooks", "ig_content_type": "Tips", "why_test_this": "signal", "facts_that_must_be_verified_before_filming": []}

    async def quality(client, *_):
        client.calls += 1
        return {"approved": True, "issues": []}

    async def download(_): return {"local_path": str(video), "caption_originale": "caption"}
    async def gemini(*_): return {"_gemini_model_used": "fake-gemini", "hook": {}, "replicable_creative_pattern": "contrast"}

    download_module = types.ModuleType("content_agent.video.downloader")
    download_module.download = download
    gemini_module = types.ModuleType("content_agent.video.gemini_analyzer")
    gemini_module.analyze_video = gemini
    gemini_module.GeminiAnalysisError = RuntimeError
    monkeypatch.setitem(sys.modules, "content_agent.video.downloader", download_module)
    monkeypatch.setitem(sys.modules, "content_agent.video.gemini_analyzer", gemini_module)
    monkeypatch.setattr(runner, "InstagramBrowser", lambda _: Browser())
    monkeypatch.setattr(runner, "NotionEditorialCalendar", Calendar)
    monkeypatch.setattr(runner, "LunaClient", Luna)
    monkeypatch.setattr(runner, "qualify_candidate", qualify)
    monkeypatch.setattr(runner, "adapt_to_angellos", adapt)
    monkeypatch.setattr(runner, "quality_check", quality)
    monkeypatch.setattr(runner, "fetch_angellos_context", lambda: "canonical context")
    monkeypatch.setenv("CONTENT_AGENT_LOCAL_STATE_PATH", str(tmp_path / "state.json"))

    result = asyncio.run(runner.run_daily_scout(cfg=Settings(allow_ephemeral_state=True)))

    assert result["status"] == "completed"
    assert result["content_ideas_created"] == 1
    assert result["ai_calls"] == {"luna": 3, "gemini": 1}
    assert created and created[0][3] is False
    assert not video.exists()
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["reels"]["abc"]["status"] == "scheduled"


def test_max_content_zero_never_downloads_or_creates(monkeypatch, tmp_path):
    class Browser:
        async def check_authentication(self): pass
        async def discover(self, _):
            return [Candidate(source_url="https://www.instagram.com/reel/abc/", shortcode="abc")]
        async def enrich(self, candidate, _):
            candidate.views, candidate.followers, candidate.media_is_video = 100_000, 10_000, True
            return candidate

    class Calendar:
        def __init__(self, *_): pass
        def recent_content(self): return []
        def create(self, *_): raise AssertionError("max-content=0 must not create Notion content")

    class Luna:
        def __init__(self, *_): self.calls = 0

    async def qualify(client, *_):
        client.calls += 1
        return {"decision": "keep", "confidence": 1, "reason": "fit", "likely_transferable_pattern": "contrast", "potential_angellos_angle": "DMs", "expected_content_category": "Tips"}

    monkeypatch.setattr(runner, "InstagramBrowser", lambda _: Browser())
    monkeypatch.setattr(runner, "NotionEditorialCalendar", Calendar)
    monkeypatch.setattr(runner, "LunaClient", Luna)
    monkeypatch.setattr(runner, "qualify_candidate", qualify)
    monkeypatch.setattr(runner, "fetch_angellos_context", lambda: "context")
    monkeypatch.setenv("CONTENT_AGENT_LOCAL_STATE_PATH", str(tmp_path / "state.json"))

    result = asyncio.run(runner.run_daily_scout(max_content=0, cfg=Settings(allow_ephemeral_state=True)))
    assert result["relevance_qualified"] == 1
    assert result["videos_downloaded"] == 0
    assert result["content_ideas_created"] == 0


def test_scout_uses_apify_only_for_browser_metric_gaps(monkeypatch, tmp_path):
    class Browser:
        async def check_authentication(self): pass
        async def discover(self, _):
            return [Candidate(source_url="https://www.instagram.com/reel/abc/", shortcode="abc")]
        async def enrich(self, candidate, _):
            candidate.creator_username = "creator"
            candidate.views, candidate.followers, candidate.media_is_video = 10_000, 10_000, True
            return candidate

    class Calendar:
        def __init__(self, *_): pass
        def recent_content(self): return []

    apify_calls = []
    async def apify(candidates, *_):
        apify_calls.append(candidates)
        raise AssertionError("Apify must not run when browser metrics are present")

    import content_agent.discovery.apify_metrics as apify_module
    monkeypatch.setattr(apify_module, "enrich_reel_metrics", apify)
    monkeypatch.setattr(runner, "InstagramBrowser", lambda _: Browser())
    monkeypatch.setattr(runner, "NotionEditorialCalendar", Calendar)
    monkeypatch.setattr(runner, "fetch_angellos_context", lambda: "context")
    monkeypatch.setenv("CONTENT_AGENT_LOCAL_STATE_PATH", str(tmp_path / "state.json"))

    result = asyncio.run(runner.run_daily_scout(max_content=0, cfg=Settings(allow_ephemeral_state=True, apify_metrics_fallback=True, apify_api_key="key")))
    assert result["rejected_ratio"] == 1
    assert apify_calls == []


def test_manual_analysis_gets_one_quality_correction_before_creating(monkeypatch, tmp_path):
    """Manual endpoint must have the same bounded correction as the scout."""
    video = tmp_path / "manual-source.mp4"
    video.write_bytes(b"video")

    class Browser:
        async def check_authentication(self): pass
        async def enrich(self, candidate, _):
            candidate.creator_username = "creator"
            candidate.views, candidate.followers = 100_000, 10_000
            return candidate

    created = []
    class Calendar:
        def __init__(self, *_): pass
        def recent_content(self): return []
        def create(self, *_args, **_kwargs):
            created.append(True)
            return "https://www.notion.so/idea"

    class Luna:
        def __init__(self, *_):
            self.calls, self.input_tokens, self.output_tokens = 0, 123, 45
        def estimated_cost_usd(self): return None

    adaptation_contexts = []
    async def adapt(client, _candidate, _gemini, context, *_args):
        client.calls += 1
        adaptation_contexts.append(context)
        return {"internal_title": "Corrected unique concept"}

    quality_answers = iter((
        {"approved": False, "issues": ["angle overlaps recent content"]},
        {"approved": True, "issues": []},
    ))
    async def quality(client, *_args):
        client.calls += 1
        return next(quality_answers)

    async def download(_): return {"local_path": str(video), "caption_originale": "caption"}
    async def gemini(*_): return {"_gemini_model_used": "fake-gemini"}
    async def apify_metrics(candidates, *_):
        candidates[0].views, candidates[0].followers = 200_000, 10_000
        from content_agent.discovery.apify_metrics import ApifyMetricsResult
        return ApifyMetricsResult(enriched=1, cost_usd=0.03, run_id="run")

    download_module = types.ModuleType("content_agent.video.downloader")
    download_module.download = download
    gemini_module = types.ModuleType("content_agent.video.gemini_analyzer")
    gemini_module.analyze_video = gemini
    gemini_module.GeminiAnalysisError = RuntimeError
    monkeypatch.setitem(sys.modules, "content_agent.video.downloader", download_module)
    monkeypatch.setitem(sys.modules, "content_agent.video.gemini_analyzer", gemini_module)
    monkeypatch.setattr(runner, "InstagramBrowser", lambda _: Browser())
    monkeypatch.setattr(runner, "NotionEditorialCalendar", Calendar)
    monkeypatch.setattr(runner, "LunaClient", Luna)
    monkeypatch.setattr(runner, "adapt_to_angellos", adapt)
    monkeypatch.setattr(runner, "quality_check", quality)
    monkeypatch.setattr(runner, "fetch_angellos_context", lambda: "canonical context")
    import content_agent.discovery.apify_metrics as apify_module
    monkeypatch.setattr(apify_module, "enrich_reel_metrics", apify_metrics)

    result = asyncio.run(runner.analyze_reel_url("https://www.instagram.com/reel/abc/", cfg=Settings(apify_metrics_fallback=True, apify_api_key="key")))

    assert result["status"] == "completed"
    assert result["content_ideas_created"] == 1
    assert result["ai_calls"] == {"luna": 4, "gemini": 1}
    assert len(adaptation_contexts) == 2
    assert "Correct these quality issues" in adaptation_contexts[1]
    assert result["costs"]["luna_input_tokens"] == 123
    assert result["costs"]["apify_usd"] == 0.03
    assert created
    assert not video.exists()


def test_computer_use_metrics_skip_browser_and_apify(monkeypatch, tmp_path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")

    class Calendar:
        def __init__(self, *_): pass
        def recent_content(self): return []
        def create(self, *_args, **_kwargs): return "https://www.notion.so/idea"

    class Luna:
        def __init__(self, *_): self.calls = 0
        def estimated_cost_usd(self): return 0.0

    class ForbiddenBrowser:
        def __init__(self, *_):
            raise AssertionError("Computer Use metrics must not start Playwright")

    async def download(_): return {"local_path": str(video), "caption_originale": "visible caption"}
    async def gemini(*_): return {"_gemini_model_used": "fake-gemini"}
    async def adapt(client, *_):
        client.calls += 1
        return {"internal_title": "Faceless Angellos idea"}
    async def quality(client, *_):
        client.calls += 1
        return {"approved": True, "issues": []}

    download_module = types.ModuleType("content_agent.video.downloader")
    download_module.download = download
    gemini_module = types.ModuleType("content_agent.video.gemini_analyzer")
    gemini_module.analyze_video = gemini
    gemini_module.GeminiAnalysisError = RuntimeError
    monkeypatch.setitem(sys.modules, "content_agent.video.downloader", download_module)
    monkeypatch.setitem(sys.modules, "content_agent.video.gemini_analyzer", gemini_module)
    monkeypatch.setattr(runner, "InstagramBrowser", ForbiddenBrowser)
    monkeypatch.setattr(runner, "NotionEditorialCalendar", Calendar)
    monkeypatch.setattr(runner, "LunaClient", Luna)
    monkeypatch.setattr(runner, "adapt_to_angellos", adapt)
    monkeypatch.setattr(runner, "quality_check", quality)
    monkeypatch.setattr(runner, "fetch_angellos_context", lambda: "canonical context")

    result = asyncio.run(runner.analyze_reel_url(
        "https://www.instagram.com/reel/abc/",
        observed_metrics={"likes": 58_800, "comments": 216, "creator_username": "creator", "caption_preview": "visible caption"},
        cfg=Settings(apify_metrics_fallback=True, apify_api_key="must-not-be-used"),
    ))

    assert result["status"] == "completed"
    assert result["content_ideas_created"] == 1
    assert result["costs"]["apify_usd"] == 0.0
    assert not video.exists()
