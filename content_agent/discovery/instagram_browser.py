from __future__ import annotations

import asyncio
import base64
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from playwright.async_api import BrowserContext, Page, async_playwright

from content_agent.config import Settings
from content_agent.discovery.metrics import parse_compact_number
from content_agent.models.schemas import Candidate


class InstagramHumanActionRequired(RuntimeError):
    pass


class InstagramBrowser:
    """A conservative authenticated browser scout; it stops on any security challenge."""
    def __init__(self, settings: Settings):
        self.settings = settings

    async def login_interactively(self) -> None:
        state_path = Path(self.settings.instagram_session_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded")
            input("Complete Instagram login in the browser (including any challenge), then press Enter here to save the session: ")
            await context.storage_state(path=str(state_path))
            await browser.close()

    async def check_authentication(self) -> None:
        if not Path(self.settings.instagram_session_path).exists() and not self._cookie_path():
            raise InstagramHumanActionRequired("instagram_session_missing")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await self._new_context(browser)
            page = await context.new_page()
            await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=45_000)
            await self._raise_if_challenge(page)
            logged_in = await page.locator('a[href^="/direct/"]').count() > 0 or await page.locator('svg[aria-label="Home"]').count() > 0
            await browser.close()
        if not logged_in:
            raise InstagramHumanActionRequired("instagram_authentication_required")

    async def discover(self, limit: int) -> list[Candidate]:
        deadline = time.monotonic() + (self.settings.scout_max_runtime_minutes * 60)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await self._new_context(browser, viewport={"width": 1280, "height": 900})
            candidates: list[Candidate] = []
            try:
                for url, method in self._sources():
                    if time.monotonic() >= deadline:
                        break
                    page = await context.new_page()
                    await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                    await self._raise_if_challenge(page)
                    candidates.extend(await self._collect_page(page, method, limit - len(candidates), deadline))
                    await page.close()
                    if len(candidates) >= limit:
                        break
            finally:
                await browser.close()
        return list({c.key: c for c in candidates if c.key}.values())[:limit]

    def _sources(self):
        # The authenticated Reels feed is the primary discovery surface.  It
        # currently exposes no per-card anchors, but updates its URL while the
        # user scrolls; _collect_page records those URLs directly.
        sources = [("https://www.instagram.com/reels/", "recommendations"), ("https://www.instagram.com/explore/", "explore")]
        sources.extend((f"https://www.instagram.com/{account}/reels/", "dream100") for account in self.settings.dream100_accounts)
        sources.extend((f"https://www.instagram.com/{account}/reels/", "seed_account") for account in self.settings.seed_accounts)
        return sources

    async def _collect_page(self, page: Page, method: str, remaining: int, deadline: float) -> list[Candidate]:
        found: dict[str, Candidate] = {}
        for _ in range(self.settings.scout_scroll_limit):
            if time.monotonic() >= deadline:
                break
            # /reels/ redirects to the active Reel but Instagram does not put
            # its cards in <a> elements.  The current URL is therefore a
            # first-class discovery signal alongside normal card links.
            current = self._candidate_from_url(page.url, method)
            if current:
                found.setdefault(current.shortcode, current)
                if len(found) >= remaining:
                    return list(found.values())
            hrefs = await page.eval_on_selector_all('a[href*="/reel/"], a[href*="/p/"]', "els => [...new Set(els.map(e => e.href))]")
            for href in hrefs:
                candidate = self._candidate_from_url(href, method)
                if not candidate:
                    continue
                found.setdefault(candidate.shortcode, candidate)
                if len(found) >= remaining:
                    return list(found.values())
            await page.mouse.wheel(0, 1400)
            await asyncio.sleep(1.2)
        return list(found.values())

    async def enrich(self, candidate: Candidate, follower_cache: dict) -> Candidate:
        """Visit the Reel and creator only when necessary; failures preserve known metrics."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await self._new_context(browser)
            page = await context.new_page()
            try:
                await page.goto(candidate.source_url, wait_until="domcontentloaded", timeout=45_000)
                await self._raise_if_challenge(page)
                candidate.media_is_video = (
                    await page.locator("video").count() > 0
                    or await page.locator('meta[property="og:video"]').count() > 0
                )
                text = await page.locator("body").inner_text(timeout=10_000)
                candidate.views = self._metric_after(text, "views") or candidate.views
                candidate.likes = self._metric_after(text, "likes") or candidate.likes
                candidate.comments = self._metric_after(text, "comments") or candidate.comments
                description = await page.locator('meta[property="og:description"]').get_attribute("content") or ""
                candidate.caption_preview = description[:1000]
                candidate.source_published_at = self._published_date(description) or candidate.source_published_at
                creator = await self._creator(page) or candidate.creator_username
                candidate.creator_username = creator
                cached = follower_cache.get(creator, {})
                fetched_at = cached.get("fetched_at", "")
                valid = fetched_at and datetime.fromisoformat(fetched_at) > datetime.now(timezone.utc) - timedelta(hours=self.settings.follower_cache_ttl_hours)
                if valid:
                    candidate.followers = cached.get("followers")
                elif creator:
                    candidate.followers = await self._followers(context, creator)
                    follower_cache[creator] = {"followers": candidate.followers, "fetched_at": datetime.now(timezone.utc).isoformat()}
            finally:
                await browser.close()
        return candidate

    @staticmethod
    def _candidate_from_url(href: str, method: str) -> Optional[Candidate]:
        match = re.search(r"/(reel|reels|p)/([A-Za-z0-9_-]+)", href)
        if not match:
            return None
        kind, shortcode = match.groups()
        # Instagram's feed uses /reels/<shortcode>/ while canonical share URLs
        # use the singular /reel/<shortcode>/.
        kind = "reel" if kind == "reels" else kind
        return Candidate(
            source_url=f"https://www.instagram.com/{kind}/{shortcode}/",
            shortcode=shortcode,
            discovery_method=method,
        )

    async def _followers(self, context: BrowserContext, creator: str):
        page = await context.new_page()
        try:
            await page.goto(f"https://www.instagram.com/{creator}/", wait_until="domcontentloaded", timeout=45_000)
            await self._raise_if_challenge(page)
            text = await page.locator("body").inner_text(timeout=10_000)
            match = re.search(r"([0-9][0-9.,\s]*[KM]?)\s+follower(?:s)?", text, re.I)
            return parse_compact_number(match.group(1)) if match else None
        finally:
            await page.close()

    @staticmethod
    async def _creator(page: Page) -> str:
        links = await page.eval_on_selector_all('a[href^="/"]', "els => els.map(e => e.getAttribute('href'))")
        return InstagramBrowser._creator_from_hrefs(links)

    @staticmethod
    def _creator_from_hrefs(links) -> str:
        # On Reel detail pages, the creator link is /username/reels/ while
        # account-navigation links such as /angellos.ai/ occur earlier.
        for href in links:
            match = re.fullmatch(r"/([A-Za-z0-9._]+)/reels/", href or "")
            if match and match.group(1) != "reels":
                return match.group(1)
        for href in links:
            if href and re.fullmatch(r"/[A-Za-z0-9._]+/", href) and href.strip("/") not in {"reel", "reels", "explore", "accounts"}:
                return href.strip("/")
        return ""

    @staticmethod
    def _metric_after(text: str, label: str):
        pattern = rf"([0-9][0-9.,\s]*[KM]?)\s+{label}"
        match = re.search(pattern, text, re.I)
        return parse_compact_number(match.group(1)) if match else None

    @staticmethod
    def _published_date(description: str) -> str:
        """Extract Instagram's public English OG date when it is available."""
        match = re.search(r"\bon ([A-Z][a-z]+ \d{1,2}, \d{4})\b", description)
        if not match:
            return ""
        try:
            return datetime.strptime(match.group(1), "%B %d, %Y").date().isoformat()
        except ValueError:
            return ""

    @staticmethod
    async def _raise_if_challenge(page: Page) -> None:
        text = (await page.locator("body").inner_text(timeout=10_000)).lower()
        terms = ("challenge_required", "security check", "confirm it\u2019s you", "confirm it's you", "captcha", "two-factor")
        if any(term in text for term in terms) or "/challenge/" in page.url:
            raise InstagramHumanActionRequired("instagram_authentication_challenge")

    async def _new_context(self, browser, **options) -> BrowserContext:
        state_path = Path(self.settings.instagram_session_path)
        if state_path.exists():
            options["storage_state"] = str(state_path)
            return await browser.new_context(**options)
        context = await browser.new_context(**options)
        cookie_path = self._cookie_path()
        if cookie_path:
            await context.add_cookies(self._parse_netscape_cookies(cookie_path))
        return context

    def _cookie_path(self) -> str:
        if self.settings.instagram_cookies_b64:
            try:
                path = Path(self.settings.instagram_cookies_file)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(base64.b64decode(self.settings.instagram_cookies_b64, validate=True))
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
            except Exception:
                return ""
        path = Path(self.settings.instagram_cookies_file)
        return str(path) if path.exists() and path.stat().st_size else ""

    @staticmethod
    def _parse_netscape_cookies(cookie_path: str) -> list[dict]:
        cookies = []
        for line in Path(cookie_path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            http_only = line.startswith("#HttpOnly_")
            if http_only:
                line = line[len("#HttpOnly_"):]
            elif not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 7:
                continue
            domain, _, path, secure, expires, name, value = parts
            cookie = {"name": name, "value": value, "domain": domain, "path": path or "/", "httpOnly": http_only, "secure": secure.upper() == "TRUE"}
            try:
                expiry = int(expires)
                # Netscape exports occasionally contain malformed millisecond or
                # arbitrarily large expiry values. Playwright accepts seconds
                # only; keep such an authenticated cookie as a session cookie.
                if 0 < expiry <= 253_402_300_799:
                    cookie["expires"] = expiry
            except ValueError:
                pass
            cookies.append(cookie)
        return cookies
