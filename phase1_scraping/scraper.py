"""
Scraper Instagram via Playwright Chromium bundled (pas le Chrome système).
- Pas de SingletonLock : Chromium est lancé en mode non-persistant, fresh context.
- Fonctionne sur les comptes publics sans login.
- Listing profil  : navigue /reels/, gère la dialog cookies, collecte les hrefs.
- Téléchargement  : navigue la page du Reel, intercepte l'URL CDN vidéo, télécharge via httpx.
"""
import asyncio
import re
from pathlib import Path
from typing import Optional

import httpx
from playwright.async_api import async_playwright, Page

from config import DOWNLOADS_DIR, MAX_REELS_PER_ACCOUNT
from utils.logger import log_info, log_success, log_error


# ──────────────────────────────────────────────────────────────────────────────
# API publique
# ──────────────────────────────────────────────────────────────────────────────

async def scrape_profile(profile_url: str) -> list:
    """
    Liste et télécharge les Reels d'un profil public Instagram.
    profile_url : https://www.instagram.com/<compte>/reels/
    """
    # Extrait le compte depuis l'URL : .../ninon.ia_officiel/reels/ → ninon.ia_officiel
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
    """Télécharge un Reel individuel depuis son URL directe."""
    # Extrait le compte si l'URL le contient : .../ninon.ia_officiel/reel/CODE/
    m = re.search(r"instagram\.com/([^/?#]+)/(?:reel|p)/", url)
    account = m.group(1) if m else None
    results = await _download_all([url], account=account)
    return results[0] if results else None


# ──────────────────────────────────────────────────────────────────────────────
# Listing profil
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
        await asyncio.sleep(3)          # attendre que la dialog cookies apparaisse
        await _dismiss_cookie_dialog(page)
        await asyncio.sleep(3)          # attendre le rechargement de la grille

        # Scroll pour charger plus de Reels
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
# Téléchargement
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
        log_error(f"Unsupported Instagram URL. Expected /reel/<code>/ or /p/<code>/: {url}")
        return None

    log_info(f"Téléchargement de {shortcode}...")

    async with async_playwright() as p:
        browser = None
        captured_video_urls: list[str] = []
        log_info(f"{shortcode}: launching Chromium")
        try:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context()
            page = await ctx.new_page()

            def capture_video_response(response):
                response_url = response.url
                content_type = response.headers.get("content-type", "")
                if response_url.startswith(("http://", "https://")) and (
                    "video" in content_type or ".mp4" in response_url
                ):
                    captured_video_urls.append(response_url)

            page.on("response", capture_video_response)

            log_info(f"{shortcode}: opening Instagram page")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await _dismiss_cookie_dialog(page)
            await asyncio.sleep(2)

            # Extrait le src MP4 progressif directement depuis la balise <video>
            log_info(f"{shortcode}: extracting video URL")
            video_url = await _extract_http_video_url(page)
            if not video_url:
                try:
                    await page.locator("video").first.evaluate("video => video.play().catch(() => {})", timeout=5000)
                    await asyncio.sleep(3)
                    video_url = await _extract_http_video_url(page)
                except Exception:
                    pass
            if not video_url and captured_video_urls:
                video_url = captured_video_urls[0]

            if not account:
                account = await _extract_account(page) or shortcode

            caption_originale = await _extract_caption(page)

            # Extract cookies before the browser closes so httpx can auth with CDN
            raw_cookies = await ctx.cookies()
            browser_cookies = {c["name"]: c["value"] for c in raw_cookies}
            log_info(f"{shortcode}: extracted {len(browser_cookies)} browser cookies for CDN download")
        finally:
            if browser:
                await browser.close()

    if not video_url:
        log_error(f"No playable HTTP video URL found for {shortcode}")
        return None

    log_info(f"{shortcode}: video_url={video_url[:80]}...")

    output_dir = Path(DOWNLOADS_DIR) / account
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{shortcode}.mp4"

    if output_path.exists():
        existing_size = output_path.stat().st_size
        if existing_size > 10000:
            log_info(f"Déjà téléchargé : {output_path.name} ({existing_size // 1024} KB)")
        else:
            log_info(f"Existing file too small ({existing_size} bytes) — re-downloading")
            output_path.unlink()
            await _stream_download(video_url, output_path, cookies=browser_cookies)
    else:
        log_info(f"{shortcode}: downloading CDN video")
        await _stream_download(video_url, output_path, cookies=browser_cookies)

    final_size = output_path.stat().st_size if output_path.exists() else 0
    if final_size < 10000:
        log_error(f"{shortcode}: downloaded file is too small ({final_size} bytes) — likely CDN auth failure")
        return None

    return {
        "url": url,
        "shortcode": shortcode,
        "account": account,
        "local_path": str(output_path),
        "caption_originale": caption_originale,
    }


async def _stream_download(video_url: str, dest: Path, cookies: Optional[dict] = None):
    """Télécharge le flux vidéo CDN vers un fichier local."""
    if not video_url.startswith(("http://", "https://")):
        raise ValueError(f"Invalid video URL extracted from Instagram: {video_url[:40]}")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
        ),
        "Referer": "https://www.instagram.com/",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=120, headers=headers, cookies=cookies or {}
    ) as client:
        async with client.stream("GET", video_url) as r:
            r.raise_for_status()
            written = 0
            with open(dest, "wb") as f:
                async for chunk in r.aiter_bytes(32768):
                    f.write(chunk)
                    written += len(chunk)
    log_success(f"Téléchargé : {dest.name} ({written // 1024} KB)")


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

async def _dismiss_cookie_dialog(page: Page):
    """Accepte la dialog cookies Instagram si elle apparaît."""
    try:
        btn = page.get_by_role("button", name=re.compile(r"autoriser|accept|allow", re.IGNORECASE))
        await btn.first.click(timeout=5000)
        await asyncio.sleep(1)
    except Exception:
        pass  # dialog absente ou déjà fermée


async def _extract_http_video_url(page: Page) -> Optional[str]:
    """Return the first HTTP(S) video source exposed by the page DOM."""
    return await page.evaluate("""
    () => {
        const videos = [...document.querySelectorAll('video')];
        for (const video of videos) {
            const candidates = [
                video.currentSrc,
                video.src,
                ...[...video.querySelectorAll('source')].map(source => source.src),
            ].filter(Boolean);
            const src = candidates.find(value => /^https?:\\/\\//.test(value));
            if (src) return src;
        }
        return null;
    }
    """)


async def _extract_caption(page: Page) -> Optional[str]:
    """Extrait la caption Instagram depuis la meta og:description."""
    try:
        text = await page.evaluate("""
        () => {
            const og = document.querySelector('meta[property="og:description"]');
            if (!og) return null;
            const content = og.getAttribute('content') || '';
            // Instagram format: "X likes, Y comments - @account: "caption text""
            const match = content.match(/^[^:]+:\\s*(.+)$/s);
            const raw = match ? match[1] : content;
            // Strip wrapping curly quotes if any
            return raw.replace(/^[“«"]+|[”»"]+$/g, '').trim() || null;
        }
        """)
        return text or None
    except Exception:
        return None


async def _extract_account(page: Page) -> Optional[str]:
    """Extrait le @username depuis les liens de la page."""
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
