import time
from typing import Optional
import google.generativeai as genai
from pathlib import Path
from config import GEMINI_API_KEY
from utils.logger import log_info, log_success, log_error

genai.configure(api_key=GEMINI_API_KEY)

ANALYSIS_PROMPT = """
Analyse ce Reel Instagram en détail. Réponds en JSON avec exactement ces champs :

{
  "hook": "texte exact des 3 premières secondes ou description visuelle de l'accroche",
  "hook_type": "question / affirmation choc / statistique / mise en situation / autre",
  "structure_narrative": "description étape par étape de la structure (ex: problème → agitation → solution → preuve → CTA)",
  "format": "talking head / voix-off + texte / tutoriel / avant-après / témoignage / autre",
  "duree_estimee": "durée en secondes",
  "cta": "call-to-action exact ou description si implicite",
  "elements_visuels_cles": ["liste des éléments visuels marquants"],
  "rythme": "rapide / moyen / lent",
  "sous_titres": true/false,
  "musique": "description ou 'aucune'",
  "points_forts": ["liste des 3 points forts qui rendent ce Reel performant"],
  "pattern_replicable": "description du pattern principal à répliquer"
}

Sois précis et factuel. Ne commente pas, retourne uniquement le JSON valide.
"""

_CAPTION_PREFIX = """La caption Instagram originale de ce Reel est :

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
