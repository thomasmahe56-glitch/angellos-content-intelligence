import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

const API_URL = import.meta.env.VITE_API_URL || "";
import {
  Download, Brain, Database, Check, Loader2, ExternalLink,
  RotateCcw, Sparkles, Users, Eye, Calendar, MessageCircle, Heart,
} from "lucide-react";
import { Toaster, toast } from "sonner";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/")({
  component: Index,
  head: () => ({
    meta: [
      { title: "Angellos Content Intelligence" },
      { name: "description", content: "Analyze top Instagram Reels from your Dream 100." },
    ],
  }),
});

// ── Types ──────────────────────────────────────────────────────────────────

type StepStatus = "pending" | "running" | "done";
type Mode = "reel" | "dream100";

interface Step {
  key: "download" | "analyze" | "push";
  label: string;
  icon: typeof Download;
  status: StepStatus;
}

interface Result {
  script: string;
  hook: string;
  format: string;
  notion_url?: string;
  caption_originale?: string;
  caption_angellos?: string;
}

interface D100Reel {
  url: string;
  thumbnail: string;
  views: number;
  comments: number;
  likes: number;
  date: string;
  caption: string;
  account: string;
}

interface D100Result {
  url: string;
  account: string;
  result: Result;
}

// ── Constants ──────────────────────────────────────────────────────────────

const INITIAL_STEPS: Step[] = [
  { key: "download", label: "Video download", icon: Download, status: "pending" },
  { key: "analyze", label: "Gemini analysis + Angellos adaptation", icon: Brain, status: "pending" },
  { key: "push", label: "Push to Notion", icon: Database, status: "pending" },
];

function fmtNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return String(n);
}

type D100Tab = "views" | "comments";

// ── Main component ─────────────────────────────────────────────────────────

function Index() {
  const [mode, setMode] = useState<Mode>("reel");

  // — pipeline state (shared between modes) —
  const [steps, setSteps] = useState<Step[]>(INITIAL_STEPS);
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<Result | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);

  // — single reel mode —
  const [url, setUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  // — Dream 100 mode —
  const [d100Account, setD100Account] = useState("");
  const [d100Fetching, setD100Fetching] = useState(false);
  const [d100Reels, setD100Reels] = useState<D100Reel[]>([]);
  const [d100Selected, setD100Selected] = useState<Set<string>>(new Set());
  const [d100Queue, setD100Queue] = useState<string[]>([]);
  const [d100CurrentIdx, setD100CurrentIdx] = useState(0);
  const [d100Results, setD100Results] = useState<D100Result[]>([]);
  const [d100Analyzing, setD100Analyzing] = useState(false);
  const [d100Tab, setD100Tab] = useState<D100Tab>("views");
  const [syncingMyStats, setSyncingMyStats] = useState(false);
  const [myStatsResult, setMyStatsResult] = useState<{
    apify_reels: number; created: number;
    analyzed: number; skipped_analyzed: number;
    dl_errors: number; analysis_errors: number;
    pattern_insight: string;
  } | null>(null);
  const d100EsRef = useRef<EventSource | null>(null);

  const reelsByViews = [...d100Reels].sort((a, b) => b.views - a.views);
  const reelsByComments = [...d100Reels].sort((a, b) => b.comments - a.comments);
  const activeReels = d100Tab === "views" ? reelsByViews : reelsByComments;

  useEffect(() => () => {
    esRef.current?.close();
    d100EsRef.current?.close();
  }, []);

  // ── Pipeline helpers ───────────────────────────────────────────────────

  const updateStep = (key: Step["key"], status: StepStatus) =>
    setSteps((prev) => prev.map((s) => (s.key === key ? { ...s, status } : s)));

  const resetPipeline = () => {
    setSteps(INITIAL_STEPS);
    setProgress(0);
    setResult(null);
    setJobId(null);
  };

  const openSSE = (
    id: string,
    ref: React.MutableRefObject<EventSource | null>,
    onDone: (r: Result) => void,
    onError: () => void,
  ) => {
    ref.current?.close();
    const es = new EventSource(`${API_URL}/status/${id}`);
    ref.current = es;
    // Guard against onerror firing after a successful done (server closes the stream).
    let completed = false;

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (typeof data.progress === "number") setProgress(data.progress);
        if (data.step && data.step_status) updateStep(data.step, data.step_status);
        if (data.status === "done" && data.result) {
          completed = true;
          setSteps((prev) => prev.map((s) => ({ ...s, status: "done" })));
          setProgress(100);
          es.close();
          onDone(data.result as Result);
        }
        if (data.status === "error") {
          completed = true;
          toast.error(data.error || "An error occurred during analysis");
          es.close();
          onError();
        }
      } catch { /* skip parse errors */ }
    };

    es.onerror = () => {
      if (completed) return; // stream closed normally after done — not a real error
      toast.error("Lost connection to the API. The analysis may still be running — refresh to check.");
      es.close();
      onError();
    };
  };

  // ── Single reel ────────────────────────────────────────────────────────

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim()) { toast.error("Paste an Instagram URL first"); return; }
    setSubmitting(true);
    resetPipeline();

    try {
      const res = await fetch(`${API_URL}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url.trim() }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const { job_id } = await res.json() as { job_id: string };
      setJobId(job_id);
      openSSE(job_id, esRef,
        (r) => { setResult(r); setSubmitting(false); },
        () => setSubmitting(false),
      );
    } catch {
      toast.error("Could not start analysis. Is the API running?");
      setSubmitting(false);
    }
  };

  const resetReel = () => {
    esRef.current?.close();
    setUrl("");
    resetPipeline();
    setSubmitting(false);
  };

  // ── Dream 100 ──────────────────────────────────────────────────────────

  const handleD100Fetch = async (e: React.FormEvent) => {
    e.preventDefault();
    const account = d100Account.trim().replace(/^@/, "");
    if (!account) { toast.error("Enter an Instagram account name"); return; }

    setD100Fetching(true);
    setD100Reels([]);
    setD100Selected(new Set());
    setD100Results([]);
    setD100Analyzing(false);
    resetPipeline();

    try {
      const res = await fetch(`${API_URL}/dream100/fetch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      if (!data.reels?.length) { toast.error("No Reels found for this account"); return; }
      setD100Reels(data.reels);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Apify scraper error");
    } finally {
      setD100Fetching(false);
    }
  };

  const toggleSelect = (reelUrl: string) =>
    setD100Selected((prev) => {
      const next = new Set(prev);
      next.has(reelUrl) ? next.delete(reelUrl) : next.add(reelUrl);
      return next;
    });

  const handleD100Analyze = () => {
    const urls = [...d100Selected];
    if (!urls.length) { toast.error("Select at least one Reel"); return; }
    setD100Queue(urls);
    setD100CurrentIdx(0);
    setD100Results([]);
    setD100Analyzing(true);
    resetPipeline();
    runNext(urls, 0);
  };

  const runNext = async (urls: string[], idx: number) => {
    if (idx >= urls.length) {
      setD100Analyzing(false);
      toast.success(`${urls.length} Reel${urls.length > 1 ? "s" : ""} analyzed!`);
      return;
    }
    setD100CurrentIdx(idx);
    resetPipeline();

    try {
      const res = await fetch(`${API_URL}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: urls[idx] }),
      });
      if (!res.ok) { runNext(urls, idx + 1); return; }
      const { job_id } = await res.json() as { job_id: string };
      setJobId(job_id);

      openSSE(job_id, d100EsRef,
        (r) => {
          const account = d100Reels.find((x) => x.url === urls[idx])?.account ?? "";
          setD100Results((prev) => [...prev, { url: urls[idx], account, result: r }]);
          setTimeout(() => runNext(urls, idx + 1), 500);
        },
        () => runNext(urls, idx + 1),
      );
    } catch {
      runNext(urls, idx + 1);
    }
  };

  const handleSyncMyStats = async () => {
    setSyncingMyStats(true);
    setMyStatsResult(null);
    try {
      const res = await fetch(`${API_URL}/sync-my-stats`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setMyStatsResult(data);
      toast.success(`${data.analyzed} Reels analyzed by Gemini · ${data.created} new Notion pages`);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Apify sync error @angellos.ai");
    } finally {
      setSyncingMyStats(false);
    }
  };

  const d100CurrentReel = d100Queue[d100CurrentIdx]
    ? d100Reels.find((r) => r.url === d100Queue[d100CurrentIdx])
    : null;

  // ── Render ─────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen px-6 py-12 md:py-20">
      <Toaster theme="dark" position="top-right" richColors />
      <div className="mx-auto max-w-3xl space-y-12">

        {/* Header */}
        <header className="space-y-4 animate-fade-in">
          <div className="flex items-center gap-2 text-xs uppercase tracking-[0.2em] text-muted-foreground">
            <Sparkles className="h-3.5 w-3.5" />
            Angellos Content Intelligence
          </div>
          <h1 className="text-4xl md:text-5xl font-semibold tracking-tight">
            {mode === "reel"
              ? <>Analyze a <span className="text-angellos-gradient">Reel</span></>
              : <>Dream <span className="text-angellos-gradient">100</span></>}
          </h1>
          {/* Mode toggle + Sync Stats */}
          <div className="flex items-center gap-3">
            <div className="flex gap-1 p-1 rounded-xl bg-secondary w-fit">
              {(["reel", "dream100"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={cn(
                    "flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all",
                    mode === m
                      ? "bg-card text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {m === "reel" ? <Download className="h-3.5 w-3.5" /> : <Users className="h-3.5 w-3.5" />}
                  {m === "reel" ? "Analyze a Reel" : "Dream 100"}
                </button>
              ))}
            </div>

            <Button
              variant="outline"
              onClick={handleSyncMyStats}
              disabled={syncingMyStats}
              title="Sync @angellos.ai Reel stats via Apify"
              className="h-9 px-3 gap-2 text-xs border-border bg-transparent hover:bg-secondary"
            >
              {syncingMyStats
                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                : <RotateCcw className="h-3.5 w-3.5" />}
              {syncingMyStats ? "Syncing…" : "🔄 Sync my stats"}
            </Button>

          </div>

          {myStatsResult && (
            <div className="rounded-xl border border-border bg-card px-4 py-3 text-sm animate-fade-in">
              <span className="text-muted-foreground">
                {myStatsResult.apify_reels} Reels
                {myStatsResult.analyzed > 0 && ` · ${myStatsResult.analyzed} analyzed by Gemini`}
                {myStatsResult.skipped_analyzed > 0 && ` · ${myStatsResult.skipped_analyzed} already analyzed`}
                {myStatsResult.created > 0 && ` · ${myStatsResult.created} new pages`}
                {(myStatsResult.dl_errors + myStatsResult.analysis_errors) > 0 && (
                  <span className="text-destructive">
                    {` · ${myStatsResult.dl_errors + myStatsResult.analysis_errors} errors`}
                  </span>
                )}
              </span>
              {myStatsResult.pattern_insight && (
                <>
                  <br />
                  <span className="font-medium text-foreground">
                    Winning pattern: {myStatsResult.pattern_insight}
                  </span>
                </>
              )}
            </div>
          )}
        </header>

        {/* ═══ MODE : Single Reel ═══════════════════════════════════════════ */}
        {mode === "reel" && (
          <>
            {/* Input */}
            <section className="animate-fade-in">
              <form onSubmit={handleSubmit} className="space-y-4">
                <div className="rounded-2xl border border-border bg-card p-2 focus-within:border-[color:var(--angellos-green)] transition-colors">
                  <textarea
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    placeholder="Paste the Instagram Reel URL..."
                    rows={3}
                    disabled={submitting}
                    className="w-full resize-none bg-transparent px-4 py-3 text-base outline-none placeholder:text-muted-foreground disabled:opacity-60"
                  />
                </div>
                <div className="flex flex-col-reverse sm:flex-row sm:items-center sm:justify-between gap-3">
                  <p className="text-xs text-muted-foreground">
                    Top Reels from your Dream 100 only
                  </p>
                  <Button
                    type="submit"
                    disabled={submitting}
                    className="bg-angellos-gradient text-black border-0 hover:opacity-90 hover:bg-angellos-gradient h-11 px-6 font-medium shadow-[0_8px_30px_-10px_rgba(200,249,123,0.5)]"
                  >
                    {submitting
                      ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" />Analyzing...</>
                      : "Run analysis"}
                  </Button>
                </div>
              </form>
            </section>

            {/* Progress */}
            {jobId !== null && (
              <section className="animate-fade-in space-y-6">
                <ProgressBar progress={progress} />
                <div className="space-y-3">
                  {steps.map((s) => <StepRow key={s.key} step={s} />)}
                </div>
              </section>
            )}

            {/* Result */}
            {result !== null && (
              <section className="animate-fade-in space-y-5">
                <div className="flex flex-wrap gap-2">
                  <Badge label="Hook" value={result.hook} />
                  <Badge label="Format" value={result.format} />
                </div>

                <ContentCard title="Generated Angellos script">
                  <div className="max-h-[420px] overflow-y-auto px-5 py-4 text-sm leading-relaxed whitespace-pre-wrap">
                    {result.script}
                  </div>
                </ContentCard>

                {result.caption_originale && (
                  <ContentCard title="Original caption">
                    <div className="max-h-[200px] overflow-y-auto px-5 py-4 text-sm leading-relaxed whitespace-pre-wrap text-muted-foreground">
                      {result.caption_originale}
                    </div>
                  </ContentCard>
                )}

                {result.caption_angellos && (
                  <div className="rounded-2xl border border-[color:var(--angellos-green)]/40 bg-[color:var(--angellos-green)]/5 overflow-hidden">
                    <div className="px-5 py-3 border-b border-[color:var(--angellos-green)]/30 text-xs uppercase tracking-wider text-muted-foreground">
                      Angellos caption
                    </div>
                    <div className="max-h-[300px] overflow-y-auto px-5 py-4 text-sm leading-relaxed whitespace-pre-wrap">
                      {result.caption_angellos}
                    </div>
                  </div>
                )}

                <div className="flex flex-wrap gap-3">
                  {result.notion_url && (
                    <Button
                      asChild
                      className="bg-angellos-gradient text-black border-0 hover:opacity-90 hover:bg-angellos-gradient h-11 px-5 shadow-[0_8px_30px_-10px_rgba(200,249,123,0.5)]"
                    >
                      <a href={result.notion_url} target="_blank" rel="noreferrer">
                        View in Notion <ExternalLink className="ml-2 h-4 w-4" />
                      </a>
                    </Button>
                  )}
                  <Button
                    variant="outline"
                    onClick={resetReel}
                    className="h-11 px-5 border-border bg-transparent hover:bg-secondary"
                  >
                    <RotateCcw className="mr-2 h-4 w-4" />
                    Analyze another Reel
                  </Button>
                </div>
              </section>
            )}
          </>
        )}

        {/* ═══ MODE : Dream 100 ════════════════════════════════════════════ */}
        {mode === "dream100" && (
          <>
            {/* Account input */}
            {!d100Analyzing && (
              <section className="animate-fade-in">
                <form onSubmit={handleD100Fetch} className="space-y-3">
                  <div className="flex gap-3">
                    <div className="flex-1 flex items-center rounded-2xl border border-border bg-card px-5 focus-within:border-[color:var(--angellos-green)] transition-colors">
                      <span className="text-muted-foreground mr-1 flex-shrink-0">@</span>
                      <input
                        value={d100Account}
                        onChange={(e) => setD100Account(e.target.value.replace(/^@/, ""))}
                        placeholder="accountname"
                        disabled={d100Fetching}
                        className="flex-1 bg-transparent py-4 text-base outline-none placeholder:text-muted-foreground disabled:opacity-60"
                      />
                    </div>
                    <Button
                      type="submit"
                      disabled={d100Fetching}
                      className="bg-angellos-gradient text-black border-0 hover:opacity-90 h-[56px] px-6 font-medium shadow-[0_8px_30px_-10px_rgba(200,249,123,0.5)]"
                    >
                      {d100Fetching
                        ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" />Scraping...</>
                        : "Scrape"}
                    </Button>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Top 5 Reels sorted by views · Apify Instagram Scraper
                  </p>
                </form>
              </section>
            )}

            {/* Reel selection */}
            {d100Reels.length > 0 && !d100Analyzing && (
              <section className="animate-fade-in space-y-4">
                <div className="flex items-center justify-between">
                  <h2 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                    {d100Reels.length} Reels · @{d100Reels[0]?.account}
                  </h2>
                  {d100Selected.size > 0 && (
                    <span className="text-xs font-medium text-[color:var(--angellos-green)]">
                      {d100Selected.size} selected
                    </span>
                  )}
                </div>

                {/* Tabs */}
                <div className="flex gap-1 p-1 rounded-xl bg-secondary w-fit">
                  {([
                    { key: "views",    label: "🔥 Top Views" },
                    { key: "comments", label: "💬 Top Comments" },
                  ] as { key: D100Tab; label: string }[]).map(({ key, label }) => (
                    <button
                      key={key}
                      onClick={() => setD100Tab(key)}
                      className={cn(
                        "px-4 py-2 rounded-lg text-sm font-medium transition-all",
                        d100Tab === key
                          ? "bg-card text-foreground shadow-sm"
                          : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      {label}
                    </button>
                  ))}
                </div>

                <div className="space-y-3">
                  {activeReels.map((reel, i) => (
                    <D100ReelCard
                      key={reel.url}
                      reel={reel}
                      rank={i + 1}
                      selected={d100Selected.has(reel.url)}
                      onToggle={() => toggleSelect(reel.url)}
                    />
                  ))}
                </div>

                <div className="flex items-center justify-between pt-2">
                  <p className="text-xs text-muted-foreground">
                    Global selection — visible across both tabs
                  </p>
                  <Button
                    onClick={handleD100Analyze}
                    disabled={d100Selected.size === 0}
                    className="bg-angellos-gradient text-black border-0 hover:opacity-90 h-11 px-6 font-medium shadow-[0_8px_30px_-10px_rgba(200,249,123,0.5)] disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    Analyze selected ({d100Selected.size})
                  </Button>
                </div>
              </section>
            )}

            {/* Current reel analysis progress */}
            {d100Analyzing && (
              <section className="animate-fade-in space-y-6">
                <div className="flex items-center gap-4 rounded-xl border border-border bg-card p-4">
                  {d100CurrentReel?.thumbnail && (
                    <img
                      src={d100CurrentReel.thumbnail}
                      alt=""
                      className="h-14 w-10 rounded-lg object-cover flex-shrink-0"
                    />
                  )}
                  <div className="min-w-0">
                    <div className="text-xs text-muted-foreground uppercase tracking-wider mb-0.5">
                      Analyzing
                    </div>
                    <div className="text-sm font-medium truncate">
                      Reel {d100CurrentIdx + 1} / {d100Queue.length}
                      {d100CurrentReel && (
                        <span className="text-muted-foreground"> — @{d100CurrentReel.account}</span>
                      )}
                    </div>
                    {d100CurrentReel?.views ? (
                      <div className="flex items-center gap-1 text-xs text-muted-foreground mt-0.5">
                        <Eye className="h-3 w-3" />
                        {fmtNum(d100CurrentReel.views)} views
                      </div>
                    ) : null}
                  </div>
                </div>
                <ProgressBar progress={progress} />
                <div className="space-y-3">
                  {steps.map((s) => <StepRow key={s.key} step={s} />)}
                </div>
              </section>
            )}

            {/* Accumulated results */}
            {d100Results.length > 0 && (
              <section className="animate-fade-in space-y-4">
                <div className="flex items-center gap-2">
                  <h2 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                    Results ({d100Results.length})
                  </h2>
                  {d100Analyzing && (
                    <span className="text-xs text-[color:var(--angellos-green)] flex items-center gap-1">
                      <Loader2 className="h-3 w-3 animate-spin" />
                      analyzing…
                    </span>
                  )}
                </div>

                <div className="space-y-3">
                  {d100Results.map((item, i) => (
                    <D100ResultCard key={item.url} item={item} index={i + 1} />
                  ))}
                </div>

                {!d100Analyzing && (
                  <Button
                    variant="outline"
                    onClick={() => {
                      setD100Reels([]);
                      setD100Selected(new Set());
                      setD100Results([]);
                      setD100Queue([]);
                      setD100Account("");
                      resetPipeline();
                    }}
                    className="h-11 px-5 border-border bg-transparent hover:bg-secondary"
                  >
                    <RotateCcw className="mr-2 h-4 w-4" />
                    New account
                  </Button>
                )}
              </section>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ── Shared components ──────────────────────────────────────────────────────

function ProgressBar({ progress }: { progress: number }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>Progress</span>
        <span>{Math.round(progress)}%</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-secondary">
        <div
          className="h-full progress-shimmer rounded-full transition-all duration-500 ease-out"
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  );
}

function ContentCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-border bg-card overflow-hidden">
      <div className="px-5 py-3 border-b border-border text-xs uppercase tracking-wider text-muted-foreground">
        {title}
      </div>
      {children}
    </div>
  );
}

function StepRow({ step }: { step: Step }) {
  const Icon = step.icon;
  const isDone = step.status === "done";
  const isRunning = step.status === "running";

  return (
    <div
      className={cn(
        "flex items-center gap-4 rounded-xl border p-4 transition-all duration-300",
        isDone
          ? "border-[color:var(--angellos-green)]/40 bg-[color:var(--angellos-green)]/5"
          : isRunning
          ? "border-border bg-card"
          : "border-border bg-card/50 opacity-70",
      )}
    >
      <div
        className={cn(
          "flex h-10 w-10 items-center justify-center rounded-lg transition-colors",
          isDone
            ? "bg-[color:var(--angellos-green)] text-black"
            : isRunning
            ? "bg-secondary text-foreground"
            : "bg-secondary text-muted-foreground",
        )}
      >
        {isDone ? (
          <Check className="h-5 w-5" />
        ) : isRunning ? (
          <Loader2 className="h-5 w-5 animate-spin" />
        ) : (
          <Icon className="h-5 w-5" />
        )}
      </div>
      <div className="flex-1">
        <div className="text-sm font-medium">{step.label}</div>
        <div className="text-xs text-muted-foreground">
          {isDone ? "Done" : isRunning ? "Running..." : "Waiting"}
        </div>
      </div>
    </div>
  );
}

function Badge({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-full border border-border bg-card px-4 py-2 text-xs">
      <span className="text-muted-foreground">{label} · </span>
      <span className="text-foreground font-medium">{value}</span>
    </div>
  );
}

// ── Dream 100 components ───────────────────────────────────────────────────

function D100ReelCard({
  reel, rank, selected, onToggle,
}: {
  reel: D100Reel;
  rank: number;
  selected: boolean;
  onToggle: () => void;
}) {
  return (
    <div
      onClick={onToggle}
      className={cn(
        "flex gap-4 rounded-xl border p-4 cursor-pointer transition-all duration-200 select-none",
        selected
          ? "border-[color:var(--angellos-green)]/50 bg-[color:var(--angellos-green)]/5"
          : "border-border bg-card hover:bg-secondary/30",
      )}
    >
      {/* Thumbnail */}
      <div className="relative flex-shrink-0">
        {reel.thumbnail ? (
          <img
            src={reel.thumbnail}
            alt=""
            className="h-20 w-[52px] rounded-lg object-cover"
          />
        ) : (
          <div className="h-20 w-[52px] rounded-lg bg-secondary flex items-center justify-center text-muted-foreground text-xs font-mono">
            #{rank}
          </div>
        )}
        <div
          className={cn(
            "absolute -top-1.5 -right-1.5 h-5 w-5 rounded-full border-2 border-background flex items-center justify-center transition-colors",
            selected ? "bg-[color:var(--angellos-green)]" : "bg-secondary",
          )}
        >
          {selected && <Check className="h-3 w-3 text-black" />}
        </div>
      </div>

      {/* Meta */}
      <div className="flex-1 min-w-0">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mb-1.5">
          <span className="text-xs font-mono text-muted-foreground">#{rank}</span>
          <span className="flex items-center gap-1 text-sm font-semibold">
            <Eye className="h-3.5 w-3.5 text-muted-foreground" />
            {fmtNum(reel.views)}
          </span>
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <MessageCircle className="h-3 w-3" />
            {fmtNum(reel.comments)}
          </span>
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Heart className="h-3 w-3" />
            {fmtNum(reel.likes)}
          </span>
          {reel.date && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <Calendar className="h-3 w-3" />
              {reel.date}
            </span>
          )}
        </div>
        {reel.caption && (
          <p className="text-xs text-muted-foreground line-clamp-2 leading-relaxed">
            {reel.caption}
          </p>
        )}
      </div>
    </div>
  );
}

function D100ResultCard({ item, index }: { item: D100Result; index: number }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      {/* Header */}
      <div
        className="flex items-center justify-between px-5 py-3 border-b border-border cursor-pointer hover:bg-secondary/20 transition-colors"
        onClick={() => setExpanded((e) => !e)}
      >
        <div className="flex items-center gap-3">
          <div className="flex h-6 w-6 items-center justify-center rounded-full bg-[color:var(--angellos-green)]">
            <Check className="h-3.5 w-3.5 text-black" />
          </div>
          <span className="text-sm font-medium">
            Reel {index}
            {item.account && (
              <span className="text-muted-foreground font-normal"> · @{item.account}</span>
            )}
          </span>
        </div>
        <div className="flex items-center gap-3" onClick={(e) => e.stopPropagation()}>
          {item.result.notion_url && (
            <a
              href={item.result.notion_url}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              Notion <ExternalLink className="h-3 w-3" />
            </a>
          )}
          <span
            className="text-xs text-muted-foreground cursor-pointer"
            onClick={() => setExpanded((e) => !e)}
          >
            {expanded ? "▲" : "▼"}
          </span>
        </div>
      </div>

      {/* Badges (always visible) */}
      <div className="px-5 py-2.5 flex flex-wrap gap-2">
        {item.result.hook && (
          <Badge
            label="Hook"
            value={item.result.hook.length > 60
              ? item.result.hook.slice(0, 60) + "…"
              : item.result.hook}
          />
        )}
        {item.result.format && <Badge label="Format" value={item.result.format} />}
      </div>

      {/* Expanded content */}
      {expanded && (
        <div className="border-t border-border">
          <div className="px-5 py-4 text-sm leading-relaxed whitespace-pre-wrap max-h-72 overflow-y-auto">
            {item.result.script}
          </div>
          {item.result.caption_angellos && (
            <>
              <div className="px-5 py-2 border-t border-[color:var(--angellos-green)]/20 bg-[color:var(--angellos-green)]/5 text-xs uppercase tracking-wider text-muted-foreground">
                Angellos caption
              </div>
              <div className="px-5 py-4 text-sm leading-relaxed whitespace-pre-wrap max-h-48 overflow-y-auto bg-[color:var(--angellos-green)]/5">
                {item.result.caption_angellos}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
