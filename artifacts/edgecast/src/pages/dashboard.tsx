import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  useGetDashboard,
  useTriggerCollection,
  useGetV2LearningProgress,
  useGetPaperTradeMetrics,
  useGetMarkets,
  useGetCityAvailability,
  getGetDashboardQueryKey,
  getGetMarketsQueryKey,
  getGetJobsQueryKey,
} from "@workspace/api-client-react";
import { format, parseISO, differenceInHours } from "date-fns";
import {
  Activity, CloudRain, Database, RefreshCw, AlertTriangle,
  AlertCircle, CheckCircle2, Cloud, Sun, CloudLightning,
  TrendingUp, Info, Brain, ExternalLink, Target, Briefcase, DollarSign,
  Zap, Clock, Star
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

// ── Shared Styling Systems ───────────────────────────────────────────────────

const COLOR_SYSTEM: Record<string, string> = {
  blue:   "bg-blue-500/10 text-blue-400 border-blue-500/30",
  green:  "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
  amber:  "bg-amber-500/10 text-amber-400 border-amber-500/30",
  red:    "bg-red-500/10 text-red-400 border-red-500/30",
  gray:   "bg-slate-500/10 text-slate-400 border-slate-500/30",
};

const TEXT_COLORS: Record<string, string> = {
  blue:   "text-blue-400",
  green:  "text-emerald-400",
  amber:  "text-amber-400",
  red:    "text-red-400",
  gray:   "text-slate-400",
};

const CITY_CHIP_COLORS: Record<string, string> = {
  learned:             COLOR_SYSTEM.green,
  partially_learned:   COLOR_SYSTEM.blue,
  collecting:          COLOR_SYSTEM.blue,
  insufficient_sample: COLOR_SYSTEM.amber,
  data_quality_issue:  COLOR_SYSTEM.red,
  not_collecting:      COLOR_SYSTEM.gray,
};

const CITY_STATUS_LABEL: Record<string, string> = {
  learned:             "Fully trained",
  partially_learned:   "Partially trained",
  insufficient_sample: "Almost ready",
  collecting:          "Collecting",
  not_collecting:      "Not started",
  data_quality_issue:  "Data issue",
};

// ── Inline Helpers ───────────────────────────────────────────────────────────

function InfoTooltip({ text, maxWidth = 260 }: { text: string; maxWidth?: number }) {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Info className="h-3 w-3 text-muted-foreground hover:text-foreground cursor-help transition-colors" />
        </TooltipTrigger>
        <TooltipContent className="font-sans text-xs leading-relaxed" style={{ maxWidth }}>
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

// ── Model Readiness Score ────────────────────────────────────────────────────

const TOTAL_CITIES = 24; // fixed universe

function computeReadinessScore(summary: {
  totalUsableObservations: number;
  citiesLearned: number;
  citiesPartiallyLearned: number;
  v2TotalTrades: number;
  v2TradesUsingHistorical: number;
}): number {
  const learnedPct  = summary.citiesLearned / TOTAL_CITIES;
  const partialPct  = summary.citiesPartiallyLearned / TOTAL_CITIES;
  const obsDensity  = Math.min(summary.totalUsableObservations / 2000, 1);
  const histRate    = summary.v2TotalTrades > 0
    ? summary.v2TradesUsingHistorical / summary.v2TotalTrades
    : 0;

  const score =
    learnedPct  * 40 +   // 40 pts: fully learned cities
    obsDensity  * 30 +   // 30 pts: raw observation density
    histRate    * 20 +   // 20 pts: trades using real learned data vs defaults
    partialPct  * 10;    // 10 pts: partially-learned cities count too

  return Math.round(Math.min(score, 100));
}

function readinessScoreLabel(score: number): { label: string; color: string } {
  if (score >= 80) return { label: "Highly Trusted",      color: "green" };
  if (score >= 55) return { label: "Getting Reliable",    color: "blue"  };
  if (score >= 30) return { label: "Building Confidence", color: "amber" };
  return               { label: "Early Stage",            color: "gray"  };
}

// ── Model Logic ──────────────────────────────────────────────────────────────

function getModelStage(summary: {
  totalUsableObservations: number;
  citiesLearned: number;
  citiesPartiallyLearned: number;
  v2TradesUsingHistorical: number;
}) {
  const { totalUsableObservations, citiesLearned, citiesPartiallyLearned, v2TradesUsingHistorical } = summary;
  
  if (citiesLearned >= 3 && v2TradesUsingHistorical > 0) {
    return {
      color: "green",
      icon: <Sun className="h-10 w-10 sm:h-12 sm:w-12" />,
      label: "Fully Trained",
      desc: "EdgeCast is using measured data for major cities and fine-tuning its probability outputs based on its own track record.",
      readiness: "Predictions use real-world data"
    };
  }
  if (v2TradesUsingHistorical > 0 || citiesLearned > 0 || citiesPartiallyLearned > 0) {
    return {
      color: "blue",
      icon: <CloudLightning className="h-10 w-10 sm:h-12 sm:w-12" />,
      label: "Getting Smarter",
      desc: "City-specific patterns are now being used. EdgeCast has learned that some cities run warmer than forecast, others cooler — adjusting for bias automatically.",
      readiness: "Predictions are educated estimates"
    };
  }
  if (totalUsableObservations >= 5) {
    return {
      color: "amber",
      icon: <CloudRain className="h-10 w-10 sm:h-12 sm:w-12" />,
      label: "Learning From Real Weather",
      desc: "Real temperature outcomes are now being compared to forecasts. The more settled markets EdgeCast sees, the better it understands prediction accuracy.",
      readiness: "Building early experience"
    };
  }
  return {
    color: "gray",
    icon: <Cloud className="h-10 w-10 sm:h-12 sm:w-12" />,
    label: "Just Getting Started",
    desc: "Every completed forecast teaches EdgeCast what actually happened so future predictions become more accurate.",
    readiness: "Predictions use built-in defaults"
  };
}

// ── Sub-components ───────────────────────────────────────────────────────────

function StatCard({ title, value, color, icon }: { title: string; value: string; color: string; icon: React.ReactNode }) {
  const scheme = COLOR_SYSTEM[color];
  // Simple hack to get a subtle gradient based on the color word for Tailwind JIT 
  const gradientMap: Record<string, string> = {
    blue: "from-blue-500/10",
    green: "from-emerald-500/10",
    amber: "from-amber-500/10",
    red: "from-red-500/10",
    gray: "from-slate-500/10",
  };
  
  return (
    <Card className="border-border/50 bg-card/40 backdrop-blur shadow-sm hover:shadow-md transition-shadow relative overflow-hidden group">
      <div className={`absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity bg-gradient-to-br ${gradientMap[color]} to-transparent pointer-events-none`} />
      <CardContent className="p-4 sm:p-5 flex flex-col gap-3">
        <div className="flex items-center justify-between text-muted-foreground">
          <span className="text-[10px] font-mono tracking-wider uppercase">{title}</span>
          <div className={TEXT_COLORS[color]}>{icon}</div>
        </div>
        <div className={`text-xl sm:text-2xl font-sans font-bold tracking-tight ${TEXT_COLORS[color]}`}>
          {value}
        </div>
      </CardContent>
    </Card>
  );
}

function LearningStat({ label, value, tip }: { label: string; value: string | number; tip: string }) {
  return (
    <div className="p-4 flex flex-col items-center justify-center text-center">
      <div className="text-2xl font-mono font-bold text-foreground mb-1">{value}</div>
      <div className="text-[9px] font-mono uppercase tracking-wider text-muted-foreground flex items-center gap-1">
        {label}
        <InfoTooltip text={tip} />
      </div>
    </div>
  );
}

function GlossaryStrip() {
  const items = [
    { label: "Lessons", tip: "Each time a weather market settles and EdgeCast confirms the actual temperature, that counts as one lesson." },
    { label: "Built-in defaults", tip: "Rough industry-standard estimates used before EdgeCast builds local experience in a city." },
    { label: "Bias correction", tip: "Automatic adjustments for cities whose forecasts consistently run a bit too warm or too cool." },
    { label: "Probability tuning", tip: "Adjusting confidence levels to match reality (e.g. if '70% confident' only happens 50% of the time, the model recalibrates)." },
  ];
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 p-4 border-t border-border/50 bg-muted/10 text-[10px] font-mono text-muted-foreground">
      <span className="uppercase tracking-widest opacity-60 flex items-center gap-1">
        <Info className="h-3 w-3" /> Terminology
      </span>
      {items.map(({ label, tip }) => (
        <TooltipProvider key={label} delayDuration={150}>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="cursor-help hover:text-primary transition-colors underline decoration-dotted underline-offset-4">
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
  );
}

// ── Today's Best Opportunities ───────────────────────────────────────────────

const CONFIDENCE_WEIGHT: Record<string, number> = {
  VERY_HIGH: 1.0,
  HIGH:      0.8,
  MODERATE:  0.6,
  LOW:       0.3,
};

function opportunityScore(edge: number, confidence: string | null | undefined): number {
  const w = CONFIDENCE_WEIGHT[confidence?.toUpperCase() ?? ""] ?? 0.4;
  return edge * w;
}

function confidenceBadgeClass(confidence: string | null | undefined): string {
  switch (confidence?.toUpperCase()) {
    case "VERY_HIGH": return COLOR_SYSTEM.green;
    case "HIGH":      return COLOR_SYSTEM.blue;
    case "MODERATE":  return COLOR_SYSTEM.amber;
    default:          return COLOR_SYSTEM.gray;
  }
}

function TodaysBestOpportunities({
  markets,
  cityReadiness,
}: {
  markets: Array<{
    id: number;
    ticker: string;
    city?: string | null;
    probabilityDiff?: number | null;
    confidence?: string | null;
    ecProbability?: number | null;
    marketProbability?: number | null;
    closeTime?: string | null;
  }>;
  cityReadiness: Record<string, string>;
}) {
  const ranked = markets
    .filter(m => (m.probabilityDiff ?? 0) > 0 && m.confidence && m.ecProbability != null)
    .map(m => ({ ...m, score: opportunityScore(m.probabilityDiff!, m.confidence) }))
    .sort((a, b) => b.score - a.score)
    .slice(0, 5);

  if (ranked.length === 0) {
    return (
      <Card className="border-border/50 bg-card/40 backdrop-blur shadow-sm">
        <CardHeader className="pb-4 border-b border-border/50 bg-muted/5">
          <CardTitle className="text-xs font-mono tracking-widest text-muted-foreground uppercase flex items-center gap-2">
            <Star className="h-4 w-4 text-amber-400" /> Today's Best Opportunities
          </CardTitle>
        </CardHeader>
        <CardContent className="p-8 text-center text-xs font-mono text-muted-foreground">
          No ranked opportunities right now — run a sync to refresh market data.
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-border/50 bg-card/40 backdrop-blur shadow-sm overflow-hidden">
      <CardHeader className="flex flex-row items-center justify-between pb-4 border-b border-border/50 bg-muted/5">
        <CardTitle className="text-xs font-mono tracking-widest text-muted-foreground uppercase flex items-center gap-2">
          <Star className="h-4 w-4 text-amber-400" /> Today's Best Opportunities
        </CardTitle>
        <Link href="/markets" className="text-[10px] font-mono text-primary hover:text-primary-foreground hover:underline flex items-center gap-1 transition-colors">
          ALL MARKETS <ExternalLink className="h-3 w-3" />
        </Link>
      </CardHeader>
      <CardContent className="p-0">
        <p className="px-4 pt-3 pb-2 text-[10px] font-mono text-muted-foreground uppercase tracking-widest">
          Ranked by edge × confidence · Top {ranked.length} of {markets.filter(m => (m.probabilityDiff ?? 0) > 0).length} active opportunities
        </p>
        <div className="divide-y divide-border/30">
          {ranked.map((m, i) => {
            const cityStatus = m.city ? (cityReadiness[m.city] ?? "not_collecting") : "not_collecting";
            const cityColor  = CITY_CHIP_COLORS[cityStatus] || CITY_CHIP_COLORS.not_collecting;
            const cityLabel  = CITY_STATUS_LABEL[cityStatus] ?? cityStatus;
            const hoursLeft  = m.closeTime
              ? differenceInHours(parseISO(m.closeTime), new Date())
              : null;

            return (
              <div key={m.id} className="px-4 py-3 hover:bg-muted/10 transition-colors flex items-center gap-3">
                {/* Rank */}
                <div className="text-[10px] font-mono font-bold text-muted-foreground w-4 shrink-0 text-center">
                  {i + 1}
                </div>

                {/* Main info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <Link
                      href={`/markets/${m.ticker}`}
                      className="text-xs font-mono font-bold text-primary hover:underline truncate"
                    >
                      {m.ticker}
                    </Link>
                    {m.city && (
                      <TooltipProvider delayDuration={150}>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className={`text-[9px] font-mono px-1.5 py-0.5 rounded border cursor-default ${cityColor}`}>
                              {m.city}
                            </span>
                          </TooltipTrigger>
                          <TooltipContent className="text-xs font-sans">
                            Model status for {m.city}: {cityLabel}
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    )}
                  </div>
                  <div className="flex items-center gap-3 text-[10px] font-mono text-muted-foreground">
                    <span className="flex items-center gap-1">
                      <Zap className="h-2.5 w-2.5 text-amber-400" />
                      Edge: <span className="text-amber-400 font-semibold">+{(m.probabilityDiff! * 100).toFixed(1)}pp</span>
                    </span>
                    {hoursLeft != null && hoursLeft > 0 && (
                      <span className="flex items-center gap-1">
                        <Clock className="h-2.5 w-2.5 text-muted-foreground" />
                        {hoursLeft < 24
                          ? `${hoursLeft}h left`
                          : `${Math.round(hoursLeft / 24)}d left`}
                      </span>
                    )}
                  </div>
                </div>

                {/* Confidence badge */}
                <span className={`text-[9px] font-mono px-2 py-1 rounded border shrink-0 ${confidenceBadgeClass(m.confidence)}`}>
                  {m.confidence?.replace("_", " ")}
                </span>
              </div>
            );
          })}
        </div>
        <div className="px-4 py-2.5 border-t border-border/30 bg-muted/5 text-[9px] font-mono text-muted-foreground">
          Edge = EdgeCast probability − market price. Confidence reflects model certainty at entry.
          City badge color = model readiness for that location.
        </div>
      </CardContent>
    </Card>
  );
}

function DashboardSkeleton() {
  return (
    <div className="space-y-8 animate-pulse pb-20">
      <div className="flex justify-between items-end">
        <div className="space-y-2">
          <div className="h-10 w-64 bg-muted/20 rounded"></div>
          <div className="h-4 w-48 bg-muted/20 rounded"></div>
        </div>
        <div className="h-10 w-32 bg-muted/20 rounded"></div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 md:gap-8">
         <div className="lg:col-span-8 space-y-6 md:space-y-8">
            <div className="h-64 bg-card/40 border border-border/50 rounded-xl"></div>
            <div className="h-24 bg-card/40 border border-border/50 rounded-xl"></div>
            <div className="h-96 bg-card/40 border border-border/50 rounded-xl"></div>
         </div>
         <div className="lg:col-span-4 space-y-6 md:space-y-8">
            <div className="h-48 bg-card/40 border border-border/50 rounded-xl"></div>
            <div className="h-64 bg-card/40 border border-border/50 rounded-xl"></div>
         </div>
      </div>
    </div>
  );
}

// ── Main Page ────────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const queryClient = useQueryClient();
  const { data: dashboard, isLoading: dashLoading, error: dashError } = useGetDashboard();
  const { data: learning, isLoading: learnLoading, error: learnError } = useGetV2LearningProgress();
  const { data: metrics, isLoading: metricsLoading } = useGetPaperTradeMetrics();
  const { data: allMarkets } = useGetMarkets();
  const { data: cityAvail } = useGetCityAvailability();
  const [showInactiveCities, setShowInactiveCities] = useState(false);
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

  if (dashLoading || learnLoading) {
    return <DashboardSkeleton />;
  }

  if (dashError || learnError || !dashboard || !learning) {
    return (
      <div className="p-6 bg-red-500/10 border border-red-500/30 rounded-md text-red-400 flex items-center gap-3">
        <AlertTriangle className="h-5 w-5" />
        <span className="font-mono">ERROR RETRIEVING MISSION CONTROL DATA</span>
      </div>
    );
  }

  // Derived dashboard metrics
  const coverage = dashboard.totalActiveMarkets > 0
    ? Math.round((dashboard.marketsWithWeather / dashboard.totalActiveMarkets) * 100)
    : 0;

  // Derived learning metrics
  const totalReached = learning.cities.reduce(
    (sum, c) => sum + (c.milestoneProgress?.reached?.filter(Boolean).length ?? 0),
    0,
  );
  const totalPossible = learning.summary.totalCities * 5;
  const overallPct = totalPossible > 0 ? Math.round((totalReached / totalPossible) * 100) : 0;
  
  const activelyLearning = learning.cities.filter(
    c => !["not_collecting", "data_quality_issue"].includes(c.readinessStatus),
  ).length;

  const SORT_ORDER = ["learned", "partially_learned", "insufficient_sample", "collecting", "not_collecting", "data_quality_issue"];
  const activeCitySet = new Set(cityAvail?.activeCities ?? []);
  const allSortedCities = [...learning.cities].sort((a, b) => {
    const ai = SORT_ORDER.indexOf(a.readinessStatus);
    const bi = SORT_ORDER.indexOf(b.readinessStatus);
    return ai !== bi ? ai - bi : b.usableObservations - a.usableObservations;
  });
  // By default only show cities that currently have active Kalshi markets.
  // Fall back to all cities if availability data hasn't loaded yet.
  const sortedCities = (cityAvail && !showInactiveCities)
    ? allSortedCities.filter(c => activeCitySet.has(c.city))
    : allSortedCities;

  const stage = getModelStage(learning.summary);

  // Model Readiness Score (0-100)
  const readinessScore = computeReadinessScore(learning.summary);
  const readinessLabel = readinessScoreLabel(readinessScore);

  // City readiness map for opportunity cross-referencing
  const cityReadinessMap: Record<string, string> = Object.fromEntries(
    learning.cities.map(c => [c.city, c.readinessStatus])
  );

  const heroGlowMap: Record<string, string> = {
    blue: "bg-blue-500",
    green: "bg-emerald-500",
    amber: "bg-amber-500",
    red: "bg-red-500",
    gray: "bg-slate-500",
  };

  return (
    <div className="space-y-6 md:space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-700 pb-20">
      
      {/* ── Header ── */}
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 mb-4">
        <div className="space-y-1">
          <h1 className="text-3xl md:text-4xl font-sans font-black tracking-tighter text-foreground flex items-center gap-3">
            <Brain className="h-7 w-7 md:h-8 md:w-8 text-primary" />
            Mission Control
          </h1>
          <p className="text-muted-foreground text-[11px] md:text-xs font-mono tracking-widest uppercase">
            EdgeCast Weather Prediction Engine
          </p>
        </div>
        <Button
          onClick={handleTrigger}
          disabled={triggerMutation.isPending}
          className="font-mono tracking-widest text-[10px] md:text-xs h-10 px-6 bg-primary hover:bg-primary/90 text-primary-foreground shadow-[0_0_15px_rgba(59,130,246,0.2)] hover:shadow-[0_0_20px_rgba(59,130,246,0.4)] transition-all"
        >
          <RefreshCw className={`h-4 w-4 mr-2 ${triggerMutation.isPending ? "animate-spin" : ""}`} />
          {triggerMutation.isPending ? "SYNCING..." : "SYNC MARKETS"}
        </Button>
      </div>

      {/* ── Global Alerts ── */}
      {dashboard.collectionSummary && (
        <div className="flex items-start gap-3 p-4 rounded-lg border border-amber-500/30 bg-amber-500/10 text-amber-400 text-sm font-mono shadow-[0_0_15px_rgba(245,158,11,0.05)]">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          <span className="leading-relaxed">{dashboard.collectionSummary}</span>
        </div>
      )}

      {/* ── Main Grid ── */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 md:gap-8">
      
        {/* Left Column (8 cols): Primary Model Status, Performance, Cities, Markets */}
        <div className="lg:col-span-8 flex flex-col gap-6 md:gap-8">
          
          {/* 1. MODEL STATUS HERO */}
          <Card className={`relative overflow-hidden border ${COLOR_SYSTEM[stage.color].split(' ')[2]} bg-card/50 backdrop-blur shadow-lg`}>
            {/* Ambient background glow */}
            <div className={`absolute -right-20 -top-20 w-72 h-72 blur-3xl opacity-20 ${heroGlowMap[stage.color]} rounded-full pointer-events-none`} />
            
            <CardContent className="p-6 md:p-8">
              <div className="flex flex-col md:flex-row md:items-center gap-6 md:gap-8">
                
                {/* Big Status */}
                <div className="flex-1">
                   <h2 className="text-xs font-mono font-semibold tracking-widest text-muted-foreground uppercase mb-3 flex items-center gap-2">
                     Model Readiness
                   </h2>
                   <div className="flex items-start sm:items-center gap-4 sm:gap-5">
                      <div className={`p-4 rounded-2xl ${COLOR_SYSTEM[stage.color]} shadow-inner shrink-0`}>
                         {stage.icon}
                      </div>
                      <div>
                        <h3 className={`text-3xl sm:text-4xl md:text-5xl font-sans font-black tracking-tight ${TEXT_COLORS[stage.color]}`}>
                          {stage.label}
                        </h3>
                        <p className="text-sm font-mono text-muted-foreground mt-2 font-medium">
                          {stage.readiness}
                        </p>
                      </div>
                   </div>
                   <p className="text-sm text-muted-foreground mt-4 leading-relaxed max-w-lg">
                     {stage.desc}
                   </p>
                </div>

                {/* Readiness Score + KPIs */}
                <div className="flex flex-col items-end gap-4 shrink-0">
                  <TooltipProvider delayDuration={150}>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <div className="text-center cursor-help">
                          <div className={`text-5xl md:text-6xl font-mono font-black tracking-tight ${TEXT_COLORS[readinessLabel.color]}`}>
                            {readinessScore}
                          </div>
                          <div className="text-[9px] font-mono uppercase tracking-widest text-muted-foreground mt-0.5">/ 100</div>
                          <div className={`text-[10px] font-mono font-semibold mt-1 ${TEXT_COLORS[readinessLabel.color]}`}>
                            {readinessLabel.label}
                          </div>
                          <div className="text-[8px] font-mono text-muted-foreground mt-0.5 uppercase tracking-widest">
                            Readiness Score
                          </div>
                        </div>
                      </TooltipTrigger>
                      <TooltipContent className="font-sans text-xs leading-relaxed max-w-[280px]">
                        Composite score (0–100): cities fully trained (40 pts), observation volume (30 pts), trades using learned data vs defaults (20 pts), cities partially trained (10 pts).
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                  <div className="flex gap-5 text-right">
                    <div className="space-y-0.5">
                      <div className="text-2xl font-mono font-bold text-foreground">{learning.summary.citiesLearned}</div>
                      <div className="text-[9px] uppercase tracking-widest text-muted-foreground">Fully Trained</div>
                    </div>
                    <div className="space-y-0.5">
                      <div className="text-2xl font-mono font-bold text-foreground">{overallPct}%</div>
                      <div className="text-[9px] uppercase tracking-widest text-muted-foreground">Progress</div>
                    </div>
                  </div>
                </div>
              </div>

              {/* Progress Bar inside Hero */}
              <div className="mt-8 space-y-2">
                <div className="flex justify-between text-[9px] sm:text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
                   <span className={overallPct >= 0 ? "text-foreground" : ""}>Waiting</span>
                   <span className={overallPct >= 5 ? "text-foreground" : ""}>Learning</span>
                   <span className={overallPct >= 30 ? "text-foreground" : ""}>Getting Smarter</span>
                   <span className={overallPct === 100 ? "text-emerald-400" : ""}>Fully Trained</span>
                </div>
                <div className="h-2.5 sm:h-3 bg-muted/50 rounded-full overflow-hidden border border-border/50">
                  <div
                    className={`h-full ${overallPct === 100 ? 'bg-emerald-500' : 'bg-primary'} transition-all duration-1000 ease-out rounded-full shadow-[0_0_10px_currentColor]`}
                    style={{ width: `${Math.max(overallPct, 2)}%` }}
                  />
                </div>
              </div>
            </CardContent>
          </Card>

          {/* 2. PERFORMANCE ROW (Paper Trading Analytics) */}
          {metrics && !metricsLoading && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <StatCard title="WIN RATE" value={metrics.winRate ? `${metrics.winRate.toFixed(1)}%` : '—'} color={metrics.winRate && metrics.winRate > 50 ? 'green' : 'gray'} icon={<Target className="h-4 w-4" />} />
              <StatCard title="ROI" value={metrics.roi ? `${metrics.roi > 0 ? '+' : ''}${metrics.roi.toFixed(1)}%` : '—'} color={metrics.roi && metrics.roi > 0 ? 'green' : (metrics.roi && metrics.roi < 0 ? 'red' : 'gray')} icon={<TrendingUp className="h-4 w-4" />} />
              <StatCard title="OPEN TRADES" value={metrics.openCount.toString()} color="blue" icon={<Briefcase className="h-4 w-4" />} />
              <StatCard title="NET P&L" value={metrics.netProfitLoss ? `$${metrics.netProfitLoss.toFixed(2)}` : '—'} color={metrics.netProfitLoss >= 0 ? 'green' : 'red'} icon={<DollarSign className="h-4 w-4" />} />
            </div>
          )}

          {/* 3. TODAY'S BEST OPPORTUNITIES */}
          <TodaysBestOpportunities
            markets={allMarkets?.markets ?? []}
            cityReadiness={cityReadinessMap}
          />

          {/* 4. CITY LEARNING MATRIX */}
          <Card className="border-border/50 bg-card/40 backdrop-blur shadow-sm overflow-hidden">
            <CardHeader className="flex flex-row items-center justify-between pb-4 border-b border-border/50 bg-muted/5">
               <CardTitle className="text-xs font-mono tracking-widest text-muted-foreground uppercase flex items-center gap-2">
                 <Database className="h-4 w-4 text-primary" /> City Learning Matrix
               </CardTitle>
               <Link href="/strategy-audit?tab=v2-learning" className="text-[10px] font-mono text-primary hover:text-primary-foreground hover:underline flex items-center gap-1 transition-colors">
                 AUDIT REPORT <ExternalLink className="h-3 w-3" />
               </Link>
            </CardHeader>
            <CardContent className="p-0">
               {/* Detail Stats */}
               <div className="grid grid-cols-2 sm:grid-cols-4 divide-x divide-y sm:divide-y-0 divide-border/50 border-b border-border/50 bg-muted/10">
                  <LearningStat 
                    label="Lessons Logged" 
                    value={learning.summary.totalUsableObservations}
                    tip="Total confirmed temperature outcomes compared to forecasts across all cities" />
                  <LearningStat 
                    label="Active Cities" 
                    value={`${activelyLearning} / ${learning.summary.totalCities}`}
                    tip="Cities currently receiving real temperature data" />
                  <LearningStat 
                    label="Fully Trained" 
                    value={learning.summary.citiesLearned}
                    tip="Cities that have collected enough data to use measured statistics" />
                  <LearningStat 
                    label="Total Progress" 
                    value={`${overallPct}%`}
                    tip="Overall progression across 5 milestones per city" />
               </div>

               {/* City Grid */}
               <div className="p-4 sm:p-6">
                 <div className="flex items-center justify-between mb-4">
                   <p className="text-[10px] font-mono text-muted-foreground uppercase tracking-widest">
                     Location Status Snapshot
                   </p>
                   {cityAvail && (
                     <label className="flex items-center gap-1.5 text-[10px] text-muted-foreground cursor-pointer">
                       <input
                         type="checkbox"
                         checked={showInactiveCities}
                         onChange={(e) => setShowInactiveCities(e.target.checked)}
                         className="rounded"
                       />
                       Show inactive ({cityAvail.inactiveCount})
                     </label>
                   )}
                 </div>
                 <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 gap-2 sm:gap-3">
                   {sortedCities.map(c => {
                     const scheme = CITY_CHIP_COLORS[c.readinessStatus] || CITY_CHIP_COLORS.not_collecting;
                     const label  = CITY_STATUS_LABEL[c.readinessStatus] || c.readinessStatus;
                     return (
                       <div key={c.city} className={`rounded border px-2 py-1.5 sm:px-2.5 sm:py-2 text-[10px] font-mono transition-colors ${scheme}`}>
                         <div className="font-bold tracking-tight truncate">{c.city}</div>
                         <div className="opacity-80 mt-1 text-[8px] sm:text-[9px] uppercase tracking-wider">
                           {c.usableObservations} LSN<br/>{label}
                         </div>
                       </div>
                     );
                   })}
                 </div>
               </div>

               <GlossaryStrip />
            </CardContent>
          </Card>

          {/* 4. MARKET WATCH */}
          <Card className="border-border/50 bg-card/40 backdrop-blur shadow-sm">
            <CardHeader className="flex flex-row items-center justify-between pb-4 border-b border-border/50 bg-muted/5">
              <CardTitle className="text-xs font-mono tracking-widest text-muted-foreground uppercase flex items-center gap-2">
                <Activity className="h-4 w-4 text-emerald-500" /> Market Watch
              </CardTitle>
              <Link href="/markets" className="text-[10px] font-mono text-primary hover:text-primary-foreground hover:underline flex items-center gap-1 transition-colors">
                VIEW ALL <ExternalLink className="h-3 w-3" />
              </Link>
            </CardHeader>
            <div className="overflow-x-auto">
              <table className="w-full text-sm font-mono text-left whitespace-nowrap">
                <thead className="bg-muted/30 border-b border-border/50 text-muted-foreground text-[10px] uppercase tracking-wider">
                  <tr>
                    <th className="px-4 py-3 font-medium">Ticker</th>
                    <th className="px-4 py-3 font-medium">City</th>
                    <th className="px-4 py-3 font-medium text-right">Yes Ask</th>
                    <th className="px-4 py-3 font-medium text-right">No Ask</th>
                    <th className="px-4 py-3 font-medium text-center">Match</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/20">
                  {dashboard.markets.slice(0, 8).map(m => (
                    <tr key={m.id} className="hover:bg-muted/20 transition-colors group">
                      <td className="px-4 py-3">
                        <Link href={`/markets/${m.ticker}`} className="text-primary group-hover:text-primary-foreground group-hover:underline font-bold transition-colors">
                          {m.ticker}
                        </Link>
                      </td>
                      <td className="px-4 py-3 text-muted-foreground text-xs">{m.city || '—'}</td>
                      <td className="px-4 py-3 text-right text-emerald-400 font-medium">
                        {m.yesAsk !== null && m.yesAsk !== undefined ? `${m.yesAsk}¢` : '-'}
                      </td>
                      <td className="px-4 py-3 text-right text-red-400 font-medium">
                        {m.noAsk !== null && m.noAsk !== undefined ? `${m.noAsk}¢` : '-'}
                      </td>
                      <td className="px-4 py-3 text-center">
                        {m.weatherMatched ? (
                          <CheckCircle2 className="h-4 w-4 text-emerald-500 inline" />
                        ) : (
                          <span className="text-muted-foreground text-xs opacity-50">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                  {dashboard.markets.length === 0 && (
                    <tr>
                      <td colSpan={6} className="p-8 text-center text-muted-foreground text-xs">
                        NO ACTIVE MARKETS
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </Card>
        </div>

        {/* Right Column (4 cols): System Health, Collection Stats, Errors */}
        <div className="lg:col-span-4 flex flex-col gap-6 md:gap-8">
          
          {/* DATA INGESTION */}
          <Card className="border-border/50 bg-card/40 backdrop-blur shadow-md flex flex-col relative overflow-hidden">
             <div className="absolute inset-0 bg-gradient-to-b from-blue-500/5 to-transparent pointer-events-none" />
             <CardHeader className="pb-4 border-b border-border/50 bg-muted/5 relative z-10">
                <CardTitle className="text-xs font-mono tracking-widest text-muted-foreground uppercase flex items-center gap-2">
                  <CloudRain className="h-4 w-4 text-blue-400" /> Data Ingestion
                </CardTitle>
             </CardHeader>
             <CardContent className="p-0 flex flex-col relative z-10">
                {/* Coverage & Active paired together */}
                <div className="grid grid-cols-2 divide-x divide-border/50 border-b border-border/50">
                   <div className="p-6 text-center flex flex-col items-center justify-center bg-card/50">
                      <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-1">Active Markets</div>
                      <div className="text-3xl md:text-4xl font-mono font-bold text-foreground">{dashboard.totalActiveMarkets}</div>
                   </div>
                   <div className="p-6 text-center flex flex-col items-center justify-center bg-blue-500/5">
                      <div className="text-[10px] font-mono uppercase tracking-widest text-blue-400 mb-1">Coverage</div>
                      <div className="text-3xl md:text-4xl font-mono font-bold text-primary">{coverage}%</div>
                      <div className="text-[9px] font-mono text-muted-foreground mt-1 uppercase tracking-wider">{dashboard.marketsWithWeather} Matched</div>
                   </div>
                </div>
                
                {/* Micro stats */}
                <div className="grid grid-cols-3 divide-x divide-border/50 bg-muted/10">
                   <div className="p-4 text-center">
                      <div className="text-2xl font-mono font-semibold text-emerald-400">{dashboard.marketsCollected ?? 0}</div>
                      <div className="text-[9px] font-mono uppercase tracking-widest text-muted-foreground mt-1">Found</div>
                   </div>
                   <div className="p-4 text-center">
                      <div className="text-2xl font-mono font-semibold text-slate-400">{dashboard.lastCollectionMarketsSkipped ?? '—'}</div>
                      <div className="text-[9px] font-mono uppercase tracking-widest text-muted-foreground mt-1">Skipped</div>
                   </div>
                   <div className="p-4 text-center">
                      <div className="text-2xl font-mono font-semibold text-red-400">{dashboard.marketsParseFailures ?? 0}</div>
                      <div className="text-[9px] font-mono uppercase tracking-widest text-muted-foreground mt-1">Errors</div>
                   </div>
                </div>
             </CardContent>
          </Card>

          {/* SYSTEM ACTIVITY (Last Job) */}
          <Card className="border-border/50 bg-card/40 backdrop-blur shadow-sm">
            <CardHeader className="flex flex-row items-center justify-between pb-4 border-b border-border/50 bg-muted/5">
              <CardTitle className="text-xs font-mono tracking-widest text-muted-foreground uppercase flex items-center gap-2">
                <Database className="h-4 w-4 text-amber-500" /> Recent Activity
              </CardTitle>
              <Link href="/jobs" className="text-[10px] font-mono text-primary hover:text-primary-foreground hover:underline flex items-center gap-1 transition-colors">
                LOGS <ExternalLink className="h-3 w-3" />
              </Link>
            </CardHeader>
            <CardContent className="p-5">
              {dashboard.lastJob ? (
                <div className="space-y-5">
                  <div className="flex items-center justify-between">
                    <div className="text-[10px] uppercase tracking-widest font-mono text-muted-foreground">Last Run Time</div>
                    <div className="flex items-center gap-3">
                      <span className="text-sm font-mono font-bold text-foreground">
                        {format(new Date(dashboard.lastJob.startedAt), "HH:mm")}
                      </span>
                      <Badge variant="outline" className={`text-[9px] font-mono border-emerald-500/30 text-emerald-400 bg-emerald-500/10 rounded-sm px-1.5 py-0`}>
                        SUCCESS
                      </Badge>
                    </div>
                  </div>
                  
                  <div className="grid grid-cols-2 gap-3 text-xs font-mono">
                    <div className="flex flex-col gap-1 bg-muted/20 p-3 rounded border border-border/50">
                      <span className="text-[9px] uppercase tracking-widest text-muted-foreground">Duration</span>
                      <span className="font-semibold text-foreground">{dashboard.lastJob.durationSeconds}s</span>
                    </div>
                    <div className="flex flex-col gap-1 bg-muted/20 p-3 rounded border border-border/50">
                      <span className="text-[9px] uppercase tracking-widest text-muted-foreground">Forecasts</span>
                      <span className="font-semibold text-blue-400">{dashboard.lastJob.forecastsRetrieved ?? 0}</span>
                    </div>
                    <div className="flex flex-col gap-1 bg-muted/20 p-3 rounded border border-border/50">
                      <span className="text-[9px] uppercase tracking-widest text-muted-foreground">Markets Found</span>
                      <span className="font-semibold text-emerald-400">{dashboard.lastJob.marketsFound ?? 0}</span>
                    </div>
                    <div className="flex flex-col gap-1 bg-muted/20 p-3 rounded border border-border/50">
                      <span className="text-[9px] uppercase tracking-widest text-muted-foreground">Skipped</span>
                      <span className="font-semibold text-slate-400">{dashboard.lastJob.marketsSkipped ?? 0}</span>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="text-xs font-mono text-muted-foreground text-center py-6">NO RECENT ACTIVITY</div>
              )}
            </CardContent>
          </Card>

          {/* RECENT ERRORS */}
          <Card className="border-border/50 bg-card/40 backdrop-blur shadow-sm flex flex-col flex-1">
            <CardHeader className="flex flex-row items-center justify-between pb-4 border-b border-border/50 bg-muted/5 shrink-0">
              <CardTitle className="text-xs font-mono tracking-widest text-muted-foreground uppercase flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 text-red-500" /> System Alerts
              </CardTitle>
              <Link href="/errors" className="text-[10px] font-mono text-primary hover:text-primary-foreground hover:underline flex items-center gap-1 transition-colors">
                ALL ALERTS <ExternalLink className="h-3 w-3" />
              </Link>
            </CardHeader>
            <CardContent className="p-0 flex-1 flex flex-col">
              {dashboard.recentErrors && dashboard.recentErrors.length > 0 ? (
                <div className="divide-y divide-border/30">
                  {dashboard.recentErrors.slice(0, 4).map(err => (
                    <div key={err.id} className="p-4 hover:bg-muted/10 transition-colors">
                      <div className="flex items-start justify-between gap-3 mb-2">
                        <span className="text-[9px] font-mono font-bold text-red-400 bg-red-500/10 px-2 py-0.5 rounded border border-red-500/20">
                          {err.errorType}
                        </span>
                        <span className="text-[9px] font-mono text-muted-foreground whitespace-nowrap opacity-60">
                          {format(new Date(err.occurredAt), "MMM d, HH:mm")}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground font-sans line-clamp-2 leading-relaxed">
                        {err.message}
                      </p>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="p-8 text-center flex-1 flex flex-col items-center justify-center gap-3">
                  <div className="p-3 rounded-full bg-emerald-500/10 text-emerald-400">
                    <CheckCircle2 className="h-6 w-6" />
                  </div>
                  <div className="text-xs font-mono tracking-widest text-emerald-400 uppercase">
                    ALL SYSTEMS NOMINAL
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

        </div>
      </div>
    </div>
  );
}
