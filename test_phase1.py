"""Test phase 1 : profil public ou URL de Reel direct."""
import asyncio, sys
from utils.logger import log_phase, log_success, log_error, console
from phase1_scraping.scraper import scrape_profile, download_reel

URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.instagram.com/ninon.ia_officiel/reels/"

async def main():
    if "/reel/" in URL:
        log_phase(1, f"Téléchargement Reel direct")
        reel = await download_reel(URL)
        reels = [reel] if reel else []
    else:
        log_phase(1, f"Listing + téléchargement profil")
        reels = await scrape_profile(URL)

    if not reels:
        log_error("Aucun Reel récupéré.")
        return

    log_success(f"{len(reels)} Reel(s) prêt(s) :")
    for r in reels:
        console.print(f"  • [bold]{r['shortcode']}[/bold] @{r['account']} → {r['local_path']}")

asyncio.run(main())
