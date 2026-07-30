import { Link } from "wouter";
import {
  ArrowLeft, AlertTriangle, CheckCircle2, XCircle, Clock,
  TrendingUp, Brain, Zap, MapPin, Calendar, BarChart2, Info,
} from "lucide-react";
import {
  useGetPaperTrade,
  useGetV2LearningProgress,
  type PaperTrade,
} from "@workspace/api-client-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

// ── Formatters ────────────────────────────────────────────────────────────────

function pct(n: number | null | undefined): string {
  if (n == null) return "—";
  return `${(n * 100).toFixed(1)}%`;
}

function usd(n: number | null | undefined, forceSign = false): string {
  if (n == null) return "—";
  const sign = n >= 0 ? (forceSign ? "+" : "") : "-";
  return `${sign}$${Math.abs(n).toFixed(2)}`;
}

function fmtTs(s: string | null | undefined): string {
  if (!s) return "—";
  return s.replace("T", " ").slice(0, 19) + " UTC";
}

// ── Small layout primitives ───────────────────────────────────────────────────

function Row({ label, children, tip }: { label: string; children: React.ReactNode; tip?: string }) {
  return (
    <div className="flex justify-between items-baseline py-2 border-b border-border/40 last:border-0">
      <span className="text-xs text-muted-foreground uppercase tracking-wide w-48 shrink-0 flex items-center gap-1">
        {label}
        {tip && (
          <TooltipProvider delayDuration={150}>
            <Tooltip>
              <TooltipTrigger asChild>
                <Info className="h-3 w-3 cursor-help text-muted-foreground/60 hover:text-muted-foreground" />
              </TooltipTrigger>
              <TooltipContent className="font-sans text-xs leading-relaxed max-w-[260px]">{tip}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
      </span>
      <span className="text-sm text-right font-mono">{children}</span>
    </div>
  );
}

function DirectionBadge({ direction }: { direction: "YES" | "NO" }) {
  return (
    <span className={`text-sm font-bold font-mono px-2.5 py-1 rounded border ${
      direction === "YES"
        ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
        : "bg-rose-500/10 text-rose-400 border-rose-500/30"
    }`}>
      {direction}
    </span>
  );
}

// ── Reasoning engine ──────────────────────────────────────────────────────────

type BulletTone = "green" | "blue" | "amber" | "red" | "gray";

interface ReasoningBullet {
  tone: BulletTone;
  icon: React.ReactNode;
  headline: string;
  detail: string;
}

const TONE_TEXT: Record<BulletTone, string> = {
  green: "text-emerald-400",
  blue:  "text-blue-400",
  amber: "text-amber-400",
  red:   "text-red-400",
  gray:  "text-slate-400",
};

const TONE_BG: Record<BulletTone, string> = {
  green: "bg-emerald-500/10 border-emerald-500/20",
  blue:  "bg-blue-500/10 border-blue-500/20",
  amber: "bg-amber-500/10 border-amber-500/20",
  red:   "bg-red-500/10 border-red-500/20",
  gray:  "bg-slate-500/10 border-slate-500/20",
};

function buildReasoningBullets(
  trade: PaperTrade & { [k: string]: any },
  snap: Record<string, any> | null,
  cityStatus: string | null,
  cityObservations: number | null
): ReasoningBullet[] {
  const bullets: ReasoningBullet[] = [];

  // 1. Edge strength
  const edge = trade.edgePctPoints;
  if (edge != null && edge > 0) {
    const ecPct  = trade.ecSideProbability != null ? (trade.ecSideProbability * 100).toFixed(1) : null;
    const mktPct = trade.sideMarketPrice    != null ? (trade.sideMarketPrice   * 100).toFixed(1) : null;
    const tone: BulletTone = edge >= 10 ? "green" : edge >= 5 ? "blue" : "amber";
    const strength = edge >= 10 ? "Strong" : edge >= 5 ? "Solid" : "Marginal";
    bullets.push({
      tone,
      icon: <Zap className="h-4 w-4" />,
      headline: `${strength} edge of +${edge.toFixed(1)} percentage points`,
      detail: ecPct && mktPct
        ? `EdgeCast estimates a ${trade.direction} probability of ${ecPct}%, while the market price implies ${mktPct}%. That ${edge.toFixed(1)} pp gap is why this trade looks attractive.`
        : `EdgeCast's estimated probability is ${edge.toFixed(1)} pp higher than the market price for this side.`,
    });
  } else if (edge != null && edge <= 0) {
    bullets.push({
      tone: "gray",
      icon: <Zap className="h-4 w-4" />,
      headline: "No positive edge detected",
      detail: "EdgeCast entered this trade despite a zero or negative edge — this may reflect a legacy trade or changed market conditions since entry.",
    });
  }

  // 2. City model maturity (fallback level)
  const fallback = trade.fallbackLevel as string | null;
  if (fallback === "city") {
    bullets.push({
      tone: "green",
      icon: <Brain className="h-4 w-4" />,
      headline: "City-specific learned data is being used",
      detail: `EdgeCast has collected enough real weather outcomes for ${trade.city ?? "this city"} to use locally-measured patterns — not generic estimates. This is the most reliable mode.`,
    });
  } else if (fallback === "global") {
    bullets.push({
      tone: "blue",
      icon: <Brain className="h-4 w-4" />,
      headline: "Using global statistical estimates",
      detail: `Not enough local observations yet for ${trade.city ?? "this city"}, so EdgeCast fell back to global patterns across all cities. Results are reasonable but less precise than city-specific data.`,
    });
  } else if (fallback === "fixed_table") {
    bullets.push({
      tone: "amber",
      icon: <Brain className="h-4 w-4" />,
      headline: "Using built-in defaults (no learned data yet)",
      detail: `EdgeCast hasn't collected enough observations for ${trade.city ?? "this city"} to use measured data. It fell back to industry-standard fixed estimates. Treat this trade's probability with extra caution.`,
    });
  }

  // 3. City learning progress (from V2 learning endpoint)
  if (cityStatus) {
    const obsNote = cityObservations != null ? ` (${cityObservations.toLocaleString()} lessons logged)` : "";
    if (cityStatus === "learned") {
      bullets.push({
        tone: "green",
        icon: <MapPin className="h-4 w-4" />,
        headline: `${trade.city ?? "This city"} is fully trained${obsNote}`,
        detail: "Enough historical outcomes have been confirmed for this city that EdgeCast's city-specific patterns are considered reliable.",
      });
    } else if (cityStatus === "partially_learned") {
      bullets.push({
        tone: "blue",
        icon: <MapPin className="h-4 w-4" />,
        headline: `${trade.city ?? "This city"} is partially trained${obsNote}`,
        detail: "This city is actively collecting data. Predictions are improving but haven't yet reached the fully-trained threshold.",
      });
    } else if (cityStatus === "insufficient_sample") {
      bullets.push({
        tone: "amber",
        icon: <MapPin className="h-4 w-4" />,
        headline: `${trade.city ?? "This city"} is still building its dataset${obsNote}`,
        detail: "EdgeCast has started collecting data for this city but doesn't yet have enough to use learned statistics reliably.",
      });
    } else if (cityStatus === "collecting") {
      bullets.push({
        tone: "amber",
        icon: <MapPin className="h-4 w-4" />,
        headline: `${trade.city ?? "This city"} is in early data collection`,
        detail: "Predictions here rely more heavily on global or fixed estimates while the local dataset grows.",
      });
    }
  }

  // 4. Forecast lead time
  const leadTime = snap?.leadTimeDays as number | null;
  if (leadTime != null) {
    let tone: BulletTone;
    let headline: string;
    let detail: string;
    if (leadTime <= 2) {
      tone = "green";
      headline = `Short lead time — ${leadTime} day${leadTime !== 1 ? "s" : ""} to settlement`;
      detail = "Near-term weather forecasts are typically more accurate. A shorter lead time reduces the window for conditions to change.";
    } else if (leadTime <= 5) {
      tone = "blue";
      headline = `Medium lead time — ${leadTime} days to settlement`;
      detail = "Forecast accuracy at this range is generally solid, though some uncertainty remains.";
    } else if (leadTime <= 9) {
      tone = "amber";
      headline = `Longer lead time — ${leadTime} days to settlement`;
      detail = "Forecast accuracy tends to decrease at this range. EdgeCast's edge is estimated from a longer-horizon prediction.";
    } else {
      tone = "red";
      headline = `Extended lead time — ${leadTime} days to settlement`;
      detail = "Long-range forecasts carry significantly more uncertainty. The edge estimate at this horizon should be treated cautiously.";
    }
    bullets.push({ tone, icon: <Calendar className="h-4 w-4" />, headline, detail });
  }

  // 5. Bias correction
  const bias = trade.biasCorrection as number | null;
  if (bias != null && Math.abs(bias) >= 0.01) {
    const dir   = bias > 0 ? "warmer" : "cooler";
    const absBp = (Math.abs(bias) * 100).toFixed(1);
    bullets.push({
      tone: "blue",
      icon: <TrendingUp className="h-4 w-4" />,
      headline: `Bias correction applied: ${bias > 0 ? "+" : ""}${absBp} pp`,
      detail: `Forecasts for ${trade.city ?? "this city"} have historically run ${dir} than actual temperatures. EdgeCast adjusted its probability estimate to account for this systematic pattern.`,
    });
  }

  // 6. Confidence summary (always last)
  const conf = trade.confidenceLabel as string | null;
  if (conf) {
    const confMap: Record<string, { tone: BulletTone; headline: string; detail: string }> = {
      VERY_HIGH: {
        tone: "green",
        headline: "Overall confidence: Very High",
        detail: "Multiple signals align strongly. EdgeCast treated this as a high-conviction trade.",
      },
      HIGH: {
        tone: "blue",
        headline: "Overall confidence: High",
        detail: "The signals look good. EdgeCast entered with full stake size.",
      },
      MODERATE: {
        tone: "amber",
        headline: "Overall confidence: Moderate",
        detail: "The edge is real but the signals aren't perfectly aligned. EdgeCast entered but at reduced certainty.",
      },
      LOW: {
        tone: "red",
        headline: "Overall confidence: Low",
        detail: "EdgeCast entered cautiously. The edge was above the minimum threshold, but the overall picture is uncertain. Treat this outcome as noisier data.",
      },
    };
    const entry = confMap[conf.toUpperCase()];
    if (entry) {
      bullets.push({ ...entry, icon: <BarChart2 className="h-4 w-4" /> });
    }
  }

  return bullets;
}

// ── Why We Like This Trade card ───────────────────────────────────────────────

function WhyWeLikeThisTrade({
  trade,
  snap,
  cityStatus,
  cityObservations,
}: {
  trade: PaperTrade & { [k: string]: any };
  snap: Record<string, any> | null;
  cityStatus: string | null;
  cityObservations: number | null;
}) {
  const bullets = buildReasoningBullets(trade, snap, cityStatus, cityObservations);

  if (bullets.length === 0) {
    return (
      <Card className="border-border/50 bg-card/40">
        <CardContent className="pt-4">
          <p className="text-sm text-muted-foreground">No reasoning data available for this trade.</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-blue-500/30 bg-blue-500/5">
      <CardHeader className="pb-3 border-b border-blue-500/20">
        <CardTitle className="text-sm font-semibold text-blue-300 flex items-center gap-2">
          <Brain className="h-4 w-4" />
          Why EdgeCast Liked This Trade
        </CardTitle>
        <p className="text-xs text-muted-foreground mt-1">
          Plain-English summary of the signals EdgeCast evaluated when deciding to enter.
        </p>
      </CardHeader>
      <CardContent className="pt-4 space-y-3">
        {bullets.map((b, i) => (
          <div key={i} className={`rounded-lg border p-3 ${TONE_BG[b.tone]}`}>
            <div className={`flex items-center gap-2 font-medium text-sm mb-1 ${TONE_TEXT[b.tone]}`}>
              <span className="shrink-0">{b.icon}</span>
              {b.headline}
            </div>
            <p className="text-xs text-muted-foreground leading-relaxed pl-6">{b.detail}</p>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

// ── Probability comparison bar ────────────────────────────────────────────────

function ProbabilityBar({ trade }: { trade: PaperTrade & { [k: string]: any } }) {
  const ecProb  = trade.ecSideProbability  ?? null;
  const mktProb = trade.sideMarketPrice    ?? null;
  if (ecProb == null || mktProb == null) return null;

  const ecPct  = Math.round(ecProb  * 100);
  const mktPct = Math.round(mktProb * 100);
  const edge   = ecPct - mktPct;

  return (
    <Card className="border-border/50 bg-card/40">
      <CardHeader className="pb-2">
        <CardTitle className="text-xs font-mono tracking-widest text-muted-foreground uppercase">
          Probability at Entry
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <div className="flex justify-between text-xs font-mono text-muted-foreground">
            <span>EdgeCast ({trade.direction})</span>
            <span className="text-foreground font-semibold">{ecPct}%</span>
          </div>
          <div className="h-2 bg-muted/30 rounded-full overflow-hidden">
            <div
              className="h-full bg-primary rounded-full transition-all"
              style={{ width: `${ecPct}%` }}
            />
          </div>
        </div>
        <div className="space-y-2">
          <div className="flex justify-between text-xs font-mono text-muted-foreground">
            <span>Market price ({trade.direction})</span>
            <span className="text-foreground font-semibold">{mktPct}%</span>
          </div>
          <div className="h-2 bg-muted/30 rounded-full overflow-hidden">
            <div
              className="h-full bg-slate-500 rounded-full transition-all"
              style={{ width: `${mktPct}%` }}
            />
          </div>
        </div>
        <div className="flex items-center justify-between pt-1 border-t border-border/40">
          <span className="text-xs text-muted-foreground font-mono">Edge (gap)</span>
          <span className={`text-sm font-mono font-bold ${edge > 0 ? "text-emerald-400" : "text-red-400"}`}>
            {edge > 0 ? "+" : ""}{edge}pp
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Outcome section ───────────────────────────────────────────────────────────

function OutcomeSection({ trade }: { trade: PaperTrade }) {
  if (trade.status === "OPEN") {
    return (
      <Card className="border-sky-500/30 bg-sky-500/5">
        <CardContent className="pt-4 flex items-start gap-3">
          <Clock className="h-5 w-5 text-sky-300 shrink-0 mt-0.5" />
          <div>
            <p className="text-sm text-sky-300 font-medium">Open — Awaiting Settlement</p>
            <p className="text-xs text-muted-foreground mt-1">
              This trade is still open. EdgeCast checks for settlement every 3 hours.
            </p>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (trade.status === "VOID") {
    return (
      <Card className="border-border">
        <CardContent className="pt-4">
          <p className="text-sm text-muted-foreground font-medium">VOID — Market Canceled</p>
          <p className="text-xs text-muted-foreground mt-1">
            The market was canceled. The ${(trade.stake ?? 0).toFixed(2)} stake was refunded.
          </p>
        </CardContent>
      </Card>
    );
  }

  if (trade.status === "SETTLED") {
    const isWin = trade.outcome === "WIN";
    return (
      <Card className={`border ${isWin ? "border-emerald-500/30 bg-emerald-500/5" : "border-rose-500/30 bg-rose-500/5"}`}>
        <CardHeader className="pb-2">
          <div className="flex items-center gap-2">
            {isWin
              ? <CheckCircle2 className="h-5 w-5 text-emerald-400" />
              : <XCircle     className="h-5 w-5 text-rose-400" />}
            <CardTitle className={`text-base ${isWin ? "text-emerald-400" : "text-rose-400"}`}>
              {trade.outcome}
            </CardTitle>
            <span className="text-xs text-muted-foreground ml-auto font-mono">
              Kalshi: {(trade.kalshiResult ?? "—").toUpperCase()}
            </span>
          </div>
        </CardHeader>
        <CardContent className="pt-0">
          <div className="grid grid-cols-3 divide-x divide-border/40 text-center">
            <div className="py-3 px-2">
              <div className={`text-xl font-mono font-bold ${isWin ? "text-emerald-400" : "text-rose-400"}`}>
                {usd(trade.profitLoss, true)}
              </div>
              <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mt-0.5">P&L</div>
            </div>
            <div className="py-3 px-2">
              <div className={`text-xl font-mono font-bold ${isWin ? "text-emerald-400" : "text-rose-400"}`}>
                {trade.returnPct != null ? `${trade.returnPct >= 0 ? "+" : ""}${trade.returnPct.toFixed(1)}%` : "—"}
              </div>
              <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mt-0.5">Return</div>
            </div>
            <div className="py-3 px-2">
              <div className="text-xl font-mono font-bold text-foreground">
                ${(trade.grossPayout ?? 0).toFixed(2)}
              </div>
              <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mt-0.5">Gross Payout</div>
            </div>
          </div>
          <p className="text-xs text-muted-foreground text-center pt-2 border-t border-border/40 font-mono">
            Settled {fmtTs(trade.settlementTimestamp)}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-destructive/30 bg-destructive/5">
      <CardContent className="pt-4">
        <p className="text-sm text-destructive font-medium">Settlement Error</p>
      </CardContent>
    </Card>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function PaperTradeDetailPage({ params }: { params: { id: string } }) {
  const tradeId = parseInt(params.id, 10);

  const { data: trade, isLoading, error } = useGetPaperTrade(tradeId);
  const { data: learning } = useGetV2LearningProgress();

  if (isLoading) {
    return (
      <div className="space-y-4 max-w-3xl">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (error || !trade) {
    return (
      <div className="p-6 bg-destructive/10 border border-destructive rounded-md text-destructive flex items-center gap-3">
        <AlertTriangle className="h-5 w-5" />
        <span className="font-mono">PAPER TRADE NOT FOUND</span>
      </div>
    );
  }

  // Extended fields returned by detail endpoint (typed loosely)
  const mkt  = (trade as any).market   as Record<string, any> | null;
  const snap = (trade as any).snapshot as Record<string, any> | null;

  const warnings: string[]        = trade.warnings ? trade.warnings.split(";").map((w: string) => w.trim()).filter(Boolean) : [];
  const qualityFlags: string[]    = (trade as any).qualityFlags ?? [];
  const isFlagged: boolean        = (trade as any).isFlagged ?? false;
  const flagDescriptions: Record<string, string> = (trade as any).qualityFlagDescriptions ?? {};

  // City learning context
  const cityData        = learning?.cities.find(c => c.city === trade.city);
  const cityStatus      = cityData?.readinessStatus ?? null;
  const cityObservations = cityData?.usableObservations ?? null;

  const tradeAny = trade as any;

  return (
    <div className="space-y-6 max-w-3xl">
      {/* Back link */}
      <Link href="/paper-trading"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors">
        <ArrowLeft className="h-4 w-4" /> Back to Paper Trading
      </Link>

      {/* Header */}
      <div className="flex items-start gap-4 flex-wrap">
        <div className="flex-1 min-w-0">
          <h1 className="text-2xl font-bold tracking-tight font-mono truncate">{trade.marketTicker}</h1>
          {mkt?.title    && <p className="text-muted-foreground mt-1">{mkt.title}</p>}
          {mkt?.subtitle && <p className="text-xs text-muted-foreground">{mkt.subtitle}</p>}
        </div>
        <div className="flex items-center gap-2 flex-wrap shrink-0">
          <DirectionBadge direction={trade.direction} />
          <Badge variant="outline" className={`font-mono text-xs ${
            trade.status === "OPEN"    ? "text-sky-400 border-sky-500/30 bg-sky-500/5" :
            trade.status === "SETTLED" ? "text-muted-foreground border-border" :
            trade.status === "VOID"    ? "text-muted-foreground border-border" :
                                         "text-destructive border-destructive/30"
          }`}>
            {trade.status}
          </Badge>
          <Badge variant="outline" className="font-mono text-xs text-muted-foreground">
            {trade.strategyVersion}
          </Badge>
          {isFlagged && (
            <Badge variant="outline" className="font-mono text-xs text-amber-400 border-amber-500/40 bg-amber-500/10">
              {qualityFlags.length} flag{qualityFlags.length !== 1 ? "s" : ""}
            </Badge>
          )}
        </div>
      </div>

      {/* Simulation disclaimer */}
      <div className="flex items-center gap-2 bg-amber-500/10 border border-amber-500/30 rounded-lg px-4 py-3">
        <AlertTriangle className="h-4 w-4 text-amber-400 shrink-0" />
        <p className="text-xs text-amber-300/90">
          Simulation only — no real trades were placed. No Kalshi trading credentials were used.
        </p>
      </div>

      {/* Outcome */}
      <OutcomeSection trade={trade} />

      {/* ── WHY WE LIKE THIS TRADE (the main feature) ── */}
      <WhyWeLikeThisTrade
        trade={tradeAny}
        snap={snap}
        cityStatus={cityStatus}
        cityObservations={cityObservations}
      />

      {/* Probability comparison bar */}
      <ProbabilityBar trade={tradeAny} />

      {/* Decision rationale (raw text from backend, if any) */}
      {trade.decisionExplanation && (
        <Card className="border-border/50 bg-card/40">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-mono tracking-widest text-muted-foreground uppercase">
              Decision Log
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm leading-relaxed text-muted-foreground font-mono">
              {trade.decisionExplanation}
            </p>
          </CardContent>
        </Card>
      )}

      {/* Trade entry details */}
      <Card className="border-border/50 bg-card/40">
        <CardHeader className="pb-2 border-b border-border/40">
          <CardTitle className="text-xs font-mono tracking-widest text-muted-foreground uppercase">
            Trade Entry Details
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-3">
          <Row label="Direction"><DirectionBadge direction={trade.direction} /></Row>
          <Row
            label="EdgeCast probability"
            tip="EdgeCast's estimated probability for the side it traded (YES or NO)."
          >{pct(tradeAny.ecSideProbability)}</Row>
          <Row
            label="Market price"
            tip="The Kalshi market price for this side at the time of entry (in probability terms)."
          >{pct(tradeAny.sideMarketPrice)}</Row>
          <Row
            label="Edge"
            tip="EdgeCast probability minus market price. Higher is better."
          >
            <span className={tradeAny.edgePctPoints > 0 ? "text-emerald-400" : "text-muted-foreground"}>
              {tradeAny.edgePctPoints != null ? `+${tradeAny.edgePctPoints.toFixed(1)}pp` : "—"}
            </span>
          </Row>
          <Row label="Confidence">{trade.confidenceLabel ?? "—"}</Row>
          <Row label="Stake">${(trade.stake ?? 0).toFixed(2)}</Row>
          <Row label="Quantity (contracts)">{trade.quantity != null ? trade.quantity.toFixed(4) : "—"}</Row>
          <Row label="Price source">{tradeAny.priceSource ?? "—"}</Row>
          <Row label="Contract type">{trade.contractType ?? "—"}</Row>
          <Row label="Weather variable">{trade.weatherVariable ?? "—"}</Row>
          <Row label="Target settlement">{trade.targetSettlementDate ? trade.targetSettlementDate.slice(0, 10) : "—"}</Row>
          <Row label="Created">{fmtTs(trade.createdAt)}</Row>
        </CardContent>
      </Card>

      {/* V2 model internals */}
      {trade.strategyVersion?.startsWith("v2") && (tradeAny.sigmaUsed != null || tradeAny.biasCorrection != null || tradeAny.fallbackLevel) && (
        <Card className="border-border/50 bg-card/40">
          <CardHeader className="pb-2 border-b border-border/40">
            <CardTitle className="text-xs font-mono tracking-widest text-muted-foreground uppercase">
              V2 Model Internals
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-3">
            <Row
              label="Sigma used"
              tip="The standard deviation of forecast errors for this city and lead time. Higher sigma = more forecast uncertainty = wider probability spread."
            >{tradeAny.sigmaUsed != null ? `${tradeAny.sigmaUsed.toFixed(2)}°F` : "—"}</Row>
            <Row
              label="Bias correction"
              tip="Systematic adjustment applied because forecasts for this city consistently run warmer or cooler than actual temperatures."
            >
              <span className={tradeAny.biasCorrection && Math.abs(tradeAny.biasCorrection) > 0 ? "text-blue-400" : "text-muted-foreground"}>
                {tradeAny.biasCorrection != null ? `${tradeAny.biasCorrection > 0 ? "+" : ""}${(tradeAny.biasCorrection * 100).toFixed(1)}pp` : "None"}
              </span>
            </Row>
            <Row
              label="Fallback level"
              tip="Whether EdgeCast used locally-learned city data, global estimates, or industry-standard fixed defaults."
            >
              <span className={
                tradeAny.fallbackLevel === "city"        ? "text-emerald-400" :
                tradeAny.fallbackLevel === "global"      ? "text-blue-400" :
                tradeAny.fallbackLevel === "fixed_table" ? "text-amber-400" :
                "text-muted-foreground"
              }>
                {tradeAny.fallbackLevel === "city"        ? "City-specific (learned)" :
                 tradeAny.fallbackLevel === "global"      ? "Global estimates" :
                 tradeAny.fallbackLevel === "fixed_table" ? "Fixed defaults" :
                 tradeAny.fallbackLevel ?? "—"}
              </span>
            </Row>
            {tradeAny.calibrationAdj != null && (
              <Row
                label="Calibration adj."
                tip="Probability adjustment applied based on EdgeCast's historical over/under-confidence at this level."
              >{tradeAny.calibrationAdj > 0 ? "+" : ""}{(tradeAny.calibrationAdj * 100).toFixed(2)}pp</Row>
            )}
          </CardContent>
        </Card>
      )}

      {/* Linked snapshot */}
      {snap && (
        <Card className="border-border/50 bg-card/40">
          <CardHeader className="pb-2 border-b border-border/40">
            <CardTitle className="text-xs font-mono tracking-widest text-muted-foreground uppercase">
              Prediction Snapshot #{snap.id}
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-3">
            <Row label="Snapshot time">{fmtTs(snap.createdAt)}</Row>
            <Row label="EC probability">{pct(snap.ecProbability)}</Row>
            <Row label="Market probability">{pct(snap.marketProbability)}</Row>
            <Row label="Confidence">{snap.confidence ?? "—"}</Row>
            <Row label="Forecast value">{snap.forecastValue != null ? `${snap.forecastValue.toFixed(1)}°F` : "—"}</Row>
            <Row label="Lead time">{snap.leadTimeDays != null ? `${snap.leadTimeDays} day${snap.leadTimeDays !== 1 ? "s" : ""}` : "—"}</Row>
            {snap.explanation && (
              <div className="mt-3 text-xs text-muted-foreground leading-relaxed border-t border-border/40 pt-3 font-mono">
                {snap.explanation}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Market prices at entry */}
      {mkt && (
        <Card className="border-border/50 bg-card/40">
          <CardHeader className="pb-2 border-b border-border/40">
            <CardTitle className="text-xs font-mono tracking-widest text-muted-foreground uppercase">
              Market Prices at Entry
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-3">
            <Row label="YES Bid">{mkt.yesBid != null ? pct(mkt.yesBid) : "—"}</Row>
            <Row label="YES Ask">{mkt.yesAsk != null ? pct(mkt.yesAsk) : "—"}</Row>
            <Row label="NO Bid">{mkt.noBid  != null ? pct(mkt.noBid)  : "—"}</Row>
            <Row label="NO Ask">{mkt.noAsk  != null ? pct(mkt.noAsk)  : "—"}</Row>
          </CardContent>
        </Card>
      )}

      {/* Quality flags */}
      {isFlagged && (
        <Card className="border-amber-500/30 bg-amber-500/5">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-amber-300 flex items-center gap-2">
              <AlertTriangle className="h-4 w-4" /> Data Quality Flags
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xs text-amber-300/70 mb-3 leading-relaxed">
              Recorded at trade creation and permanent. Flagged trades are not automatically excluded
              from metrics — use the "Flagged only / Clean only" filter to control inclusion.
            </p>
            <ul className="space-y-2">
              {qualityFlags.map((flag: string) => (
                <li key={flag} className="text-xs">
                  <span className="font-mono text-amber-400 font-semibold">{flag}</span>
                  {flagDescriptions[flag] && (
                    <p className="text-amber-300/70 mt-0.5 leading-relaxed">{flagDescriptions[flag]}</p>
                  )}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {/* Warnings */}
      {warnings.length > 0 && (
        <Card className="border-amber-500/30 bg-amber-500/5">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-amber-300 flex items-center gap-2">
              <AlertTriangle className="h-4 w-4" /> Warnings
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1.5">
              {warnings.map((w: string, i: number) => (
                <li key={i} className="text-xs text-amber-300/80 leading-relaxed">• {w}</li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
