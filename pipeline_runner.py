"""
Wrapper async du pipeline complet.
Émet des événements SSE compatibles avec le dashboard React (dashboard/src/routes/index.tsx).
"""
import asyncio
import json
from typing import Callable


def _format_script(claude_result: dict) -> str:
    """Converts Claude JSON dict into a readable multiline string for the dashboard."""
    lines = []
    script = claude_result.get("script", {})

    hook = script.get("hook") or claude_result.get("raw_response", "")
    dev = script.get("developpement", [])
    cta = script.get("cta", "")

    if hook:
        lines += ["🪝 HOOK (0-3s)", hook, ""]
    if dev:
        lines += ["📝 DÉVELOPPEMENT"]
        for step in dev:
            lines.append(f"• {step}")
        lines.append("")
    if cta:
        lines += ["📣 CTA", cta, ""]

    tournage = claude_result.get("indications_tournage", {})
    if tournage:
        lines.append("🎬 TOURNAGE")
        if tournage.get("format_camera"):
            lines.append(f"Format : {tournage['format_camera']}")
        if tournage.get("décor_recommandé"):
            lines.append(f"Décor : {tournage['décor_recommandé']}")
        if tournage.get("rythme"):
            lines.append(f"Rythme : {tournage['rythme']}")
        if tournage.get("durée_cible"):
            lines.append(f"Durée : {tournage['durée_cible']}")
        lines.append("")

    hashtags = claude_result.get("hashtags_suggeres", [])
    if hashtags:
        lines += ["🏷️ HASHTAGS", " ".join(f"#{h.lstrip('#')}" for h in hashtags), ""]

    why = claude_result.get("pourquoi_ca_marche", "")
    if why:
        lines += ["💡 POURQUOI ÇA MARCHE", why]

    return "\n".join(lines).strip()


async def run(url: str, emit: Callable):
    """
    Lance les 3 phases du pipeline et émet des événements de progression.
    emit(event: dict) pousse un événement vers le flux SSE.
    """
    loop = asyncio.get_event_loop()

    async def progress(pct: int):
        await emit({"progress": pct})

    async def step(key: str, status: str):
        await emit({"step": key, "step_status": status})

    # ── PHASE 1 — Download ────────────────────────────────────────────────────
    await step("download", "running")
    await progress(5)
    try:
        from phase1_scraping.scraper import download_reel
        reel = await download_reel(url)
        if not reel:
            await emit({"status": "error", "error": "Impossible de télécharger ce Reel. Vérifiez que le compte est public."})
            return
    except Exception as e:
        await emit({"status": "error", "error": f"Téléchargement : {e}"})
        return

    await step("download", "done")
    await progress(33)

    # ── PHASE 2a — Gemini ─────────────────────────────────────────────────────
    await step("analyze", "running")
    await progress(38)
    try:
        from phase2_analysis.gemini_analyzer import analyze_reel
        caption_originale = reel.get("caption_originale")
        gemini = await loop.run_in_executor(None, analyze_reel, reel["local_path"], caption_originale)
    except Exception as e:
        await emit({"status": "error", "error": f"Gemini : {e}"})
        return

    await progress(55)

    # ── PHASE 2b — Claude ─────────────────────────────────────────────────────
    try:
        from phase2_analysis.claude_adapter import adapt_to_angellos
        claude_result = await loop.run_in_executor(None, adapt_to_angellos, gemini, reel["account"])
    except Exception as e:
        await emit({"status": "error", "error": f"Claude : {e}"})
        return

    await step("analyze", "done")
    await progress(66)

    # ── PHASE 3 — Notion ──────────────────────────────────────────────────────
    await step("push", "running")
    await progress(71)
    try:
        from phase3_notion.notion_pusher import push_to_notion
        notion_url = await loop.run_in_executor(None, push_to_notion, reel, gemini, claude_result)
    except Exception as e:
        await emit({"status": "error", "error": f"Notion : {e}"})
        return

    await step("push", "done")
    await progress(100)

    script_str = _format_script(claude_result)
    hook_text = (claude_result.get("script") or {}).get("hook") or gemini.get("hook", "")
    format_text = gemini.get("format", "") or (claude_result.get("indications_tournage") or {}).get("format_camera", "")

    await emit({
        "status": "done",
        "result": {
            "script": script_str,
            "hook": hook_text,
            "format": format_text,
            "notion_url": notion_url,
            "caption_originale": gemini.get("caption_originale") or "",
            "caption_angellos": claude_result.get("caption_angellos") or "",
        },
    })
