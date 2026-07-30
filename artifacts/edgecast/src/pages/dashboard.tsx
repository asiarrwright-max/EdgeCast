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

// ── Learning stage definitions ────────────────────────────────────────────────

type LearningStage = {
  emoji: string;
  label: string;
  whyItMatters: string;
  color: string;
  bgColor: string;
};

const STAGES: LearningStage[] = [
  {
    emoji: "🌱",
    label: "Just Getting Started",
    whyItMatters:
      "Every completed forecast teaches EdgeCast what actually happened so future predictions become more accurate.",
    color: "text-orange-400",
    bgColor: "bg-orange-500/10 border-orange-500/20",
  },
  {
    emoji: "📚",
    label: "Learning From Real Weather",
    whyItMatters:
      "Real temperature outcomes are now being compared to forecasts. The more settled markets EdgeCast sees, the better it understands how accurate weather predictions actually are.",
    color: "text-yellow-400",
    bgColor: "bg-yellow-500/10 border-yellow-500/20",
  },
  {
    emoji: "🧠",
    label: "Getting Smarter",
    whyItMatters:
      "City-specific patterns are now being used. EdgeCast has learned that some cities run warmer than forecast, others cooler — and it adjusts for those tendencies automatically.",
    color: "text-blue-400",
    bgColor: "bg-blue-500/10 border-blue-500/20",
  },
  {
    emoji: "🎯",
    label: "Fully Trained",
    whyItMatters:
      "EdgeCast is using measured data for all major cities and fine-tuning its probability outputs based on its own track record.",
    color: "text-emerald-400",
    bgColor: "bg-emerald-500/10 border-emerald-500/20",
  },
];

type Readiness = { dot: string; label: string; sub: string; color: string };

function getReadiness(summary: {
  totalUsableObservations: number;
  citiesLearned: number;
  citiesPartiallyLearned: number;
  v2TradesUsingHistorical: number;
}): Readiness {
  const { totalUsableObservations, citiesLearned, v2TradesUsingHistorical } = summary;
  if (citiesLearned >= 3 && v2TradesUsingHistorical > 0) {
    return { dot: "🟢", label: "Ready", sub: "Using real-world data for predictions", color: "text-emerald-400" };
  }
  if (totalUsableObservations >= 5) {
    return { dot: "🟡", label: "Still Learning", sub: "Building experience — predictions are educated estimates", color: "text-yellow-400" };
  }
  return { dot: "🔴", label: "Not Enough Data Yet", sub: "Very early stage — predictions use built-in defaults", color: "text-orange-400" };
}

function getLearningStage(summary: {
  totalUsableObservations: number;
  citiesLearned: number;
  citiesPartiallyLearned: number;
  v2TradesUsingHistorical: number;
}): LearningStage {
  const { totalUsableObservations, citiesLearned, citiesPartiallyLearned, v2TradesUsingHistorical } = summary;
  if (citiesLearned >= 3 && v2TradesUsingHistorical > 0) return STAGES[3]; // Fully Trained
  if (v2TradesUsingHistorical > 0 || citiesLearned > 0 || citiesPartiallyLearned > 0) return STAGES[2]; // Getting Smarter
  if (totalUsableObservations >= 5) return STAGES[1]; // Learning From Real Weather
  return STAGES[0]; // Just Getting Started
}

const CITY_CHIP_COLORS: Record<string, string> = {
  learned:             "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  partially_learned:   "bg-blue-500/20   text-blue-400   border-blue-500/30",
  insufficient_sample: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  collecting:          "bg-orange-500/20 text-orange-400 border-orange-500/30",
  not_collecting:      "bg-muted/50      text-muted-foreground border-border",
  data_quality_issue:  "bg-red-500/20    text-red-400    border-red-500/30",
};

const CITY_STATUS_LABEL: Record<string, string> = {
  learned:             "Fully trained",
  partially_learned:   "Partially trained",
  insufficient_sample: "Almost ready",
  collecting:          "Collecting",
  not_collecting:      "Not started",
  data_quality_issue:  "Data issue",
};

// ── How EdgeCast Learns section ───────────────────────────────────────────────

function HowEdgeCastLearns() {
  const { data, isLoading } = useGetV2LearningProgress();

  if (isLoading) {
    return (
      <Card className="border-border/50 bg-card/50">
        <CardContent className="pt-5 pb-5">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-16 rounded" />
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  if (!data) return null;

  const { summary, cities } = data;
  const stage     = getLearningStage(summary);
  const readiness = getReadiness(summary);

  // Overall learning progress: milestones reached across all cities ÷ total possible
  // Each city has 5 milestones (5 / 15 / 30 / 50 / 100 observations)
  const totalReached = cities.reduce(
    (sum, c) => sum + (c.milestoneProgress?.reached?.filter(Boolean).length ?? 0),
    0,
  );
  const totalPossible  = summary.totalCities * 5;
  const overallPct     = totalPossible > 0 ? Math.round((totalReached / totalPossible) * 100) : 0;

  // Cities actively learning = any city with at least 1 observation
  const activelyLearning = cities.filter(
    c => !["not_collecting", "data_quality_issue"].includes(c.readinessStatus),
  ).length;

  // Sort: fully trained first, then partial, then collecting, then not started
  const SORT_ORDER = ["learned", "partially_learned", "insufficient_sample", "collecting", "not_collecting", "data_quality_issue"];
  const sortedCities = [...cities].sort((a, b) => {
    const ai = SORT_ORDER.indexOf(a.readinessStatus);
    const bi = SORT_ORDER.indexOf(b.readinessStatus);
    return ai !== bi ? ai - bi : b.usableObservations - a.usableObservations;
  });

  return (
    <Card className="border-border/50 bg-card/50">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-4">
          <CardTitle className="text-sm font-mono text-muted-foreground flex items-center gap-2">
            <Brain className="h-4 w-4 text-primary" />
            HOW EDGECAST LEARNS
          </CardTitle>
          <Link
            href="/strategy-audit?tab=v2-learning"
            className="text-[10px] font-mono text-primary hover:underline whitespace-nowrap flex items-center gap-1"
          >
            DEEP DIVE <ExternalLink className="h-3 w-3" />
          </Link>
        </div>
      </CardHeader>

      <CardContent className="space-y-5 pt-0">

        {/* Model Readiness + Stage — top row */}
        <div className="flex flex-col sm:flex-row gap-3">
          {/* Readiness pill */}
          <div className="flex items-center gap-3 px-4 py-3 rounded-lg border bg-muted/20 sm:w-64 shrink-0">
            <span className="text-2xl leading-none">{readiness.dot}</span>
            <div>
              <div className={`text-sm font-semibold ${readiness.color}`}>{readiness.label}</div>
              <div className="text-[10px] text-muted-foreground mt-0.5">{readiness.sub}</div>
            </div>
          </div>

          {/* Stage card */}
          <div className={`flex-1 flex items-start gap-3 px-4 py-3 rounded-lg border ${stage.bgColor}`}>
            <span className="text-2xl leading-none mt-0.5">{stage.emoji}</span>
            <div className="space-y-1">
              <div className={`text-sm font-semibold ${stage.color}`}>{stage.label}</div>
              <div className="text-[11px] text-muted-foreground leading-relaxed">{stage.whyItMatters}</div>
            </div>
          </div>
        </div>

        {/* Progress metrics */}
        <div className="space-y-3">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="bg-muted/30 rounded-lg px-3 py-2.5 text-center">
              <div className="text-xl font-mono font-bold text-foreground">{summary.totalUsableObservations}</div>
              <div className="text-[10px] text-muted-foreground mt-0.5 flex items-center justify-center gap-1">
                Forecasts Learned From
                <InfoTooltip text="Each time a weather market settles, EdgeCast looks up what the temperature actually was and compares it to the forecast. That comparison is one lesson. The more lessons, the smarter the model gets." />
              </div>
            </div>
            <div className="bg-muted/30 rounded-lg px-3 py-2.5 text-center">
              <div className="text-xl font-mono font-bold text-foreground">
                {activelyLearning}
                <span className="text-muted-foreground text-base font-normal"> of {summary.totalCities}</span>
              </div>
              <div className="text-[10px] text-muted-foreground mt-0.5 flex items-center justify-center gap-1">
                Cities Actively Learning
                <InfoTooltip text="Cities where EdgeCast has started receiving real temperature data to compare against its forecasts. Cities not yet started are still waiting for their first settled market." />
              </div>
            </div>
            <div className="bg-muted/30 rounded-lg px-3 py-2.5 text-center">
              <div className="text-xl font-mono font-bold text-foreground">{summary.citiesLearned}</div>
              <div className="text-[10px] text-muted-foreground mt-0.5 flex items-center justify-center gap-1">
                Cities Fully Trained
                <InfoTooltip text="Cities where EdgeCast has enough real-world data to use its own measured statistics instead of built-in defaults. A city needs at least 5 confirmed temperature observations to reach this stage." />
              </div>
            </div>
            <div className="bg-muted/30 rounded-lg px-3 py-2.5 text-center">
              <div className="text-xl font-mono font-bold text-foreground">{overallPct}%</div>
              <div className="text-[10px] text-muted-foreground mt-0.5 flex items-center justify-center gap-1">
                Overall Learning Progress
                <InfoTooltip text="How far EdgeCast has come across all cities, measured against five learning milestones per city (5, 15, 30, 50, and 100 observations). 100% means all cities have reached every milestone." />
              </div>
            </div>
          </div>

          {/* Overall progress bar */}
          <div className="space-y-1">
            <div className="h-2 bg-muted rounded-full overflow-hidden">
              <div
                className="h-full bg-primary transition-all duration-700 rounded-full"
                style={{ width: `${Math.max(overallPct, overallPct > 0 ? 2 : 0)}%` }}
              />
            </div>
            <div className="flex justify-between text-[9px] text-muted-foreground font-mono">
              <span>Getting Started</span>
              <span>Learning</span>
              <span>Getting Smarter</span>
              <span>Fully Trained</span>
            </div>
          </div>
        </div>

        {/* City grid */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-[10px] font-mono text-muted-foreground uppercase tracking-wide">
              Progress by City
            </p>
            <div className="flex items-center gap-2 text-[9px] text-muted-foreground">
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 rounded-sm bg-emerald-500/40 border border-emerald-500/50 inline-block" />
                Fully trained
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
            {sortedCities.map(c => {
              const colors = CITY_CHIP_COLORS[c.readinessStatus] ?? "bg-muted/50 text-muted-foreground border-border";
              const label  = CITY_STATUS_LABEL[c.readinessStatus] ?? c.readinessStatus;
              return (
                <div key={c.city} className={`rounded border px-2 py-1.5 text-[10px] font-mono ${colors}`}>
                  <div className="font-semibold truncate">{c.city}</div>
                  <div className="opacity-70 mt-0.5">
                    {c.usableObservations > 0 ? `${c.usableObservations} lessons` : "0 lessons"} · {label}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Glossary strip */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border/50 pt-3 text-[10px] text-muted-foreground">
          <span className="uppercase tracking-wide opacity-60">What do these mean?</span>
          {[
            {
              label: "Lessons",
              tip: "Each time a weather market settles and EdgeCast confirms the actual temperature, that counts as one lesson. More lessons = more accurate predictions.",
            },
            {
              label: "Built-in defaults",
              tip: "When EdgeCast is new to a city, it uses rough industry-standard estimates for how far off weather forecasts typically are. Think of it as the textbook answer before you have personal experience.",
            },
            {
              label: "Systematic bias",
              tip: "Some cities' forecasts consistently run a bit too warm or too cool. Once EdgeCast spots this pattern, it automatically corrects for it — like knowing your oven always runs 10° hot.",
            },
            {
              label: "Probability fine-tuning",
              tip: "After enough settled trades, EdgeCast checks: when it said '70% confident,' did that actually happen 70% of the time? If not, it adjusts its confidence levels to match reality.",
            },
            {
              label: "Probability accuracy score",
              tip: "A number that measures how well EdgeCast's confidence levels match real outcomes. Zero is perfect; 0.25 is no better than random guessing. Lower is better.",
            },
          ].map(({ label, tip }) => (
            <TooltipProvider key={label} delayDuration={150}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="cursor-help hover:text-foreground transition-colors underline decoration-dotted underline-offset-2">
                    {label}
                  </span>
                </TooltipTrigger>
                <TooltipContent className="font-sans text-xs leading-relaxed max-w-[280px]">
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
