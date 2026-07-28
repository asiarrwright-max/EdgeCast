import { Link } from "wouter";
import { ArrowLeft, AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import { useGetPaperTrade, type PaperTrade } from "@workspace/api-client-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

// ── Helpers ──────────────────────────────────────────────────────────────────

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

// ── Label-value pair ──────────────────────────────────────────────────────────

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between items-baseline py-2 border-b border-border/40 last:border-0">
      <span className="text-xs text-muted-foreground uppercase tracking-wide w-44 shrink-0">{label}</span>
      <span className="text-sm text-right font-mono">{children}</span>
    </div>
  );
}

// ── Direction badge ───────────────────────────────────────────────────────────

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

// ── Outcome section ───────────────────────────────────────────────────────────

function OutcomeSection({ trade }: { trade: PaperTrade }) {
  if (trade.status === "OPEN") {
    return (
      <Card className="border-sky-500/30 bg-sky-500/5">
        <CardContent className="pt-4">
          <p className="text-sm text-sky-300 font-medium">⏳ Open — Awaiting Settlement</p>
          <p className="text-xs text-muted-foreground mt-1">
            This trade is still open. EdgeCast will check for settlement on the next
            scheduled settlement cycle (every 3 hours).
          </p>
        </CardContent>
      </Card>
    );
  }

  if (trade.status === "VOID") {
    return (
      <Card className="border-border">
        <CardContent className="pt-4">
          <p className="text-sm text-muted-foreground font-medium">VOID — Market Canceled</p>
          <div className="mt-2 space-y-0">
            <Row label="Stake refunded">${(trade.stake ?? 0).toFixed(2)}</Row>
          </div>
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
              : <XCircle className="h-5 w-5 text-rose-400" />}
            <CardTitle className={`text-base ${isWin ? "text-emerald-400" : "text-rose-400"}`}>
              {trade.outcome}
            </CardTitle>
          </div>
        </CardHeader>
        <CardContent className="pt-0">
          <Row label="Kalshi Result">{(trade.kalshiResult ?? "—").toUpperCase()}</Row>
          <Row label="Gross Payout">${(trade.grossPayout ?? 0).toFixed(4)}</Row>
          <Row label="Profit / Loss">
            <span className={isWin ? "text-emerald-400" : "text-rose-400"}>
              {usd(trade.profitLoss, true)}
            </span>
          </Row>
          <Row label="Return">
            <span className={isWin ? "text-emerald-400" : "text-rose-400"}>
              {trade.returnPct != null ? `${trade.returnPct >= 0 ? "+" : ""}${trade.returnPct.toFixed(1)}%` : "—"}
            </span>
          </Row>
          <Row label="Settled At">{fmtTs(trade.settlementTimestamp)}</Row>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-destructive/30 bg-destructive/5">
      <CardContent className="pt-4">
        <p className="text-sm text-destructive font-medium">ERROR</p>
        <p className="text-xs text-muted-foreground mt-1">Settlement check encountered an error.</p>
      </CardContent>
    </Card>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function PaperTradeDetailPage({ params }: { params: { id: string } }) {
  const tradeId = parseInt(params.id, 10);

  const { data: trade, isLoading, error } = useGetPaperTrade(tradeId);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
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

  // Type-cast extended fields returned by the detail endpoint
  const mkt = (trade as any).market as Record<string, any> | null;
  const snap = (trade as any).snapshot as Record<string, any> | null;

  const warnings = trade.warnings ? trade.warnings.split(";").map((w: string) => w.trim()).filter(Boolean) : [];
  const qualityFlags: string[] = (trade as any).qualityFlags ?? [];
  const isFlagged: boolean = (trade as any).isFlagged ?? false;
  const flagDescriptions: Record<string, string> = (trade as any).qualityFlagDescriptions ?? {};

  return (
    <div className="space-y-6">
      {/* Back link */}
      <Link href="/paper-trading"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors">
        <ArrowLeft className="h-4 w-4" /> Back to Paper Trading
      </Link>

      {/* Title */}
      <div className="flex items-start gap-4 flex-wrap">
        <div className="flex-1">
          <h1 className="text-2xl font-bold tracking-tight font-mono">{trade.marketTicker}</h1>
          {mkt?.title && <p className="text-muted-foreground mt-1">{mkt.title}</p>}
          {mkt?.subtitle && <p className="text-xs text-muted-foreground">{mkt.subtitle}</p>}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <DirectionBadge direction={trade.direction} />
          <span className={`text-xs px-1.5 py-0.5 rounded border font-medium ${
            trade.status === "OPEN"    ? "text-sky-400 border-sky-500/30" :
            trade.status === "SETTLED" ? "text-muted-foreground border-border" :
            trade.status === "VOID"    ? "text-muted-foreground border-border" :
                                         "text-destructive border-destructive/30"
          }`}>
            {trade.status}
          </span>
          {isFlagged && (
            <span className="text-xs px-1.5 py-0.5 rounded border font-medium text-amber-400 border-amber-500/40 bg-amber-500/10">
              ⚑ {qualityFlags.length} flag{qualityFlags.length !== 1 ? "s" : ""}
            </span>
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

      {/* Outcome / settlement */}
      <OutcomeSection trade={trade} />

      {/* Decision rationale */}
      <Card className="border-border">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Decision Rationale</CardTitle>
        </CardHeader>
        <CardContent>
          {trade.decisionExplanation ? (
            <p className="text-sm leading-relaxed">{trade.decisionExplanation}</p>
          ) : (
            <p className="text-muted-foreground text-sm">No explanation recorded.</p>
          )}
        </CardContent>
      </Card>

      {/* Trade entry */}
      <Card className="border-border">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Trade Entry</CardTitle>
        </CardHeader>
        <CardContent>
          <Row label="Direction"><DirectionBadge direction={trade.direction} /></Row>
          <Row label="EC YES Probability">{pct(trade.ecYesProbability)}</Row>
          <Row label="EC Side Probability">{pct(trade.ecSideProbability)}</Row>
          <Row label="Market YES Probability">{pct(trade.marketYesProbability)}</Row>
          <Row label="Purchase Price">{trade.sideMarketPrice != null ? pct(trade.sideMarketPrice) : "—"}</Row>
          <Row label="Price Source">{trade.priceSource ?? "—"}</Row>
          <Row label="Edge">
            <span className="text-primary">
              {trade.edgePctPoints != null ? `+${trade.edgePctPoints.toFixed(1)}pp` : "—"}
            </span>
          </Row>
          <Row label="Confidence">{trade.confidenceLabel ?? "—"}</Row>
          <Row label="Stake">${(trade.stake ?? 0).toFixed(2)}</Row>
          <Row label="Quantity (contracts)">
            {trade.quantity != null ? trade.quantity.toFixed(4) : "—"}
          </Row>
          <Row label="Contract Type">{trade.contractType ?? "—"}</Row>
          <Row label="Weather Variable">{trade.weatherVariable ?? "—"}</Row>
          <Row label="Target Settlement">{trade.targetSettlementDate ? trade.targetSettlementDate.slice(0, 10) : "—"}</Row>
          <Row label="Strategy Version">{trade.strategyVersion}</Row>
          <Row label="Created At">{fmtTs(trade.createdAt)}</Row>
        </CardContent>
      </Card>

      {/* Snapshot info */}
      {snap && (
        <Card className="border-border">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Linked Prediction Snapshot #{snap.id}</CardTitle>
          </CardHeader>
          <CardContent>
            <Row label="Snapshot Time">{fmtTs(snap.createdAt)}</Row>
            <Row label="EC Probability">{pct(snap.ecProbability)}</Row>
            <Row label="Market Probability">{pct(snap.marketProbability)}</Row>
            <Row label="Confidence">{snap.confidence ?? "—"}</Row>
            <Row label="Forecast Value">{snap.forecastValue != null ? `${snap.forecastValue.toFixed(1)}°F` : "—"}</Row>
            <Row label="Lead Time">{snap.leadTimeDays != null ? `${snap.leadTimeDays} day(s)` : "—"}</Row>
            {snap.explanation && (
              <div className="mt-3 text-xs text-muted-foreground leading-relaxed border-t border-border/40 pt-3">
                {snap.explanation}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Market prices at entry */}
      {mkt && (
        <Card className="border-border">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Market Prices at Entry</CardTitle>
          </CardHeader>
          <CardContent>
            <Row label="YES Bid">{mkt.yesBid != null ? pct(mkt.yesBid) : "—"}</Row>
            <Row label="YES Ask">{mkt.yesAsk != null ? pct(mkt.yesAsk) : "—"}</Row>
            <Row label="NO Bid">{mkt.noBid != null ? pct(mkt.noBid) : "—"}</Row>
            <Row label="NO Ask">{mkt.noAsk != null ? pct(mkt.noAsk) : "—"}</Row>
          </CardContent>
        </Card>
      )}

      {/* Quality Flags */}
      {isFlagged && (
        <Card className="border-amber-500/30 bg-amber-500/5">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-amber-300 flex items-center gap-2">
              <AlertTriangle className="h-4 w-4" /> Data Quality Flags
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xs text-amber-300/70 mb-3">
              These flags were recorded at trade creation and are permanent. They indicate potential
              data-quality concerns; flagged trades are <strong>not</strong> automatically excluded
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
