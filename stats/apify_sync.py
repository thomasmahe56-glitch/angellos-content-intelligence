"""
Sync @angellos.ai stats via Apify → Notion "📊 Performance Angellos"
For each Reel: download via yt-dlp, analyze with Gemini, save to Notion.
Then cross-analysis stats × format/hook via Claude → performance_patterns.json
"""
import asyncio
import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
from typing import Optional

import anthropic
from notion_client import Client

from config import ANTHROPIC_API_KEY, APIFY_API_KEY, NOTION_API_KEY
from phase1_scraping.apify_scraper import scrape_account_reels
from phase2_analysis.gemini_analyzer import analyze_reel
from utils.logger import log_error, log_info, log_success

_GEMINI_RETRY_WAITS = [60, 120, 240]  # secondes entre les tentatives sur 429


async def _analyze_with_retry(local_path: str, caption: str) -> Optional[dict]:
    """Appelle analyze_reel() avec retry exponentiel sur quota Gemini (429/503)."""
    for attempt, wait in enumerate(_GEMINI_RETRY_WAITS + [None]):
        try:
            return analyze_reel(local_path, caption)
        except Exception as e:
            msg = str(e)
            is_quota = "429" in msg or "quota" in msg.lower() or "ResourceExhausted" in msg
            is_unavail = "503" in msg or "unavailable" in msg.lower()
            if (is_quota or is_unavail) and wait is not None:
                retry_wait = wait if is_quota else 30
                log_info(f"Gemini {'429 quota' if is_quota else '503'} — retry dans {retry_wait}s (tentative {attempt + 1}/3)...")
                await asyncio.sleep(retry_wait)
            else:
                raise
    return None

# Switch to @angellos.ai once first Reels are published
ANGELLOS_ACCOUNT = "angellos.ai"
PERF_DB_TITLE = "📊 Performance Angellos"
_PARENT_PAGE_ID = "3608275241468052b02eede76e3ee6ff"

_DIR = os.path.dirname(__file__)
_PATTERNS_FILE = os.path.abspath(os.path.join(_DIR, "..", "performance_patterns.json"))
_DB_CACHE = os.path.join(_DIR, ".perf_db_id")
_DOWNLOADS_DIR = os.path.abspath(os.path.join(_DIR, "..", "downloads", ANGELLOS_ACCOUNT))

_notion = Client(auth=NOTION_API_KEY)
_claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ── Notion schemas ─────────────────────────────────────────────────────────

PERF_DB_SCHEMA = {
    "Titre": {"title": {}},
    "URL": {"url": {}},
    "Date de publication": {"date": {}},
    "Vues": {"number": {"format": "number"}},
    "Likes": {"number": {"format": "number"}},
    "Commentaires": {"number": {"format": "number"}},
    "Hook détecté": {"rich_text": {}},
    "Hook exact": {"rich_text": {}},
    "Structure narrative": {"rich_text": {}},
    "Format vidéo": {
        "select": {
            "options": [
                {"name": "Talking head", "color": "blue"},
                {"name": "Texte à l'écran", "color": "purple"},
                {"name": "Démo exercice", "color": "green"},
                {"name": "Voix-off terrain", "color": "orange"},
                {"name": "Mixte", "color": "gray"},
            ]
        }
    },
    "CTA utilisé": {"rich_text": {}},
    "Durée (s)": {"number": {"format": "number"}},
    "Rythme": {
        "select": {
            "options": [
                {"name": "Rapide", "color": "red"},
                {"name": "Moyen", "color": "yellow"},
                {"name": "Lent", "color": "green"},
            ]
        }
    },
    "Statut": {
        "select": {
            "options": [
                {"name": "Analysé", "color": "green"},
                {"name": "À analyser", "color": "gray"},
            ]
        }
    },
}

_ANALYSIS_FIELDS = {
    "Hook exact", "Structure narrative", "Format vidéo",
    "CTA utilisé", "Durée (s)", "Rythme",
}

_analysis_props_ready = False


# ── DB find / create ───────────────────────────────────────────────────────

def _load_cached_db_id() -> Optional[str]:
    if os.path.exists(_DB_CACHE):
        try:
            return open(_DB_CACHE).read().strip() or None
        except Exception:
            pass
    return None


def _save_cached_db_id(db_id: str) -> None:
    try:
        with open(_DB_CACHE, "w") as f:
            f.write(db_id)
    except Exception:
        pass


def _find_or_create_performance_db() -> str:
    cached = _load_cached_db_id()
    if cached:
        try:
            _notion.databases.retrieve(cached)
            log_info(f"Base '{PERF_DB_TITLE}' depuis cache : {cached}")
            return cached
        except Exception:
            pass

    try:
        results = _notion.search(
            query=PERF_DB_TITLE,
            filter={"value": "database", "property": "object"},
        )
        for obj in results.get("results", []):
            title = "".join(t.get("plain_text", "") for t in obj.get("title", []))
            if "Performance Angellos" in title:
                _save_cached_db_id(obj["id"])
                log_info(f"Base '{PERF_DB_TITLE}' trouvée : {obj['id']}")
                return obj["id"]
    except Exception as e:
        log_error(f"Recherche DB : {e}")

    db = _notion.databases.create(
        parent={"type": "page_id", "page_id": _PARENT_PAGE_ID},
        title=[{"type": "text", "text": {"content": PERF_DB_TITLE}}],
        properties=PERF_DB_SCHEMA,
    )
    db_id = db["id"]
    _save_cached_db_id(db_id)
    log_success(f"Base '{PERF_DB_TITLE}' créée : {db_id}")
    return db_id


def _ensure_analysis_properties(db_id: str) -> None:
    global _analysis_props_ready
    if _analysis_props_ready:
        return
    try:
        db = _notion.databases.retrieve(database_id=db_id)
        existing = set(db.get("properties", {}).keys())
        missing = {
            k: v for k, v in PERF_DB_SCHEMA.items()
            if k in _ANALYSIS_FIELDS and k not in existing
        }
        if missing:
            _notion.databases.update(database_id=db_id, properties=missing)
            log_info(f"Propriétés Gemini ajoutées : {list(missing.keys())}")
        _analysis_props_ready = True
    except Exception as e:
        log_error(f"Ensure analysis props : {e}")


# ── Notion page helpers ────────────────────────────────────────────────────

def _fetch_pages_full(db_id: str) -> dict:
    """Returns {url: page_dict} for all pages in the performance DB."""
    pages_map: dict = {}
    cursor = None
    while True:
        kwargs: dict = {"database_id": db_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = _notion.databases.query(**kwargs)
        for page in resp.get("results", []):
            url = (page.get("properties", {}).get("URL") or {}).get("url") or ""
            if url:
                pages_map[url] = page
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return pages_map


def _has_gemini_analysis(page: dict) -> bool:
    hook_rts = (page.get("properties", {}).get("Hook exact") or {}).get("rich_text", [])
    return bool(hook_rts)


def _extract_hook(caption: str) -> str:
    if not caption:
        return ""
    for sep in ["\n", "?", "!", "."]:
        idx = caption.find(sep)
        if 5 < idx < 150:
            return caption[: idx + 1].strip()
    return caption[:120].strip()


def _create_reel_page(db_id: str, reel: dict) -> None:
    caption = reel.get("caption", "") or ""
    title = (caption[:80] if caption else reel.get("url", "")[:80]) or "Reel Angellos"
    hook = _extract_hook(caption)
    pub_date = (reel.get("timestamp_raw", "") or "")[:10] or None

    props: dict = {
        "Titre": {"title": [{"text": {"content": title}}]},
        "Vues": {"number": reel.get("views") or 0},
        "Likes": {"number": reel.get("likes") or 0},
        "Commentaires": {"number": reel.get("comments") or 0},
        "Statut": {"select": {"name": "À analyser"}},
    }
    url = reel.get("url", "")
    if url:
        props["URL"] = {"url": url}
    if pub_date:
        props["Date de publication"] = {"date": {"start": pub_date}}
    if hook:
        props["Hook détecté"] = {"rich_text": [{"text": {"content": hook}}]}

    _notion.pages.create(parent={"database_id": db_id}, properties=props)


# ── yt-dlp download ────────────────────────────────────────────────────────

def _download_reel_yt_dlp(url: str, downloads_dir: str) -> Optional[str]:
    """Download an Instagram reel using yt-dlp. Returns local path or None."""
    url_hash = hashlib.md5(url.encode()).hexdigest()[:10]
    base_path = os.path.join(downloads_dir, f"angellos_{url_hash}")

    for ext in (".mp4", ".webm", ".mkv", ".mov"):
        if os.path.exists(base_path + ext):
            return base_path + ext

    os.makedirs(downloads_dir, exist_ok=True)

    ydl_opts = {
        "format": "mp4/bestvideo+bestaudio/best",
        "outtmpl": base_path + ".%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "extractor_retries": 3,
        "socket_timeout": 60,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        },
    }

    try:
        import yt_dlp
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        log_error(f"yt-dlp {url} : {e}")
        return None

    for ext in (".mp4", ".webm", ".mkv", ".mov"):
        if os.path.exists(base_path + ext):
            return base_path + ext

    log_error(f"Fichier introuvable après download : {base_path}.*")
    return None


# ── Gemini → Notion mapping ────────────────────────────────────────────────

_FORMAT_MAP = [
    ("talking head", "Talking head"),
    ("talking-head", "Talking head"),
    ("face caméra", "Talking head"),
    ("texte à l'écran", "Texte à l'écran"),
    ("tutoriel", "Démo exercice"),
    ("exercice", "Démo exercice"),
    ("démo", "Démo exercice"),
    ("demo", "Démo exercice"),
    ("voix-off + terrain", "Voix-off terrain"),
    ("voix-off terrain", "Voix-off terrain"),
    ("terrain", "Voix-off terrain"),
    ("voix-off", "Voix-off terrain"),
]


def _map_format(format_raw: str) -> str:
    low = (format_raw or "").lower()
    for key, val in _FORMAT_MAP:
        if key in low:
            return val
    return "Mixte"


def _parse_duration(duree_raw: str) -> Optional[int]:
    if not duree_raw:
        return None
    duree_raw = str(duree_raw).lower()
    minutes = 0
    seconds = 0
    m = re.search(r"(\d+)\s*m(?:in(?:ute)?)?", duree_raw)
    if m:
        minutes = int(m.group(1))
    s = re.search(r"(\d+)\s*s(?:ec(?:onde)?)?", duree_raw)
    if s:
        seconds = int(s.group(1))
    if not m and not s:
        n = re.search(r"\d+", duree_raw)
        if n:
            seconds = int(n.group())
    total = minutes * 60 + seconds
    return total if total > 0 else None


def _map_rythme(rythme_raw: str) -> str:
    low = (rythme_raw or "").lower()
    if "rapide" in low or "fast" in low:
        return "Rapide"
    if "lent" in low or "slow" in low:
        return "Lent"
    return "Moyen"


def _update_notion_gemini(page_id: str, gemini: dict) -> None:
    """Update Notion page with Gemini analysis fields."""
    props: dict = {}

    hook = (gemini.get("hook", "") or "")[:2000]
    if hook:
        props["Hook exact"] = {"rich_text": [{"text": {"content": hook}}]}

    structure = (gemini.get("structure_narrative", "") or "")[:2000]
    if structure:
        props["Structure narrative"] = {"rich_text": [{"text": {"content": structure}}]}

    format_raw = gemini.get("format", "") or ""
    props["Format vidéo"] = {"select": {"name": _map_format(format_raw)}}

    cta = (gemini.get("cta", "") or "")[:2000]
    if cta:
        props["CTA utilisé"] = {"rich_text": [{"text": {"content": cta}}]}

    duree = _parse_duration(str(gemini.get("duree_estimee", "") or ""))
    if duree is not None:
        props["Durée (s)"] = {"number": duree}

    props["Rythme"] = {"select": {"name": _map_rythme(gemini.get("rythme", ""))}}
    props["Statut"] = {"select": {"name": "Analysé"}}

    if props:
        _notion.pages.update(page_id=page_id, properties=props)


# ── Claude cross-analysis ──────────────────────────────────────────────────

def _fix_json_newlines(raw: str) -> str:
    """Replace literal newlines inside JSON string values."""
    result = []
    in_string = False
    i = 0
    while i < len(raw):
        c = raw[i]
        if c == "\\" and i + 1 < len(raw):
            result.append(c)
            result.append(raw[i + 1])
            i += 2
            continue
        if c == '"':
            in_string = not in_string
        elif in_string and c in ("\n", "\r"):
            result.append(" ")
            i += 1
            continue
        result.append(c)
        i += 1
    return "".join(result)


def _safe(s: str, max_len: int = 80) -> str:
    if not s:
        return ""
    return s.replace('"', "'").replace("\\", "").replace("\n", " ").replace("\r", "")[:max_len]


def _analyze_with_claude(enriched_reels: list) -> str:
    """
    Cross-analysis : Apify stats × Gemini format/hook → patterns.
    enriched_reels: reels dicts, possibly merged with Gemini analysis fields.
    Saves result to performance_patterns.json. Returns pattern_court.
    """
    n = len(enriched_reels)
    if not n:
        return ""

    summary = [
        {
            "titre": _safe(r.get("caption", "") or ""),
            "hook": _safe(r.get("hook", "") or _extract_hook(r.get("caption", "") or "")),
            "hook_type": _safe(r.get("hook_type", "") or ""),
            "format": _safe(r.get("format", "") or ""),
            "structure": _safe(r.get("structure_narrative", "") or "", 100),
            "cta": _safe(r.get("cta", "") or ""),
            "rythme": _safe(r.get("rythme", "") or ""),
            "duree": _safe(str(r.get("duree_estimee", "") or "")),
            "vues": r.get("views", 0),
            "likes": r.get("likes", 0),
            "commentaires": r.get("comments", 0),
        }
        for r in enriched_reels
    ]

    avg_views = sum(r["vues"] for r in summary) // n
    avg_likes = sum(r["likes"] for r in summary) // n
    avg_comments = sum(r["commentaires"] for r in summary) // n
    top5 = sorted(summary, key=lambda r: r["vues"], reverse=True)[:5]

    has_gemini = any(r.get("hook_type") or r.get("format") for r in summary)
    gemini_note = (
        "Chaque Reel inclut : stats Apify (vues/likes/commentaires) + analyse Gemini "
        "(hook exact, hook_type, format de tournage, CTA, rythme). "
        "Croise ces données pour identifier quels formats et hook_types génèrent le plus de vues."
        if has_gemini else
        "Chaque Reel inclut les stats Apify. Identifie les patterns de hooks et sujets gagnants."
    )

    schema = (
        '{"generated_at":"' + date.today().isoformat() + '",'
        '"total_reels":' + str(n) + ','
        '"avg_views":' + str(avg_views) + ','
        '"avg_likes":' + str(avg_likes) + ','
        '"avg_comments":' + str(avg_comments) + ','
        '"top_performers":[{"titre":"...","vues":0,"hook":"...","format":"...","url":"..."}],'
        '"patterns":{'
        '"hooks_gagnants":["...","..."],'
        '"formats_gagnants":["...","..."],'
        '"sujets_performants":["...","..."],'
        '"formule_gagnante":"hook+format+sujet gagnant en 1 phrase"},'
        '"insights":["...","...","..."],'
        '"pattern_court":"1 phrase résumé pour le dashboard"}'
    )

    prompt = (
        f"You analyze the Instagram Reel performance of @angellos.ai "
        f"(AI setting agent for coaches and infopreneurs selling high-ticket via DMs).\n\n"
        f"{gemini_note}\n\n"
        f"Données ({n} Reels) :\n{json.dumps(summary, ensure_ascii=False)}\n\n"
        f"Top 5 par vues : {json.dumps(top5, ensure_ascii=False)}\n\n"
        f"Retourne UNIQUEMENT ce JSON valide (une seule ligne, pas de saut de ligne "
        f"dans les valeurs string) :\n{schema}"
    )

    resp = _claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        log_error("Claude n'a pas retourné de JSON pour les patterns")
        return ""

    raw = _fix_json_newlines(raw[start:end])
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        log_error(f"JSON patterns invalide : {e}")
        return ""

    with open(_PATTERNS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log_success(f"Patterns sauvegardés → {_PATTERNS_FILE}")

    return data.get("pattern_court", "")


# ── Main ───────────────────────────────────────────────────────────────────

async def sync_angellos_stats_via_apify() -> dict:
    """
    Full pipeline:
    1. Scrape @angellos.ai via Apify (top 50 Reels)
    2. Create missing Notion pages in '📊 Performance Angellos'
    3. For each Reel without Gemini analysis:
       - Download video via yt-dlp
       - Analyze with Gemini (hook, format, structure, CTA, duration, pace)
       - Save to Notion
    4. Cross-analysis stats × format/hook via Claude → performance_patterns.json
    """
    db_id = _find_or_create_performance_db()
    _ensure_analysis_properties(db_id)

    # 1. Scrape
    log_info(f"Scraping @{ANGELLOS_ACCOUNT} via Apify (top 50)...")
    reels = await scrape_account_reels(ANGELLOS_ACCOUNT, APIFY_API_KEY, top=10)
    log_info(f"{len(reels)} Angellos Reels fetched")

    # 2. Create missing Notion pages
    pages_map = _fetch_pages_full(db_id)
    created = 0
    for reel in reels:
        if reel.get("url") not in pages_map:
            try:
                _create_reel_page(db_id, reel)
                created += 1
            except Exception as e:
                log_error(f"Création page : {e}")
    if created:
        pages_map = _fetch_pages_full(db_id)

    # 3. Download + Gemini analysis
    os.makedirs(_DOWNLOADS_DIR, exist_ok=True)
    enriched_reels: list = []
    analyzed = skipped_analyzed = dl_errors = analysis_errors = 0
    total = len(reels)

    for i, reel in enumerate(reels, 1):
        url = reel.get("url", "")
        page = pages_map.get(url)

        if page and _has_gemini_analysis(page):
            log_info(f"[{i}/{total}] Déjà analysé — {url}")
            skipped_analyzed += 1
            enriched_reels.append(reel)
            continue

        log_info(f"[{i}/{total}] Téléchargement : {url}")
        local_path: Optional[str] = None
        try:
            local_path = _download_reel_yt_dlp(url, _DOWNLOADS_DIR)
            if not local_path:
                dl_errors += 1
                enriched_reels.append(reel)
                continue

            log_info(f"[{i}/{total}] Analyse Gemini : {url}")
            gemini = await _analyze_with_retry(local_path, reel.get("caption", "") or "")

            if gemini is None:
                log_error(f"[{i}/{total}] Quota Gemini épuisé après 3 tentatives — Reel skippé")
                analysis_errors += 1
                enriched_reels.append(reel)
                continue

            if page:
                _update_notion_gemini(page["id"], gemini)

            enriched = {**reel, **gemini}
            enriched_reels.append(enriched)
            analyzed += 1
            log_success(
                f"[{i}/{total}] {_map_format(gemini.get('format','?'))} "
                f"— {reel.get('views', 0):,} vues — {url}"
            )

        except Exception as e:
            analysis_errors += 1
            log_error(f"[{i}/{total}] Échec analyse : {e}")
            enriched_reels.append(reel)
        finally:
            if local_path and os.path.exists(local_path):
                os.remove(local_path)

        await asyncio.sleep(2)

    # 4. Cross-analysis Claude
    pattern_insight = ""
    if enriched_reels:
        log_info("Analyse croisée stats × format/hook via Claude...")
        try:
            pattern_insight = _analyze_with_claude(enriched_reels)
        except Exception as e:
            log_error(f"Analyse Claude : {e}")

    return {
        "apify_reels": len(reels),
        "created": created,
        "analyzed": analyzed,
        "skipped_analyzed": skipped_analyzed,
        "dl_errors": dl_errors,
        "analysis_errors": analysis_errors,
        "pattern_insight": pattern_insight,
    }
