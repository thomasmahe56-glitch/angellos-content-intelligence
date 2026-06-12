import asyncio
import json
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates

# jobs : job_id → asyncio.Queue
jobs: dict[str, asyncio.Queue] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    jobs.clear()


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/analyze")
async def analyze(request: Request):
    body = await request.json()
    url = (body.get("url") or "").strip()
    if not url or "/reel/" not in url and "/p/" not in url:
        return JSONResponse({"error": "URL invalide — colle un lien Instagram Reel."}, status_code=400)

    job_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    jobs[job_id] = queue

    async def emit(event: dict):
        await queue.put(event)

    asyncio.create_task(_run_pipeline(url, emit))
    return JSONResponse({"job_id": job_id})


@app.get("/stream/{job_id}")
async def stream(job_id: str):
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

                if event.get("type") in ("complete", "error"):
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


async def _run_pipeline(url: str, emit):
    try:
        from pipeline_runner import run
        await run(url, emit)
    except Exception as e:
        await emit({"type": "error", "message": str(e)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("dashboard:app", host="0.0.0.0", port=8000, reload=False)
