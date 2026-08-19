/**
 * Real-Money Readiness Dashboard
 *
 * Displays truthful readiness status based exclusively on OFFICIAL
 * forward-test evidence.  This page never enables or implies real-money
 * execution capability.
 *
 * SAFETY:
 *  - Data source: OFFICIAL paper trades only (eligibility_status == "OFFICIAL")
 *  - No protected threshold is activated; status remains NOT_READY /
 *    NEEDS_EVIDENCE until a separate owner YELLOW decision is recorded.
 *  - realMoneyExecutionEnabled is always false.
 */
import {
  AlertTriangle,
  CheckCircle,
  CircleOff,
  Info,
  ShieldAlert,
  XCircle,
  TrendingDown,
  BarChart2,
  Target,
  MapPin,
  Activity,
  AlertCircle,
  ClipboardList,
} from "lucide-react";
import { useGetReadiness } from "@workspace/api-client-react";
import type {
  ReadinessDashboard,
  CityBreakdownRow,
  StrategyBreakdownRow,
  EdgeBucketRow,
  ConfidenceBreakdownRow,
} from "@workspace/api-client-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(val: number | null | undefined, suffix = "", digits = 1): string {
  if (val == null) return "Insufficient data";
  return `${val.toFixed(digits)}${suffix}`;
}

function fmtPct(val: number | null | undefined): string {
  if (val == null) return "Insufficient data";
  return `${(val * 100).toFixed(1)}%`;
}

function smallSampleNote(small: boolean): string | null {
  return small ? "⚠ Small sample — preliminary only" : null;
}

// ---------------------------------------------------------------------------
// Status banner
// ---------------------------------------------------------------------------

function StatusBanner({ status, reason }: { status: string; reason: string }) {
  const isNotReady = status === "NOT_READY";
  const borderColor = isNotReady
    ? "border-destructive/50 bg-destructive/10"
    : "border-yellow-600/50 bg-yellow-950/20";
  const textColor = isNotReady ? "text-destructive" : "text-yellow-400";
  const Icon = isNotReady ? XCircle : AlertCircle;

  return (
    <div className={`rounded-lg border p-4 flex gap-3 items-start ${borderColor}`}>
      <Icon className={`h-5 w-5 mt-0.5 shrink-0 ${textColor}`} />
      <div className="space-y-1">
        <p className={`font-mono font-bold text-base ${textColor}`}>{status.replace("_", " ")}</p>
        <p className="text-sm text-muted-foreground">{reason}</p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Safety notice
// ---------------------------------------------------------------------------

function SafetyNotice() {
  return (
    <div className="rounded-lg border border-sky-700/40 bg-sky-950/20 p-4 flex gap-3 items-start">
      <ShieldAlert className="h-5 w-5 text-sky-400 shrink-0 mt-0.5" />
      <div className="space-y-1 text-sm">
        <p className="font-semibold text-sky-300">Evidence only — no real-money execution</p>
        <p className="text-muted-foreground">
          This dashboard surfaces forward-test evidence from OFFICIAL paper trades. No real-money
          order placement, Kalshi execution, or automatic trading capability is present or activated.
          Readiness thresholds require a separate owner YELLOW decision before they can be set.
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Evidence gap panel
// ---------------------------------------------------------------------------

function EvidenceGapPanel({ gaps }: { gaps: string[] }) {
  return (
    <Card className="border-border/60">
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          <ClipboardList className="h-4 w-4 text-yellow-400" />
          <CardTitle className="text-sm font-mono uppercase tracking-wide text-yellow-400">
            What EdgeCast needs next
          </CardTitle>
        </div>
      </CardHeader>
      <CardContent>
        {gaps.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No critical evidence gaps detected. Readiness thresholds are pending owner approval
            (separate YELLOW decision required before status can advance).
          </p>
        ) : (
          <ul className="space-y-2">
            {gaps.map((gap, i) => (
              <li key={i} className="flex gap-2 text-sm">
                <AlertTriangle className="h-4 w-4 text-yellow-400 shrink-0 mt-0.5" />
                <span className="text-muted-foreground">{gap}</span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Metric card
// ---------------------------------------------------------------------------

function MetricCard({
  label,
  value,
  note,
  warn,
}: {
  label: string;
  value: string;
  note?: string | null;
  warn?: boolean;
}) {
  return (
    <div className="rounded-lg border border-border/60 bg-card/70 p-4 space-y-1">
      <p className="text-xs uppercase tracking-wide text-muted-foreground font-mono">{label}</p>
      <p className={`text-2xl font-mono font-bold ${warn ? "text-yellow-400" : "text-foreground"}`}>
        {value}
      </p>
      {note && <p className="text-xs text-muted-foreground">{note}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Breakdown table
// ---------------------------------------------------------------------------

interface BreakdownTableProps<T> {
  title: string;
  icon: React.ReactNode;
  rows: T[];
  columns: { header: string; render: (row: T) => React.ReactNode }[];
  emptyMessage?: string;
}

function BreakdownTable<T>({
  title,
  icon,
  rows,
  columns,
  emptyMessage = "No data",
}: BreakdownTableProps<T>) {
  return (
    <Card className="border-border/60">
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          {icon}
          <CardTitle className="text-sm font-mono uppercase tracking-wide">{title}</CardTitle>
        </div>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">{emptyMessage}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm font-mono">
              <thead>
                <tr className="border-b border-border/60">
                  {columns.map((col) => (
                    <th
                      key={col.header}
                      className="py-2 px-2 text-left text-xs uppercase tracking-wide text-muted-foreground whitespace-nowrap"
                    >
                      {col.header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr key={i} className="border-b border-border/40 last:border-0 hover:bg-secondary/30">
                    {columns.map((col) => (
                      <td key={col.header} className="py-2 px-2 whitespace-nowrap">
                        {col.render(row)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function ReadinessPage() {
  const { data, isLoading, isError, error } = useGetReadiness();

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-24 w-full" />
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-20 w-full" />
          ))}
        </div>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-6 text-sm space-y-2">
        <p className="font-bold text-destructive font-mono">READINESS DATA UNAVAILABLE</p>
        <p className="text-muted-foreground">
          {(error instanceof Error ? error.message : "Failed to load readiness data. Check that the API server is running.")}
        </p>
      </div>
    );
  }

  const d: ReadinessDashboard = data;
  const e = d.evidence;
  const cov = d.settlementCoverage;
  const qq = d.quoteQuality;
  const ab = d.abstentionAnalysis;

  return (
    <div className="space-y-6">

      {/* Header */}
      <div>
        <h1 className="text-2xl font-mono font-bold text-primary uppercase tracking-wide">
          Real-Money Readiness
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Evidence-only view. OFFICIAL forward-test trades · no execution capability.
        </p>
      </div>

      {/* Safety notice */}
      <SafetyNotice />

      {/* Readiness status */}
      <StatusBanner
        status={d.readiness.status}
        reason={d.readiness.reason}
      />

      {/* Population note */}
      <div className="flex items-start gap-2 rounded-lg border border-border/40 bg-secondary/30 px-4 py-3">
        <Info className="h-4 w-4 text-muted-foreground shrink-0 mt-0.5" />
        <p className="text-xs text-muted-foreground">{e.populationNote}</p>
      </div>

      {/* Core evidence metrics */}
      <div className="space-y-3">
        <h2 className="text-xs font-mono uppercase tracking-widest text-muted-foreground">
          Evidence Summary
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <MetricCard label="OFFICIAL Trades" value={String(e.officialTradeCount)} />
          <MetricCard
            label="Settled"
            value={String(e.settledCount)}
            note={e.smallSampleWarning ? "⚠ Preliminary — < 30 settled" : undefined}
            warn={e.smallSampleWarning}
          />
          <MetricCard label="Win Rate" value={fmtPct(e.winRate)} warn={e.smallSampleWarning} />
          <MetricCard label="ROI" value={fmt(e.roi, "%")} warn={e.smallSampleWarning} />
          <MetricCard label="Net P&L" value={fmt(e.netProfitLoss, "$")} />
          <MetricCard label="Brier Score" value={fmt(e.brierScore, "", 4)} />
          <MetricCard label="Avg Entry Edge" value={fmt(e.avgEntryEdgePp, "pp")} />
          <MetricCard label="Cities" value={String(e.cityCount)} />
          <MetricCard label="Max Drawdown" value={fmt(e.maxDrawdown, "$")} />
          <MetricCard label="Longest Losing Streak" value={e.longestLosingStreak == null ? "Insufficient data" : String(e.longestLosingStreak)} />
          <MetricCard label="Wins" value={String(e.wins)} />
          <MetricCard label="Losses" value={String(e.losses)} />
        </div>
      </div>

      {/* What EdgeCast needs next */}
      <EvidenceGapPanel gaps={d.evidenceGaps} />

      {/* Settlement coverage */}
      <Card className="border-border/60">
        <CardHeader className="pb-2">
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-primary" />
            <CardTitle className="text-sm font-mono uppercase tracking-wide">Settlement Coverage</CardTitle>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            <MetricCard label="Total" value={String(cov.total)} />
            <MetricCard label="Settled" value={String(cov.settled)} />
            <MetricCard label="Open" value={String(cov.open)} />
            <MetricCard label="Coverage" value={fmt(cov.settlementCoveragePct, "%")} />
          </div>
          {Object.keys(cov.regimeBreakdown).length > 0 && (
            <div className="space-y-1">
              <p className="text-xs uppercase tracking-wide text-muted-foreground font-mono">Settlement Regime</p>
              <div className="flex flex-wrap gap-2 mt-1">
                {Object.entries(cov.regimeBreakdown).map(([regime, count]) => (
                  <Badge key={regime} variant="secondary" className="font-mono">
                    {regime}: {count}
                  </Badge>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* City breakdown */}
      <BreakdownTable<CityBreakdownRow>
        title="City Breakdown"
        icon={<MapPin className="h-4 w-4 text-primary" />}
        rows={d.cityBreakdown}
        emptyMessage="No OFFICIAL trades with city data."
        columns={[
          { header: "City", render: (r) => r.city },
          { header: "Total", render: (r) => r.total },
          { header: "Settled", render: (r) => r.settled },
          { header: "Wins", render: (r) => r.wins },
          {
            header: "Win Rate",
            render: (r) => (
              <span className={r.smallSample ? "text-yellow-400" : ""}>
                {fmtPct(r.winRate)}
                {r.smallSample && <span className="ml-1 text-xs opacity-70">⚠</span>}
              </span>
            ),
          },
        ]}
      />

      {/* Strategy breakdown */}
      <BreakdownTable<StrategyBreakdownRow>
        title="Strategy Breakdown"
        icon={<Target className="h-4 w-4 text-primary" />}
        rows={d.strategyBreakdown}
        emptyMessage="No OFFICIAL trades with strategy data."
        columns={[
          { header: "Strategy", render: (r) => r.strategy },
          { header: "Total", render: (r) => r.total },
          { header: "Settled", render: (r) => r.settled },
          {
            header: "Win Rate",
            render: (r) => (
              <span className={r.smallSample ? "text-yellow-400" : ""}>
                {fmtPct(r.winRate)}
                {r.smallSample && <span className="ml-1 text-xs opacity-70">⚠</span>}
              </span>
            ),
          },
          { header: "Brier", render: (r) => fmt(r.brierScore, "", 4) },
          { header: "Avg Edge", render: (r) => fmt(r.avgEdgePp, "pp") },
        ]}
      />

      {/* Edge bucket breakdown */}
      <BreakdownTable<EdgeBucketRow>
        title="Edge Bucket Breakdown"
        icon={<BarChart2 className="h-4 w-4 text-primary" />}
        rows={d.edgeBucketBreakdown}
        emptyMessage="No edge data available."
        columns={[
          { header: "Edge Range", render: (r) => r.bucket },
          { header: "Total", render: (r) => r.total },
          { header: "Settled", render: (r) => r.settled },
          {
            header: "Win Rate",
            render: (r) => (
              <span className={r.smallSample ? "text-yellow-400" : ""}>
                {fmtPct(r.winRate)}
                {r.smallSample && <span className="ml-1 text-xs opacity-70">⚠</span>}
              </span>
            ),
          },
        ]}
      />

      {/* Confidence breakdown */}
      <BreakdownTable<ConfidenceBreakdownRow>
        title="Confidence Label Breakdown"
        icon={<CheckCircle className="h-4 w-4 text-primary" />}
        rows={d.confidenceBreakdown}
        emptyMessage="No confidence label data available."
        columns={[
          { header: "Confidence", render: (r) => r.confidenceLabel },
          { header: "Total", render: (r) => r.total },
          { header: "Settled", render: (r) => r.settled },
          {
            header: "Win Rate",
            render: (r) => (
              <span className={r.smallSample ? "text-yellow-400" : ""}>
                {fmtPct(r.winRate)}
                {r.smallSample && <span className="ml-1 text-xs opacity-70">⚠</span>}
              </span>
            ),
          },
        ]}
      />

      {/* Quote quality */}
      <Card className="border-border/60">
        <CardHeader className="pb-2">
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-primary" />
            <CardTitle className="text-sm font-mono uppercase tracking-wide">Quote Quality</CardTitle>
          </div>
          <CardDescription className="text-xs">
            Stale or missing quote rates across OFFICIAL trades.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <MetricCard label="Total Trades" value={String(qq.total)} />
            <MetricCard label="Missing Quotes" value={String(qq.missingQuoteCount)} />
            <MetricCard label="Stale Quotes" value={String(qq.staleQuoteCount)} />
            <MetricCard label="Stale Rate" value={fmtPct(qq.staleQuoteRate)} />
          </div>
        </CardContent>
      </Card>

      {/* Abstention analysis */}
      <Card className="border-border/60">
        <CardHeader className="pb-2">
          <div className="flex items-center gap-2">
            <CircleOff className="h-4 w-4 text-muted-foreground" />
            <CardTitle className="text-sm font-mono uppercase tracking-wide">
              Abstention Analysis (RESEARCH_ONLY)
            </CardTitle>
          </div>
          <CardDescription className="text-xs">
            Trades classified RESEARCH_ONLY — excluded from all official metrics.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm font-mono text-muted-foreground mb-3">
            RESEARCH_ONLY count:{" "}
            <span className="text-foreground font-bold">{ab.researchOnlyCount}</span>
          </p>
          {Object.keys(ab.reasonBreakdown).length > 0 && (
            <div className="space-y-1">
              <p className="text-xs uppercase tracking-wide text-muted-foreground font-mono">By Reason</p>
              <div className="flex flex-wrap gap-2 mt-1">
                {Object.entries(ab.reasonBreakdown).map(([reason, count]) => (
                  <Badge key={reason} variant="secondary" className="font-mono text-xs">
                    {reason}: {count}
                  </Badge>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Settlement integrity exceptions */}
      {d.settlementIntegrityExceptions.length > 0 && (
        <Card className="border-yellow-700/40 bg-yellow-950/10">
          <CardHeader className="pb-2">
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-yellow-400" />
              <CardTitle className="text-sm font-mono uppercase tracking-wide text-yellow-400">
                Settlement Integrity Exceptions ({d.settlementIntegrityExceptions.length})
              </CardTitle>
            </div>
            <CardDescription className="text-xs">
              Settled OFFICIAL trades with data-quality flags. Review before drawing conclusions.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-xs font-mono">
                <thead>
                  <tr className="border-b border-border/60">
                    <th className="py-2 px-2 text-left text-muted-foreground">ID</th>
                    <th className="py-2 px-2 text-left text-muted-foreground">Ticker</th>
                    <th className="py-2 px-2 text-left text-muted-foreground">City</th>
                    <th className="py-2 px-2 text-left text-muted-foreground">Flags</th>
                    <th className="py-2 px-2 text-left text-muted-foreground">Settled At</th>
                  </tr>
                </thead>
                <tbody>
                  {d.settlementIntegrityExceptions.map((exc) => (
                    <tr key={exc.tradeId} className="border-b border-border/40 last:border-0">
                      <td className="py-1.5 px-2">{exc.tradeId}</td>
                      <td className="py-1.5 px-2 max-w-xs truncate">{exc.ticker}</td>
                      <td className="py-1.5 px-2">{exc.city ?? "—"}</td>
                      <td className="py-1.5 px-2">
                        {exc.flags.map((f) => (
                          <Badge key={f} variant="outline" className="mr-1 text-yellow-400 border-yellow-700/50 text-xs">
                            {f}
                          </Badge>
                        ))}
                      </td>
                      <td className="py-1.5 px-2 text-muted-foreground">
                        {exc.settledAt ? exc.settledAt.slice(0, 10) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Footer disclaimer */}
      <div className="rounded-lg border border-border/40 bg-secondary/20 px-4 py-3 text-xs text-muted-foreground space-y-1">
        <p className="font-semibold">Disclaimer</p>
        <p>
          This dashboard is a read-only evidence display. It does not constitute financial advice,
          does not activate any readiness threshold, and does not enable real-money trading.
          All metrics are derived from paper-trading simulations only.
          Specific criteria for advancing the readiness status require a separate owner YELLOW decision.
        </p>
      </div>

    </div>
  );
}
