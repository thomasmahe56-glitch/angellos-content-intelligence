"""
Gemini video analysis with model fallback chain.

Upload: video uploaded once (Files API is model-agnostic).
Generation: each model in the chain is tried; quota/not-found/param errors fall through.

Available models for this API key (verified via ListModels):
  gemini-2.0-flash, gemini-2.0-flash-lite, gemini-2.0-flash-001, gemini-2.5-flash, gemini-2.5-pro

NOT available: gemini-1.5-flash, gemini-1.5-pro (404 for this key).

gemini-2.5-* models have thinking enabled by default in SDK 0.7.x, which is
incompatible with response_mime_type="application/json". Those models run without
JSON mode and rely on prompt-based JSON instruction + text parsing.
"""
import json
import threading
import time
from typing import Optional
import google.generativeai as genai
from pathlib import Path
from config import (
    GEMINI_API_KEY,
    GEMINI_MODEL_PRIMARY,
    GEMINI_MODEL_FALLBACKS,
    GEMINI_THINKING_MODELS,
)
from utils.logger import log_info, log_success, log_error

if not GEMINI_API_KEY:
    raise EnvironmentError(
        "GEMINI_API_KEY is not set. "
        "Add it to your Railway environment variables."
    )

genai.configure(api_key=GEMINI_API_KEY)

_UPLOAD_TIMEOUT_SECONDS = 180
_GENERATION_TIMEOUT_SECONDS = 120
_MAX_UPLOAD_POLLS = 60

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
        raise TimeoutError(
            f"{label} did not complete within {timeout_s}s. "
            "Railway may be unable to reach the Gemini API endpoint."
        )
    if exc[0] is not None:
        raise exc[0]
    return result[0]


def _should_try_next_model(exc: Exception) -> bool:
    """
    Return True if this error means the model is unavailable/quota-blocked
    and we should try the next model in the chain.
    """
    msg = str(exc).lower()
    return (
        "429" in msg
        or "resource exhausted" in msg
        or "quota" in msg
        or "rate limit" in msg
        or "404" in msg
        or "not found" in msg
        # thinking-mode incompatibility with JSON mode (SDK 0.7.x + gemini-2.5)
        or ("400" in msg and ("thinking" in msg or "incompatible" in msg))
    )


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


def _generate_with_model(model_name: str, video_file, prompt: str) -> dict:
    """
    Attempt generation with one specific model.
    - 2.0-generation models: use response_mime_type=application/json (structured output).
    - 2.5-generation models: omit JSON mode (thinking incompatible), rely on prompt + parsing.
    Raises original exception; caller decides whether to fall through.
    """
    use_json_mode = model_name not in GEMINI_THINKING_MODELS

    log_info(
        f"[Gemini generate] Trying model={model_name} "
        f"json_mode={use_json_mode} "
        f"timeout={_GENERATION_TIMEOUT_SECONDS}s"
    )

    model = genai.GenerativeModel(model_name)

    gen_cfg_kwargs = {"temperature": 0.2}
    if use_json_mode:
        gen_cfg_kwargs["response_mime_type"] = "application/json"

    def _generate():
        return model.generate_content(
            [video_file, prompt],
            generation_config=genai.types.GenerationConfig(**gen_cfg_kwargs),
            request_options={"timeout": _GENERATION_TIMEOUT_SECONDS},
        )

    response = _run_in_daemon_thread(
        _generate,
        timeout_s=_GENERATION_TIMEOUT_SECONDS + 10,
        label=f"gemini.generate.{model_name}",
    )

    try:
        raw = response.text
    except ValueError as exc:
        finish_reason = None
        try:
            finish_reason = response.candidates[0].finish_reason
        except Exception:
            pass
        raise RuntimeError(
            f"[Gemini] No usable content (model={model_name} "
            f"finish_reason={finish_reason}). "
            "The video may have triggered a safety filter."
        ) from exc

    log_success(f"[Gemini] Response received from {model_name} — parsing JSON")
    raw = raw.strip().removeprefix("```json").removesuffix("```").strip()

    try:
        analysis = json.loads(raw)
    except json.JSONDecodeError:
        log_error(
            f"[Gemini] Invalid JSON from {model_name} "
            f"(first 200 chars): {raw[:200]}"
        )
        analysis = {"raw_response": raw}

    analysis["_gemini_model_used"] = model_name
    return analysis


def analyze_reel(local_path: str, caption_originale: Optional[str] = None) -> dict:
    """Upload video once, then walk the model fallback chain for generation."""
    video_file = upload_video(local_path)

    prompt = ANALYSIS_PROMPT
    if caption_originale:
        prompt = _CAPTION_PREFIX.format(caption=caption_originale) + ANALYSIS_PROMPT

    models_to_try = [GEMINI_MODEL_PRIMARY] + list(GEMINI_MODEL_FALLBACKS)
    last_error: Optional[Exception] = None

    try:
        for model_name in models_to_try:
            try:
                analysis = _generate_with_model(model_name, video_file, prompt)
                if caption_originale:
                    analysis["caption_originale"] = caption_originale
                log_success(
                    f"[Gemini] Analysis complete — model={model_name} "
                    f"file={Path(local_path).name}"
                )
                return analysis

            except Exception as exc:
                if _should_try_next_model(exc):
                    log_error(
                        f"[Gemini] model={model_name} unavailable/quota-blocked — "
                        f"trying next. Detail: {exc}"
                    )
                    last_error = exc
                    continue
                raise  # non-transient error: propagate immediately

        raise RuntimeError(
            f"[Gemini] All models in the fallback chain failed. "
            f"Models tried: {models_to_try}. "
            f"Last error: {last_error}. "
            "To fix: enable billing on your Google AI project at "
            "https://console.cloud.google.com/billing or set GEMINI_MODEL_PRIMARY "
            "to a model with available quota."
        )

    finally:
        try:
            genai.delete_file(video_file.name)
            log_info(f"[Gemini] Remote file deleted: {video_file.name}")
        except Exception:
            pass
