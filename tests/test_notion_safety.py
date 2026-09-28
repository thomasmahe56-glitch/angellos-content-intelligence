import pytest

from content_agent.config import Settings
from content_agent.storage.notion import NotionEditorialCalendar
from content_agent.storage.notion import _body
from content_agent.models.schemas import Candidate


def test_notion_schema_conflict_never_updates():
    class Databases:
        def retrieve(self, **_):
            return {"properties": {"Source URL": {"type": "rich_text"}}}
        def update(self, **_):
            raise AssertionError("must not update an incompatible existing property")
    class FakeClient:
        databases = Databases()
    calendar = NotionEditorialCalendar(Settings(notion_api_key="x", notion_programme_content_db="db"))
    calendar.client = FakeClient()
    with pytest.raises(RuntimeError, match="Source URL"):
        calendar.ensure_v2_properties()


def test_editorial_calendar_dedupes_source_url():
    class Databases:
        def retrieve(self, **_): return {"properties": {name: {"type": kind} for name, kind in {"Source URL": "url", "Source Creator": "rich_text", "Source Views": "number", "Source Likes": "number", "Source Comments": "number", "Source Followers": "number", "Viral Ratio": "number", "Discovery Method": "rich_text", "Discovered At": "date", "Source Published At": "date", "Why Selected": "rich_text"}.items()}}
        def query(self, **_): return {"results": [{"url": "https://www.notion.so/existing"}]}
    class FakeClient:
        databases = Databases()
    calendar = NotionEditorialCalendar(Settings(notion_api_key="x", notion_programme_content_db="db"))
    calendar.client = FakeClient()
    assert calendar.source_page_url("https://www.instagram.com/reel/abc/") == "https://www.notion.so/existing"


def test_dry_run_does_not_touch_notion():
    class Explosive:
        def __getattr__(self, _): raise AssertionError("dry-run must not call Notion")
    calendar = NotionEditorialCalendar(Settings(notion_api_key="x", notion_programme_content_db="db"))
    calendar.client = Explosive()
    assert calendar.create(None, {}, {}, dry_run=True) is None


def test_production_body_has_required_source_and_production_sections():
    candidate = Candidate(source_url="https://www.instagram.com/reel/abc/", shortcode="abc")
    gemini = {"hook": {"spoken": "Stop", "on_screen_text": "Stop", "visual": "face"}, "narrative_structure": ["hook", "proof"], "retention_mechanisms": ["curiosity"], "replicable_creative_pattern": "contrast"}
    adaptation = {"why_selected": "why", "angellos_angle": "angle", "hook": "hook", "script": {"hook": "hook", "development": [], "cta": "cta"}, "shot_list": [{"start": 0, "end": 3, "shot": "face", "spoken": "hi", "on_screen_text": "hi", "b_roll": "dashboard", "editing": "cut"}], "filming": {"screen_recordings_needed": ["inbox"]}, "editing": {}, "caption": "caption", "cta": "cta", "why_test_this": "test", "facts_that_must_be_verified_before_filming": []}
    headings = [b.get("heading_1", {}).get("rich_text", [{}])[0].get("text", {}).get("content") for b in _body(candidate, gemini, adaptation) if b["type"] == "heading_1"]
    assert {"Source analysis", "Screen recordings required", "B-roll", "Editing instructions", "Caption", "CTA"}.issubset(headings)


def test_recent_content_paginates_all_editorial_rows():
    def page(title):
        return {"properties": {"Name": {"title": [{"plain_text": title}]}}}

    class Databases:
        def query(self, **kwargs):
            if kwargs.get("start_cursor") == "second":
                return {"results": [page("second")], "has_more": False}
            return {"results": [page("first")], "has_more": True, "next_cursor": "second"}

    class FakeClient:
        databases = Databases()

    calendar = NotionEditorialCalendar(Settings(notion_api_key="key", notion_programme_content_db="db"))
    calendar.client = FakeClient()
    assert [item["title"] for item in calendar.recent_content()] == ["first", "second"]


def test_performance_sync_updates_only_a_matched_published_calendar_page():
    stats = {name: {"type": "number"} for name in ("Vues IG", "Saves IG", "Reach IG", "Likes IG", "Commentaires IG", "Partages IG")}
    page = {
        "id": "published-page",
        "properties": {
            **stats,
            "IG Status": {"type": "select", "select": {"name": "Published"}},
            "Date of Publish": {"type": "date", "date": {"start": "2026-09-20"}},
            "URL Reel": {"type": "url", "url": "https://www.instagram.com/reel/ours/"},
            "Optional URL": {"type": "url", "url": None},
        },
    }

    class Databases:
        def retrieve(self, **_): return {"properties": {**stats}}
        def query(self, **_): return {"results": [page], "has_more": False}
        def update(self, **_): raise AssertionError("all performance fields already exist")

    class Pages:
        def __init__(self): self.updates = []
        def update(self, **kwargs): self.updates.append(kwargs)

    class FakeClient:
        databases = Databases()
        pages = Pages()

    calendar = NotionEditorialCalendar(Settings(notion_api_key="key", notion_programme_content_db="db"))
    calendar.client = FakeClient()
    result = calendar.sync_published_metrics([{"url": "https://www.instagram.com/reel/ours/", "views": 1200, "likes": 30, "comments": 2}], "apify")
    assert result == {"source": "apify", "reels_observed": 1, "updated": 1, "skipped": 0}
    assert FakeClient.pages.updates[0]["properties"] == {"Vues IG": {"number": 1200}, "Likes IG": {"number": 30}, "Commentaires IG": {"number": 2}}
