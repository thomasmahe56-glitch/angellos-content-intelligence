from dotenv import load_dotenv
import os

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
APIFY_API_KEY = os.getenv("APIFY_API_KEY", "")
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_ACCOUNT_ID = os.getenv("INSTAGRAM_ACCOUNT_ID", "me")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
NOTION_ANGELLOS_CONTEXT_DB = os.getenv("NOTION_ANGELLOS_CONTEXT_DB", "")
NOTION_ANGELLOS_RESULTS_DB = os.getenv("NOTION_ANGELLOS_RESULTS_DB", "")

DOWNLOADS_DIR = os.getenv("DOWNLOADS_DIR", "./downloads")
MAX_REELS_PER_ACCOUNT = int(os.getenv("MAX_REELS_PER_ACCOUNT", 10))
VIDEO_DOWNLOAD_TIMEOUT_SECONDS = int(os.getenv("VIDEO_DOWNLOAD_TIMEOUT_SECONDS", 90))
GEMINI_ANALYSIS_TIMEOUT_SECONDS = int(os.getenv("GEMINI_ANALYSIS_TIMEOUT_SECONDS", 420))

ANGELLOS_NICHE_CONTEXT = """
You are a content expert for Angellos — an AI setting agent that automates prospect qualification in Instagram and WhatsApp DMs.
Target audience: anglophone coaches, infopreneurs, and content creators selling $500–$5000+ offers via sales calls.
Tone: authentic founder, direct, no corporate speak. Write as someone who has been in the trenches building this.
Language: English only.
Goal: generate Reel content that attracts beta testers and future paying clients for Angellos.
Key angles: time wasted qualifying leads manually, DMs that don't convert, missing calls because of cold prospects, automation that feels human, closing high-ticket without grinding every DM.
Reel format: hook on the pain (manual DM grind / missed deals) → agitate → Angellos solution → social proof / results → CTA to DM or join beta.
"""
