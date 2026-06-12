import time
from typing import Optional
import google.generativeai as genai
from pathlib import Path
from config import GEMINI_API_KEY
from utils.logger import log_info, log_success, log_error

genai.configure(api_key=GEMINI_API_KEY)

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
  "sous_titres": true/false,
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
    log_info(f"Upload vers Gemini Files API : {Path(local_path).name}")
    video_file = genai.upload_file(path=local_path, mime_type="video/mp4")

    # Attente que le fichier soit traité
    while video_file.state.name == "PROCESSING":
        time.sleep(5)
        video_file = genai.get_file(video_file.name)

    if video_file.state.name == "FAILED":
        raise RuntimeError(f"Échec traitement Gemini pour {local_path}")

    log_success(f"Fichier prêt : {video_file.name}")
    return video_file


def analyze_reel(local_path: str, caption_originale: Optional[str] = None) -> dict:
    """Envoie la vidéo à Gemini 2.5 Flash et retourne l'analyse structurée."""
    import json

    video_file = upload_video(local_path)

    try:
        prompt = ANALYSIS_PROMPT
        if caption_originale:
            prompt = _CAPTION_PREFIX.format(caption=caption_originale) + ANALYSIS_PROMPT

        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(
            [video_file, prompt],
            generation_config=genai.types.GenerationConfig(
                temperature=0.2,
                response_mime_type="application/json",
            ),
        )

        raw = response.text.strip().removeprefix("```json").removesuffix("```").strip()

        try:
            analysis = json.loads(raw)
        except json.JSONDecodeError:
            log_error("JSON invalide de Gemini, retour brut conservé")
            analysis = {"raw_response": raw}

        if caption_originale:
            analysis["caption_originale"] = caption_originale

        log_success(f"Analyse Gemini terminée pour {Path(local_path).name}")
        return analysis

    finally:
        # Toujours supprimer le fichier uploadé pour ne pas consumer le quota fichiers
        try:
            genai.delete_file(video_file.name)
        except Exception:
            pass
