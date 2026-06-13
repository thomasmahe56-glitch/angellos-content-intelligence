import threading
import time
from typing import Optional
import google.generativeai as genai
from pathlib import Path
from config import GEMINI_API_KEY
from utils.logger import log_info, log_success, log_error

if not GEMINI_API_KEY:
    raise EnvironmentError(
        "GEMINI_API_KEY is not set. "
        "Add it to your Railway environment variables."
    )

genai.configure(api_key=GEMINI_API_KEY)

# gemini-2.0-flash: no thinking mode by default, supports JSON mode + Files API video.
# gemini-2.5-flash enables thinking by default which is incompatible with
# response_mime_type="application/json" in google-generativeai==0.7.x.
_MODEL = "gemini-2.0-flash"

_UPLOAD_TIMEOUT_SECONDS = 180   # 3 min hard limit on the upload HTTP call
_GENERATION_TIMEOUT_SECONDS = 120  # 2 min hard limit on generate_content
_MAX_UPLOAD_POLLS = 60          # 60 × 5 s = 5 min max waiting for PROCESSING state

ANALYSIS_PROMPT = """
Analyze this Instagram Reel in detail. Reply in JSON with exactly these fields:

{
  "hook": "exact text of the first 3 seconds or visual description of the hook",
  "hook_type": "question / shock statement / statistic / scenario / other",
  "structure_narrative": "step-by-step description of the structure (e.g. problem → agitation → solution → proof → CTA)",
  "format": "talking head / voiceover + text / tutorial / before-after / testimonial / other",
  "duree_estimee": "duration in seconds",
  "cta": "exact call-to-action or description if implicit",
  "elements_visuels_cles": ["list of notable visual elements"],
  "rythme": "fast / medium / slow",
  "sous_titres": true,
  "musique": "description or 'none'",
  "points_forts": ["list of the 3 strengths that make this Reel perform well"],
  "pattern_replicable": "description of the main pattern to replicate"
}

Be precise and factual. Do not comment, return only valid JSON.
"""

_CAPTION_PREFIX = """The original Instagram caption of this Reel is:

{caption}

---

"""


def _run_in_daemon_thread(fn, *args, timeout_s: int, label: str):
    """
    Run fn(*args) in a daemon thread.  Raises TimeoutError if it does not
    complete within timeout_s seconds.  Using a daemon thread (vs an executor)
    means a hung upload cannot prevent the process from exiting.
    """
    result: list = [None]
    exc: list = [None]

    def _work():
        try:
            result[0] = fn(*args)
        except Exception as e:
            exc[0] = e

    t = threading.Thread(target=_work, daemon=True, name=label)
    t.start()
    t.join(timeout=timeout_s)

    if t.is_alive():
        # Thread is still blocking — the HTTP call never returned.
        raise TimeoutError(
            f"{label} did not complete within {timeout_s}s. "
            "Railway may be unable to reach the Gemini API endpoint, "
            "or the file is too large for the configured time limit."
        )
    if exc[0] is not None:
        raise exc[0]
    return result[0]


def upload_video(local_path: str) -> genai.types.File:
    path = Path(local_path)

    if not path.exists():
        raise FileNotFoundError(f"Video file not found: {local_path}")

    size_mb = path.stat().st_size / (1024 * 1024)
    mime_type = "video/mp4"

    log_info(
        f"[Gemini upload] START — file={path.name} "
        f"size={size_mb:.2f} MB mime={mime_type} "
        f"timeout={_UPLOAD_TIMEOUT_SECONDS}s"
    )

    video_file = _run_in_daemon_thread(
        lambda: genai.upload_file(path=str(path), mime_type=mime_type),
        timeout_s=_UPLOAD_TIMEOUT_SECONDS,
        label="gemini.upload_file",
    )

    log_success(
        f"[Gemini upload] DONE — remote_name={video_file.name} "
        f"state={video_file.state.name}"
    )

    polls = 0
    while video_file.state.name == "PROCESSING":
        polls += 1
        if polls > _MAX_UPLOAD_POLLS:
            raise TimeoutError(
                f"[Gemini upload] PROCESSING state did not resolve after "
                f"{_MAX_UPLOAD_POLLS * 5}s for {path.name}"
            )
        log_info(f"[Gemini upload] still PROCESSING… (poll {polls}/{_MAX_UPLOAD_POLLS})")
        time.sleep(5)
        video_file = genai.get_file(video_file.name)

    if video_file.state.name == "FAILED":
        raise RuntimeError(
            f"[Gemini upload] File processing FAILED for {path.name} "
            f"({size_mb:.2f} MB). "
            "Verify the file is a valid MP4 and within the Gemini size limit."
        )

    log_success(f"[Gemini upload] File ACTIVE and ready: {video_file.name}")
    return video_file


def analyze_reel(local_path: str, caption_originale: Optional[str] = None) -> dict:
    """Upload video to Gemini Files API and return structured analysis."""
    import json

    video_file = upload_video(local_path)

    try:
        prompt = ANALYSIS_PROMPT
        if caption_originale:
            prompt = _CAPTION_PREFIX.format(caption=caption_originale) + ANALYSIS_PROMPT

        model = genai.GenerativeModel(_MODEL)
        log_info(
            f"[Gemini generate] Sending to {_MODEL} — "
            f"timeout={_GENERATION_TIMEOUT_SECONDS}s"
        )

        def _generate():
            return model.generate_content(
                [video_file, prompt],
                generation_config=genai.types.GenerationConfig(
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
                request_options={"timeout": _GENERATION_TIMEOUT_SECONDS},
            )

        response = _run_in_daemon_thread(
            _generate,
            timeout_s=_GENERATION_TIMEOUT_SECONDS + 10,  # daemon thread slightly longer
            label="gemini.generate_content",
        )

        # response.text raises ValueError when candidates are blocked or empty
        try:
            raw = response.text
        except ValueError as exc:
            finish_reason = None
            try:
                finish_reason = response.candidates[0].finish_reason
            except Exception:
                pass
            raise RuntimeError(
                f"[Gemini generate] No usable content returned "
                f"(finish_reason={finish_reason}). "
                "The video may have triggered a safety filter."
            ) from exc

        log_success(f"[Gemini generate] Response received — parsing JSON")
        raw = raw.strip().removeprefix("```json").removesuffix("```").strip()

        try:
            analysis = json.loads(raw)
        except json.JSONDecodeError:
            log_error(f"[Gemini generate] Invalid JSON — storing raw response (first 200 chars): {raw[:200]}")
            analysis = {"raw_response": raw}

        if caption_originale:
            analysis["caption_originale"] = caption_originale

        log_success(f"[Gemini] Analysis complete for {Path(local_path).name}")
        return analysis

    finally:
        try:
            genai.delete_file(video_file.name)
            log_info(f"[Gemini] Remote file deleted: {video_file.name}")
        except Exception:
            pass
