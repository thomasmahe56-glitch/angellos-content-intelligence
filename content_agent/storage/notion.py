from __future__ import annotations

from datetime import date
from typing import Any, Optional

from notion_client import Client

from content_agent.config import Settings
from content_agent.models.schemas import Candidate


class NotionEditorialCalendar:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = Client(auth=settings.notion_api_key) if settings.notion_api_key else None

    def _require(self):
        if not self.client or not self.settings.notion_programme_content_db:
            raise RuntimeError("NOTION_API_KEY and NOTION_PROGRAMME_CONTENT_DB are required")

    def recent_content(self) -> list[dict[str, Any]]:
        self._require()
        content = []
        cursor = None
        while True:
            kwargs = {"database_id": self.settings.notion_programme_content_db, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            response = self.client.databases.query(**kwargs)
            for page in response.get("results", []):
                p = page.get("properties", {})
                content.append({"title": _text(p.get("Name", {})), "date": ((p.get("Date of Publish", {}).get("date") or {}).get("start") or ""), "status": ((p.get("IG Status", {}).get("select") or {}).get("name") or ""), "source_url": p.get("Source URL", {}).get("url") or ""})
            if not response.get("has_more"):
                return content
            cursor = response.get("next_cursor")

    def performance_rows(self) -> list[dict[str, Any]]:
        """Read published Angellos signals without guessing missing metrics."""
        self._require()
        rows: list[dict[str, Any]] = []
        cursor = None
        while True:
            kwargs = {"database_id": self.settings.notion_programme_content_db, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            response = self.client.databases.query(**kwargs)
            for page in response.get("results", []):
                p = page.get("properties", {})
                status = ((p.get("IG Status", {}).get("select") or {}).get("name") or "")
                views = (p.get("Vues IG", {}).get("number"))
                if status == "Published" or views is not None:
                    rows.append({
                        "title": _text(p.get("Name", {})),
                        "status": status,
                        "date": ((p.get("Date of Publish", {}).get("date") or {}).get("start") or ""),
                        "views": views,
                        "likes": p.get("Likes IG", {}).get("number"),
                        "comments": p.get("Commentaires IG", {}).get("number"),
                        "saves": p.get("Saves IG", {}).get("number"),
                        "shares": p.get("Partages IG", {}).get("number"),
                        "hook": _rich_text_value(p.get("Hook analysé", {})) or _rich_text_value(p.get("Why Selected", {})),
                        "content_type": ((p.get("Content Type", {}).get("select") or {}).get("name") or ""),
                        "ig_content_type": ((p.get("IG Content Type", {}).get("select") or {}).get("name") or ""),
                    })
            if not response.get("has_more"):
                return rows
            cursor = response.get("next_cursor")

    def sync_published_metrics(self, reels: list[dict[str, Any]], source: str) -> dict[str, Any]:
        """Write observed own-account metrics into this editorial calendar only.

        A post is matched by an existing Instagram URL when available, otherwise
        by the closest already-Published editorial date in a conservative window.
        Planned ideas are never promoted or updated as published by this method.
        """
        self._require()
        self._ensure_performance_properties()
        pages = self._all_pages()
        used_page_ids: set[str] = set()
        updated = 0
        for reel in reels:
            page = self._match_published_page(reel, pages, used_page_ids)
            if not page:
                continue
            properties: dict[str, Any] = {}
            values = {
                "Vues IG": reel.get("views"),
                "Likes IG": reel.get("likes"),
                "Commentaires IG": reel.get("comments"),
                "Saves IG": reel.get("saves"),
                "Reach IG": reel.get("reach"),
                "Partages IG": reel.get("shares"),
            }
            for name, value in values.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    properties[name] = {"number": value}
            if properties:
                self.client.pages.update(page_id=page["id"], properties=properties)
                used_page_ids.add(page["id"])
                updated += 1
        return {"source": source, "reels_observed": len(reels), "updated": updated, "skipped": len(reels) - updated}

    def _ensure_performance_properties(self) -> None:
        stats = {"Vues IG": "number", "Saves IG": "number", "Reach IG": "number", "Likes IG": "number", "Commentaires IG": "number", "Partages IG": "number"}
        database = self.client.databases.retrieve(database_id=self.settings.notion_programme_content_db)
        existing = database.get("properties", {})
        conflicts = [name for name, expected in stats.items() if name in existing and existing[name].get("type") != expected]
        if conflicts:
            raise RuntimeError(f"Notion schema type conflict for performance properties: {', '.join(conflicts)}")
        missing = {name: {kind: {}} for name, kind in stats.items() if name not in existing}
        if missing:
            self.client.databases.update(database_id=self.settings.notion_programme_content_db, properties=missing)

    def _all_pages(self) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        cursor = None
        while True:
            kwargs = {"database_id": self.settings.notion_programme_content_db, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            response = self.client.databases.query(**kwargs)
            pages.extend(response.get("results", []))
            if not response.get("has_more"):
                return pages
            cursor = response.get("next_cursor")

    @staticmethod
    def _match_published_page(reel: dict[str, Any], pages: list[dict[str, Any]], used_page_ids: set[str]) -> Optional[dict[str, Any]]:
        reel_url = (reel.get("url") or "").rstrip("/")
        for page in pages:
            if page.get("id") in used_page_ids:
                continue
            urls = [str(prop.get("url") or "").rstrip("/") for prop in page.get("properties", {}).values() if prop.get("type") == "url"]
            if reel_url and reel_url in urls:
                return page
        reel_date = _iso_date(reel.get("timestamp") or reel.get("timestamp_raw") or reel.get("date") or "")
        if not reel_date:
            return None
        candidates = []
        for page in pages:
            if page.get("id") in used_page_ids:
                continue
            props = page.get("properties", {})
            status = ((props.get("IG Status", {}).get("select") or {}).get("name") or "").lower()
            planned = _iso_date(((props.get("Date of Publish", {}).get("date") or {}).get("start") or ""))
            if status != "published" or not planned:
                continue
            delta = (reel_date - planned).days
            if -3 <= delta <= 30:
                candidates.append((abs(delta), page))
        return min(candidates, key=lambda item: item[0])[1] if candidates else None

    def ensure_v2_properties(self) -> None:
        self._require()
        database = self.client.databases.retrieve(database_id=self.settings.notion_programme_content_db)
        existing = database.get("properties", {})
        wanted = {"Source URL": {"url": {}}, "Source Creator": {"rich_text": {}}, "Source Views": {"number": {}}, "Source Likes": {"number": {}}, "Source Comments": {"number": {}}, "Source Followers": {"number": {}}, "Viral Ratio": {"number": {"format": "number"}}, "Discovery Method": {"rich_text": {}}, "Content Intelligence Run ID": {"rich_text": {}}, "Discovered At": {"date": {}}, "Source Published At": {"date": {}}, "Why Selected": {"rich_text": {}}}
        conflicts = [name for name, spec in wanted.items() if name in existing and existing[name].get("type") != next(iter(spec))]
        if conflicts:
            raise RuntimeError(f"Notion schema type conflict for V2 properties: {', '.join(conflicts)}")
        missing = {name: schema for name, schema in wanted.items() if name not in existing}
        if missing:
            self.client.databases.update(database_id=self.settings.notion_programme_content_db, properties=missing)

    def source_page_url(self, source_url: str) -> Optional[str]:
        """Durable dedupe against the editorial calendar itself, not just agent state."""
        self._require()
        self.ensure_v2_properties()
        response = self.client.databases.query(
            database_id=self.settings.notion_programme_content_db,
            filter={"property": "Source URL", "url": {"equals": source_url}},
            page_size=1,
        )
        matches = response.get("results", [])
        return matches[0].get("url") if matches else None

    def count_run_items(self, run_id: str) -> int:
        """Count current-run pages without reading the historical state ledger."""
        self._require()
        self.ensure_v2_properties()
        response = self.client.databases.query(
            database_id=self.settings.notion_programme_content_db,
            filter={"property": "Content Intelligence Run ID", "rich_text": {"equals": run_id}},
            page_size=100,
        )
        return len(response.get("results", []))

    def create(self, candidate: Candidate, gemini: dict, adaptation: dict, dry_run: bool = False) -> Optional[str]:
        self._require()
        if dry_run:
            return None
        self.ensure_v2_properties()
        existing = self.source_page_url(candidate.source_url)
        if existing:
            return existing
        # Content Intelligence is an ideas inbox.  Scheduling belongs to ARIA
        # after a run has finished, so source analysis must never reserve a
        # publishing slot or attach a date at creation time.
        props = {"Name": {"title": [{"text": {"content": adaptation["internal_title"][:1800]}}]}, "Platform": {"select": {"name": "Instagram"}}, "IG Content Type": {"select": {"name": adaptation.get("ig_content_type") or "Tips"}}, "IG Status": {"select": {"name": "Idea"}}, "Content Type": {"select": {"name": adaptation.get("content_type") or "Hooks"}}, "Source URL": {"url": candidate.source_url}, "Source Creator": {"rich_text": _rich(candidate.creator_username)}, "Source Views": {"number": candidate.views}, "Source Likes": {"number": candidate.likes}, "Source Comments": {"number": candidate.comments}, "Source Followers": {"number": candidate.followers}, "Viral Ratio": {"number": candidate.viral_ratio}, "Discovery Method": {"rich_text": _rich(candidate.discovery_method)}, "Content Intelligence Run ID": {"rich_text": _rich(candidate.run_id)}, "Discovered At": {"date": {"start": candidate.discovered_at}}, "Why Selected": {"rich_text": _rich(adaptation.get("why_selected", ""))}}
        if candidate.source_published_at:
            props["Source Published At"] = {"date": {"start": candidate.source_published_at}}
        page = self.client.pages.create(parent={"database_id": self.settings.notion_programme_content_db}, properties=props, children=_body(candidate, gemini, adaptation))
        return page["url"]


def _rich(value: str):
    return [{"text": {"content": value[:1800]}}] if value else []


def _iso_date(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _text(prop: dict) -> str:
    key = "title" if "title" in prop else "rich_text"
    return "".join(part.get("plain_text", "") for part in prop.get(key, []))


def _rich_text_value(prop: dict) -> str:
    return "".join(part.get("plain_text", "") for part in prop.get("rich_text", []))


def _block(kind: str, text: str):
    return {"object": "block", "type": kind, kind: {"rich_text": _rich(text)}}


def _body(c: Candidate, g: dict, a: dict):
    hook = g.get("hook", {})
    hook_text = hook if isinstance(hook, str) else "\n".join(filter(None, [hook.get("spoken", ""), hook.get("on_screen_text", ""), hook.get("visual", "")]))
    structure = g.get("narrative_structure", "")
    retention = g.get("retention_mechanisms", [])
    blocks = [_block("heading_1", "Why this Reel was selected"), _block("paragraph", a.get("why_selected", "")), _block("heading_1", "Source"), _block("paragraph", f"Creator: @{c.creator_username}\nURL: {c.source_url}\nViews: {c.views}\nFollowers: {c.followers}\nViral ratio: {c.viral_ratio}\nDiscovery: {c.discovery_method}"), _block("heading_1", "Source analysis"), _block("heading_2", "Original hook"), _block("paragraph", hook_text), _block("heading_2", "Structure"), _block("paragraph", str(structure)), _block("heading_2", "Retention mechanisms"), _block("paragraph", "\n".join(retention) if isinstance(retention, list) else str(retention)), _block("heading_2", "Replicable pattern"), _block("paragraph", g.get("replicable_creative_pattern", "")), _block("heading_1", "Angellos adaptation"), _block("heading_2", "Content angle"), _block("paragraph", a.get("angellos_angle", "")), _block("heading_2", "Hook"), _block("paragraph", a.get("hook", "")), _block("heading_2", "Full script")]
    blocks.extend(_block("bulleted_list_item", line) for line in [a.get("script", {}).get("hook", ""), *a.get("script", {}).get("development", []), a.get("script", {}).get("cta", "")] if line)
    blocks += [_block("heading_1", "Shot list")]
    blocks.extend(_block("bulleted_list_item", f"{s.get('start')}–{s.get('end')}s: {s.get('shot')} | Say: {s.get('spoken')} | On-screen: {s.get('on_screen_text')} | Edit: {s.get('editing')}") for s in a.get("shot_list", []))
    filming = a.get("filming", {})
    blocks += [_block("heading_1", "What Thomas needs to film"), _block("paragraph", str(filming)), _block("heading_1", "Screen recordings required"), _block("paragraph", "\n".join(filming.get("screen_recordings_needed", []))), _block("heading_1", "B-roll"), _block("paragraph", "\n".join(str(s.get("b_roll", "")) for s in a.get("shot_list", []) if s.get("b_roll"))), _block("heading_1", "Editing instructions"), _block("paragraph", str(a.get("editing", {}))), _block("heading_1", "Caption"), _block("paragraph", a.get("caption", "")), _block("heading_1", "CTA"), _block("paragraph", a.get("cta", "")), _block("heading_1", "Why we're testing this"), _block("paragraph", a.get("why_test_this", "")), _block("heading_1", "Things to verify"), _block("paragraph", "\n".join(a.get("facts_that_must_be_verified_before_filming", [])))]
    return blocks
