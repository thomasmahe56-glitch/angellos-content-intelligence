from __future__ import annotations

from phase1_scraping.scraper import download_reel


async def download(candidate_url: str) -> dict:
    """Uses the established browser-session downloader, then its yt-dlp fallback."""
    result = await download_reel(candidate_url)
    if not result:
        raise RuntimeError("video_download_failed")
    return result
