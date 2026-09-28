from __future__ import annotations

import argparse
import asyncio
import json
import sys

from content_agent.config import is_configured, settings
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m content_agent")
    sub = parser.add_subparsers(dest="command", required=True)
    scout = sub.add_parser("scout")
    scout.add_argument("--once", action="store_true")
    scout.add_argument("--dry-run", action="store_true")
    scout.add_argument("--json", action="store_true")
    scout.add_argument("--hermes", action="store_true", help="emit the compact Hermes event contract")
    scout.add_argument("--max-reels", type=int)
    scout.add_argument("--max-content", type=int)
    analyze = sub.add_parser("analyze")
    analyze.add_argument("instagram_reel_url")
    analyze.add_argument("--dry-run", action="store_true")
    sub.add_parser("sync-performance")
    sub.add_parser("health")
    sub.add_parser("status")
    sub.add_parser("login")
    args = parser.parse_args()
    if args.command == "login":
        from content_agent.discovery.instagram_browser import InstagramBrowser
        asyncio.run(InstagramBrowser(settings).login_interactively()); return
    if args.command == "health":
        missing = [name for name, value in {"OPENAI_API_KEY": settings.openai_api_key, "OPENAI_CONTENT_MODEL": settings.openai_content_model, "GEMINI_API_KEY": settings.gemini_api_key, "NOTION_API_KEY": settings.notion_api_key, "NOTION_PROGRAMME_CONTENT_DB": settings.notion_programme_content_db}.items() if not is_configured(value)]
        instagram_auth_configured = Path(settings.instagram_session_path).exists() or is_configured(settings.instagram_cookies_b64)
        result = {"status": "ok" if not missing else "configuration_error", "missing": missing, "instagram_session_ready": instagram_auth_configured, "durable_state_configured": is_configured(settings.notion_state_page_id), "model": settings.openai_content_model or None}
    elif args.command == "status":
        from content_agent.runner import status
        result = status()
    elif args.command == "sync-performance":
        from content_agent.runner import sync_performance
        result = asyncio.run(sync_performance())
    else:
        if args.command == "analyze":
            from content_agent.runner import analyze_reel_url
            result = asyncio.run(analyze_reel_url(args.instagram_reel_url, dry_run=args.dry_run))
        else:
            from content_agent.runner import run_daily_scout
            result = asyncio.run(run_daily_scout(dry_run=args.dry_run, max_reels=args.max_reels, max_content=args.max_content))
    if args.command == "scout" and args.hermes:
        from content_agent.integrations.hermes import event_from_report
        result = event_from_report(result)
    print(json.dumps(result, ensure_ascii=False))
    status = result.get("status")
    sys.exit(2 if result.get("human_action_required") else 3 if status == "configuration_error" else 1 if status == "partial_failure" else 0)
