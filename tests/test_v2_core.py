from content_agent.content.scheduler import next_editorial_slot
from content_agent.discovery.dedupe import is_duplicate
from content_agent.discovery.metrics import parse_compact_number, viral_ratio
from content_agent.intelligence.luna_client import LunaError, parse_json
from content_agent.models.schemas import Candidate
from content_agent.storage.state import StateStore
from content_agent.config import Settings
import asyncio
from content_agent.runner import analyze_reel_url
from content_agent.discovery.instagram_browser import InstagramBrowser
from content_agent.config import is_configured
from content_agent.discovery.apify_metrics import _cost_usd, _creator_username, _number, _shortcode
from content_agent.runner import _qualified_rank, _set_cost_report, _record_gemini_usage
from content_agent.models.schemas import RunReport
from content_agent.intelligence.luna_client import LunaClient
from content_agent.intelligence.adaptation import adapt_to_angellos, quality_check


def test_metric_normalization():
    assert parse_compact_number("12K") == 12_000
    assert parse_compact_number("12.5K") == 12_500
    assert parse_compact_number("1.3M") == 1_300_000
    assert parse_compact_number("1,2 M") == 1_200_000


def test_profile_follower_parser_supports_singular_label():
    assert InstagramBrowser._metric_after("1 follower", "follower(?:s)?") == 1


def test_reel_creator_prefers_detail_creator_link_over_own_navigation_link():
    assert InstagramBrowser._creator_from_hrefs(["/reels/", "/angellos.ai/", "/realskytan/reels/"]) == "realskytan"


def test_viral_ratio():
    assert viral_ratio(100_000, 20_000) == 5


def test_dedupe_by_shortcode():
    candidate = Candidate(source_url="https://www.instagram.com/reel/abc/", shortcode="abc")
    assert is_duplicate(candidate, {"reels": {"abc": {"status": "scheduled"}}})


def test_insufficient_metrics_can_be_explicitly_retried_without_weakening_normal_dedupe():
    candidate = Candidate(source_url="https://www.instagram.com/reel/abc/", shortcode="abc")
    state = {"reels": {"abc": {"status": "insufficient_metrics"}}}
    assert is_duplicate(candidate, state)
    assert not is_duplicate(candidate, state, retry_insufficient_metrics=True)


def test_state_transitions():
    candidate = Candidate(source_url="https://www.instagram.com/reel/abc/", shortcode="abc")
    state = {"reels": {}}
    StateStore.mark(state, candidate, "discovered")
    StateStore.mark(state, candidate, "qualified")
    StateStore.mark(state, candidate, "analyzed")
    StateStore.mark(state, candidate, "scheduled")
    assert state["reels"]["abc"]["status"] == "scheduled"


def test_state_does_not_persist_large_analysis_payloads():
    candidate = Candidate(source_url="https://www.instagram.com/reel/abc/", shortcode="abc")
    state = {"reels": {}}
    StateStore.mark(state, candidate, "analyzed", gemini_analysis={"timeline": ["x" * 10_000]}, adaptation={"script": "x" * 10_000})
    assert "gemini_analysis" not in state["reels"]["abc"]
    assert "adaptation" not in state["reels"]["abc"]


def test_luna_bad_json_is_safe():
    try:
        parse_json("not json")
    except LunaError:
        return
    assert False


def test_editorial_schedule_does_not_overwrite_busy_day():
    assert next_editorial_slot({"2026-09-28"}, ("MON",), 1) == "2026-10-05"


def test_state_store_keeps_unowned_notion_blocks(tmp_path):
    class Blocks:
        def __init__(self):
            self.results = [{"id": "human", "type": "paragraph", "paragraph": {}}, {"id": "ours", "type": "code", "code": {"caption": [{"plain_text": "content-agent-state"}], "rich_text": [{"plain_text": '{"reels":{},"followers":{}}'}]}}]
            self.children = self
            self.deleted = []
        def list(self, **_): return {"results": self.results, "has_more": False}
        def delete(self, block_id): self.deleted.append(block_id)
        def append(self, **_): pass
    class FakeNotion:
        def __init__(self): self.blocks = Blocks()
    cfg = Settings(notion_api_key="x", notion_state_page_id="page")
    store = StateStore(cfg)
    store.notion = FakeNotion()
    assert store.load() == {"reels": {}, "followers": {}}
    store.save({"reels": {}, "followers": {}})
    assert store.notion.blocks.deleted == ["ours"]


def test_manual_analysis_rejects_invalid_url_without_browser():
    result = asyncio.run(analyze_reel_url("https://example.com/not-instagram"))
    assert result["status"] == "configuration_error"


def test_discovery_sources_include_dream100_and_seeds():
    browser = InstagramBrowser(Settings(dream100_accounts=("dream",), seed_accounts=("seed",)))
    assert browser._sources()[0] == ("https://www.instagram.com/reels/", "recommendations")
    assert ("https://www.instagram.com/dream/reels/", "dream100") in browser._sources()
    assert ("https://www.instagram.com/seed/reels/", "seed_account") in browser._sources()


def test_deployment_placeholder_is_not_a_configured_secret():
    assert not is_configured("__REPLACE_ME__")
    assert is_configured("a-real-value")


def test_state_store_rejects_placeholder_page_id():
    assert not StateStore(Settings(notion_api_key="key", notion_state_page_id="__REPLACE_ME__")).durable


def test_state_store_strict_mode_rejects_durable_read_failure(tmp_path):
    class BrokenBlocks:
        children = None
    class BrokenNotion:
        blocks = BrokenBlocks()
    store = StateStore(Settings(notion_api_key="key", notion_state_page_id="page"))
    store.notion = BrokenNotion()
    try:
        store.load(strict=True)
    except RuntimeError as exc:
        assert "durable state read failed" in str(exc)
        return
    assert False


def test_health_counts_cookie_auth_as_instagram_ready():
    from content_agent.runner import health
    assert health(Settings(instagram_cookies_b64="Y29va2ll")).get("instagram_session_ready") is True


def test_health_requires_durable_state_page_for_production():
    from content_agent.runner import health
    result = health(Settings(openai_api_key="key", gemini_api_key="key", notion_api_key="key", notion_programme_content_db="db"))
    assert result["status"] == "configuration_error"
    assert "NOTION_CONTENT_AGENT_STATE_PAGE_ID" in result["missing"]


def test_netscape_cookie_parser_supports_http_only(tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t123\tsessionid\tvalue\n")
    cookies = InstagramBrowser._parse_netscape_cookies(str(cookie_file))
    assert cookies == [{"name": "sessionid", "value": "value", "domain": ".instagram.com", "path": "/", "httpOnly": True, "secure": True, "expires": 123}]


def test_netscape_cookie_parser_drops_invalid_huge_expiry(tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(".instagram.com\tTRUE\t/\tTRUE\t999999999999999\tsessionid\tvalue\n")
    assert "expires" not in InstagramBrowser._parse_netscape_cookies(str(cookie_file))[0]


def test_candidate_urls_support_reel_and_video_post_routes():
    reel = InstagramBrowser._candidate_from_url("https://www.instagram.com/reel/abc/", "explore")
    feed_reel = InstagramBrowser._candidate_from_url("https://www.instagram.com/reels/feed-abc/", "recommendations")
    post = InstagramBrowser._candidate_from_url("https://www.instagram.com/p/xyz/", "explore")
    assert (reel.source_url, reel.shortcode) == ("https://www.instagram.com/reel/abc/", "abc")
    assert (feed_reel.source_url, feed_reel.shortcode) == ("https://www.instagram.com/reel/feed-abc/", "feed-abc")
    assert (post.source_url, post.shortcode) == ("https://www.instagram.com/p/xyz/", "xyz")


def test_public_og_date_is_normalized_for_freshness_ranking():
    description = '2,000 likes - creator on September 10, 2026: "caption"'
    assert InstagramBrowser._published_date(description) == "2026-09-10"
    assert InstagramBrowser._published_date("no date exposed") == ""


def test_apify_metric_helpers_only_accept_explicit_numeric_values():
    assert _shortcode("https://www.instagram.com/reel/abc-12/") == "abc-12"
    assert _shortcode("abc-12") == "abc-12"
    assert _number({"videoViewCount": 1200}, "videoViewCount") == 1200
    assert _number({"videoViewCount": "1200"}, "videoViewCount") == 1200
    assert _number({"videoViewCount": "12.5K"}, "videoViewCount") == 12_500
    assert _cost_usd({"usageTotalUsd": 0.12}) == 0.12
    assert _cost_usd({}) is None
    assert _creator_username({"owner": {"username": "creator"}}) == "creator"


def test_qualified_rank_prefers_virality_then_transferability_then_freshness():
    older = Candidate(source_url="https://www.instagram.com/reel/old/", viral_ratio=5, discovered_at="2026-01-01T00:00:00+00:00")
    stronger_fit = Candidate(source_url="https://www.instagram.com/reel/fit/", viral_ratio=5, discovered_at="2026-01-02T00:00:00+00:00")
    more_viral = Candidate(source_url="https://www.instagram.com/reel/viral/", viral_ratio=6)
    assert _qualified_rank((more_viral, {"confidence": 0})) > _qualified_rank((stronger_fit, {"confidence": 1}))
    assert _qualified_rank((stronger_fit, {"confidence": 1})) > _qualified_rank((older, {"confidence": 0.5}))


def test_cost_report_only_claims_total_when_all_used_services_have_rates():
    class Luna:
        def estimated_cost_usd(self): return 0.2

    report = RunReport(videos_analyzed=2)
    cfg = Settings(gemini_analysis_usd_per_video=0.1)
    _set_cost_report(report, cfg, Luna(), apify_cost_usd=0.05, apify_cost_known=True)
    assert report.costs["total_usd"] == 0.45
    assert report.costs["complete"] is True


def test_cost_report_uses_actual_gemini_token_usage_when_rates_are_configured():
    class Luna:
        input_tokens = output_tokens = 0
        def estimated_cost_usd(self): return 0.0

    report = RunReport(videos_analyzed=1)
    _record_gemini_usage(report, {"_gemini_usage": {"input_tokens": 100_000, "output_tokens": 10_000}})
    cfg = Settings(gemini_input_usd_per_million_tokens=0.30, gemini_output_usd_per_million_tokens=2.50)
    _set_cost_report(report, cfg, Luna(), apify_cost_usd=0.0, apify_cost_known=True)
    assert report.costs["gemini_usd"] == 0.055
    assert report.costs["total_usd"] == 0.055


def test_historical_signals_prefer_durable_notion_ledger_over_local_cache():
    from content_agent.runner import _historical_signals
    durable = {
        "performance_patterns": {
            "historical_signals": {
                "successful_hook_patterns": ["durable hook"],
                "successful_formats": ["talking head"],
                "successful_topics": ["qualification"],
                "weak_patterns": ["durable weak"],
                "notes": ["durable note"],
            }
        }
    }
    signals = _historical_signals(Settings(), durable)
    assert signals["successful_hook_patterns"] == ["durable hook"]
    assert signals["notes"] == ["durable note"]


def test_luna_omits_temperature_unless_explicitly_configured(monkeypatch):
    captured = {}

    class Response:
        status_code = 200
        def json(self): return {"choices": [{"message": {"content": "{}"}}], "usage": {}}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def post(self, _, **kwargs):
            captured.update(kwargs["json"])
            return Response()

    import content_agent.intelligence.luna_client as module
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_: Client())
    client = LunaClient(Settings(openai_api_key="key"))
    asyncio.run(client.json("system", "prompt", {"type": "object"}))
    assert "temperature" not in captured


def test_adaptation_and_quality_prompts_protect_against_unverified_ctas():
    class Luna:
        def __init__(self): self.prompts = []
        async def json(self, system, prompt, _schema):
            self.prompts.append((system, prompt))
            return {"approved": True, "issues": []} if "quality gate" in system else {"source": {}}

    luna = Luna()
    candidate = Candidate(source_url="https://www.instagram.com/reel/abc/", shortcode="abc")
    asyncio.run(adapt_to_angellos(luna, candidate, {}, "canonical product facts", [], {}, "English"))
    asyncio.run(quality_check(luna, {}, "canonical product facts", []))

    adaptation_prompt = luna.prompts[0][1]
    quality_system = luna.prompts[1][0]
    assert "Do not invent a lead magnet" in adaptation_prompt
    assert "65 English words or fewer" in adaptation_prompt
    assert "Follow @angellos.ai for practical qualification systems." in adaptation_prompt
    assert "does not require a promised reply" in quality_system
