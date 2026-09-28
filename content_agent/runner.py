from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Optional

from content_agent.config import Settings, is_configured, settings
from content_agent.discovery.dedupe import is_duplicate
from content_agent.discovery.instagram_browser import InstagramBrowser, InstagramHumanActionRequired
from content_agent.discovery.metrics import viral_ratio
from content_agent.intelligence.adaptation import adapt_to_angellos, quality_check
from content_agent.intelligence.luna_client import LunaClient
from content_agent.intelligence.relevance import qualify_candidate
from content_agent.models.schemas import RunReport
from content_agent.storage.notion import NotionEditorialCalendar
from content_agent.storage.state import StateStore
from content_agent.utils.logging import event, get_logger
from config import ANGELLOS_NICHE_CONTEXT
from phase2_analysis.notion_context import fetch_angellos_context


async def run_daily_scout(*, dry_run: bool = False, max_reels: Optional[int] = None, max_content: Optional[int] = None, retry_insufficient_metrics: bool = False, cfg: Settings = settings) -> dict:
    """Run the complete V2 pipeline. Each candidate is isolated by design."""
    report = RunReport()
    apify_cost_usd = 0.0
    apify_cost_known = True
    logger = get_logger(cfg.log_format == "json")
    event(logger, "SCOUT started", dry_run=dry_run)
    deadline = time.monotonic() + cfg.run_timeout_seconds
    state_store = StateStore(cfg)
    if not state_store.durable and not (dry_run or cfg.allow_ephemeral_state):
        report.status = "configuration_error"
        report.errors = ["NOTION_CONTENT_AGENT_STATE_PAGE_ID is required for durable production state"]
        return report.finish()
    try:
        state = state_store.load(strict=state_store.durable and not cfg.allow_ephemeral_state)
    except Exception as exc:
        report.status = "partial_failure"
        report.errors = [f"durable_state_unavailable: {exc}"]
        return report.finish()
    browser = InstagramBrowser(cfg)
    try:
        await browser.check_authentication()
    except InstagramHumanActionRequired as exc:
        report.human_action_required, report.human_action_reason, report.status = True, str(exc), "human_action_required"
        return report.finish()
    calendar = NotionEditorialCalendar(cfg)
    try:
        # Notion is canonical when reachable.  A deliberately small existing
        # product description keeps the safety gates meaningful during a
        # transient Notion outage; it is not a replacement for live context.
        context = fetch_angellos_context() or ANGELLOS_NICHE_CONTEXT.strip()
        recent = calendar.recent_content()
    except Exception as exc:
        report.status, report.errors = "configuration_error", [str(exc)]
        return report.finish()
    historical = _historical_signals(cfg, state)
    try:
        requested_reels = cfg.discovery_max_reels_per_run if max_reels is None else max(0, max_reels)
        candidates = await browser.discover(min(requested_reels, cfg.discovery_max_reels_per_run))
    except InstagramHumanActionRequired as exc:
        report.human_action_required, report.human_action_reason, report.status = True, str(exc), "human_action_required"
        return report.finish()
    report.scanned = len(candidates)
    event(logger, "SCOUT discovered", scanned=report.scanned)
    # Instagram is the primary metric source. Apify is deliberately deferred
    # until the browser has tried every candidate, so a normal run does not pay
    # to enrich visible counters again.
    metric_candidates = []
    missing_metrics = []
    for candidate in candidates:
        if time.monotonic() >= deadline:
            report.status = "partial_failure"
            report.errors.append("run_timeout_reached")
            break
        if is_duplicate(candidate, state, retry_insufficient_metrics=retry_insufficient_metrics):
            report.duplicates += 1
            continue
        try:
            await browser.enrich(candidate, state.setdefault("followers", {}))
            if candidate.media_is_video is False:
                StateStore.mark(state, candidate, "rejected_non_video")
                continue
            if candidate.creator_username.lower().lstrip("@") == cfg.angellos_instagram_account:
                StateStore.mark(state, candidate, "rejected_own_account")
                continue
            report.new_reels += 1
            if candidate.views is None or candidate.followers is None:
                missing_metrics.append(candidate)
            else:
                metric_candidates.append(candidate)
        except InstagramHumanActionRequired as exc:
            report.human_action_required, report.human_action_reason = True, str(exc)
            break
        except Exception as exc:
            StateStore.mark(state, candidate, "failed", error=str(exc))
            report.failed += 1
    if report.human_action_required:
        # Never continue into downloads or AI work after Instagram has requested
        # a security action. Persist only the safe progress collected so far.
        try:
            state_store.save(state)
        except Exception as exc:
            report.errors.append(f"state_persistence_failed: {exc}")
        report.status = "human_action_required"
        event(logger, "SCOUT human action required", reason=report.human_action_reason)
        return report.finish()
    if missing_metrics and cfg.apify_metrics_fallback and cfg.apify_api_key:
        try:
            from content_agent.discovery.apify_metrics import enrich_reel_metrics
            apify_result = await enrich_reel_metrics(missing_metrics, cfg.apify_api_key, min(cfg.apify_metrics_max_reels, len(missing_metrics)))
            apify_cost_usd = apify_result.cost_usd or 0.0
            apify_cost_known = apify_result.cost_usd is not None
            event(logger, "APIFY metric fallback completed", requested=len(missing_metrics), reels_with_views=apify_result.enriched)
        except Exception as exc:
            # Browser discovery remains usable if the optional paid fallback is
            # unavailable. Candidates without verifiable metrics are safely
            # retained as insufficient_metrics below.
            report.errors.append(f"apify_metrics_fallback_failed: {exc}")
    new = []
    for candidate in [*metric_candidates, *missing_metrics]:
        candidate.viral_ratio = viral_ratio(candidate.views, candidate.followers)
        if candidate.viral_ratio is None:
            StateStore.mark(state, candidate, "insufficient_metrics")
        elif candidate.viral_ratio < cfg.viral_ratio_min:
            StateStore.mark(state, candidate, "rejected_ratio")
            report.rejected_ratio += 1
        else:
            StateStore.mark(state, candidate, "discovered")
            new.append(candidate)
            report.viral_candidates += 1
    luna = LunaClient(cfg)
    event(logger, "VIRAL filtering complete", viral_candidates=report.viral_candidates, rejected_ratio=report.rejected_ratio)
    qualified = []
    for candidate in sorted(new, key=lambda x: x.viral_ratio or 0, reverse=True)[:cfg.qualified_max_per_run]:
        if time.monotonic() >= deadline:
            report.status = "partial_failure"
            report.errors.append("run_timeout_reached")
            break
        try:
            relevance = await qualify_candidate(luna, candidate, context, recent, historical)
            if relevance["decision"] == "keep":
                StateStore.mark(state, candidate, "qualified", relevance=relevance)
                qualified.append((candidate, relevance))
                report.relevance_qualified += 1
            else:
                StateStore.mark(state, candidate, "rejected_relevance", relevance=relevance)
                report.rejected_relevance += 1
        except Exception as exc:
            StateStore.mark(state, candidate, "intelligence_failed", error=str(exc))
            report.failed += 1
    # Relevance confidence is an explicit transferability signal. Freshness
    # falls back to discovery time when Instagram does not expose publication
    # time, so ties stay deterministic and newer candidates are preferred.
    qualified.sort(key=_qualified_rank, reverse=True)
    requested_content = cfg.analyze_max_per_run if max_content is None else max(0, max_content)
    for candidate, relevance in qualified[:min(requested_content, cfg.analyze_max_per_run)]:
        if time.monotonic() >= deadline:
            report.status = "partial_failure"
            report.errors.append("run_timeout_reached")
            break
        local_path = ""
        try:
            # Keep health/status importable even if a platform-specific Gemini SDK
            # wheel is unavailable; only an actual analysis needs that dependency.
            from content_agent.video.downloader import download
            from content_agent.video.gemini_analyzer import GeminiAnalysisError, analyze_video
            reel = await download(candidate.source_url)
            local_path = reel["local_path"]
            report.videos_downloaded += 1
            event(logger, "DOWNLOAD completed", shortcode=candidate.shortcode)
            StateStore.mark(state, candidate, "downloaded", video_downloaded=True)
            try:
                gemini = await analyze_video(local_path, reel.get("caption_originale", candidate.caption_preview))
            except GeminiAnalysisError as exc:
                StateStore.mark(state, candidate, "video_analysis_pending", error=str(exc))
                report.failed += 1
                continue
            report.ai_calls["gemini"] += 1
            report.videos_analyzed += 1
            _record_gemini_usage(report, gemini)
            event(logger, "GEMINI analyzed", shortcode=candidate.shortcode)
            StateStore.mark(state, candidate, "analyzed", gemini_analysis_available=True, gemini_model=gemini.get("_gemini_model_used", ""))
            adaptation = await adapt_to_angellos(luna, candidate, gemini, context, recent, historical, cfg.content_language, cfg.content_presentation_style)
            quality = await quality_check(luna, adaptation, context, recent)
            if not quality["approved"]:
                # One bounded correction; no unbounded self-revision loop.
                adaptation = await adapt_to_angellos(luna, candidate, gemini, context + "\nCorrect these quality issues: " + "; ".join(quality["issues"]), recent, historical, cfg.content_language, cfg.content_presentation_style)
                quality = await quality_check(luna, adaptation, context, recent)
            if not quality["approved"]:
                StateStore.mark(state, candidate, "rejected_quality", quality=quality)
                continue
            existing_url = None if dry_run else calendar.source_page_url(candidate.source_url)
            if existing_url:
                StateStore.mark(state, candidate, "scheduled", notion_url=existing_url)
                report.duplicates += 1
                continue
            url = calendar.create(candidate, gemini, adaptation, dry_run=dry_run)
            # The full analysis and brief live in the Notion editorial page. The
            # state store intentionally remains compact and durable at scale.
            StateStore.mark(state, candidate, "scheduled" if not dry_run else "adapted", adaptation_ready=True, notion_url=url or "")
            if not dry_run:
                report.content_ideas_created += 1
                report.created_items.append({"title": adaptation["internal_title"], "notion_url": url, "viral_ratio": candidate.viral_ratio})
                event(logger, "NOTION idea created", shortcode=candidate.shortcode)
        except Exception as exc:
            StateStore.mark(state, candidate, "failed", error=str(exc))
            report.failed += 1
        finally:
            if local_path and cfg.delete_source_video_after_analysis:
                try:
                    Path(local_path).unlink(missing_ok=True)
                except OSError:
                    pass
    report.ai_calls["luna"] = luna.calls
    _set_cost_report(report, cfg, luna, apify_cost_usd, apify_cost_known)
    try:
        state_store.save(state)
    except Exception as exc:
        report.errors.append(f"state_persistence_failed: {exc}")
        report.status = "partial_failure"
    if report.failed and report.status == "completed":
        report.status = "partial_failure"
    event(logger, "SCOUT finished", status=report.status, created=report.content_ideas_created, failed=report.failed)
    return report.finish()


async def analyze_reel_url(source_url: str, *, dry_run: bool = False, cfg: Settings = settings) -> dict:
    """Analyze one supplied Instagram Reel through the same Gemini→Luna safety gates."""
    from content_agent.models.schemas import Candidate
    from content_agent.video.downloader import download
    from content_agent.video.gemini_analyzer import analyze_video, GeminiAnalysisError

    match = re.search(r"/(?:reel|p)/([A-Za-z0-9_-]+)", source_url)
    if not match:
        return {"status": "configuration_error", "error": "Expected an Instagram /reel/<shortcode>/ URL"}
    report = RunReport(scanned=1, new_reels=1)
    browser = InstagramBrowser(cfg)
    try:
        await browser.check_authentication()
    except InstagramHumanActionRequired as exc:
        report.status, report.human_action_required, report.human_action_reason = "human_action_required", True, str(exc)
        return report.finish()
    candidate = Candidate(source_url=source_url, shortcode=match.group(1), discovery_method="manual_url")
    calendar = NotionEditorialCalendar(cfg)
    apify_cost_usd = 0.0
    apify_cost_known = True
    try:
        await browser.enrich(candidate, {})
        # The direct/manual route is still a production artifact: preserve the
        # same verifiable source metrics as the daily scout when the bounded
        # Apify fallback is enabled. Browser data stays the free first choice.
        if cfg.apify_metrics_fallback and cfg.apify_api_key:
            try:
                from content_agent.discovery.apify_metrics import enrich_reel_metrics
                apify_result = await enrich_reel_metrics([candidate], cfg.apify_api_key, 1)
                apify_cost_usd = apify_result.cost_usd or 0.0
                apify_cost_known = apify_result.cost_usd is not None
            except Exception as exc:
                report.errors.append(f"apify_metrics_fallback_failed: {exc}")
        candidate.viral_ratio = viral_ratio(candidate.views, candidate.followers)
        context, recent, historical = fetch_angellos_context(), calendar.recent_content(), _historical_signals(cfg)
        reel = await download(candidate.source_url)
        report.videos_downloaded += 1
        try:
            gemini = await analyze_video(reel["local_path"], reel.get("caption_originale", candidate.caption_preview))
        except GeminiAnalysisError as exc:
            report.status, report.failed, report.errors = "partial_failure", 1, [f"video_analysis_pending: {exc}"]
            report.costs = _cost_report_without_luna(report, cfg, apify_cost_usd, apify_cost_known)
            return report.finish()
        report.ai_calls["gemini"] = 1
        report.videos_analyzed = 1
        _record_gemini_usage(report, gemini)
        luna = LunaClient(cfg)
        adaptation = await adapt_to_angellos(luna, candidate, gemini, context, recent, historical, cfg.content_language, cfg.content_presentation_style)
        gate = await quality_check(luna, adaptation, context, recent)
        if not gate["approved"]:
            # A supplied Reel receives the same single corrective pass as the
            # scheduled scout.  This preserves the quality gate while giving a
            # viable source one precise chance to resolve novelty or claim
            # issues; it is deliberately not an open-ended rewrite loop.
            adaptation = await adapt_to_angellos(
                luna,
                candidate,
                gemini,
                context + "\nCorrect these quality issues: " + "; ".join(gate["issues"]),
                recent,
                historical,
                cfg.content_language,
                cfg.content_presentation_style,
            )
            gate = await quality_check(luna, adaptation, context, recent)
        report.ai_calls["luna"] = luna.calls
        if not gate["approved"]:
            report.status, report.failed, report.errors = "partial_failure", 1, ["quality_gate_rejected: " + "; ".join(gate["issues"])]
            _set_cost_report(report, cfg, luna, apify_cost_usd, apify_cost_known)
            return report.finish()
        notion_url = calendar.create(candidate, gemini, adaptation, dry_run=dry_run)
        if not dry_run:
            report.content_ideas_created = 1
            report.created_items.append({"title": adaptation["internal_title"], "notion_url": notion_url, "viral_ratio": candidate.viral_ratio})
        _set_cost_report(report, cfg, luna, apify_cost_usd, apify_cost_known)
        return report.finish()
    except Exception as exc:
        report.status, report.failed, report.errors = "partial_failure", 1, [str(exc)]
        report.costs = _unknown_cost_report()
        return report.finish()
    finally:
        # This direct command follows the same source-retention policy as the scout.
        try:
            if cfg.delete_source_video_after_analysis and 'reel' in locals():
                Path(reel["local_path"]).unlink(missing_ok=True)
        except OSError:
            pass


def _historical_signals(cfg: Settings = settings, state: Optional[dict] = None) -> dict:
    """Load durable learning first; local JSON is only a development cache."""
    if state is None:
        try:
            state = StateStore(cfg).load(strict=False)
        except Exception:
            state = {}
    learned = (state or {}).get("performance_patterns")
    if isinstance(learned, dict):
        signals = learned.get("historical_signals", learned)
        if isinstance(signals, dict):
            return {
                "successful_hook_patterns": signals.get("successful_hook_patterns", []),
                "successful_formats": signals.get("successful_formats", []),
                "successful_topics": signals.get("successful_topics", []),
                "weak_patterns": signals.get("weak_patterns", []),
                "notes": signals.get("notes", learned.get("insights", [])),
            }
    path = Path("performance_patterns.json")
    if not path.exists():
        return {"successful_hook_patterns": [], "successful_formats": [], "successful_topics": [], "weak_patterns": [], "notes": []}
    import json
    try:
        data = json.loads(path.read_text())
        patterns = data.get("patterns", {})
        return {"successful_hook_patterns": patterns.get("hooks_gagnants", []), "successful_formats": patterns.get("formats_gagnants", []), "successful_topics": patterns.get("sujets_performants", []), "weak_patterns": patterns.get("patterns_faibles", []), "notes": data.get("insights", [])}
    except Exception:
        return {"successful_hook_patterns": [], "successful_formats": [], "successful_topics": [], "weak_patterns": [], "notes": []}


def _qualified_rank(item):
    candidate, relevance = item
    return (
        candidate.viral_ratio or 0,
        relevance.get("confidence", 0),
        candidate.source_published_at or candidate.discovered_at,
    )


def _set_cost_report(report: RunReport, cfg: Settings, luna, apify_cost_usd: float, apify_cost_known: bool) -> None:
    luna_cost = getattr(luna, "estimated_cost_usd", lambda: None)()
    gemini_cost = _gemini_cost(report, cfg)
    values = {"apify_usd": apify_cost_usd if apify_cost_known else None, "luna_usd": luna_cost, "gemini_usd": gemini_cost}
    total = sum(values.values()) if all(value is not None for value in values.values()) else None
    report.costs = {
        **values,
        "total_usd": total,
        "currency": "USD",
        "complete": total is not None,
        # Token counts remain useful and auditable when an environment has not
        # configured a price card for its provider/model yet.
        "luna_input_tokens": getattr(luna, "input_tokens", 0),
        "luna_output_tokens": getattr(luna, "output_tokens", 0),
        "gemini_videos": report.videos_analyzed,
        "gemini_input_tokens": report.gemini_input_tokens,
        "gemini_output_tokens": report.gemini_output_tokens,
    }


def _unknown_cost_report() -> dict:
    return {
        "apify_usd": 0.0,
        "luna_usd": None,
        "gemini_usd": None,
        "total_usd": None,
        "currency": "USD",
        "complete": False,
        "luna_input_tokens": 0,
        "luna_output_tokens": 0,
        "gemini_videos": 0,
        "gemini_input_tokens": 0,
        "gemini_output_tokens": 0,
    }


def _cost_report_without_luna(report: RunReport, cfg: Settings, apify_cost_usd: float, apify_cost_known: bool) -> dict:
    """Report known collection/perception costs when Gemini fails before Luna."""
    gemini_cost = _gemini_cost(report, cfg)
    values = {"apify_usd": apify_cost_usd if apify_cost_known else None, "luna_usd": 0.0, "gemini_usd": gemini_cost}
    total = sum(values.values()) if all(value is not None for value in values.values()) else None
    return {**values, "total_usd": total, "currency": "USD", "complete": total is not None, "luna_input_tokens": 0, "luna_output_tokens": 0, "gemini_videos": report.videos_analyzed, "gemini_input_tokens": report.gemini_input_tokens, "gemini_output_tokens": report.gemini_output_tokens}


def _record_gemini_usage(report: RunReport, analysis: dict) -> None:
    usage = analysis.get("_gemini_usage", {}) if isinstance(analysis, dict) else {}
    if not isinstance(usage, dict):
        return
    report.gemini_input_tokens += int(usage.get("input_tokens", 0) or 0)
    report.gemini_output_tokens += int(usage.get("output_tokens", 0) or 0)


def _gemini_cost(report: RunReport, cfg: Settings):
    input_rate = cfg.gemini_input_usd_per_million_tokens
    output_rate = cfg.gemini_output_usd_per_million_tokens
    if input_rate is not None and output_rate is not None:
        return (report.gemini_input_tokens * input_rate + report.gemini_output_tokens * output_rate) / 1_000_000
    if not report.videos_analyzed:
        return 0.0
    if cfg.gemini_analysis_usd_per_video is not None:
        return report.videos_analyzed * cfg.gemini_analysis_usd_per_video
    return None


async def sync_performance(cfg: Settings = settings) -> dict:
    """Refresh @angellos.ai stats then let Luna write explicitly non-deterministic signals."""
    from content_agent.intelligence.performance_learning import analyze_performance_signals, save_patterns
    state_store = StateStore(cfg)
    if not state_store.durable and not cfg.allow_ephemeral_state:
        return {"status": "configuration_error", "errors": ["NOTION_CONTENT_AGENT_STATE_PAGE_ID is required for durable performance learning"], "costs": _unknown_cost_report()}
    try:
        state = state_store.load(strict=state_store.durable and not cfg.allow_ephemeral_state)
    except Exception as exc:
        return {"status": "partial_failure", "errors": [f"durable_state_unavailable: {exc}"], "costs": _unknown_cost_report()}
    calendar = NotionEditorialCalendar(cfg)
    try:
        from stats.instagram_stats import fetch_my_reels
        reels = await fetch_my_reels(limit=50)
        stats_result = calendar.sync_published_metrics(reels, "instagram_graph")
        apify_cost_usd, apify_cost_known = 0.0, True
    except Exception as graph_exc:
        if not (cfg.apify_metrics_fallback and cfg.apify_api_key):
            return {"status": "partial_failure", "errors": [f"performance_data_sync_failed: {graph_exc}"], "costs": _unknown_cost_report()}
        try:
            from content_agent.discovery.apify_metrics import fetch_account_reels
            result = await fetch_account_reels(cfg.angellos_instagram_account, cfg.apify_api_key, limit=50)
            stats_result = calendar.sync_published_metrics(result.reels, "apify")
            apify_cost_usd = result.cost_usd or 0.0
            apify_cost_known = result.cost_usd is not None
        except Exception as apify_exc:
            return {"status": "partial_failure", "errors": [f"performance_data_sync_failed: graph={graph_exc}; apify={apify_exc}"], "costs": _unknown_cost_report()}
    try:
        rows = calendar.performance_rows()
    except Exception as exc:
        return {"status": "partial_failure", "errors": [f"performance_data_sync_failed: {exc}"], "costs": _unknown_cost_report()}
    client = LunaClient(cfg)
    if not rows:
        report = RunReport()
        _set_cost_report(report, cfg, client, apify_cost_usd, apify_cost_known)
        return {"status": "completed", "stats_sync": stats_result, "performance_rows": 0, "ai_calls": {"luna": 0}, "patterns_updated": False, "costs": report.costs}
    try:
        patterns = await analyze_performance_signals(client, rows)
    except Exception as exc:
        report = RunReport()
        report.status, report.errors = "partial_failure", [f"performance_learning_failed: {exc}"]
        report.ai_calls["luna"] = client.calls
        _set_cost_report(report, cfg, client, apify_cost_usd, apify_cost_known)
        return report.finish()
    save_patterns(patterns)
    # Railway disk is ephemeral; the Notion ledger is the authoritative memory
    # injected into every following scout. Keep the JSON file only as a local
    # developer convenience and recovery aid.
    state["performance_patterns"] = patterns
    try:
        state_store.save(state)
    except Exception as exc:
        report = RunReport()
        report.status, report.errors = "partial_failure", [f"state_persistence_failed: {exc}"]
        report.ai_calls["luna"] = client.calls
        _set_cost_report(report, cfg, client, apify_cost_usd, apify_cost_known)
        return report.finish()
    report = RunReport()
    report.ai_calls["luna"] = client.calls
    _set_cost_report(report, cfg, client, apify_cost_usd, apify_cost_known)
    return {"status": "completed", "stats_sync": stats_result, "performance_rows": len(rows), "ai_calls": {"luna": client.calls}, "patterns_updated": True, "costs": report.costs}


def health(cfg: Settings = settings) -> dict:
    missing = [name for name, value in {"OPENAI_API_KEY": cfg.openai_api_key, "OPENAI_CONTENT_MODEL": cfg.openai_content_model, "GEMINI_API_KEY": cfg.gemini_api_key, "NOTION_API_KEY": cfg.notion_api_key, "NOTION_PROGRAMME_CONTENT_DB": cfg.notion_programme_content_db, "NOTION_CONTENT_AGENT_STATE_PAGE_ID": cfg.notion_state_page_id}.items() if not is_configured(value)]
    instagram_auth_configured = Path(cfg.instagram_session_path).exists() or is_configured(cfg.instagram_cookies_b64)
    return {"status": "ok" if not missing else "configuration_error", "missing": missing, "instagram_session_ready": instagram_auth_configured, "durable_state_configured": is_configured(cfg.notion_state_page_id), "model": cfg.openai_content_model or None}


def status(cfg: Settings = settings) -> dict:
    """Read-only operational summary suitable for Hermes and humans."""
    state = StateStore(cfg).load()
    counts: dict[str, int] = {}
    for record in state.get("reels", {}).values():
        label = record.get("status", "unknown")
        counts[label] = counts.get(label, 0) + 1
    return {"status": "ok", "durable_state_configured": StateStore(cfg).durable, "reels_by_state": counts, "follower_cache_entries": len(state.get("followers", {}))}
