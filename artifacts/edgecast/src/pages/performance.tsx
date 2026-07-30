/**
 * Performance Analytics Dashboard
 * =================================
 * Read-only page showing EdgeCast's paper-trading performance over time.
 * Charts: Cumulative ROI, Rolling Win Rate, Brier Score, Daily P/L, Daily Trade Count.
 * Summary cards: Total Trades, Settled, Open, Win Rate, ROI, Brier Score, Avg Edge, Avg Confidence.
 * Strategy comparison table: V1 vs V2 side-by-side.
 */
import { useState, ReactNode } from "react";
import {
  useGetPerformanceAnalytics,
  type PerformancePeriod,
  type PerformanceStrategy,
  type StrategyVersionStats,
  type StrategyGroupStats,
  type PerformanceStrategyComparison,
} from "@workspace/api-client-react";
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

const pct = (v: number | null | undefined, d = 1) =>
  v == null ? "—" : `${(v * 100).toFixed(d)}%`;
const pp = (v: number | null | undefined, d = 1) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(d)}pp`;
const money = (v: number | null | undefined) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}$${Math.abs(v).toFixed(2)}`;
const roiFmt = (v: number | null | undefined) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
const num = (v: number | null | undefined, d = 2) =>
  v == null ? "—" : v.toFixed(d);

function shortDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = iso.slice(0, 10);
  // e.g. "2025-07-15" → "Jul 15"
  try {
    return new Date(d + "T12:00:00Z").toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      timeZone: "UTC",
    });
  } catch {
    return d;
  }
}

// ---------------------------------------------------------------------------
// Layout helpers
// ---------------------------------------------------------------------------

function SectionCard({ title, children, tooltip }: { title: string; children: ReactNode; tooltip?: string }) {
  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="px-4 py-3 border-b border-border flex items-center gap-2">
        <h2 className="text-sm font-semibold">{title}</h2>
        {tooltip && (
          <div className="relative group">
            <span className="text-xs text-muted-foreground cursor-help rounded-full border border-border w-4 h-4 flex items-center justify-center select-none">
              i
            </span>
            <div className="absolute left-6 top-0 z-10 hidden group-hover:block w-64 rounded bg-popover border border-border px-3 py-2 text-xs text-muted-foreground shadow-lg">
              {tooltip}
            </div>
          </div>
        )}
      </div>
      {children}
    </div>
  );
}

function MetricCard({
  label,
  value,
  sub,
  tone,
  tooltip,
}: {
  label: string;
  value: ReactNode;
  sub?: string;
  tone?: "green" | "red" | "amber" | "neutral";
  tooltip?: string;
}) {
  const toneClass =
    tone === "green"
      ? "text-green-600 dark:text-green-400"
      : tone === "red"
      ? "text-red-600 dark:text-red-400"
      : tone === "amber"
      ? "text-amber-600 dark:text-amber-500"
      : "text-foreground";

  return (
    <div className="bg-muted/40 rounded-lg border border-border p-4 relative">
      <div className="flex items-center gap-1.5">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{label}</p>
        {tooltip && (
          <div className="relative group">
            <span className="text-xs text-muted-foreground cursor-help rounded-full border border-border w-3.5 h-3.5 flex items-center justify-center text-[10px] select-none">
              i
            </span>
            <div className="absolute left-5 top-0 z-10 hidden group-hover:block w-60 rounded bg-popover border border-border px-3 py-2 text-xs text-muted-foreground shadow-lg">
              {tooltip}
            </div>
          </div>
        )}
      </div>
      <p className={`mt-1 text-2xl font-bold ${toneClass}`}>{value}</p>
      {sub && <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>}
    </div>
  );
}

function Loading() {
  return <p className="text-sm text-muted-foreground px-4 py-8 text-center">Loading…</p>;
}

function Empty({ msg }: { msg?: string }) {
  return (
    <p className="text-sm text-muted-foreground px-4 py-8 text-center">
      {msg ?? "No data yet."}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Period + Strategy filters
// ---------------------------------------------------------------------------

const PERIODS: { value: PerformancePeriod; label: string }[] = [
  { value: "7d", label: "Last 7 Days" },
  { value: "30d", label: "Last 30 Days" },
  { value: "all", label: "All Time" },
];

const STRATEGIES: { value: PerformanceStrategy; label: string }[] = [
  { value: "all", label: "All Strategies" },
  { value: "v1.0", label: "V1.0 Only" },
  { value: "v2.0", label: "V2.0 Only" },
];

function FilterBar({
  period,
  strategy,
  onPeriod,
  onStrategy,
}: {
  period: PerformancePeriod;
  strategy: PerformanceStrategy;
  onPeriod: (p: PerformancePeriod) => void;
  onStrategy: (s: PerformanceStrategy) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2 items-center">
      <div className="flex rounded-md border border-border overflow-hidden">
        {PERIODS.map((p) => (
          <button
            key={p.value}
            onClick={() => onPeriod(p.value)}
            className={`px-3 py-1.5 text-xs font-medium transition-colors border-r border-border last:border-r-0 ${
              period === p.value
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-muted"
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>
      <div className="flex rounded-md border border-border overflow-hidden">
        {STRATEGIES.map((s) => (
          <button
            key={s.value}
            onClick={() => onStrategy(s.value)}
            className={`px-3 py-1.5 text-xs font-medium transition-colors border-r border-border last:border-r-0 ${
              strategy === s.value
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-muted"
            }`}
          >
            {s.label}
          </button>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Custom Recharts tooltip
// ---------------------------------------------------------------------------

function ChartTooltip({ active, payload, label, formatter }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded border border-border bg-popover px-3 py-2 shadow-md text-xs">
      <p className="font-medium mb-1">{label}</p>
      {payload.map((p: any) => (
        <p key={p.dataKey} style={{ color: p.color }}>
          {p.name}: {formatter ? formatter(p.value, p.dataKey) : p.value}
        </p>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Charts
// ---------------------------------------------------------------------------

function CumulativeRoiChart({ data }: { data: { date: string | null; roi: number }[] }) {
  if (!data.length) return <Empty msg="No settled trades yet." />;
  const formatted = data.map((d, i) => ({
    ...d,
    label: shortDate(d.date) || `#${i + 1}`,
  }));
  const last = formatted[formatted.length - 1];
  const color = (last?.roi ?? 0) >= 0 ? "#22c55e" : "#ef4444";
  return (
    <div className="px-4 pb-4 pt-2">
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={formatted} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} tickLine={false} interval="preserveStartEnd" />
          <YAxis
            tick={{ fontSize: 11 }}
            tickFormatter={(v) => `${v.toFixed(0)}%`}
            width={48}
          />
          <Tooltip
            content={<ChartTooltip formatter={(v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`} />}
          />
          <ReferenceLine y={0} stroke="hsl(var(--muted-foreground))" strokeDasharray="4 2" />
          <Line
            type="monotone"
            dataKey="roi"
            name="Cumulative ROI"
            stroke={color}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function RollingWinRateChart({
  data,
}: {
  data: { date: string | null; winRate: number; tradeNum: number }[];
}) {
  if (!data.length) return <Empty msg="No settled trades yet." />;
  const formatted = data.map((d) => ({
    ...d,
    label: shortDate(d.date) || `#${d.tradeNum}`,
    winRatePct: +(d.winRate * 100).toFixed(1),
  }));
  return (
    <div className="px-4 pb-4 pt-2">
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={formatted} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} tickLine={false} interval="preserveStartEnd" />
          <YAxis
            domain={[0, 100]}
            tick={{ fontSize: 11 }}
            tickFormatter={(v) => `${v}%`}
            width={40}
          />
          <Tooltip
            content={<ChartTooltip formatter={(v: number) => `${v.toFixed(1)}%`} />}
          />
          <ReferenceLine y={50} stroke="hsl(var(--muted-foreground))" strokeDasharray="4 2" />
          <Line
            type="monotone"
            dataKey="winRatePct"
            name="Win Rate (rolling)"
            stroke="#6366f1"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function BrierOverTimeChart({
  data,
}: {
  data: { date: string | null; brierScore: number; tradeCount: number }[];
}) {
  if (!data.length) return <Empty msg="No calibration data yet." />;
  const formatted = data.map((d, i) => ({
    ...d,
    label: shortDate(d.date) || `#${i + 1}`,
    bs: +d.brierScore.toFixed(4),
  }));
  return (
    <div className="px-4 pb-4 pt-2">
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={formatted} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} tickLine={false} interval="preserveStartEnd" />
          <YAxis
            domain={[0, 0.5]}
            tick={{ fontSize: 11 }}
            tickFormatter={(v) => v.toFixed(2)}
            width={40}
          />
          <Tooltip
            content={<ChartTooltip formatter={(v: number) => v.toFixed(4)} />}
          />
          {/* Perfect calibration reference: Brier ~0.25 for random 50/50 */}
          <ReferenceLine y={0.25} stroke="hsl(var(--muted-foreground))" strokeDasharray="4 2" />
          <Line
            type="monotone"
            dataKey="bs"
            name="Brier Score"
            stroke="#f59e0b"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
          />
        </LineChart>
      </ResponsiveContainer>
      <p className="text-xs text-muted-foreground px-1 mt-1">
        Lower is better. Dashed line = 0.25 (uninformative baseline).
      </p>
    </div>
  );
}

function DailyPlChart({ data }: { data: { date: string; pl: number }[] }) {
  if (!data.length) return <Empty msg="No settled trades yet." />;
  const formatted = data.map((d) => ({
    ...d,
    label: shortDate(d.date) || d.date,
    pl: +d.pl.toFixed(2),
  }));
  return (
    <div className="px-4 pb-4 pt-2">
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={formatted} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} tickLine={false} interval="preserveStartEnd" />
          <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `$${v.toFixed(0)}`} width={48} />
          <Tooltip
            content={<ChartTooltip formatter={(v: number) => `${v >= 0 ? "+" : ""}$${v.toFixed(2)}`} />}
          />
          <ReferenceLine y={0} stroke="hsl(var(--muted-foreground))" />
          <Bar
            dataKey="pl"
            name="Daily P/L"
            fill="#6366f1"
            radius={[2, 2, 0, 0]}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function DailyTradeCountChart({ data }: { data: { date: string; count: number }[] }) {
  if (!data.length) return <Empty msg="No trades yet." />;
  const formatted = data.map((d) => ({
    ...d,
    label: shortDate(d.date) || d.date,
  }));
  return (
    <div className="px-4 pb-4 pt-2">
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={formatted} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} tickLine={false} interval="preserveStartEnd" />
          <YAxis tick={{ fontSize: 11 }} allowDecimals={false} width={32} />
          <Tooltip content={<ChartTooltip formatter={(v: number) => `${v} trade${v !== 1 ? "s" : ""}`} />} />
          <Bar dataKey="count" name="Trades" fill="#94a3b8" radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Strategy comparison table
// ---------------------------------------------------------------------------

function StrategyRow({
  label,
  v1Stats,
  v2Stats,
  count,
}: {
  label: string;
  v1Stats: StrategyVersionStats | StrategyGroupStats | null;
  v2Stats: StrategyVersionStats | StrategyGroupStats | null;
  count?: number;
}) {
  return (
    <tr className="border-t border-border hover:bg-muted/30">
      <td className="px-4 py-2 text-sm font-medium">
        {label}
        {count != null && (
          <span className="ml-1.5 text-xs text-muted-foreground">({count})</span>
        )}
      </td>
      {[v1Stats, v2Stats].map((s, i) =>
        s == null ? (
          <td key={i} colSpan={3} className="px-4 py-2 text-xs text-muted-foreground text-center">
            —
          </td>
        ) : (
          <>
            <td key={`${i}-wr`} className="px-3 py-2 text-xs text-right">
              {pct(s.winRate)}
            </td>
            <td key={`${i}-roi`} className="px-3 py-2 text-xs text-right">
              {s.roi != null ? (
                <span className={s.roi >= 0 ? "text-green-600" : "text-red-600"}>
                  {roiFmt(s.roi)}
                </span>
              ) : (
                "—"
              )}
            </td>
            <td key={`${i}-pl`} className="px-3 py-2 text-xs text-right">
              {s.netPl != null ? (
                <span className={s.netPl >= 0 ? "text-green-600" : "text-red-600"}>
                  {money(s.netPl)}
                </span>
              ) : (
                "—"
              )}
            </td>
          </>
        )
      )}
    </tr>
  );
}

function StrategyComparisonTable({
  comparison,
}: {
  comparison: PerformanceStrategyComparison;
}) {
  const { v1, v2 } = comparison;

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="bg-muted/40 text-xs text-muted-foreground uppercase">
            <th className="text-left px-4 py-2">Segment</th>
            <th className="text-right px-3 py-2 border-l border-border" colSpan={3}>
              V1.0
            </th>
            <th className="text-right px-3 py-2 border-l border-border" colSpan={3}>
              V2.0
            </th>
          </tr>
          <tr className="bg-muted/20 text-xs text-muted-foreground">
            <th className="text-left px-4 py-2" />
            <th className="text-right px-3 py-2">Win Rate</th>
            <th className="text-right px-3 py-2">ROI</th>
            <th className="text-right px-3 py-2">Net P/L</th>
            <th className="text-right px-3 py-2 border-l border-border">Win Rate</th>
            <th className="text-right px-3 py-2">ROI</th>
            <th className="text-right px-3 py-2">Net P/L</th>
          </tr>
        </thead>
        <tbody>
          {/* Overall */}
          <tr className="border-t border-border bg-muted/10">
            <td className="px-4 py-2 text-sm font-semibold">Overall</td>
            <td className="px-3 py-2 text-xs text-right">{pct(v1.winRate)}</td>
            <td className="px-3 py-2 text-xs text-right">
              {v1.roi != null ? (
                <span className={v1.roi >= 0 ? "text-green-600" : "text-red-600"}>{roiFmt(v1.roi)}</span>
              ) : "—"}
            </td>
            <td className="px-3 py-2 text-xs text-right">
              {v1.netPl != null ? (
                <span className={v1.netPl >= 0 ? "text-green-600" : "text-red-600"}>{money(v1.netPl)}</span>
              ) : "—"}
            </td>
            <td className="px-3 py-2 text-xs text-right border-l border-border">{pct(v2.winRate)}</td>
            <td className="px-3 py-2 text-xs text-right">
              {v2.roi != null ? (
                <span className={v2.roi >= 0 ? "text-green-600" : "text-red-600"}>{roiFmt(v2.roi)}</span>
              ) : "—"}
            </td>
            <td className="px-3 py-2 text-xs text-right">
              {v2.netPl != null ? (
                <span className={v2.netPl >= 0 ? "text-green-600" : "text-red-600"}>{money(v2.netPl)}</span>
              ) : "—"}
            </td>
          </tr>
          {/* Brier score row */}
          <tr className="border-t border-border">
            <td className="px-4 py-2 text-xs text-muted-foreground">Brier Score</td>
            <td className="px-3 py-2 text-xs text-right" colSpan={3}>
              {num(v1.brierScore, 4)}
            </td>
            <td className="px-3 py-2 text-xs text-right border-l border-border" colSpan={3}>
              {num(v2.brierScore, 4)}
            </td>
          </tr>
          {/* Settled / Total */}
          <tr className="border-t border-border">
            <td className="px-4 py-2 text-xs text-muted-foreground">Settled / Total</td>
            <td className="px-3 py-2 text-xs text-right" colSpan={3}>
              {v1.settled} / {v1.total}
            </td>
            <td className="px-3 py-2 text-xs text-right border-l border-border" colSpan={3}>
              {v2.settled} / {v2.total}
            </td>
          </tr>

          {/* Segment rows — each column uses its own strategy's stats */}
          <StrategyRow
            label="Shared Markets"
            count={comparison.sharedCount}
            v1Stats={comparison.sharedV1}
            v2Stats={comparison.sharedV2}
          />
          <StrategyRow
            label="V1 Only"
            count={comparison.v1OnlyCount}
            v1Stats={comparison.v1OnlyV1}
            v2Stats={null}
          />
          <StrategyRow
            label="V2 Only"
            count={comparison.v2OnlyCount}
            v1Stats={null}
            v2Stats={comparison.v2OnlyV2}
          />
          <StrategyRow
            label="Opposite Sides"
            count={comparison.oppositeSideCount}
            v1Stats={comparison.oppositeSideV1}
            v2Stats={comparison.oppositeSideV2}
          />
        </tbody>
      </table>
      <p className="text-xs text-muted-foreground px-4 py-2 border-t border-border">
        Each column shows that strategy's own results for the segment.
        Strategy comparison always uses all-time data regardless of the period filter above.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function PerformancePage() {
  const [period, setPeriod] = useState<PerformancePeriod>("all");
  const [strategy, setStrategy] = useState<PerformanceStrategy>("all");

  const { data, isLoading, error } = useGetPerformanceAnalytics(period, strategy);

  const summary = data?.summary;
  const charts = data?.charts;
  const comparison = data?.strategyComparison;

  const roiTone =
    summary?.roi == null ? "neutral" : summary.roi >= 0 ? "green" : "red";
  const winRateTone =
    summary?.winRate == null
      ? "neutral"
      : summary.winRate >= 0.55
      ? "green"
      : summary.winRate < 0.4
      ? "red"
      : "neutral";

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Performance Analytics</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Read-only view of EdgeCast paper-trading results over time.
        </p>
      </div>

      {/* Filters */}
      <FilterBar
        period={period}
        strategy={strategy}
        onPeriod={setPeriod}
        onStrategy={setStrategy}
      />

      {/* Error */}
      {!!error && (
        <div className="rounded border border-destructive bg-destructive/10 px-4 py-3 text-sm text-destructive">
          Failed to load analytics. {String(error)}
        </div>
      )}

      {/* Preliminary warning */}
      {summary?.sampleSizeWarning && (
        <div className="rounded border border-amber-300 bg-amber-50 dark:bg-amber-900/20 dark:border-amber-700 px-4 py-3 text-sm text-amber-800 dark:text-amber-300">
          ⚠ {summary.preliminaryNote ?? "Results are preliminary — fewer than 30 settled trades."}
        </div>
      )}

      {/* Summary cards */}
      {isLoading ? (
        <Loading />
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <MetricCard
            label="Total Trades"
            value={summary?.totalCount ?? "—"}
            sub={`${summary?.openCount ?? 0} open`}
          />
          <MetricCard
            label="Settled"
            value={summary?.settledCount ?? "—"}
            sub={`${summary?.wins ?? 0}W / ${summary?.losses ?? 0}L`}
          />
          <MetricCard
            label="Win Rate"
            value={pct(summary?.winRate)}
            tone={winRateTone}
            sub="settled trades only"
          />
          <MetricCard
            label="ROI"
            value={roiFmt(summary?.roi)}
            tone={roiTone}
            sub={`Net P/L: ${money(summary?.netProfitLoss)}`}
          />
          <MetricCard
            label="Brier Score"
            value={summary?.brierScore != null ? summary.brierScore.toFixed(4) : "—"}
            tone={
              summary?.brierScore == null
                ? "neutral"
                : summary.brierScore < 0.2
                ? "green"
                : summary.brierScore > 0.3
                ? "red"
                : "neutral"
            }
            tooltip="Only covers trades EdgeCast entered; markets it passed on are not scored. Lower is better (0 = perfect, 0.25 = uninformative baseline)."
            sub="lower is better"
          />
          <MetricCard
            label="Avg Edge (at entry)"
            value={summary?.avgEntryEdgePp != null ? `${summary.avgEntryEdgePp.toFixed(1)}pp` : "—"}
            sub="includes open trades"
          />
          <MetricCard
            label="Avg Confidence"
            value={summary?.avgConfidenceLabel ?? "—"}
            sub={
              summary?.confidenceDistribution
                ? Object.entries(summary.confidenceDistribution as Record<string, number>)
                    .sort((a, b) => (b[1] as number) - (a[1] as number))
                    .slice(0, 3)
                    .map(([k, v]) => `${k}: ${v as number}`)
                    .join(" · ")
                : undefined
            }
          />
          <MetricCard
            label="Void Trades"
            value={summary?.voidCount ?? "—"}
            sub="excluded from metrics"
          />
        </div>
      )}

      {/* Charts — 2-column grid on large screens */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <SectionCard title="Cumulative ROI">
          {isLoading ? <Loading /> : <CumulativeRoiChart data={charts?.cumulativeRoi ?? []} />}
        </SectionCard>

        <SectionCard title="Rolling Win Rate (10-trade window)">
          {isLoading ? <Loading /> : <RollingWinRateChart data={charts?.rollingWinRate ?? []} />}
        </SectionCard>

        <SectionCard
          title="Brier Score Over Time"
          tooltip="Only covers trades EdgeCast entered; markets it passed on are not scored."
        >
          {isLoading ? <Loading /> : <BrierOverTimeChart data={charts?.brierOverTime ?? []} />}
        </SectionCard>

        <SectionCard title="Daily P/L">
          {isLoading ? <Loading /> : <DailyPlChart data={charts?.dailyPl ?? []} />}
        </SectionCard>
      </div>

      {/* Daily trade count (full width) */}
      <SectionCard title="Daily Trade Count">
        {isLoading ? <Loading /> : <DailyTradeCountChart data={charts?.dailyTradeCount ?? []} />}
      </SectionCard>

      {/* Strategy comparison */}
      <SectionCard title="Strategy Comparison: V1 vs V2">
        {isLoading ? (
          <Loading />
        ) : comparison ? (
          <StrategyComparisonTable comparison={comparison} />
        ) : (
          <Empty msg="No strategy data." />
        )}
      </SectionCard>
    </div>
  );
}
