"""
Scraper Instagram via Playwright Chromium bundled.
Download strategy (in order):
  1. ctx.request.get() — Playwright's own HTTP stack, reuses browser session/cookies/TLS.
  2. yt-dlp           — handles signed CDN URLs and all Instagram quirks.
"""
import asyncio
import base64
import os
import re
from pathlib import Path
from typing import Optional

import httpx
from playwright.async_api import async_playwright, Page, BrowserContext

from config import (
    DOWNLOADS_DIR,
    INSTAGRAM_COOKIES_B64,
    INSTAGRAM_COOKIES_FILE,
    MAX_REELS_PER_ACCOUNT,
)
from utils.logger import log_info, log_success, log_error


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

async def scrape_profile(profile_url: str) -> list:
    m = re.search(r"instagram\.com/([^/?#]+)", profile_url)
    account = m.group(1) if m else None
    log_info(f"Listing Reels depuis {profile_url}")
    reel_urls = await _list_reel_urls(profile_url)
    if not reel_urls:
        log_error("Aucun Reel trouvé sur ce profil.")
        return []
    log_success(f"{len(reel_urls)} Reel(s) trouvé(s)")
    return await _download_all(reel_urls, account=account)


async def download_reel(url: str) -> Optional[dict]:
    m = re.search(r"instagram\.com/([^/?#]+)/(?:reel|p)/", url)
    account = m.group(1) if m else None
    results = await _download_all([url], account=account)
    return results[0] if results else None


# ──────────────────────────────────────────────────────────────────────────────
# Profile listing
# ──────────────────────────────────────────────────────────────────────────────

async def _list_reel_urls(profile_url: str) -> list:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="fr-FR",
        )
        page = await ctx.new_page()
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        await _dismiss_cookie_dialog(page)
        await asyncio.sleep(3)
        for _ in range(4):
            await page.keyboard.press("End")
            await asyncio.sleep(2)
        hrefs = await page.eval_on_selector_all(
            'a[href*="/reel/"]',
            "els => [...new Set(els.map(e => e.href))]",
        )
        await browser.close()

    urls = []
    seen = set()
    for href in hrefs:
        sc = _extract_shortcode(href)
        if sc and sc not in seen:
            seen.add(sc)
            urls.append(f"https://www.instagram.com/reel/{sc}/")
            if len(urls) >= MAX_REELS_PER_ACCOUNT:
                break
    return urls


# ──────────────────────────────────────────────────────────────────────────────
# Download
# ──────────────────────────────────────────────────────────────────────────────

async def _download_all(urls: list, account: Optional[str] = None) -> list:
    results = []
    for url in urls:
        result = await _download_one(url, account=account)
        if result:
            results.append(result)
    return results


async def _download_one(url: str, account: Optional[str] = None) -> Optional[dict]:
    shortcode = _extract_shortcode(url)
    if not shortcode:
        log_error(f"Unsupported Instagram URL (expected /reel/ or /p/): {url}")
        return None

    log_info(f"Téléchargement de {shortcode}...")

    video_url: Optional[str] = None
    caption_originale: Optional[str] = None
    output_path: Optional[Path] = None
    downloaded = False

    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/16.0 Mobile/15E148 Safari/604.1"
                )
            )
            cookie_path = _get_instagram_cookie_file()
            if cookie_path:
                await _add_instagram_cookies_to_context(ctx, cookie_path)
            page = await ctx.new_page()

            # Capture CDN video URLs from network responses as fallback
            captured_video_urls: list[str] = []

            def _on_response(response):
                rurl = response.url
                ct = response.headers.get("content-type", "")
                if rurl.startswith(("http://", "https://")) and (
                    "video" in ct or ".mp4" in rurl
                ):
                    captured_video_urls.append(rurl)

            page.on("response", _on_response)

            log_info(f"{shortcode}: opening Instagram page")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await _dismiss_cookie_dialog(page)
            await asyncio.sleep(2)

            log_info(f"{shortcode}: extracting video URL")
            video_url = await _extract_http_video_url(page)
            if not video_url:
                try:
                    await page.locator("video").first.evaluate(
                        "video => video.play().catch(() => {})", timeout=5000
                    )
                    await asyncio.sleep(3)
                    video_url = await _extract_http_video_url(page)
                except Exception:
                    pass
            if not video_url and captured_video_urls:
                video_url = captured_video_urls[0]
                log_info(f"{shortcode}: using network-intercepted video URL")

            if not account:
                account = await _extract_account(page) or shortcode
            caption_originale = await _extract_caption(page)

            # Build output path now that we have account
            output_dir = Path(DOWNLOADS_DIR) / account
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"{shortcode}.mp4"

            # Clear stale file (<10 KB means a previous bad download)
            if output_path.exists() and output_path.stat().st_size < 10_000:
                log_info(f"{shortcode}: removing stale file ({output_path.stat().st_size} bytes)")
                output_path.unlink()

            if output_path.exists() and output_path.stat().st_size >= 10_000:
                log_info(
                    f"{shortcode}: already downloaded "
                    f"({output_path.stat().st_size // 1024} KB) — skipping"
                )
                downloaded = True
            elif video_url:
                log_info(f"{shortcode}: video_url={video_url[:120]}")
                # Strategy 1: Playwright ctx.request (reuses browser session & TLS)
                downloaded = await _playwright_ctx_download(
                    ctx, video_url, output_path, shortcode
                )
            else:
                log_error(f"{shortcode}: no video URL found in page or network — will try yt-dlp")
                downloaded = False

        finally:
            if browser:
                await browser.close()

    # Strategy 2: yt-dlp (handles signed CDN URLs, Instagram quirks)
    if not downloaded and output_path is not None:
        log_info(f"{shortcode}: trying yt-dlp fallback")
        downloaded = await _ytdlp_download(url, output_path)

    if not downloaded:
        log_error(f"{shortcode}: all download strategies failed")
        return None

    return {
        "url": url,
        "shortcode": shortcode,
        "account": account or shortcode,
        "local_path": str(output_path),
        "caption_originale": caption_originale,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Download strategies
# ──────────────────────────────────────────────────────────────────────────────

async def _playwright_ctx_download(
    ctx: BrowserContext,
    video_url: str,
    dest: Path,
    shortcode: str,
) -> bool:
    """
    Download via Playwright's APIRequestContext.
    Uses the browser's cookie jar and TLS stack — avoids CDN signed-URL rejection.
    """
    try:
        resp = await ctx.request.get(
            video_url,
            headers={
                "Accept": "video/mp4,video/*;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.instagram.com/",
            },
            timeout=120_000,
        )
        status = resp.status
        content_type = resp.headers.get("content-type", "")
        content_length = resp.headers.get("content-length", "unknown")
        log_info(
            f"{shortcode}: ctx.request → "
            f"status={status} content-type={content_type} "
            f"content-length={content_length}"
        )

        body = await resp.body()
        if len(body) < 10_000:
            preview = body[:300]
            try:
                preview_text = preview.decode("utf-8", errors="replace")
            except Exception:
                preview_text = repr(preview)
            log_error(
                f"{shortcode}: ctx.request returned only {len(body)} bytes — "
                f"preview: {preview_text}"
            )
            return False

        dest.write_bytes(body)
        log_success(
            f"{shortcode}: ctx.request downloaded {len(body) // 1024} KB → {dest.name}"
        )
        return True

    except Exception as exc:
        log_error(f"{shortcode}: ctx.request.get failed: {exc}")
        return False


async def _ytdlp_download(url: str, dest: Path) -> bool:
    """
    Download via yt-dlp as final fallback.
    Runs in a thread executor (yt-dlp is synchronous).
    """
    import yt_dlp  # already in requirements.txt

    shortcode = dest.stem
    log_info(f"{shortcode}: yt-dlp starting")

    def _run() -> bool:
        ydl_opts = _build_ytdlp_opts(dest, _get_instagram_cookie_file())
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception as exc:
            log_error(f"{shortcode}: yt-dlp exception: {exc}")
            return False

        # yt-dlp sometimes writes <name>.mp4 even when outtmpl = <name> (no ext)
        if not dest.exists() or dest.stat().st_size < 10_000:
            for ext in ("mp4", "webm", "mkv", "m4a"):
                candidate = dest.with_suffix(f".{ext}")
                if candidate.exists() and candidate != dest and candidate.stat().st_size > 10_000:
                    candidate.rename(dest)
                    break

        size = dest.stat().st_size if dest.exists() else 0
        if size < 10_000:
            log_error(f"{shortcode}: yt-dlp produced only {size} bytes")
            return False

        log_success(f"{shortcode}: yt-dlp downloaded {size // 1024} KB")
        return True

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_instagram_cookie_file() -> Optional[str]:
    """
    Decode the configured Instagram Netscape cookies file if present.
    Never logs or returns cookie contents; only returns an existing non-empty path.
    """
    if INSTAGRAM_COOKIES_B64:
        try:
            cookie_bytes = base64.b64decode(INSTAGRAM_COOKIES_B64, validate=True)
            cookie_path = Path(INSTAGRAM_COOKIES_FILE)
            cookie_path.parent.mkdir(parents=True, exist_ok=True)
            cookie_path.write_bytes(cookie_bytes)
            try:
                os.chmod(cookie_path, 0o600)
            except OSError:
                pass
        except Exception as exc:
            log_error(f"Instagram cookie file setup failed: {exc}")
            return None

    cookie_path = Path(INSTAGRAM_COOKIES_FILE)
    if cookie_path.exists() and cookie_path.stat().st_size > 0:
        return str(cookie_path)
    return None


def _build_ytdlp_opts(dest: Path, cookie_path: Optional[str] = None) -> dict:
    ydl_opts = {
        "format": "mp4/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": str(dest),
        "merge_output_format": "mp4",
        "quiet": False,
        "no_warnings": False,
        "noplaylist": True,
    }
    if cookie_path and Path(cookie_path).exists() and Path(cookie_path).stat().st_size > 0:
        ydl_opts["cookiefile"] = cookie_path
        log_info("yt-dlp using Instagram cookie file")
    return ydl_opts


async def _add_instagram_cookies_to_context(ctx: BrowserContext, cookie_path: str) -> None:
    try:
        cookies = _parse_netscape_cookies(cookie_path)
        if not cookies:
            return
        await ctx.add_cookies(cookies)
        log_info("Playwright using Instagram cookie file")
    except Exception as exc:
        log_error(f"Playwright Instagram cookie injection skipped: {exc}")


def _parse_netscape_cookies(cookie_path: str) -> list[dict]:
    cookies = []
    with open(cookie_path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("# Netscape"):
                continue
            http_only = False
            if line.startswith("#HttpOnly_"):
                http_only = True
                line = line[len("#HttpOnly_"):]
            elif line.startswith("#"):
                continue

            parts = line.split("\t")
            if len(parts) != 7:
                continue
            domain, _include_subdomains, path, secure, expires, name, value = parts
            if not name:
                continue

            cookie = {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path or "/",
                "httpOnly": http_only,
                "secure": secure.upper() == "TRUE",
            }
            try:
                expires_int = int(expires)
                if expires_int > 0:
                    cookie["expires"] = expires_int
            except ValueError:
                pass
            cookies.append(cookie)
    return cookies


async def _dismiss_cookie_dialog(page: Page):
    try:
        btn = page.get_by_role(
            "button", name=re.compile(r"autoriser|accept|allow", re.IGNORECASE)
        )
        await btn.first.click(timeout=5000)
        await asyncio.sleep(1)
    except Exception:
        pass


async def _extract_http_video_url(page: Page) -> Optional[str]:
    return await page.evaluate("""
    () => {
        const videos = [...document.querySelectorAll('video')];
        for (const video of videos) {
            const candidates = [
                video.currentSrc,
                video.src,
                ...[...video.querySelectorAll('source')].map(s => s.src),
            ].filter(Boolean);
            const src = candidates.find(v => /^https?:\\/\\//.test(v));
            if (src) return src;
        }
        return null;
    }
    """)


async def _extract_caption(page: Page) -> Optional[str]:
    try:
        text = await page.evaluate("""
        () => {
            const og = document.querySelector('meta[property="og:description"]');
            if (!og) return null;
            const content = og.getAttribute('content') || '';
            const match = content.match(/^[^:]+:\\s*(.+)$/s);
            const raw = match ? match[1] : content;
            return raw.replace(/^["«"]+|["»"]+$/g, '').trim() || null;
        }
        """)
        return text or None
    except Exception:
        return None


async def _extract_account(page: Page) -> Optional[str]:
    try:
        href = await page.eval_on_selector(
            'a[href^="/"][href$="/"]',
            "el => el.href",
        )
        match = re.search(r"instagram\.com/([^/?#]+)", href or "")
        if match:
            slug = match.group(1)
            if slug not in ("reel", "p", "explore", "stories"):
                return slug
    except Exception:
        pass
    return None


def _extract_shortcode(url: str) -> Optional[str]:
    match = re.search(r"/(?:reel|p)/([A-Za-z0-9_-]+)", url)
    return match.group(1) if match else None
