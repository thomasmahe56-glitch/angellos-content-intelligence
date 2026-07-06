from dotenv import load_dotenv
import os

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
APIFY_API_KEY = os.getenv("APIFY_API_KEY", "")
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_ACCOUNT_ID = os.getenv("INSTAGRAM_ACCOUNT_ID", "me")
INSTAGRAM_COOKIES_B64 = os.getenv("INSTAGRAM_COOKIES_B64", "")
INSTAGRAM_COOKIES_FILE = os.getenv("INSTAGRAM_COOKIES_FILE", "/tmp/instagram-cookies.txt")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
NOTION_ANGELLOS_CONTEXT_DB = os.getenv("NOTION_ANGELLOS_CONTEXT_DB", "")
NOTION_ANGELLOS_RESULTS_DB = os.getenv("NOTION_ANGELLOS_RESULTS_DB", "")
NOTION_PROGRAMME_CONTENT_DB = os.getenv("NOTION_PROGRAMME_CONTENT_DB", "")

DOWNLOADS_DIR = os.getenv("DOWNLOADS_DIR", "./downloads")
MAX_REELS_PER_ACCOUNT = int(os.getenv("MAX_REELS_PER_ACCOUNT", 10))
VIDEO_DOWNLOAD_TIMEOUT_SECONDS = int(os.getenv("VIDEO_DOWNLOAD_TIMEOUT_SECONDS", 90))

# Gemini model fallback chain.
# If the primary model returns 429 quota-limit-0 (model unavailable for this key),
# the analyzer tries each fallback in order before giving up on Gemini entirely.
GEMINI_MODEL_PRIMARY = os.getenv("GEMINI_MODEL_PRIMARY", "gemini-2.0-flash")
# Fallback chain built from models actually present for this API key (verified via ListModels).
# gemini-1.5-* are NOT available for this key (404).
# gemini-2.5-flash works but requires JSON mode disabled (thinking incompatible with JSON mode in SDK 0.7.x).
_gemini_fallbacks_default = "gemini-2.0-flash-lite,gemini-2.0-flash-001,gemini-2.5-flash"
GEMINI_MODEL_FALLBACKS: list = [
    m.strip()
    for m in os.getenv("GEMINI_MODEL_FALLBACKS", _gemini_fallbacks_default).split(",")
    if m.strip()
]
# Models that have thinking enabled by default in SDK 0.7.x — cannot use response_mime_type=application/json.
GEMINI_THINKING_MODELS: set = {
    m.strip()
    for m in os.getenv(
        "GEMINI_THINKING_MODELS", "gemini-2.5-flash,gemini-2.5-pro,gemini-2.5-flash-lite"
    ).split(",")
    if m.strip()
}
# Outer asyncio timeout for the entire Gemini phase (upload + processing + generation).
# Inner daemon-thread timeouts are 180s (upload) + 120s (generation).
# Keep this larger than their sum so the inner timeouts always fire first.
GEMINI_ANALYSIS_TIMEOUT_SECONDS = int(os.getenv("GEMINI_ANALYSIS_TIMEOUT_SECONDS", 360))
CLAUDE_ADAPTATION_TIMEOUT_SECONDS = int(os.getenv("CLAUDE_ADAPTATION_TIMEOUT_SECONDS", 180))
NOTION_PUSH_TIMEOUT_SECONDS = int(os.getenv("NOTION_PUSH_TIMEOUT_SECONDS", 60))

ANGELLOS_NICHE_CONTEXT = """
You are a content expert for Angellos — an AI setting agent that automates prospect qualification in Instagram and WhatsApp DMs.
Target audience: anglophone coaches, infopreneurs, and content creators selling $500–$5000+ offers via sales calls.
Tone: authentic founder, direct, no corporate speak. Write as someone who has been in the trenches building this.
Language: English only.
Goal: generate Reel content that attracts beta testers and future paying clients for Angellos.
Key angles: time wasted qualifying leads manually, DMs that don't convert, missing calls because of cold prospects, automation that feels human, closing high-ticket without grinding every DM.
Reel format: hook on the pain (manual DM grind / missed deals) → agitate → Angellos solution → social proof / results → CTA to DM or join beta.
"""
