"""Test phase 2 : Gemini analyse + Claude adapte → affiche le résultat."""
import json
from utils.logger import log_phase, log_success, log_error, console
from phase2_analysis.gemini_analyzer import analyze_reel
from phase2_analysis.claude_adapter import adapt_to_ttr

VIDEO = "downloads/ninon.ia_officiel/DYAj5wgOHvg.mp4"
ACCOUNT = "ninon.ia_officiel"

log_phase(2, "Analyse Gemini")
try:
    gemini = analyze_reel(VIDEO)
    log_success("Analyse Gemini reçue")
    console.print(json.dumps(gemini, ensure_ascii=False, indent=2))
except Exception as e:
    log_error(f"Gemini : {e}")
    raise SystemExit(1)

log_phase(2, "Adaptation Claude → TTR")
try:
    script = adapt_to_ttr(gemini, ACCOUNT)
    log_success("Script TTR généré")
    console.print(json.dumps(script, ensure_ascii=False, indent=2))
except Exception as e:
    log_error(f"Claude : {e}")
    raise SystemExit(1)
