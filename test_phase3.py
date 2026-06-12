"""Test phase 3 : reprend les outputs de phase 2 et pousse dans Notion."""
from utils.logger import log_phase, log_success, log_error
from phase2_analysis.gemini_analyzer import analyze_reel
from phase2_analysis.claude_adapter import adapt_to_ttr
from phase3_notion.notion_pusher import push_to_notion

REEL = {
    "url": "https://www.instagram.com/reel/DYAj5wgOHvg/",
    "shortcode": "DYAj5wgOHvg",
    "account": "ninon.ia_officiel",
    "local_path": "downloads/ninon.ia_officiel/DYAj5wgOHvg.mp4",
}

log_phase(2, "Analyse Gemini + Claude")
gemini = analyze_reel(REEL["local_path"])
claude_script = adapt_to_ttr(gemini, REEL["account"])

log_phase(3, "Push Notion")
page_url = push_to_notion(REEL, gemini, claude_script)

if page_url:
    log_success(f"Page créée : {page_url}")
else:
    log_error("Échec push Notion")
