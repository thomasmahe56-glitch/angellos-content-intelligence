# Angellos Content Intelligence V2

Autonomous, production-oriented Instagram discovery pipeline: authenticated browser
discovery → deterministic virality filter → Luna relevance → video download → Gemini
video perception → Luna original production brief → Notion editorial calendar.

The dashboard remains available, but no business workflow requires it. The supported
automation boundary is:

```bash
python -m content_agent scout --once --json
```

For Hermes, use the same bounded command with its compact, secret-free event
contract:

```bash
python -m content_agent scout --once --hermes
```

When calling the deployed service instead, use its bearer-protected V2 routes:
`POST /agent/scout`, `POST /agent/analyze`, and `POST /agent/sync-performance`.
The latter persists learning signals to the dedicated Notion state page; it does
not rely on Railway disk.

For a production content window, submit the CUA-observed candidates in one
bounded `POST /agent/analyze-batch` request. The endpoint deduplicates URLs before
any download or LLM call, limits the batch with `ANALYZE_BATCH_MAX_CANDIDATES`
(31 by default), and runs up to `ANALYZE_BATCH_CONCURRENCY` analyses (3 by
default) concurrently. The ordinary `/agent/analyze` cap remains unchanged for
single-candidate calls; the batch cap is the explicit, finite production budget.
Every candidate must include only metrics visibly read in Instagram and the
response aggregates Notion URLs, errors, token costs, and the exact `run_id` for
ARIA.

## Setup

Install dependencies and Playwright Chromium:

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

Required production variables:

```env
OPENAI_API_KEY=
OPENAI_CONTENT_MODEL=gpt-5.6-luna
GEMINI_API_KEY=
GEMINI_VIDEO_MODEL=gemini-2.5-flash
NOTION_API_KEY=
NOTION_PROGRAMME_CONTENT_DB=
NOTION_CONTENT_AGENT_STATE_PAGE_ID=
INSTAGRAM_SESSION_PATH=/data/instagram-session.json
INSTAGRAM_COOKIES_FILE=/data/instagram-cookies.txt
DREAM100_ACCOUNTS=creator_one,creator_two
INSTAGRAM_SEED_ACCOUNTS=creator_three
VIRAL_RATIO_MIN=4.0
# Creator-relative outlier detection. Followers remain a bonus, not a gate.
OUTLIER_SCORE_MIN=60
OUTLIER_BASELINE_REELS=12
OUTLIER_BASELINE_TTL_HOURS=72
OUTLIER_BASELINES_MAX_PER_RUN=5
OUTLIER_PRESELECT_MIN_VIEWS=10000
OUTLIER_PRESELECT_MIN_LIKES=250
OUTLIER_PRESELECT_MIN_COMMENTS=15
OUTLIER_ABSOLUTE_MIN_VIEWS=50000
OUTLIER_ABSOLUTE_MIN_LIKES=1000
OUTLIER_ABSOLUTE_MIN_COMMENTS=75
DISCOVERY_MAX_REELS_PER_RUN=100
QUALIFIED_MAX_PER_RUN=5
ANALYZE_MAX_PER_RUN=5
# Explicit finite production batch budget and concurrency
ANALYZE_BATCH_MAX_CANDIDATES=31
ANALYZE_BATCH_CONCURRENCY=3
SCOUT_SCROLL_LIMIT=30
SCOUT_MAX_RUNTIME_MINUTES=20
CONTENT_LANGUAGE=EN
CONTENT_PRESENTATION_STYLE=faceless
TIMEZONE=Europe/Paris
CONTENT_PUBLISH_DAYS=MON,TUE,WED,THU,FRI
CONTENT_ITEMS_PER_DAY=1
DELETE_SOURCE_VIDEO_AFTER_ANALYSIS=true
CONTENT_AGENT_LOG_FORMAT=json
LUNA_INPUT_USD_PER_MILLION_TOKENS=
LUNA_OUTPUT_USD_PER_MILLION_TOKENS=
GEMINI_INPUT_USD_PER_MILLION_TOKENS=
GEMINI_OUTPUT_USD_PER_MILLION_TOKENS=
GEMINI_ANALYSIS_USD_PER_VIDEO=
```

`CONTENT_AGENT_ALLOW_EPHEMERAL_STATE=true` is only for local tests or deliberate
dry-runs. Never set it in Railway production.

Every scout report includes a `costs` object in USD. Apify reports its actual
run charge when available; configure the Luna and Gemini token rates above to
complete the total. `GEMINI_ANALYSIS_USD_PER_VIDEO` is a legacy fallback; token
usage is preferred because video duration changes the charge. The report never
invents a provider price.

## How a Reel is selected

The primary signal is now a transparent creator-relative outlier score out of
100: views against the creator's median recent Reel, then velocity, like rate,
comment rate, freshness, and finally views/followers as a small bonus. For each
preselected source, the agent observes up to 12 recent Reels from that creator,
caches their medians in the durable Notion state for 72 hours, and only sends
strong outliers to the paid relevance and video-analysis stages. This baseline
collection uses the authenticated Instagram browser; it does not call Apify.

`NOTION_CONTENT_AGENT_STATE_PAGE_ID` must be a private, dedicated Notion page shared
with the integration. It keeps compact candidate state, follower cache and dedupe
records across Railway deploys. Never put keys, cookies or a password in Git.

## Instagram authentication

Run this locally with a visible browser and complete login yourself:

```bash
python -m content_agent login
```

It saves Playwright storage state to `INSTAGRAM_SESSION_PATH`. If Instagram asks for a
challenge, CAPTCHA or 2FA, the scout returns `human_action_required` and stops. It does
not attempt to bypass any security control.

## Run

```bash
python -m content_agent health
python -m content_agent scout --once --dry-run --max-reels 10 --max-content 1
python -m content_agent analyze https://www.instagram.com/reel/SHORTCODE/ --dry-run
python -m content_agent scout --once --json
python -m content_agent scout --once --hermes
python -m content_agent scout --once --retry-insufficient-metrics
python -m content_agent sync-performance
```

Exit codes: `0` complete, `1` partial failure, `2` human action required, `3`
configuration error. The `--json` report is the Hermes contract. Hermes should call the
command in cron, inspect the JSON, then route only a required human action to ARIA/Thomas.

## Troubleshooting

- `instagram_authentication_challenge`: run `login` locally and complete the prompt.
- Gemini errors leave the candidate `video_analysis_pending`; Luna is never asked to
  invent video observations.
- Luna failures create no partial Notion idea.
- A Notion schema conflict is surfaced as a failed item; existing property types are
  never overwritten.
