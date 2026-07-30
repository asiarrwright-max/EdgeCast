import { useQueryClient } from "@tanstack/react-query";
import {
  useGetDashboard,
  useTriggerCollection,
  useGetV2LearningProgress,
  getGetDashboardQueryKey,
  getGetMarketsQueryKey,
  getGetJobsQueryKey,
} from "@workspace/api-client-react";
import { format } from "date-fns";
import {
  Activity, CloudRain, Database, RefreshCw, AlertTriangle,
  AlertCircle, CheckCircle2, SkipForward, XOctagon,
  TrendingUp, Info, Brain, ExternalLink,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Link } from "wouter";

// ── Inline helpers ────────────────────────────────────────────────────────────

function InfoTooltip({ text, maxWidth = 260 }: { text: string; maxWidth?: number }) {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Info className="h-3 w-3 text-muted-foreground cursor-help inline shrink-0" />
        </TooltipTrigger>
        <TooltipContent
          className="font-sans text-xs leading-relaxed"
          style={{ maxWidth }}
        >
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/** Inline term with a hover tooltip — renders as underlined text + ℹ icon */
function Term({ label, tip }: { label: string; tip: string }) {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="underline decoration-dotted underline-offset-2 cursor-help text-foreground/80 hover:text-foreground transition-colors">
            {label}
          </span>
        </TooltipTrigger>
        <TooltipContent className="font-sans text-xs leading-relaxed max-w-[260px]">
          {tip}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

const READINESS_COLORS: Record<string, string> = {
  learned:             "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  partially_learned:   "bg-blue-500/20   text-blue-400   border-blue-500/30",
  insufficient_sample: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  collecting:          "bg-orange-500/20 text-orange-400 border-orange-500/30",
  not_collecting:      "bg-muted/50      text-muted-foreground border-border",
  data_quality_issue:  "bg-red-500/20    text-red-400    border-red-500/30",
};

const READINESS_LABEL: Record<string, string> = {
  learned:             "Learned",
  partially_learned:   "Partial",
  insufficient_sample: "Needs FES",
  collecting:          "Collecting",
  not_collecting:      "Not started",
  data_quality_issue:  "Quality issue",
};

function CityChip({ city, status, obs }: { city: string; status: string; obs: number }) {
  const colors = READINESS_COLORS[status] ?? "bg-muted/50 text-muted-foreground border-border";
  return (
    <div className={`rounded border px-2 py-1.5 text-[10px] font-mono ${colors}`}>
      <div className="font-semibold truncate">{city}</div>
      <div className="opacity-70 mt-0.5">{obs} obs · {READINESS_LABEL[status] ?? status}</div>
    </div>
  );
}

type LearningStage = {
  label: string;
  description: string;
  color: string;
};

function getLearningStage(summary: {
  totalUsableObservations: number;
  citiesLearned: number;
  citiesPartiallyLearned: number;
  v2TradesUsingHistorical: number;
  v2TotalTrades: number;
}): LearningStage {
  const { totalUsableObservations, citiesLearned, citiesPartiallyLearned, v2TradesUsingHistorical } = summary;

  if (totalUsableObservations < 5) {
    return {
      label: "Getting Started",
      description: "Using built-in estimates for all cities. EdgeCast is collecting its first real-world observations.",
      color: "text-orange-400",
    };
  }
  if (citiesLearned === 0 && citiesPartiallyLearned === 0) {
    return {
      label: "Collecting Evidence",
      description: "Observations are coming in. Waiting for enough data per city to switch from built-in to measured statistics.",
      color: "text-yellow-400",
    };
  }
  if (v2TradesUsingHistorical === 0) {
    return {
      label: "Early Learning",
      description: "Some cities have enough data to learn from, but no completed trades have used city-specific stats yet.",
      color: "text-blue-400",
    };
  }
  if (citiesLearned > 0) {
    return {
      label: "Active Learning",
      description: "City-specific forecast error data is being used for predictions. More cities will graduate as data accumulates.",
      color: "text-emerald-400",
    };
  }
  return {
    label: "Building",
    description: "Partially learned cities are informing predictions. Full city data is accumulating.",
    color: "text-blue-400",
  };
}

// ── How EdgeCast Learns section ───────────────────────────────────────────────

function HowEdgeCastLearns() {
  const { data, isLoading } = useGetV2LearningProgress();

  if (isLoading) {
    return (
      <Card className="border-border/50 bg-card/50">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-mono text-muted-foreground flex items-center gap-2">
            <Brain className="h-4 w-4" />
            HOW EDGECAST LEARNS
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 rounded" />
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  if (!data) return null;

  const { summary, cities } = data;
  const stage = getLearningStage(summary);

  // Sort cities: learned first, then partially, then collecting, then not started
  const SORT_ORDER = ["learned", "partially_learned", "insufficient_sample", "collecting", "not_collecting", "data_quality_issue"];
  const sortedCities = [...cities].sort((a, b) => {
    const ai = SORT_ORDER.indexOf(a.readinessStatus);
    const bi = SORT_ORDER.indexOf(b.readinessStatus);
    if (ai !== bi) return ai - bi;
    return b.usableObservations - a.usableObservations;
  });

  const learnedCount = summary.citiesLearned + summary.citiesPartiallyLearned;
  const totalCities  = summary.totalCities;

  return (
    <Card className="border-border/50 bg-card/50">
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1">
            <CardTitle className="text-sm font-mono text-muted-foreground flex items-center gap-2">
              <Brain className="h-4 w-4 text-primary" />
              HOW EDGECAST LEARNS
            </CardTitle>
            <div className="flex items-center gap-2">
              <span className={`text-base font-mono font-bold ${stage.color}`}>
                {stage.label.toUpperCase()}
              </span>
              <span className="text-xs text-muted-foreground">—</span>
              <span className="text-xs text-muted-foreground">{stage.description}</span>
            </div>
          </div>
          <Link
            href="/strategy-audit"
            className="text-[10px] font-mono text-primary hover:underline whitespace-nowrap flex items-center gap-1 mt-1"
          >
            DEEP DIVE <ExternalLink className="h-3 w-3" />
          </Link>
        </div>
      </CardHeader>

      <CardContent className="space-y-4 pt-0">
        {/* Plain-English narrative */}
        <div className="text-xs text-muted-foreground leading-relaxed border-l-2 border-primary/30 pl-3 space-y-2">
          <p>
            EdgeCast compares its weather forecast to the market price on Kalshi. The key question is:{" "}
            <em>how far off could this forecast realistically be?</em> That uncertainty is expressed as{" "}
            <Term
              label="sigma (σ)"
              tip="Sigma is the standard deviation of forecast errors — how many degrees a weather forecast is typically off by. A σ of 2.5°F means most forecasts miss by ≤2.5°F. Smaller σ = more confident predictions."
            />
            .
          </p>
          <p>
            At first, EdgeCast uses a built-in{" "}
            <Term
              label="fixed-table fallback"
              tip="When there isn't enough local data, EdgeCast uses a pre-set table of typical forecast errors by lead time (e.g., 1-day forecasts: σ = 2.5°F, 3-day: σ = 4.3°F). These are rough industry estimates, not measured from this city's data."
            />
            . After each market settles, it fetches the actual temperature and compares it to what was forecast. Once{" "}
            enough observations accumulate, it switches to measured statistics for that city — learning its own{" "}
            <Term
              label="sigma and bias"
              tip="Bias correction accounts for systematic forecast errors. If a city's forecasts consistently run 1°F too high, EdgeCast subtracts that offset before calculating probabilities."
            />
            . With more settled trades, it also applies{" "}
            <Term
              label="calibration"
              tip="Calibration fine-tunes the final probability. If EdgeCast says 70% but actually wins 80% of those trades, calibration nudges the output upward. Requires 30+ settled trades to activate."
            />
            {" "}to match its probability outputs to reality.
          </p>
        </div>

        {/* Progress stats row */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="bg-muted/30 rounded px-3 py-2 text-center">
            <div className="text-lg font-mono font-bold text-foreground">{summary.totalUsableObservations}</div>
            <div className="text-[10px] text-muted-foreground font-mono flex items-center justify-center gap-1">
              OBSERVATIONS
              <InfoTooltip text="Each time a market settles and EdgeCast retrieves the actual temperature from NOAA or ERA5, that's one observation. More observations → more accurate sigma estimates." />
            </div>
          </div>
          <div className="bg-muted/30 rounded px-3 py-2 text-center">
            <div className="text-lg font-mono font-bold text-foreground">
              {learnedCount}<span className="text-muted-foreground text-sm font-normal">/{totalCities}</span>
            </div>
            <div className="text-[10px] text-muted-foreground font-mono flex items-center justify-center gap-1">
              CITIES LEARNING
              <InfoTooltip text="Cities that have at least 5 usable observations and have started using measured (rather than built-in) forecast error statistics." />
            </div>
          </div>
          <div className="bg-muted/30 rounded px-3 py-2 text-center">
            <div className="text-lg font-mono font-bold text-foreground">{summary.totalFesGroups}</div>
            <div className="text-[10px] text-muted-foreground font-mono flex items-center justify-center gap-1">
              ERROR GROUPS
              <InfoTooltip text="Forecast Error Stat groups — each one covers a city × weather variable × lead-time bucket (e.g. Chicago · high temp · 2–3 days). A group needs ≥5 observations to be used." />
            </div>
          </div>
          <div className="bg-muted/30 rounded px-3 py-2 text-center">
            <div className="text-lg font-mono font-bold text-foreground">
              {summary.v2TotalTrades > 0
                ? `${Math.round((summary.v2TradesUsingHistorical / summary.v2TotalTrades) * 100)}%`
                : "—"}
            </div>
            <div className="text-[10px] text-muted-foreground font-mono flex items-center justify-center gap-1">
              USING REAL DATA
              <InfoTooltip text="Percentage of Strategy V2 trades that used measured city or global statistics instead of the built-in fixed table. As more data accumulates, this number climbs toward 100%." />
            </div>
          </div>
        </div>

        {/* City progress grid */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-[10px] font-mono text-muted-foreground uppercase tracking-wide">
              City Learning Progress
            </p>
            <div className="flex items-center gap-2 text-[9px] text-muted-foreground font-mono">
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-sm bg-emerald-500/40 border border-emerald-500/50 inline-block" />
                Learned
              </span>
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-sm bg-blue-500/40 border border-blue-500/50 inline-block" />
                Partial
              </span>
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-sm bg-orange-500/40 border border-orange-500/50 inline-block" />
                Collecting
              </span>
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-sm bg-muted border border-border inline-block" />
                Not started
              </span>
            </div>
          </div>
          <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 gap-2">
            {sortedCities.map(city => (
              <CityChip
                key={city.city}
                city={city.city}
                status={city.readinessStatus}
                obs={city.usableObservations}
              />
            ))}
          </div>
        </div>

        {/* Glossary strip */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border/50 pt-3 text-[10px] text-muted-foreground font-mono">
          <span className="text-[10px] text-muted-foreground/60 uppercase">Glossary:</span>
          {[
            {
              label: "Sigma (σ)",
              tip: "Standard deviation of forecast errors — how many degrees a weather forecast is typically off. Learned per city once ≥5 observations are available.",
            },
            {
              label: "Bias",
              tip: "Systematic offset: if a city's forecasts always run too warm or too cold, EdgeCast subtracts that offset before calculating probabilities.",
            },
            {
              label: "Fallback",
              tip: "Three levels: Fixed Table (built-in estimates) → Global (cross-city averages) → City (local measured stats). EdgeCast upgrades as data accumulates.",
            },
            {
              label: "Calibration",
              tip: "Fine-tunes probability outputs using historical outcome rates. Activates after 30+ settled trades. Ensures '70% confident' actually wins ~70% of the time.",
            },
            {
              label: "Brier Score",
              tip: "Measures probability accuracy. Range: 0 (perfect) to 1 (worst). Random guessing scores 0.25. Lower is better. Only covers trades EdgeCast actually entered.",
            },
          ].map(({ label, tip }) => (
            <TooltipProvider key={label} delayDuration={150}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="cursor-help hover:text-foreground transition-colors underline decoration-dotted underline-offset-2">
                    {label}
                  </span>
                </TooltipTrigger>
                <TooltipContent className="font-sans text-xs leading-relaxed max-w-[260px]">
                  {tip}
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const queryClient = useQueryClient();
  const { data: dashboard, isLoading, error } = useGetDashboard();
  const triggerMutation = useTriggerCollection();

  const handleTrigger = () => {
    triggerMutation.mutate(undefined, {
      onSuccess: () => {
        setTimeout(() => {
          queryClient.invalidateQueries({ queryKey: getGetDashboardQueryKey() });
          queryClient.invalidateQueries({ queryKey: getGetMarketsQueryKey() });
          queryClient.invalidateQueries({ queryKey: getGetJobsQueryKey() });
        }, 8000);
      }
    });
  };

  if (isLoading) {
    return (
      <div className="space-y-6">
        <h1 className="text-3xl font-mono font-bold tracking-tight">DASHBOARD</h1>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-28 w-full" />)}
        </div>
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (error || !dashboard) {
    return (
      <div className="p-6 bg-destructive/10 border border-destructive rounded-md text-destructive flex items-center gap-3">
        <AlertTriangle className="h-5 w-5" />
        <span className="font-mono">ERROR RETRIEVING DASHBOARD METRICS</span>
      </div>
    );
  }

  const {
    totalActiveMarkets,
    marketsWithWeather,
    marketsCollected,
    marketsParseFailures,
    lastCollectionTime,
    lastCollectionStatus,
    lastCollectionDuration,
    lastCollectionMarketsFound,
    lastCollectionMarketsSkipped,
    collectionSummary,
    markets,
    recentErrors,
    lastJob,
  } = dashboard;

  const coverage = totalActiveMarkets > 0
    ? Math.round((marketsWithWeather / totalActiveMarkets) * 100)
    : 0;

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <h1 className="text-3xl font-mono font-bold tracking-tight">DASHBOARD</h1>
        <Button
          onClick={handleTrigger}
          disabled={triggerMutation.isPending}
          className="font-mono"
        >
          <RefreshCw className={`h-4 w-4 mr-2 ${triggerMutation.isPending ? "animate-spin" : ""}`} />
          {triggerMutation.isPending ? "COLLECTING..." : "RUN COLLECTION NOW"}
        </Button>
      </div>

      {collectionSummary && (
        <div className="flex items-start gap-2 p-3 rounded-md border border-yellow-500/30 bg-yellow-500/5 text-yellow-400 text-sm font-mono">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          {collectionSummary}
        </div>
      )}

      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
        <Card className="border-border/50 bg-card/50 backdrop-blur">
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-xs font-mono text-muted-foreground">ACTIVE</CardTitle>
            <Database className="h-3 w-3 text-primary" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-mono font-bold">{totalActiveMarkets}</div>
            <p className="text-[10px] text-muted-foreground font-mono">MARKETS</p>
          </CardContent>
        </Card>

        <Card className="border-border/50 bg-card/50 backdrop-blur">
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-xs font-mono text-muted-foreground">COVERAGE</CardTitle>
            <CloudRain className="h-3 w-3 text-blue-400" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-mono font-bold">{coverage}%</div>
            <p className="text-[10px] text-muted-foreground font-mono">{marketsWithWeather} MATCHED</p>
          </CardContent>
        </Card>

        <Card className="border-border/50 bg-card/50 backdrop-blur">
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-xs font-mono text-muted-foreground">FOUND</CardTitle>
            <TrendingUp className="h-3 w-3 text-emerald-400" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-mono font-bold">{marketsCollected ?? 0}</div>
            <p className="text-[10px] text-muted-foreground font-mono">LAST RUN</p>
          </CardContent>
        </Card>

        <Card className="border-border/50 bg-card/50 backdrop-blur">
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-xs font-mono text-muted-foreground">PARSE FAIL</CardTitle>
            <XOctagon className="h-3 w-3 text-orange-400" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-mono font-bold text-orange-400">{marketsParseFailures ?? 0}</div>
            <p className="text-[10px] text-muted-foreground font-mono">NO CITY MATCH</p>
          </CardContent>
        </Card>

        <Card className="border-border/50 bg-card/50 backdrop-blur">
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-xs font-mono text-muted-foreground">SKIPPED</CardTitle>
            <SkipForward className="h-3 w-3 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-mono font-bold text-muted-foreground">
              {lastCollectionMarketsSkipped ?? '—'}
            </div>
            <p className="text-[10px] text-muted-foreground font-mono">NON-WEATHER</p>
          </CardContent>
        </Card>

        <Card className="border-border/50 bg-card/50 backdrop-blur">
          <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
            <CardTitle className="text-xs font-mono text-muted-foreground">LAST RUN</CardTitle>
            <Activity className="h-3 w-3 text-emerald-400" />
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-1">
              <span className="text-lg font-mono font-bold">
                {lastCollectionTime ? format(new Date(lastCollectionTime), "HH:mm") : "—"}
              </span>
              {lastCollectionStatus && (
                <Badge
                  variant={lastCollectionStatus === 'success' ? 'success' : 'destructive'}
                  className="font-mono text-[9px]"
                >
                  {lastCollectionStatus === 'success' ? '✓' : '✗'}
                </Badge>
              )}
            </div>
            <p className="text-[10px] text-muted-foreground font-mono">
              {lastCollectionDuration != null ? `${lastCollectionDuration.toFixed(1)}s` : ''}
              {lastCollectionMarketsFound != null ? ` · ${lastCollectionMarketsFound} found` : ''}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* How EdgeCast Learns */}
      <HowEdgeCastLearns />

      {/* Market Watch + Side Panel */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-mono font-bold tracking-tight">MARKET WATCH</h2>
            <Link href="/markets" className="text-xs font-mono text-primary hover:underline">VIEW ALL →</Link>
          </div>
          <Card className="border-border/50">
            <div className="overflow-x-auto">
              <table className="w-full text-sm font-mono text-left">
                <thead className="bg-muted/30 border-b border-border text-muted-foreground">
                  <tr>
                    <th className="p-3 font-medium">TICKER</th>
                    <th className="p-3 font-medium">CITY</th>
                    <th className="p-3 font-medium">YES ASK</th>
                    <th className="p-3 font-medium">NO ASK</th>
                    <th className="p-3 font-medium">VOL</th>
                    <th className="p-3 font-medium text-center">MATCH</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/50">
                  {markets.slice(0, 8).map(m => (
                    <tr key={m.id} className="hover:bg-muted/20 transition-colors">
                      <td className="p-3">
                        <Link href={`/markets/${m.ticker}`} className="text-primary hover:underline font-bold">
                          {m.ticker}
                        </Link>
                      </td>
                      <td className="p-3 text-muted-foreground text-xs">{m.city || '—'}</td>
                      <td className="p-3 text-emerald-400">
                        {m.yesAsk !== null && m.yesAsk !== undefined ? `${m.yesAsk}¢` : '-'}
                      </td>
                      <td className="p-3 text-red-400">
                        {m.noAsk !== null && m.noAsk !== undefined ? `${m.noAsk}¢` : '-'}
                      </td>
                      <td className="p-3 text-muted-foreground">{m.volume ?? 0}</td>
                      <td className="p-3 text-center">
                        {m.weatherMatched ? (
                          <CheckCircle2 className="h-4 w-4 text-emerald-500 inline" />
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                  {markets.length === 0 && (
                    <tr>
                      <td colSpan={6} className="p-8 text-center text-muted-foreground">
                        NO ACTIVE MARKETS
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </Card>
        </div>

        <div className="space-y-4">
          {lastJob && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-mono font-bold tracking-tight text-muted-foreground">LAST JOB</h2>
                <Link href="/jobs" className="text-xs font-mono text-primary hover:underline">VIEW ALL →</Link>
              </div>
              <Card className="border-border/50 p-4 font-mono text-xs space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">STARTED</span>
                  <span>{format(new Date(lastJob.startedAt), "HH:mm:ss")}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">DURATION</span>
                  <span>
                    {lastJob.durationSeconds != null ? `${lastJob.durationSeconds.toFixed(2)}s` : '—'}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">FOUND</span>
                  <span className="text-emerald-400 font-bold">{lastJob.marketsFound ?? '—'}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">SKIPPED</span>
                  <span>{lastJob.marketsSkipped ?? '—'}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">FORECASTS</span>
                  <span className="text-blue-400 font-bold">{lastJob.forecastsRetrieved ?? '—'}</span>
                </div>
              </Card>
            </div>
          )}

          <div className="flex items-center justify-between">
            <h2 className="text-sm font-mono font-bold tracking-tight text-muted-foreground">RECENT ERRORS</h2>
            <Link href="/errors" className="text-xs font-mono text-primary hover:underline">VIEW ALL →</Link>
          </div>
          <Card className="border-border/50">
            <div className="divide-y divide-border/50">
              {recentErrors.slice(0, 4).map(err => (
                <div key={err.id} className="p-3 text-sm">
                  <div className="flex items-start gap-2 mb-1">
                    <AlertCircle className="h-4 w-4 text-destructive shrink-0 mt-0.5" />
                    <span className="font-mono font-bold truncate">{err.errorType}</span>
                  </div>
                  <p className="text-xs text-muted-foreground line-clamp-2 ml-6">{err.message}</p>
                  <p className="text-[10px] text-muted-foreground/60 font-mono mt-2 ml-6">
                    {format(new Date(err.occurredAt), "HH:mm:ss yyyy-MM-dd")}
                  </p>
                </div>
              ))}
              {recentErrors.length === 0 && (
                <div className="p-6 text-center text-muted-foreground font-mono text-sm">
                  NO RECENT ERRORS
                </div>
              )}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
