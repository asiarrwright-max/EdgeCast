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
  type CumulativeRoiPoint,
  type RollingWinRatePoint,
  type BrierOverTimePoint,
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

function SectionCard({
  title,
  subtitle,
  children,
  tooltip,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  tooltip?: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
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
        {subtitle && (
          <p className="text-xs text-muted-foreground mt-0.5">{subtitle}</p>
        )}
      </div>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Trend helpers
// ---------------------------------------------------------------------------

type TrendDirection = "improving" | "worse" | "unchanged" | "insufficient";

/**
 * Compare the average of the first third vs last third of a numeric series.
 * Returns "insufficient" when fewer than 6 data points exist.
 * `higherIsBetter` flips the green/red semantic.
 */
function computeTrendFromSeries(
  values: number[],
  higherIsBetter: boolean
): TrendDirection {
  if (values.length < 6) return "insufficient";
  const third = Math.floor(values.length / 3);
  const early = values.slice(0, third).reduce((s, v) => s + v, 0) / third;
  const late = values.slice(-third).reduce((s, v) => s + v, 0) / third;
  const delta = late - early;
  const threshold = Math.abs(early) * 0.03; // 3% relative change to count as movement
  if (Math.abs(delta) <= threshold) return "unchanged";
  const isImproving = higherIsBetter ? delta > 0 : delta < 0;
  return isImproving ? "improving" : "worse";
}

function TrendBadge({ direction }: { direction: TrendDirection }) {
  if (direction === "insufficient")
    return <span className="text-[10px] text-muted-foreground">Not enough data</span>;
  if (direction === "improving")
    return (
      <span className="inline-flex items-center gap-0.5 text-[10px] font-medium text-emerald-400">
        ↑ Improving
      </span>
    );
  if (direction === "worse")
    return (
      <span className="inline-flex items-center gap-0.5 text-[10px] font-medium text-red-400">
        ↓ Getting worse
      </span>
    );
  return (
    <span className="inline-flex items-center gap-0.5 text-[10px] text-muted-foreground">
      → Mostly unchanged
    </span>
  );
}

// ---------------------------------------------------------------------------
// Key Takeaways
// ---------------------------------------------------------------------------

function KeyTakeaways({
  summary,
  comparison,
  roiTrend,
  brierTrend,
}: {
  summary: {
    roi?: number | null;
    brierScore?: number | null;
    sampleSizeWarning?: boolean | null;
    settledCount?: number | null;
  } | null | undefined;
  comparison: { v2: { settled: number } } | null | undefined;
  roiTrend: TrendDirection;
  brierTrend: TrendDirection;
}) {
  if (!summary) return null;

  const points: string[] = [];

  // ROI statement
  if (summary.roi != null) {
    if (roiTrend === "improving" && summary.roi < 0)
      points.push("ROI is improving but remains negative. The long-term goal is to reach positive ROI.");
    else if (roiTrend === "improving" && summary.roi >= 0)
      points.push("ROI is improving and currently positive.");
    else if (roiTrend === "worse" && summary.roi >= 0)
      points.push("ROI is positive but has been declining recently.");
    else if (roiTrend === "worse")
      points.push("ROI has been declining in this period and remains negative.");
    else if (summary.roi >= 0)
      points.push("ROI is positive and holding steady.");
    else
      points.push("ROI is negative. More settled trades are needed to see whether the trend improves.");
  }

  // Brier score statement
  if (summary.brierScore != null) {
    const bs = summary.brierScore;
    if (brierTrend === "improving")
      points.push(
        `Prediction accuracy is improving (Brier Score ${bs.toFixed(3)} and trending down). Lower is better.`
      );
    else if (bs < 0.2)
      points.push(
        `Prediction accuracy is strong — Brier Score (${bs.toFixed(3)}) is well below the 50/50 baseline of 0.25.`
      );
    else if (bs > 0.3)
      points.push(
        `Prediction accuracy is above the 50/50 baseline of 0.25 (current: ${bs.toFixed(3)}). Lower is better.`
      );
    else
      points.push(
        `Prediction accuracy is near the 50/50 baseline (Brier Score ${bs.toFixed(3)}). Lower is better.`
      );
  }

  // V2 sample size
  if (comparison != null) {
    const v2Settled = comparison.v2?.settled ?? 0;
    if (v2Settled < 30) {
      points.push(
        `V2 has only ${v2Settled} settled trade${v2Settled !== 1 ? "s" : ""}, so the V1 vs V2 comparison is not yet reliable.`
      );
    }
  }

  // General sample size warning (after specific checks)
  if (summary.sampleSizeWarning && (summary.settledCount ?? 0) < 15) {
    points.push("More settled trades are needed before drawing firm conclusions from any metric.");
  }

  if (points.length === 0) return null;

  return (
    <div className="rounded-lg border border-blue-500/30 bg-blue-500/5 px-4 py-3">
      <p className="text-xs font-semibold text-blue-400 uppercase tracking-wide mb-2">Key Takeaways</p>
      <ul className="space-y-1.5">
        {points.map((p, i) => (
          <li key={i} className="text-sm text-foreground/80 flex gap-2">
            <span className="text-blue-400 shrink-0 mt-0.5">•</span>
            <span>{p}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// MetricCard
// ---------------------------------------------------------------------------

function MetricCard({
  label,
  value,
  sub,
  tone,
  tooltip,
  trend,
}: {
  label: string;
  value: ReactNode;
  sub?: string;
  tone?: "green" | "red" | "amber" | "neutral";
  tooltip?: string;
  trend?: TrendDirection;
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
      <div className="flex items-center gap-2 mt-0.5">
        {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
        {trend != null && <TrendBadge direction={trend} />}
      </div>
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
          {/* 50/50 baseline — not a definition of "good", just the uninformative reference */}
          <ReferenceLine
            y={0.25}
            stroke="hsl(var(--muted-foreground))"
            strokeDasharray="4 2"
            label={{ value: "50/50 baseline", position: "insideTopRight", fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
          />
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
        Lower is better. The dashed line at 0.25 is a simple 50/50 baseline — scoring below it does not automatically mean the model is profitable or strong.
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

// Derive a plain-English status label for the V1 vs V2 comparison.
const MIN_SETTLED_FOR_RELIABLE = 30;

function comparisonStatus(v1: { settled: number; roi?: number | null }, v2: { settled: number; roi?: number | null }): {
  label: string;
  tone: "amber" | "blue" | "green" | "neutral";
  reason: string;
} {
  if (v1.settled < MIN_SETTLED_FOR_RELIABLE || v2.settled < MIN_SETTLED_FOR_RELIABLE) {
    const low = v1.settled < v2.settled ? `V1 (${v1.settled})` : `V2 (${v2.settled})`;
    return {
      label: "Too Early to Call",
      tone: "amber",
      reason: `${low} settled trade${v1.settled < MIN_SETTLED_FOR_RELIABLE ? "s" : ""} — the comparison is not yet reliable. Both strategies need at least ${MIN_SETTLED_FOR_RELIABLE} settled trades for a meaningful result.`,
    };
  }
  if (v1.roi == null || v2.roi == null) {
    return { label: "Too Early to Call", tone: "amber", reason: "ROI data is not available for one or both strategies." };
  }
  const diff = Math.abs(v1.roi - v2.roi);
  if (diff < 2) {
    return { label: "Mixed Results", tone: "neutral", reason: "V1 and V2 ROI are within 2 percentage points of each other — results are too close to call a clear winner." };
  }
  if (v1.roi > v2.roi) {
    return { label: "V1 Currently Leading", tone: "blue", reason: `V1 ROI (${roiFmt(v1.roi)}) is ahead of V2 (${roiFmt(v2.roi)}). Check Shared Markets for the fairest comparison.` };
  }
  return { label: "V2 Currently Leading", tone: "blue", reason: `V2 ROI (${roiFmt(v2.roi)}) is ahead of V1 (${roiFmt(v1.roi)}). Check Shared Markets for the fairest comparison.` };
}

const TONE_CLASSES: Record<string, string> = {
  amber: "bg-amber-500/10 text-amber-400 border-amber-500/30",
  blue:  "bg-blue-500/10 text-blue-400 border-blue-500/30",
  green: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
  neutral: "bg-muted/40 text-muted-foreground border-border",
};

function StrategyComparisonTable({
  comparison,
}: {
  comparison: PerformanceStrategyComparison;
}) {
  const { v1, v2 } = comparison;
  const status = comparisonStatus(v1, v2);

  return (
    <div>
      {/* Status badge + reason */}
      <div className="px-4 py-3 border-b border-border flex flex-wrap items-start gap-3">
        <span className={`inline-block text-xs font-semibold px-2.5 py-1 rounded border ${TONE_CLASSES[status.tone]}`}>
          {status.label}
        </span>
        <p className="text-xs text-muted-foreground flex-1 min-w-0">{status.reason}</p>
      </div>

      {/* Shared Markets callout */}
      <div className="px-4 py-2.5 border-b border-border bg-blue-500/5 flex items-start gap-2">
        <span className="text-blue-400 text-xs shrink-0 mt-0.5">ℹ</span>
        <p className="text-xs text-muted-foreground">
          <span className="font-medium text-foreground/80">Shared Markets is the fairest comparison.</span>{" "}
          It includes only markets where both strategies evaluated the same opportunity. Overall results can differ because V1 and V2 do not always enter the same trades.
        </p>
      </div>

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

  // Derive trends from chart time series
  const roiTrend = computeTrendFromSeries(
    (charts?.cumulativeRoi ?? [] as CumulativeRoiPoint[]).map((d) => d.roi),
    true // higher ROI is better
  );
  const winRateTrend = computeTrendFromSeries(
    (charts?.rollingWinRate ?? [] as RollingWinRatePoint[]).map((d) => d.winRate),
    true
  );
  const brierTrend = computeTrendFromSeries(
    (charts?.brierOverTime ?? [] as BrierOverTimePoint[]).map((d) => d.brierScore),
    false // lower Brier is better
  );

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

      {/* Key Takeaways — computed from live data */}
      {!isLoading && (
        <KeyTakeaways
          summary={summary}
          comparison={comparison ?? null}
          roiTrend={roiTrend}
          brierTrend={brierTrend}
        />
      )}

      {/* Preliminary warning */}
      {summary?.sampleSizeWarning && (
        <div className="rounded border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-400">
          {summary.preliminaryNote ?? "Results are preliminary — fewer than 30 settled trades."}
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
            trend={winRateTrend}
          />
          <MetricCard
            label="ROI"
            value={roiFmt(summary?.roi)}
            tone={roiTone}
            sub={`Net P/L: ${money(summary?.netProfitLoss)}`}
            trend={roiTrend}
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
            tooltip="Only covers trades EdgeCast entered; markets it passed on are not scored. Lower is better — 0 is perfect, 0.25 is a simple 50/50 baseline. Scoring below 0.25 does not automatically mean the model is profitable."
            sub="lower is better"
            trend={brierTrend}
          />
          <MetricCard
            label="Average Entry Edge"
            value={summary?.avgEntryEdgePp != null ? `${summary.avgEntryEdgePp.toFixed(1)}pp` : "—"}
            sub="includes open trades"
            tooltip="The average difference between EdgeCast's estimated probability and the market price when the trade was placed. This includes open trades and does not represent realized profit."
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
        <SectionCard
          title="Cumulative ROI"
          subtitle="Shows how paper-trading profitability has changed over time. Higher is better."
        >
          {isLoading ? <Loading /> : <CumulativeRoiChart data={charts?.cumulativeRoi ?? []} />}
        </SectionCard>

        <SectionCard
          title="Rolling Win Rate (10-trade window)"
          subtitle="Shows the percentage of winning trades across the most recent 10 settled trades."
        >
          {isLoading ? <Loading /> : <RollingWinRateChart data={charts?.rollingWinRate ?? []} />}
        </SectionCard>

        <SectionCard
          title="Brier Score Over Time"
          subtitle="Shows how closely EdgeCast's confidence matched actual outcomes. Lower is better."
          tooltip="Only covers trades EdgeCast entered; markets it passed on are not scored."
        >
          {isLoading ? <Loading /> : <BrierOverTimeChart data={charts?.brierOverTime ?? []} />}
        </SectionCard>

        <SectionCard
          title="Daily P/L"
          subtitle="Shows the paper-trading profit or loss recorded each day."
        >
          {isLoading ? <Loading /> : <DailyPlChart data={charts?.dailyPl ?? []} />}
        </SectionCard>
      </div>

      {/* Daily trade count (full width) */}
      <SectionCard
        title="Daily Trade Count"
        subtitle="Shows how many paper trades EdgeCast placed each day."
      >
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
