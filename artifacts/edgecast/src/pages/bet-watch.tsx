import {
  AlertTriangle,
  CheckCircle,
  Clock,
  Eye,
  RefreshCw,
  TrendingUp,
  XCircle,
  Zap,
  MinusCircle,
  Info,
  Activity,
  Star,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useGetBetWatch } from "@workspace/api-client-react";
import type { BetWatchCandidate } from "@workspace/api-client-react";

// ---------------------------------------------------------------------------
// Status helpers
// ---------------------------------------------------------------------------

type WatchStatus =
  | "OFFICIAL-ELIGIBLE"
  | "NEAR OFFICIAL"
  | "WATCHING"
  | "PRELIMINARY"
  | "AVOID / STALE";

type TodaySort = "probability" | "edge";

const UNAVAILABLE_COPY = "Unavailable / insufficient data";

function statusColor(status: WatchStatus): string {
  switch (status) {
    case "OFFICIAL-ELIGIBLE": return "text-emerald-400";
    case "NEAR OFFICIAL":     return "text-yellow-400";
    case "WATCHING":          return "text-sky-400";
    case "PRELIMINARY":       return "text-muted-foreground";
    case "AVOID / STALE":     return "text-destructive";
    default:                  return "text-muted-foreground";
  }
}

function statusBg(status: WatchStatus): string {
  switch (status) {
    case "OFFICIAL-ELIGIBLE": return "bg-emerald-950/60 border-emerald-700/50";
    case "NEAR OFFICIAL":     return "bg-yellow-950/60 border-yellow-700/50";
    case "WATCHING":          return "bg-sky-950/60 border-sky-700/50";
    case "PRELIMINARY":       return "bg-secondary/50 border-border";
    case "AVOID / STALE":     return "bg-destructive/10 border-destructive/30";
    default:                  return "bg-secondary/50 border-border";
  }
}

function StatusIcon({ status }: { status: WatchStatus }) {
  switch (status) {
    case "OFFICIAL-ELIGIBLE": return <CheckCircle className="h-4 w-4 text-emerald-400" />;
    case "NEAR OFFICIAL":     return <Zap className="h-4 w-4 text-yellow-400" />;
    case "WATCHING":          return <Eye className="h-4 w-4 text-sky-400" />;
    case "PRELIMINARY":       return <MinusCircle className="h-4 w-4 text-muted-foreground" />;
    case "AVOID / STALE":     return <XCircle className="h-4 w-4 text-destructive" />;
    default:                  return <MinusCircle className="h-4 w-4 text-muted-foreground" />;
  }
}

function StatusBadge({ status }: { status: WatchStatus }) {
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-mono font-bold border ${statusBg(status)} ${statusColor(status)}`}
    >
      <StatusIcon status={status} />
      {status}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function fmtPct(n: number | null | undefined): string {
  if (n == null) return "UNKNOWN";
  return `${(n * 100).toFixed(0)}%`;
}

function fmtPrice(n: number | null | undefined): string {
  if (n == null) return "UNKNOWN";
  return `${(n * 100).toFixed(0)}¢`;
}

function fmtAge(secs: number | null | undefined): string {
  if (secs == null) return "UNKNOWN";
  if (secs < 60) return `${Math.round(secs)}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hrs = (secs / 3600).toFixed(1);
  return `${hrs} hr ago`;
}

function fmtClose(mins: number | null | undefined): string {
  if (mins == null) return "UNKNOWN";
  if (mins < 0) return "CLOSED";
  if (mins < 60) return `${Math.round(mins)} min`;
  const hrs = (mins / 60).toFixed(1);
  return `${hrs} hr`;
}

function todayPct(n: number | null | undefined): string {
  if (n == null) return UNAVAILABLE_COPY;
  return `${(n * 100).toFixed(0)}%`;
}

function todayKalshi(n: number | null | undefined): string {
  if (n == null) return UNAVAILABLE_COPY;
  return `${(n * 100).toFixed(0)}% · ${(n * 100).toFixed(0)}¢`;
}

function todayEdge(n: number | null | undefined): string {
  if (n == null) return UNAVAILABLE_COPY;
  return `${n >= 0 ? "+" : ""}${n.toFixed(1)}pp`;
}

function evidenceStatus(c: BetWatchCandidate): {
  label: "UNKNOWN" | "INSUFFICIENT";
  detail: string;
} {
  if (c.failed_ftb_guards.length > 0) {
    return {
      label: "INSUFFICIENT",
      detail: c.failed_ftb_guards.join(" · "),
    };
  }

  return {
    label: "UNKNOWN",
    detail: "Canonical runtime evidence baseline unavailable in this slice.",
  };
}

function compareCandidates(a: BetWatchCandidate, b: BetWatchCandidate, sortBy: TodaySort): number {
  if (sortBy === "probability") {
    return (
      b.model_probability - a.model_probability ||
      b.edge - a.edge ||
      a.rank - b.rank ||
      a.city.localeCompare(b.city) ||
      a.ticker.localeCompare(b.ticker)
    );
  }

  return (
    b.edge - a.edge ||
    b.model_probability - a.model_probability ||
    a.rank - b.rank ||
    a.city.localeCompare(b.city) ||
    a.ticker.localeCompare(b.ticker)
  );
}

function uniqueCandidates(candidates: BetWatchCandidate[]): BetWatchCandidate[] {
  const seen = new Set<string>();
  return candidates.filter((candidate) => {
    const key = `${candidate.ticker}:${candidate.side}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

// ---------------------------------------------------------------------------
// Candidate detail card
// ---------------------------------------------------------------------------

function CandidateCard({
  c,
  isBest = false,
}: {
  c: BetWatchCandidate;
  isBest?: boolean;
}) {
  const borderClass = isBest
    ? "border-2 border-primary/60 shadow-lg shadow-primary/10"
    : "border border-border";

  return (
    <div
      className={`rounded-lg bg-card p-4 md:p-5 space-y-4 ${borderClass}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="space-y-1">
          {isBest && (
            <p className="text-xs font-mono font-bold text-primary uppercase tracking-widest">
              ★ Best Bet Right Now
            </p>
          )}
          {c.specialization_city && !isBest && (
            <p className="text-xs font-mono text-yellow-400 flex items-center gap-1">
              <Star className="h-3 w-3" /> Focus city
            </p>
          )}
          {!c.specialization_city && (
            <p className="text-xs font-mono text-muted-foreground">
              Outside specialization set — WATCHING only
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={c.watch_status} />
            <span
              className={`text-sm font-mono font-bold px-2 py-0.5 rounded border ${
                c.side === "YES"
                  ? "text-emerald-400 bg-emerald-950/50 border-emerald-700/40"
                  : "text-red-400 bg-red-950/50 border-red-700/40"
              }`}
            >
              {c.side}
            </span>
            {c.ftb_eligible && (
              <span className="text-xs font-mono text-emerald-400 bg-emerald-950/40 border border-emerald-700/40 px-2 py-0.5 rounded">
                FTB ✓
              </span>
            )}
          </div>
          <p className="text-lg font-bold text-foreground">{c.city}</p>
          <p className="text-xs text-muted-foreground font-mono">{c.ticker}</p>
        </div>
        <div className="text-right space-y-0.5">
          <p className="text-2xl font-mono font-bold text-primary">
            {fmtPrice(c.kalshi_price)}
          </p>
          <p className="text-xs text-muted-foreground">Kalshi ask</p>
        </div>
      </div>

      <div className="bg-secondary/30 rounded p-3">
        <p className="text-sm text-foreground italic">"{c.contract_question}"</p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="space-y-0.5">
          <p className="text-xs text-muted-foreground uppercase tracking-wide">EdgeCast P(side)</p>
          <p className="text-base font-mono font-bold text-foreground">
            {fmtPct(c.model_probability)}
          </p>
        </div>
        <div className="space-y-0.5">
          <p className="text-xs text-muted-foreground uppercase tracking-wide">Est. Edge</p>
          <p
            className={`text-base font-mono font-bold ${
              c.edge >= 10 ? "text-emerald-400" : c.edge >= 5 ? "text-yellow-400" : "text-muted-foreground"
            }`}
          >
            {c.edge >= 0 ? "+" : ""}
            {c.edge.toFixed(1)} pp
          </p>
        </div>
        <div className="space-y-0.5">
          <p className="text-xs text-muted-foreground uppercase tracking-wide">Confidence</p>
          <p className="text-base font-mono font-bold text-foreground">{c.confidence}</p>
        </div>
        <div className="space-y-0.5">
          <p className="text-xs text-muted-foreground uppercase tracking-wide">Model</p>
          <p className="text-base font-mono font-bold text-foreground">{c.model_version}</p>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm">
        {c.forecast_value != null && (
          <div className="space-y-0.5">
            <p className="text-xs text-muted-foreground uppercase tracking-wide">Forecast</p>
            <p className="font-mono text-foreground">{c.forecast_value.toFixed(1)}°F</p>
          </div>
        )}
        {c.contract_boundary && (
          <div className="space-y-0.5">
            <p className="text-xs text-muted-foreground uppercase tracking-wide">Threshold</p>
            <p className="font-mono text-foreground">{c.contract_boundary}</p>
          </div>
        )}
        {c.settlement_date && (
          <div className="space-y-0.5">
            <p className="text-xs text-muted-foreground uppercase tracking-wide">Settles</p>
            <p className="font-mono text-foreground text-xs">{c.settlement_date.slice(0, 10)}</p>
          </div>
        )}
        <div className="space-y-0.5">
          <p className="text-xs text-muted-foreground uppercase tracking-wide">Quote age</p>
          <p
            className={`font-mono ${
              c.quote_age_seconds == null
                ? "text-muted-foreground"
                : c.quote_age_seconds < 300
                ? "text-emerald-400"
                : c.quote_age_seconds < 900
                ? "text-yellow-400"
                : "text-destructive"
            }`}
          >
            {fmtAge(c.quote_age_seconds)}
          </p>
        </div>
        <div className="space-y-0.5">
          <p className="text-xs text-muted-foreground uppercase tracking-wide">Closes in</p>
          <p
            className={`font-mono ${
              c.minutes_to_close == null
                ? "text-muted-foreground"
                : c.minutes_to_close < 0
                ? "text-destructive"
                : c.minutes_to_close < 60
                ? "text-yellow-400"
                : "text-foreground"
            }`}
          >
            {fmtClose(c.minutes_to_close)}
          </p>
        </div>
        <div className="space-y-0.5">
          <p className="text-xs text-muted-foreground uppercase tracking-wide">Liquidity</p>
          <p
            className={`font-mono text-xs ${
              c.liquidity_status === "executable"
                ? "text-emerald-400"
                : c.liquidity_status.includes("none")
                ? "text-destructive"
                : "text-yellow-400"
            }`}
          >
            {c.liquidity_status === "executable"
              ? "✓ Executable"
              : c.liquidity_status.includes("none")
              ? "✗ None"
              : "~ Limited"}
          </p>
        </div>
      </div>

      <div className="bg-secondary/20 rounded p-3 space-y-1">
        <p className="text-xs font-mono text-muted-foreground uppercase tracking-wide">
          FTB Status
        </p>
        <p className="text-sm text-foreground">{c.ftb_status}</p>
        {c.failed_ftb_guards.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {c.failed_ftb_guards.map((g) => (
              <span
                key={g}
                className="text-xs font-mono px-2 py-0.5 rounded bg-destructive/20 text-destructive border border-destructive/30"
              >
                {g}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <TrendingUp className="h-4 w-4 text-primary shrink-0" />
          <p className="text-xs font-mono text-primary uppercase tracking-wide">
            Why EdgeCast Likes This
          </p>
        </div>
        <p className="text-sm text-foreground leading-relaxed">{c.why_this_bet}</p>
      </div>

      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 text-yellow-400 shrink-0" />
          <p className="text-xs font-mono text-yellow-400 uppercase tracking-wide">
            What to Watch
          </p>
        </div>
        <p className="text-sm text-muted-foreground leading-relaxed">{c.what_to_watch}</p>
      </div>

      {c.changed_since_previous_scan.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs font-mono text-muted-foreground uppercase tracking-wide">
            Recent Changes
          </p>
          <ul className="space-y-1">
            {c.changed_since_previous_scan.map((ch, i) => (
              <li key={i} className="text-xs text-muted-foreground flex items-start gap-2">
                <span className="text-primary mt-0.5">→</span>
                {ch}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex items-center justify-between border-t border-border pt-3">
        <p className="text-xs text-muted-foreground font-mono">
          Data freshness:{" "}
          <span
            className={
              c.data_freshness.includes("very fresh") || c.data_freshness.includes("fresh (")
                ? "text-emerald-400"
                : c.data_freshness.includes("aging")
                ? "text-yellow-400"
                : "text-destructive"
            }
          >
            {c.data_freshness}
          </span>
        </p>
        <p className="text-xs text-muted-foreground font-mono">
          {c.station_verified ? "✓ Station verified" : "⚠ Station unverified"}
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Today cards
// ---------------------------------------------------------------------------

function OpportunityRow({ c }: { c: BetWatchCandidate }) {
  const evidence = evidenceStatus(c);

  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="space-y-2 min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-mono text-muted-foreground">
              #{c.rank}
            </span>
            <StatusBadge status={c.watch_status} />
            <span
              className={`text-xs font-mono font-bold px-1.5 py-0.5 rounded border ${
                c.side === "YES"
                  ? "text-emerald-400 bg-emerald-950/50 border-emerald-700/40"
                  : "text-red-400 bg-red-950/50 border-red-700/40"
              }`}
            >
              {c.side}
            </span>
            <span
              className={`text-xs font-mono font-bold px-2 py-0.5 rounded border ${
                evidence.label === "INSUFFICIENT"
                  ? "text-yellow-300 bg-yellow-950/40 border-yellow-700/40"
                  : "text-muted-foreground bg-secondary/40 border-border"
              }`}
            >
              Evidence {evidence.label}
            </span>
          </div>
          <div>
            <p className="text-base font-semibold text-foreground">{c.city}</p>
            <p className="text-xs font-mono text-muted-foreground">{c.ticker}</p>
          </div>
          <p className="text-sm text-muted-foreground">{c.contract_question}</p>
        </div>

        <div className="rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-left md:text-right">
          <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
            Model provenance
          </p>
          <p className="font-mono text-sm font-bold text-foreground">
            {c.model_version || UNAVAILABLE_COPY}
          </p>
          <p className="text-xs text-muted-foreground">
            Confidence: {c.confidence || UNAVAILABLE_COPY}
          </p>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        <div className="rounded-md bg-secondary/20 p-3">
          <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
            EdgeCast P({c.side})
          </p>
          <p className="mt-1 font-mono text-lg font-bold text-foreground">
            {todayPct(c.model_probability)}
          </p>
        </div>
        <div className="rounded-md bg-secondary/20 p-3">
          <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
            Kalshi implied P({c.side}) / price
          </p>
          <p className="mt-1 font-mono text-lg font-bold text-foreground">
            {todayKalshi(c.kalshi_price)}
          </p>
        </div>
        <div className="rounded-md bg-secondary/20 p-3">
          <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
            Display edge
          </p>
          <p
            className={`mt-1 font-mono text-lg font-bold ${
              c.edge >= 10 ? "text-emerald-400" : c.edge >= 5 ? "text-yellow-400" : "text-foreground"
            }`}
          >
            {todayEdge(c.edge)}
          </p>
        </div>
        <div className="rounded-md bg-secondary/20 p-3">
          <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
            Quote freshness
          </p>
          <p className="mt-1 font-mono text-sm font-bold text-foreground">
            {c.quote_age_seconds == null ? UNAVAILABLE_COPY : fmtAge(c.quote_age_seconds)}
          </p>
          <p className="text-xs text-muted-foreground">
            {c.data_freshness || UNAVAILABLE_COPY}
          </p>
        </div>
        <div className="rounded-md bg-secondary/20 p-3">
          <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
            Existing status
          </p>
          <p className="mt-1 text-sm font-medium text-foreground">
            {c.ftb_eligible ? "FTB eligible" : "Not FTB eligible"}
          </p>
          <p className="text-xs text-muted-foreground">
            {c.ftb_status || UNAVAILABLE_COPY}
          </p>
        </div>
        <div className="rounded-md bg-secondary/20 p-3">
          <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
            Evidence detail
          </p>
          <p className="mt-1 text-sm font-medium text-foreground">
            {evidence.label}
          </p>
          <p className="text-xs text-muted-foreground">
            {evidence.detail}
          </p>
        </div>
      </div>

      {c.failed_ftb_guards.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {c.failed_ftb_guards.map((guard) => (
            <span
              key={guard}
              className="rounded border border-yellow-700/40 bg-yellow-950/30 px-2 py-1 text-xs font-mono text-yellow-200"
            >
              {guard}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function BetWatchPage() {
  const [sortBy, setSortBy] = useState<TodaySort>("probability");
  const { data, isLoading, error, isFetching, refetch, dataUpdatedAt } =
    useGetBetWatch({ refetchInterval: 60_000 });

  const displayCandidates = useMemo(() => {
    if (!data) return [];

    return uniqueCandidates(
      [data.best_opportunity, ...data.candidates].filter(
        (candidate): candidate is BetWatchCandidate => candidate != null
      )
    ).sort((a, b) => compareCandidates(a, b, sortBy));
  }, [data, sortBy]);

  const lastUpdated = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString()
    : null;

  return (
    <div className="space-y-6 pb-8">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <Eye className="h-5 w-5 text-primary" />
            <h1 className="text-xl font-bold font-mono text-foreground uppercase tracking-widest">
              TODAY
            </h1>
          </div>
          <p className="text-sm text-muted-foreground">
            Default read-only workflow for current opportunities. Existing forecast + Kalshi quote data only. No execution.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {lastUpdated && (
            <p className="text-xs text-muted-foreground font-mono">
              Updated {lastUpdated}
            </p>
          )}
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="flex items-center gap-2 px-3 py-1.5 rounded border border-border text-xs font-mono text-muted-foreground hover:bg-secondary hover:text-foreground transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`h-3 w-3 ${isFetching ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>
      </div>

      {isLoading && (
        <div className="flex items-center justify-center py-20">
          <div className="flex items-center gap-3 text-muted-foreground">
            <Activity className="h-5 w-5 animate-pulse text-primary" />
            <span className="font-mono text-sm">Scanning opportunities…</span>
          </div>
        </div>
      )}

      {!!error && !isLoading && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive font-mono">
          Unable to load Today data. {error instanceof Error ? error.message : String(error)}
        </div>
      )}

      {data && (
        <>
          <div className="rounded-lg border border-primary/30 bg-primary/5 p-4">
            <div className="flex items-start gap-3">
              <Info className="h-4 w-4 text-primary mt-0.5 shrink-0" />
              <div className="space-y-2 flex-1">
                <p className="text-sm font-medium text-foreground">{data.summary.text}</p>
                <p className="text-xs text-muted-foreground">
                  Evidence defaults to <span className="font-mono text-foreground">UNKNOWN</span> or{" "}
                  <span className="font-mono text-foreground">INSUFFICIENT</span> here when the canonical runtime baseline is unavailable.
                </p>
                <div className="flex flex-wrap gap-3 text-xs font-mono">
                  <span className="text-emerald-400">
                    {data.summary.actionable} actionable
                  </span>
                  <span className="text-yellow-400">
                    {data.summary.near_official} near-official
                  </span>
                  <span className="text-sky-400">
                    {data.summary.watching} watching
                  </span>
                  <span className="text-muted-foreground">
                    {data.summary.preliminary} preliminary
                  </span>
                  <span className="text-destructive">
                    {data.summary.avoid_stale} stale/avoid
                  </span>
                  <span className="text-muted-foreground border-l border-border pl-3">
                    {data.summary.total_evaluated} total evaluated
                  </span>
                </div>
              </div>
            </div>
          </div>

          <section className="space-y-3">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-xs font-mono text-primary uppercase tracking-widest flex items-center gap-2">
                  <TrendingUp className="h-3.5 w-3.5" />
                  Today&apos;s Opportunities
                </h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  Client-side display sort only. Missing fields fail closed as unavailable.
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => setSortBy("probability")}
                  className={`rounded border px-3 py-1.5 text-xs font-mono transition-colors ${
                    sortBy === "probability"
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border text-muted-foreground hover:bg-secondary hover:text-foreground"
                  }`}
                >
                  Highest win probability
                </button>
                <button
                  type="button"
                  onClick={() => setSortBy("edge")}
                  className={`rounded border px-3 py-1.5 text-xs font-mono transition-colors ${
                    sortBy === "edge"
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border text-muted-foreground hover:bg-secondary hover:text-foreground"
                  }`}
                >
                  Largest estimated edge
                </button>
              </div>
            </div>

            {displayCandidates.length > 0 ? (
              <div className="space-y-3">
                {displayCandidates.map((c) => (
                  <OpportunityRow key={`${c.ticker}:${c.side}`} c={c} />
                ))}
              </div>
            ) : (
              <div className="rounded-lg border border-border bg-card p-8 text-center space-y-2">
                <Eye className="h-8 w-8 text-muted-foreground mx-auto" />
                <p className="text-base font-mono text-muted-foreground">
                  No current opportunity rows are available.
                </p>
                <p className="text-xs text-muted-foreground">
                  Check back after the next scan or refresh if stored quote/probability data is unavailable.
                </p>
              </div>
            )}
          </section>

          {data.specialization_cities.length > 0 && (
            <div className="rounded-lg border border-yellow-700/40 bg-yellow-900/10 p-3 flex items-start gap-2">
              <Star className="h-4 w-4 text-yellow-400 shrink-0 mt-0.5" />
              <div className="space-y-0.5">
                <p className="text-xs font-mono font-semibold text-yellow-400">
                  Focus cities: {data.specialization_cities.join(" · ")}
                </p>
                <p className="text-xs text-muted-foreground">{data.specialization_note}</p>
              </div>
            </div>
          )}

          <div
            className={`rounded-lg border p-4 ${
              data.best_opportunity
                ? "border-primary/40 bg-primary/5"
                : "border-border bg-secondary/20"
            }`}
          >
            <div className="flex items-start gap-3">
              <Zap
                className={`h-5 w-5 mt-0.5 shrink-0 ${
                  data.best_opportunity ? "text-primary" : "text-muted-foreground"
                }`}
              />
              <div className="space-y-1">
                <p className="text-xs font-mono text-muted-foreground uppercase tracking-wide">
                  Diagnostics / existing recommendation text
                </p>
                <p className="text-sm md:text-base text-foreground leading-relaxed">
                  {data.recommendation}
                </p>
                {data.wait_message && (
                  <p className="text-sm text-yellow-400 mt-2">{data.wait_message}</p>
                )}
              </div>
            </div>
          </div>

          {data.best_opportunity ? (
            <section className="space-y-3">
              <h2 className="text-xs font-mono text-primary uppercase tracking-widest flex items-center gap-2">
                <Zap className="h-3.5 w-3.5" />
                Existing Top Candidate Detail
              </h2>
              <CandidateCard c={data.best_opportunity} isBest />
            </section>
          ) : (
            <div className="rounded-lg border border-border bg-card p-8 text-center space-y-2">
              <Eye className="h-8 w-8 text-muted-foreground mx-auto" />
              <p className="text-base font-mono text-muted-foreground">
                EdgeCast does not see a bet worth taking right now.
              </p>
              <p className="text-xs text-muted-foreground">
                All current candidates fail at least one key quality gate. Check back after the next scan.
              </p>
            </div>
          )}

          {displayCandidates.length > 1 && (
            <section className="space-y-4">
              <h2 className="text-xs font-mono text-muted-foreground uppercase tracking-widest">
                Diagnostics / full candidate detail
              </h2>
              {displayCandidates
                .filter(
                  (c) =>
                    `${c.ticker}:${c.side}` !==
                    `${data.best_opportunity?.ticker}:${data.best_opportunity?.side}`
                )
                .map((c) => (
                  <CandidateCard key={`${c.ticker}:${c.side}`} c={c} />
                ))}
            </section>
          )}

          {(() => {
            const allChanges = data.candidates.flatMap((c) =>
              c.changed_since_previous_scan.map((ch) => ({
                ticker: c.ticker,
                city: c.city,
                change: ch,
              }))
            );

            if (allChanges.length === 0) return null;

            return (
              <section className="space-y-3">
                <h2 className="text-xs font-mono text-muted-foreground uppercase tracking-widest flex items-center gap-2">
                  <Clock className="h-3.5 w-3.5" />
                  What Changed?
                </h2>
                <div className="rounded-lg border border-border bg-card p-4 space-y-2">
                  {allChanges.map((item, i) => (
                    <div key={i} className="flex items-start gap-3 text-sm">
                      <span className="text-primary font-mono shrink-0">→</span>
                      <span className="text-muted-foreground">
                        <span className="text-foreground font-medium">{item.city}</span>{" "}
                        ({item.ticker}): {item.change}
                      </span>
                    </div>
                  ))}
                </div>
              </section>
            );
          })()}

          <div className="rounded-lg border border-border bg-secondary/10 p-4 space-y-1">
            <p className="text-xs font-mono text-muted-foreground uppercase tracking-wide">
              Safety Attestations
            </p>
            <div className="flex flex-wrap gap-4 text-xs font-mono">
              <span className="text-emerald-400 flex items-center gap-1">
                <CheckCircle className="h-3 w-3" />
                Trading state not modified
              </span>
              <span className="text-emerald-400 flex items-center gap-1">
                <CheckCircle className="h-3 w-3" />
                Forward Test B untouched
              </span>
              <span className="text-emerald-400 flex items-center gap-1">
                <CheckCircle className="h-3 w-3" />
                No real trades placed
              </span>
              <span className="text-muted-foreground">
                Generated {new Date(data.generated_at).toLocaleTimeString()}
              </span>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
