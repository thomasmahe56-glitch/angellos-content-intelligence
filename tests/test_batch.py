import asyncio

from content_agent import runner
from content_agent.config import Settings


def test_analyze_batch_deduplicates_and_aggregates(monkeypatch):
    calls = []

    async def fake_analyze(url, **kwargs):
        calls.append((url, kwargs["batch_mode"], kwargs["run_id"]))
        return {
            "status": "completed",
            "created_items": [{"notion_url": f"https://notion/{url.rsplit('/', 2)[-2]}"}],
            "costs": {"total_usd": 0.01, "gemini_usd": 0.007, "luna_usd": 0.003},
        }

    monkeypatch.setattr(runner, "analyze_reel_url", fake_analyze)
    cfg = Settings(analyze_batch_max_candidates=3, analyze_batch_concurrency=2)
    result = asyncio.run(runner.analyze_batch([
        {"url": "https://www.instagram.com/reel/a/", "observed_metrics": {"likes": 10}},
        {"url": "https://www.instagram.com/reel/a/", "observed_metrics": {"likes": 10}},
        {"url": "https://www.instagram.com/reel/b/", "observed_metrics": {"likes": 20}},
    ], run_id="batch-1", cfg=cfg))

    assert result["status"] == "completed"
    assert result["batch_size"] == 2
    assert result["content_ideas_created"] == 2
    assert result["costs"]["total_usd"] == 0.02
    assert len(calls) == 2
    assert all(batch_mode and run_id == "batch-1" for _, batch_mode, run_id in calls)


def test_analyze_batch_rejects_unbounded_size():
    cfg = Settings(analyze_batch_max_candidates=1)
    result = asyncio.run(runner.analyze_batch([
        {"url": "https://www.instagram.com/reel/a/", "observed_metrics": {"likes": 10}},
        {"url": "https://www.instagram.com/reel/b/", "observed_metrics": {"likes": 20}},
    ], run_id="batch-1", cfg=cfg))
    assert result["status"] == "configuration_error"
    assert "at most 1" in result["error"]


def test_analyze_batch_requires_cua_metrics():
    result = asyncio.run(runner.analyze_batch([
        {"url": "https://www.instagram.com/reel/a/"},
    ], run_id="batch-1", cfg=Settings()))
    assert result["status"] == "configuration_error"
    assert "observed_metrics" in result["error"]
