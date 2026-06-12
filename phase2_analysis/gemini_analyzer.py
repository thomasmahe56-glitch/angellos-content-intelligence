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

_MAX_UPLOAD_POLLS = 60  # 60 × 5 s = 5 min max before giving up

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


def upload_video(local_path: str) -> genai.types.File:
    path = Path(local_path)
    if not path.exists():
        raise FileNotFoundError(f"Video file not found: {local_path}")

    size_mb = path.stat().st_size / (1024 * 1024)
    log_info(f"Uploading to Gemini Files API: {path.name} ({size_mb:.1f} MB)")

    video_file = genai.upload_file(path=str(path), mime_type="video/mp4")

    polls = 0
    while video_file.state.name == "PROCESSING":
        polls += 1
        if polls > _MAX_UPLOAD_POLLS:
            raise TimeoutError(
                f"Gemini file processing timed out after {_MAX_UPLOAD_POLLS * 5}s "
                f"for {path.name}"
            )
        time.sleep(5)
        video_file = genai.get_file(video_file.name)

    if video_file.state.name == "FAILED":
        raise RuntimeError(
            f"Gemini file processing FAILED for {path.name}. "
            "Check that the file is a valid MP4 and under the size limit."
        )

    log_success(f"File ready: {video_file.name}")
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
        log_info(f"Sending to Gemini ({_MODEL})…")
        response = model.generate_content(
            [video_file, prompt],
            generation_config=genai.types.GenerationConfig(
                temperature=0.2,
                response_mime_type="application/json",
            ),
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
                f"Gemini returned no usable content "
                f"(finish_reason={finish_reason}). "
                "The video may have triggered a safety filter or the model "
                "returned an empty response."
            ) from exc

        raw = raw.strip().removeprefix("```json").removesuffix("```").strip()

        try:
            analysis = json.loads(raw)
        except json.JSONDecodeError:
            log_error("Invalid JSON from Gemini — storing raw response")
            analysis = {"raw_response": raw}

        if caption_originale:
            analysis["caption_originale"] = caption_originale

        log_success(f"Gemini analysis complete for {Path(local_path).name}")
        return analysis

    finally:
        try:
            genai.delete_file(video_file.name)
        except Exception:
            pass
