import json
import threading
import anthropic
from config import ANTHROPIC_API_KEY, ANGELLOS_NICHE_CONTEXT
from utils.logger import log_info, log_success, log_error
from phase2_analysis.notion_context import fetch_angellos_context, fetch_recent_topics, fetch_performance_patterns

_CLAUDE_TIMEOUT_SECONDS = 120

if not ANTHROPIC_API_KEY:
    raise EnvironmentError(
        "ANTHROPIC_API_KEY is not set. "
        "Add it to your Railway environment variables."
    )

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

ADAPTATION_PROMPT_TEMPLATE = """
{niche_context}

---

## Brand context — Angellos voice & positioning

{angellos_context}

---

## Topics already covered (last 30 days)

{recent_topics_section}

---

{performance_section}## Source Reel analysis (@{account})

```json
{gemini_analysis}
```

---

## Task

Based on this analysis, generate a ready-to-shoot Reel script AND an Instagram caption for Angellos.
Adapt the identified pattern to the Angellos niche **using the exact vocabulary, concepts, and tone from the brand context above**.

If a specific pain point from the context is relevant (e.g. wasted hours in DMs, cold leads booking calls, manual qualification killing revenue),
weave it naturally into the script.

For the Angellos caption, draw inspiration from the original caption (if present in the analysis) but rewrite it
with the Angellos voice: strong hook on line 1, 2–3 value sentences, relevant emojis (DM / AI / money / automation), strong CTA, 5–8 hashtags at the end.

Reply with this exact JSON:

{{
  "titre_interne": "short internal title to identify this script (e.g. 'DMs eating your day')",
  "ig_content_type": "choose the best match from: Histoire, Carousel, Liste, Tips, Mythe, Erreur commune, Step by step, Citation, Humour, Exercices, offre",
  "content_type": "choose the best match from: Promotion, Hooks, Personnal Branding, Preuve Social, Objections, Croyances Limitantes, Mythes et Croyances, Conseils Pratiques, Inspiration, Erreurs, Présentation",
  "script": {{
    "hook": "exact hook text (0-3s)",
    "developpement": [
      "step 1: ...",
      "step 2: ...",
      "step 3: ..."
    ],
    "cta": "exact final call-to-action"
  }},
  "caption_angellos": "full Instagram caption adapted for Angellos — strong hook line 1, 2–3 value sentences, CTA, hashtags at the end",
  "concepts_used": ["list of Angellos concepts integrated, empty if none"],
  "indications_tournage": {{
    "format_camera": "face cam / voiceover / screen record / b-roll / other",
    "décor_recommandé": "description",
    "sous_titres": true,
    "rythme": "fast / medium / slow",
    "durée_cible": "duration in seconds"
  }},
  "hashtags_suggeres": ["list of 5-8 hashtags"],
  "pourquoi_ca_marche": "short explanation"
}}

Return only valid JSON, no comments.
"""


def _call_claude_with_timeout(prompt: str, timeout_s: int) -> anthropic.types.Message:
    """Run client.messages.create in a daemon thread with a hard timeout."""
    result: list = [None]
    exc: list = [None]

    def _work():
        try:
            result[0] = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2500,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            exc[0] = e

    t = threading.Thread(target=_work, daemon=True, name="claude.messages.create")
    t.start()
    t.join(timeout=timeout_s)

    if t.is_alive():
        raise TimeoutError(
            f"Claude API call did not complete within {timeout_s}s. "
            "The Anthropic API may be slow or unreachable from Railway."
        )
    if exc[0] is not None:
        raise exc[0]
    return result[0]


def adapt_to_angellos(gemini_analysis: dict, account: str) -> dict:
    """
    Enriches the Claude prompt with:
    - Angellos brand context (product vision, avatar, positioning)
    - recent topics (30 days) to avoid repetition
    """
    angellos_context = fetch_angellos_context()
    if not angellos_context:
        log_info("Notion context empty — generating without brand enrichment")
        angellos_context = "(context unavailable — configure Notion access)"

    recent = fetch_recent_topics(days=30)
    if recent:
        recent_topics_section = (
            f"{recent}\n\n"
            "You can draw inspiration from these themes but **find a different angle or a new topic**. "
            "Any topic older than 30 days is available again."
        )
    else:
        recent_topics_section = "No scripts generated in the last 30 days — you have full creative freedom."

    patterns = fetch_performance_patterns()
    if patterns:
        performance_section = (
            "## Feedback loop — Real performance from published Angellos Reels\n\n"
            "Here are the stats from published Angellos posts:\n\n"
            + patterns
            + "\n\n---\n\n"
        )
    else:
        performance_section = ""

    log_info(
        f"[Claude] Adapting pattern — model=claude-sonnet-4-6 "
        f"timeout={_CLAUDE_TIMEOUT_SECONDS}s"
    )

    prompt = ADAPTATION_PROMPT_TEMPLATE.format(
        niche_context=ANGELLOS_NICHE_CONTEXT,
        angellos_context=angellos_context,
        recent_topics_section=recent_topics_section,
        performance_section=performance_section,
        gemini_analysis=json.dumps(gemini_analysis, ensure_ascii=False, indent=2),
        account=account,
    )

    response = _call_claude_with_timeout(prompt, _CLAUDE_TIMEOUT_SECONDS)

    if not response.content:
        raise RuntimeError(
            "[Claude] Empty response — the model returned no content blocks. "
            "Check ANTHROPIC_API_KEY and model availability."
        )

    raw = response.content[0].text.strip().removeprefix("```json").removesuffix("```").strip()

    try:
        script = json.loads(raw)
    except json.JSONDecodeError:
        log_error(f"[Claude] Invalid JSON — storing raw response (first 200 chars): {raw[:200]}")
        script = {"raw_response": raw}

    log_success("[Claude] Angellos script generated")
    return script
