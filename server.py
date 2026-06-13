"""
Lance le dashboard React (Vite, port 5173) et l'API FastAPI (port 8000)
avec une seule commande : python server.py
"""
import asyncio
import json
import os
import signal
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager

from typing import Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

_APP_VERSION_DEFAULT = "cdn-cookies-fix-v3"
# Railway env var overrides the literal — lets us verify a deploy without a code push.
APP_VERSION = os.getenv("APP_VERSION", _APP_VERSION_DEFAULT)

# Written by the Dockerfile RUN step — changes on every build, proves the image is fresh.
_BUILD_TIME_FILE = os.path.join(os.path.dirname(__file__), ".build_time")
BUILD_TIME = open(_BUILD_TIME_FILE).read().strip() if os.path.exists(_BUILD_TIME_FILE) else "unknown"

DASHBOARD_DIR = os.path.join(os.path.dirname(__file__), "dashboard")

jobs: dict[str, asyncio.Queue] = {}
_vite_proc: Optional[subprocess.Popen] = None


def _start_vite():
    global _vite_proc
    # Install deps if needed
    if not os.path.isdir(os.path.join(DASHBOARD_DIR, "node_modules")):
        print("📦 Installation des dépendances npm du dashboard...")
        subprocess.run(["npm", "install"], cwd=DASHBOARD_DIR, check=True)

    print("🎨 Démarrage du dashboard Vite sur http://localhost:8080 ...")
    _vite_proc = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=DASHBOARD_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop_vite():
    global _vite_proc
    if _vite_proc and _vite_proc.poll() is None:
        _vite_proc.terminate()
        try:
            _vite_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _vite_proc.kill()
        _vite_proc = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"[STARTUP] APP_VERSION={APP_VERSION} BUILD_TIME={BUILD_TIME}", flush=True)
    if not os.getenv("RAILWAY_ENVIRONMENT"):
        _start_vite()
    yield
    if not os.getenv("RAILWAY_ENVIRONMENT"):
        _stop_vite()
    jobs.clear()


app = FastAPI(lifespan=lifespan)

_cors_origins = [
    "http://localhost:8080",
    "http://localhost:5173",
    "https://angellos-content-dashboard.vercel.app",
]
if os.getenv("CORS_ALLOWED_ORIGINS"):
    _cors_origins += [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS").split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "version": APP_VERSION, "build_time": BUILD_TIME})


@app.post("/analyze")
async def analyze(request: Request):
    body = await request.json()
    url = (body.get("url") or "").strip()
    if not url:
        return JSONResponse({"error": "URL manquante."}, status_code=400)
    if "/reel/" not in url and "/p/" not in url:
        return JSONResponse(
            {"error": "URL invalide — colle un lien Instagram Reel."},
            status_code=400,
        )

    job_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    jobs[job_id] = queue

    async def emit(event: dict):
        await queue.put(event)

    asyncio.create_task(_run_pipeline(url, emit))
    return JSONResponse({"job_id": job_id})


@app.get("/status/{job_id}")
async def status(job_id: str):
    queue = jobs.get(job_id)
    if queue is None:
        return JSONResponse({"error": "Job introuvable."}, status_code=404)

    async def generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=60)
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
                    continue

                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

                if event.get("status") in ("done", "error"):
                    break
        finally:
            jobs.pop(job_id, None)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/dream100/fetch")
async def dream100_fetch(request: Request):
    body = await request.json()
    account = (body.get("account") or "").strip().lstrip("@")
    if not account:
        return JSONResponse({"error": "Nom de compte manquant."}, status_code=400)
    try:
        from phase1_scraping.apify_scraper import scrape_account_reels
        from config import APIFY_API_KEY
        if not APIFY_API_KEY:
            return JSONResponse({"error": "APIFY_API_KEY non configurée."}, status_code=500)
        reels = await scrape_account_reels(account, APIFY_API_KEY)
        if not reels:
            return JSONResponse({"error": f"Aucun Reel trouvé pour @{account}."}, status_code=404)
        return JSONResponse({"reels": reels, "account": account})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/sync-my-stats")
async def sync_my_stats():
    try:
        from config import APIFY_API_KEY
        if not APIFY_API_KEY:
            return JSONResponse({"error": "APIFY_API_KEY non configurée."}, status_code=500)
        from stats.apify_sync import sync_angellos_stats_via_apify
        result = await sync_angellos_stats_via_apify()
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/sync-stats")
async def sync_stats():
    try:
        from config import INSTAGRAM_ACCESS_TOKEN
        if not INSTAGRAM_ACCESS_TOKEN:
            return JSONResponse(
                {"error": "INSTAGRAM_ACCESS_TOKEN manquant dans .env"},
                status_code=400,
            )
        from stats.notion_sync import sync_instagram_stats
        result = await sync_instagram_stats()
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def _run_pipeline(url: str, emit):
    try:
        from pipeline_runner import run
        await run(url, emit)
    except Exception as e:
        await emit({"status": "error", "error": str(e)})


if __name__ == "__main__":
    print("🚀 Starting Angellos Content Intelligence")
    print("   API  → http://localhost:8000")
    print("   Dashboard → http://localhost:8080")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
