import sys
from utils.logger import log_info, log_success, log_error, log_phase, console
from phase1_scraping.scraper import scrape_profile, download_reel
from phase2_analysis.gemini_analyzer import analyze_reel
from phase2_analysis.claude_adapter import adapt_to_angellos
from phase3_notion.notion_pusher import push_to_notion


async def run_pipeline(inputs: list):
    """
    inputs : liste d'URLs — profil (/reels/) ou Reel individuel (/reel/CODE/).
    """
    import asyncio
    console.rule("[bold]Pipeline Angellos[/bold]")

    # Phase 1 : collecte de tous les Reels
    log_phase(1, "Scraping Instagram")
    reels = []
    for url in inputs:
        if "/reel/" in url:
            r = await download_reel(url)
            if r:
                reels.append(r)
        else:
            reels.extend(await scrape_profile(url))

    if not reels:
        log_error("Aucun Reel récupéré.")
        return

    log_success(f"{len(reels)} Reel(s) prêt(s) pour l'analyse")

    # Phases 2 & 3 : analyse + push Notion
    for i, reel in enumerate(reels, 1):
        console.rule(f"Reel {i}/{len(reels)} — {reel['shortcode']}")

        log_phase(2, "Analyse IA")
        try:
            gemini_analysis = analyze_reel(reel["local_path"])
        except Exception as e:
            log_error(f"Gemini : {e}")
            continue

        try:
            claude_script = adapt_to_angellos(gemini_analysis, reel["account"])
        except Exception as e:
            log_error(f"Claude: {e}")
            continue

        log_phase(3, "Push Notion")
        page_url = push_to_notion(reel, gemini_analysis, claude_script)
        if page_url:
            log_success(f"Notion : {page_url}")

    console.rule("[bold green]Pipeline done[/bold green]")


if __name__ == "__main__":
    import asyncio
    if len(sys.argv) < 2:
        print("Usage : python main.py <url_profil_ou_reel> [url2 ...]")
        print("Ex.   : python main.py https://www.instagram.com/ninon.ia_officiel/reels/")
        sys.exit(1)
    asyncio.run(run_pipeline(sys.argv[1:]))
