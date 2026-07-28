/**
 * Strategy Differences & Loss Audit
 * ===================================
 * Five diagnostic sections:
 *   1. Strategy Differences — v1 vs v2 per-market comparison
 *   2. V1 Loss Audit        — probability-bucketed win/loss breakdown
 *   3. Long-shot Analysis   — entry-price-bucketed breakdown
 *   4. Settlement Check     — payout verification for all settled v1 trades
 *   5. V2 Readiness         — how much verified data v2 has accumulated
 */
import { useState, ReactNode } from "react";
import { Link } from "wouter";
import {
  useGetStrategyDifferences,
  useGetLossAudit,
  useGetLongShot,
  useGetSettlementCheck,
  useGetV2Readiness,
  type ComparisonRow,
  type LossAuditBucket,
  type LongShotBucket,
  type SettlementTrade,
  type V2ReadinessRow,
} from "@workspace/api-client-react";

// ---------------------------------------------------------------------------
// Layout helpers
// ---------------------------------------------------------------------------

function PageHeader() {
  return (
    <div>
      <h1 className="text-2xl font-bold tracking-tight">Strategy Differences &amp; Loss Audit</h1>
      <p className="text-sm text-muted-foreground mt-1">
        Read-only diagnostic report. No trades are modified.
      </p>
    </div>
  );
}

function TabBar({
  tabs,
  active,
  onChange,
}: {
  tabs: { id: string; label: string }[];
  active: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="flex gap-1 border-b border-border overflow-x-auto">
      {tabs.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          className={`px-4 py-2 text-sm font-medium whitespace-nowrap transition-colors border-b-2 -mb-px ${
            active === t.id
              ? "border-primary text-primary"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="px-4 py-3 border-b border-border">
        <h2 className="text-sm font-semibold">{title}</h2>
      </div>
      {children}
    </div>
  );
}

function StatBox({ label, value, sub }: { label: string; value: ReactNode; sub?: string }) {
  return (
    <div className="bg-muted/40 rounded border border-border px-4 py-3 text-center">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-xl font-bold mt-0.5">{value}</p>
      {sub && <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>}
    </div>
  );
}

function Loading() {
  return <p className="text-sm text-muted-foreground px-4 py-6">Loading…</p>;
}

function pct(v: number | null | undefined, decimals = 1) {
  if (v == null) return "—";
  return `${v.toFixed(decimals)}%`;
}

function money(v: number | null | undefined) {
  if (v == null) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}$${v.toFixed(2)}`;
}

function num(v: number | null | undefined, d = 2) {
  if (v == null) return "—";
  return v.toFixed(d);
}

// ---------------------------------------------------------------------------
// Tab 1 — Strategy Differences
// ---------------------------------------------------------------------------

const DIFF_FILTER_OPTIONS = [
  { value: "", label: "All markets" },
  { value: "both", label: "Both traded" },
  { value: "only_v1", label: "Only v1 traded" },
  { value: "only_v2", label: "Only v2 traded" },
  { value: "diff_side", label: "Different sides" },
];

const STATUS_OPTIONS = [
  { value: "", label: "Any status" },
  { value: "open", label: "Open" },
  { value: "settled", label: "Settled" },
];

const V2_ADJ_OPTIONS = [
  { value: "", label: "Any" },
  { value: "adj", label: "v2 used historical data" },
  { value: "fallback", label: "v2 using fallback" },
];

function StrategyDifferencesTab() {
  const [filter, setFilter] = useState("");
  const [minProbDiff, setMinProbDiff] = useState(0);
  const [v2Adj, setV2Adj] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  const { data, isLoading } = useGetStrategyDifferences({
    filter,
    min_prob_diff: minProbDiff,
    v2_adj: v2Adj,
    status: statusFilter,
  });

  const rows: ComparisonRow[] = data?.rows ?? [];

  return (
    <div className="space-y-4 p-4">
      {/* Filters */}
      <div className="flex flex-wrap gap-3 items-end">
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Show</label>
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="text-sm border border-border rounded px-2 py-1.5 bg-background"
          >
            {DIFF_FILTER_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Min prob diff</label>
          <select
            value={minProbDiff}
            onChange={(e) => setMinProbDiff(Number(e.target.value))}
            className="text-sm border border-border rounded px-2 py-1.5 bg-background"
          >
            <option value={0}>Any</option>
            <option value={5}>≥ 5pp</option>
            <option value={10}>≥ 10pp</option>
          </select>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">v2 data</label>
          <select
            value={v2Adj}
            onChange={(e) => setV2Adj(e.target.value)}
            className="text-sm border border-border rounded px-2 py-1.5 bg-background"
          >
            {V2_ADJ_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">Status</label>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="text-sm border border-border rounded px-2 py-1.5 bg-background"
          >
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>
        <p className="text-xs text-muted-foreground ml-auto self-end pb-1">
          {data ? `${data.total} markets` : ""}
        </p>
      </div>

      {isLoading ? (
        <Loading />
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground py-4 text-center">No markets match the selected filters.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full text-xs">
            <thead>
              <tr className="bg-muted/30 text-muted-foreground uppercase text-[10px]">
                <th className="text-left px-3 py-2">Market</th>
                <th className="text-left px-3 py-2">City</th>
                <th className="text-left px-3 py-2">Date</th>
                <th className="text-center px-3 py-2">v1 Dir</th>
                <th className="text-right px-3 py-2">v1 Prob</th>
                <th className="text-right px-3 py-2">v1 Price</th>
                <th className="text-right px-3 py-2">v1 Edge</th>
                <th className="text-center px-3 py-2">v1 Out</th>
                <th className="text-center px-3 py-2">v2 Dir</th>
                <th className="text-right px-3 py-2">v2 Prob</th>
                <th className="text-right px-3 py-2">v2 Price</th>
                <th className="text-right px-3 py-2">v2 Edge</th>
                <th className="text-center px-3 py-2">v2 Out</th>
                <th className="text-right px-3 py-2">Δ Prob</th>
                <th className="text-center px-3 py-2">σ src</th>
                <th className="text-right px-3 py-2">Bias</th>
                <th className="text-left px-3 py-2 max-w-[240px]">Reason</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.ticker} className="border-t border-border hover:bg-muted/20">
                  <td className="px-3 py-2 font-mono max-w-[160px] truncate">
                    <Link
                      href={`/paper-trading`}
                      className="text-primary hover:underline"
                      title={r.title ?? r.ticker}
                    >
                      {r.ticker}
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">{r.city ?? "—"}</td>
                  <td className="px-3 py-2 text-muted-foreground whitespace-nowrap">{r.settlementDate ?? "—"}</td>
                  {/* v1 */}
                  <td className="text-center px-3 py-2">
                    {r.v1Direction ? (
                      <DirBadge dir={r.v1Direction} status={r.v1Status} />
                    ) : <span className="text-muted-foreground">—</span>}
                  </td>
                  <td className="text-right px-3 py-2">{r.v1EcSideProb != null ? `${r.v1EcSideProb}%` : "—"}</td>
                  <td className="text-right px-3 py-2">{r.v1EntryPrice != null ? `${(r.v1EntryPrice * 100).toFixed(0)}¢` : "—"}</td>
                  <td className="text-right px-3 py-2">{r.v1Edge != null ? `${r.v1Edge.toFixed(1)}pp` : "—"}</td>
                  <td className="text-center px-3 py-2"><OutcomeBadge outcome={r.v1Outcome} /></td>
                  {/* v2 */}
                  <td className="text-center px-3 py-2">
                    {r.v2Direction ? (
                      <DirBadge dir={r.v2Direction} status={r.v2Status} />
                    ) : <span className="text-muted-foreground">—</span>}
                  </td>
                  <td className="text-right px-3 py-2">{r.v2EcSideProb != null ? `${r.v2EcSideProb}%` : "—"}</td>
                  <td className="text-right px-3 py-2">{r.v2EntryPrice != null ? `${(r.v2EntryPrice * 100).toFixed(0)}¢` : "—"}</td>
                  <td className="text-right px-3 py-2">{r.v2Edge != null ? `${r.v2Edge.toFixed(1)}pp` : "—"}</td>
                  <td className="text-center px-3 py-2"><OutcomeBadge outcome={r.v2Outcome} /></td>
                  {/* diff */}
                  <td className={`text-right px-3 py-2 font-medium ${(r.probDiffPp ?? 0) >= 10 ? "text-amber-500" : ""}`}>
                    {r.probDiffPp != null ? `${r.probDiffPp}pp` : "—"}
                  </td>
                  <td className="text-center px-3 py-2">
                    <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                      r.v2UsedHistorical
                        ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
                        : "bg-muted text-muted-foreground"
                    }`}>
                      {r.v2FallbackLevel ?? "—"}
                    </span>
                  </td>
                  <td className="text-right px-3 py-2 text-muted-foreground">
                    {r.v2BiasCorrection != null ? `${r.v2BiasCorrection > 0 ? "+" : ""}${r.v2BiasCorrection.toFixed(2)}°` : "—"}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground max-w-[240px] text-[10px]">
                    {r.differenceReason}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function DirBadge({ dir, status }: { dir: string; status: string | null }) {
  if (status === "V2_EXCLUDED") {
    return <span className="text-[10px] bg-muted text-muted-foreground px-1.5 py-0.5 rounded">EXCL</span>;
  }
  return (
    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${
      dir === "YES" ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
                    : "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
    }`}>{dir}</span>
  );
}

function OutcomeBadge({ outcome }: { outcome: string | null | undefined }) {
  if (!outcome) return <span className="text-muted-foreground">—</span>;
  return (
    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${
      outcome === "WIN" ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
      : outcome === "LOSS" ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
      : "bg-muted text-muted-foreground"
    }`}>{outcome}</span>
  );
}

// ---------------------------------------------------------------------------
// Tab 2 — V1 Loss Audit
// ---------------------------------------------------------------------------

function LossAuditTab() {
  const { data, isLoading } = useGetLossAudit();

  if (isLoading) return <Loading />;
  if (!data) return <p className="text-sm text-muted-foreground px-4 py-4">No data.</p>;

  const s = data.summary;

  return (
    <div className="space-y-6 p-4">
      {/* Summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatBox label="Settled" value={s.totalSettled} />
        <StatBox label="Wins" value={s.totalWins} />
        <StatBox label="Losses" value={s.totalLosses} />
        <StatBox
          label="Win rate"
          value={pct(s.overallWinRate)}
          sub={`Expected ${pct(s.expectedWins ? (s.expectedWins / s.totalSettled) * 100 : null)}`}
        />
        <StatBox
          label="Expected wins"
          value={num(s.expectedWins)}
          sub={`(sum of probabilities)`}
        />
        <StatBox
          label="Actual wins"
          value={s.actualWins}
          sub={s.expectedVsActualDiff != null
            ? `${s.expectedVsActualDiff > 0 ? "+" : ""}${s.expectedVsActualDiff.toFixed(1)} vs expected`
            : undefined}
        />
        <StatBox
          label="Longest loss streak"
          value={s.longestLosingStreak}
          sub="consecutive losses"
        />
        <StatBox
          label="Shortfall"
          value={s.expectedVsActualDiff != null ? num(s.expectedVsActualDiff) : "—"}
          sub={s.expectedVsActualDiff != null && s.expectedVsActualDiff < 0
            ? "fewer wins than predicted"
            : "more wins than predicted"}
        />
      </div>

      {/* Bucket table */}
      <Section title="Win/Loss by EdgeCast Probability at Entry">
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="bg-muted/30 text-muted-foreground text-xs uppercase">
                <th className="text-left px-4 py-2">EC Prob Bucket</th>
                <th className="text-right px-4 py-2">Trades</th>
                <th className="text-right px-4 py-2">Wins</th>
                <th className="text-right px-4 py-2">Losses</th>
                <th className="text-right px-4 py-2">Actual W%</th>
                <th className="text-right px-4 py-2">Exp Wins</th>
                <th className="text-right px-4 py-2">Avg Prob</th>
                <th className="text-right px-4 py-2">Avg Price</th>
                <th className="text-right px-4 py-2">Stake</th>
                <th className="text-right px-4 py-2">P/L</th>
                <th className="text-right px-4 py-2">ROI</th>
              </tr>
            </thead>
            <tbody>
              {data.buckets.map((b: LossAuditBucket) => (
                <tr key={b.bucket} className="border-t border-border hover:bg-muted/20">
                  <td className="px-4 py-2 font-medium">{b.bucket}</td>
                  <td className="text-right px-4 py-2">{b.settledCount}</td>
                  <td className="text-right px-4 py-2 text-green-600">{b.wins}</td>
                  <td className="text-right px-4 py-2 text-red-500">{b.losses}</td>
                  <td className={`text-right px-4 py-2 font-medium ${
                    b.actualWinRate == null ? "text-muted-foreground"
                    : b.actualWinRate >= 50 ? "text-green-600"
                    : "text-red-500"
                  }`}>{pct(b.actualWinRate)}</td>
                  <td className="text-right px-4 py-2 text-muted-foreground">{num(b.expectedWins)}</td>
                  <td className="text-right px-4 py-2 text-muted-foreground">{pct(b.avgPredictedProb)}</td>
                  <td className="text-right px-4 py-2 text-muted-foreground">
                    {b.avgEntryPrice != null ? `${(b.avgEntryPrice * 100).toFixed(1)}¢` : "—"}
                  </td>
                  <td className="text-right px-4 py-2">${num(b.totalStake)}</td>
                  <td className={`text-right px-4 py-2 font-medium ${
                    b.profitLoss == null ? "text-muted-foreground"
                    : b.profitLoss >= 0 ? "text-green-600" : "text-red-500"
                  }`}>{money(b.profitLoss)}</td>
                  <td className={`text-right px-4 py-2 ${
                    b.roi == null ? "text-muted-foreground"
                    : b.roi >= 0 ? "text-green-600" : "text-red-500"
                  }`}>{pct(b.roi)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      {/* Trade timeline */}
      <Section title="All Settled v1 Trades (chronological)">
        <div className="overflow-x-auto max-h-96">
          <table className="min-w-full text-xs">
            <thead className="sticky top-0 bg-card z-10">
              <tr className="bg-muted/30 text-muted-foreground text-[10px] uppercase">
                <th className="text-left px-3 py-2">Settled</th>
                <th className="text-left px-3 py-2">Ticker</th>
                <th className="text-left px-3 py-2">City</th>
                <th className="text-center px-3 py-2">Dir</th>
                <th className="text-right px-3 py-2">EC Prob</th>
                <th className="text-right px-3 py-2">Price</th>
                <th className="text-right px-3 py-2">Edge</th>
                <th className="text-right px-3 py-2">Stake</th>
                <th className="text-center px-3 py-2">Out</th>
                <th className="text-right px-3 py-2">P/L</th>
              </tr>
            </thead>
            <tbody>
              {data.trades.map((t) => (
                <tr key={t.id} className="border-t border-border hover:bg-muted/20">
                  <td className="px-3 py-1.5 text-muted-foreground whitespace-nowrap">
                    {t.settlementTimestamp ? t.settlementTimestamp.slice(0, 10) : "—"}
                  </td>
                  <td className="px-3 py-1.5 font-mono max-w-[140px] truncate">{t.ticker}</td>
                  <td className="px-3 py-1.5 text-muted-foreground">{t.city ?? "—"}</td>
                  <td className="text-center px-3 py-1.5">
                    {t.direction && <DirBadge dir={t.direction} status={null} />}
                  </td>
                  <td className="text-right px-3 py-1.5">{pct(t.ecSideProb)}</td>
                  <td className="text-right px-3 py-1.5">
                    {t.entryPrice != null ? `${(t.entryPrice * 100).toFixed(0)}¢` : "—"}
                  </td>
                  <td className="text-right px-3 py-1.5">
                    {t.edge != null ? `${t.edge.toFixed(1)}pp` : "—"}
                  </td>
                  <td className="text-right px-3 py-1.5">${num(t.stake)}</td>
                  <td className="text-center px-3 py-1.5"><OutcomeBadge outcome={t.outcome} /></td>
                  <td className={`text-right px-3 py-1.5 font-medium ${
                    t.pl == null ? "text-muted-foreground"
                    : t.pl >= 0 ? "text-green-600" : "text-red-500"
                  }`}>{money(t.pl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 3 — Long-shot Analysis
// ---------------------------------------------------------------------------

function LongShotTab() {
  const { data, isLoading } = useGetLongShot();

  if (isLoading) return <Loading />;
  if (!data) return <p className="text-sm text-muted-foreground px-4 py-4">No data.</p>;

  // Detect whether low-price trades dominate
  const lowPriceCount = data.buckets
    .filter((b) => ["1¢", "2–5¢", "6–10¢"].includes(b.bucket))
    .reduce((s, b) => s + b.settledCount, 0);
  const pctLowPrice = data.total > 0 ? (lowPriceCount / data.total) * 100 : 0;

  return (
    <div className="space-y-4 p-4">
      <div className={`rounded border px-4 py-3 text-sm ${
        pctLowPrice >= 50
          ? "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-700 dark:bg-amber-900/20 dark:text-amber-200"
          : "border-border bg-muted/30"
      }`}>
        {data.conclusion}
      </div>

      <Section title="Win/Loss by Entry Price">
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="bg-muted/30 text-muted-foreground text-xs uppercase">
                <th className="text-left px-4 py-2">Entry Price</th>
                <th className="text-right px-4 py-2">Settled</th>
                <th className="text-right px-4 py-2">Wins</th>
                <th className="text-right px-4 py-2">Losses</th>
                <th className="text-right px-4 py-2">Avg EC Prob</th>
                <th className="text-right px-4 py-2">Exp Wins</th>
                <th className="text-right px-4 py-2">Act Wins</th>
                <th className="text-right px-4 py-2">Stake</th>
                <th className="text-right px-4 py-2">P/L</th>
                <th className="text-right px-4 py-2">ROI</th>
              </tr>
            </thead>
            <tbody>
              {data.buckets.map((b: LongShotBucket) => (
                <tr key={b.bucket} className="border-t border-border hover:bg-muted/20">
                  <td className="px-4 py-2 font-medium">{b.bucket}</td>
                  <td className="text-right px-4 py-2">{b.settledCount}</td>
                  <td className="text-right px-4 py-2 text-green-600">{b.wins}</td>
                  <td className="text-right px-4 py-2 text-red-500">{b.losses}</td>
                  <td className="text-right px-4 py-2 text-muted-foreground">{pct(b.avgEcProb)}</td>
                  <td className="text-right px-4 py-2 text-muted-foreground">{num(b.expectedWins)}</td>
                  <td className="text-right px-4 py-2">{b.actualWins}</td>
                  <td className="text-right px-4 py-2">${num(b.totalStake)}</td>
                  <td className={`text-right px-4 py-2 font-medium ${
                    b.profitLoss == null ? "text-muted-foreground"
                    : b.profitLoss >= 0 ? "text-green-600" : "text-red-500"
                  }`}>{money(b.profitLoss)}</td>
                  <td className={`text-right px-4 py-2 ${
                    b.roi == null ? "text-muted-foreground"
                    : b.roi >= 0 ? "text-green-600" : "text-red-500"
                  }`}>{pct(b.roi)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <p className="text-xs text-muted-foreground">
        Low-price (long-shot) trades have a high expected loss rate by design. A 3¢ trade needs
        to win more than 1-in-33 times to be profitable. Whether EdgeCast's probability estimates
        are accurate at these price levels is what matters — expected wins vs actual wins shows
        whether the model is over- or under-confident.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 4 — Settlement Check
// ---------------------------------------------------------------------------

const CLASSIFICATION_LABELS: Record<string, { label: string; cls: string }> = {
  correct: { label: "✓ Correct", cls: "text-green-600" },
  payout_mismatch: { label: "⚠ Payout mismatch", cls: "text-amber-500" },
  outcome_mismatch: { label: "✗ Outcome mismatch", cls: "text-red-500" },
  unresolved: { label: "? Unresolved", cls: "text-muted-foreground" },
  missing_result: { label: "– Missing result", cls: "text-muted-foreground" },
};

function SettlementCheckTab() {
  const { data, isLoading } = useGetSettlementCheck();
  const [showOnlyProblems, setShowOnlyProblems] = useState(false);

  if (isLoading) return <Loading />;
  if (!data) return <p className="text-sm text-muted-foreground px-4 py-4">No data.</p>;

  const s = data.summary;
  const trades = showOnlyProblems
    ? data.trades.filter((t: SettlementTrade) => !["correct"].includes(t.classification))
    : data.trades;

  return (
    <div className="space-y-4 p-4">
      {/* Summary */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        <StatBox label="Total settled" value={s.total} />
        <StatBox
          label="Correctly settled"
          value={<span className="text-green-600">{s.correctlySettled}</span>}
        />
        <StatBox
          label="Incorrectly settled"
          value={<span className={s.incorrectlySettled > 0 ? "text-red-500" : ""}>{s.incorrectlySettled}</span>}
          sub="outcome or payout wrong"
        />
        <StatBox
          label="Unresolved"
          value={s.unresolved}
          sub="not treated as losses"
        />
        <StatBox
          label="Missing result"
          value={s.missingResult}
          sub="no Kalshi result stored"
        />
      </div>

      {s.incorrectlySettled === 0 && (
        <div className="rounded border border-green-300 bg-green-50 dark:border-green-700 dark:bg-green-900/20 px-4 py-3 text-sm text-green-800 dark:text-green-200">
          ✓ All {s.correctlySettled} settled trades have correct outcome classification and payout calculations.
        </div>
      )}

      {/* Filter toggle */}
      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          id="problems-only"
          checked={showOnlyProblems}
          onChange={(e) => setShowOnlyProblems(e.target.checked)}
          className="rounded"
        />
        <label htmlFor="problems-only" className="text-sm text-muted-foreground cursor-pointer">
          Show problems only
        </label>
        <span className="text-xs text-muted-foreground ml-auto">{trades.length} trades shown</span>
      </div>

      <Section title="Settlement Verification">
        <div className="overflow-x-auto max-h-[480px]">
          <table className="min-w-full text-xs">
            <thead className="sticky top-0 bg-card z-10">
              <tr className="bg-muted/30 text-muted-foreground text-[10px] uppercase">
                <th className="text-left px-3 py-2">Settled</th>
                <th className="text-left px-3 py-2">Ticker</th>
                <th className="text-center px-3 py-2">Dir</th>
                <th className="text-center px-3 py-2">Kalshi</th>
                <th className="text-center px-3 py-2">Recorded</th>
                <th className="text-center px-3 py-2">Expected</th>
                <th className="text-right px-3 py-2">Stake</th>
                <th className="text-right px-3 py-2">Qty</th>
                <th className="text-right px-3 py-2">Payout</th>
                <th className="text-right px-3 py-2">Exp Payout</th>
                <th className="text-right px-3 py-2">P/L</th>
                <th className="text-center px-3 py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t: SettlementTrade) => {
                const cls = CLASSIFICATION_LABELS[t.classification] ?? {
                  label: t.classification, cls: "text-muted-foreground"
                };
                return (
                  <tr key={t.id} className="border-t border-border hover:bg-muted/20">
                    <td className="px-3 py-1.5 text-muted-foreground whitespace-nowrap">
                      {t.settlementTimestamp ? t.settlementTimestamp.slice(0, 10) : "—"}
                    </td>
                    <td className="px-3 py-1.5 font-mono max-w-[160px] truncate">{t.ticker}</td>
                    <td className="text-center px-3 py-1.5">
                      {t.direction && <DirBadge dir={t.direction} status={null} />}
                    </td>
                    <td className="text-center px-3 py-1.5 uppercase font-medium text-muted-foreground">
                      {t.kalshiResult ?? "—"}
                    </td>
                    <td className="text-center px-3 py-1.5">
                      <OutcomeBadge outcome={t.recordedOutcome} />
                    </td>
                    <td className="text-center px-3 py-1.5">
                      <OutcomeBadge outcome={t.expectedOutcome} />
                    </td>
                    <td className="text-right px-3 py-1.5">${num(t.stake)}</td>
                    <td className="text-right px-3 py-1.5 text-muted-foreground">{num(t.quantity)}</td>
                    <td className="text-right px-3 py-1.5">${num(t.grossPayout)}</td>
                    <td className="text-right px-3 py-1.5 text-muted-foreground">
                      {t.expectedGrossPayout != null ? `$${num(t.expectedGrossPayout)}` : "—"}
                    </td>
                    <td className={`text-right px-3 py-1.5 font-medium ${
                      t.profitLoss == null ? "text-muted-foreground"
                      : t.profitLoss >= 0 ? "text-green-600" : "text-red-500"
                    }`}>{money(t.profitLoss)}</td>
                    <td className={`text-center px-3 py-1.5 text-[10px] font-medium ${cls.cls}`}>
                      {cls.label}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab 5 — V2 Readiness
// ---------------------------------------------------------------------------

const TIER_STYLES: Record<string, string> = {
  full: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  sigma_only: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
  fallback: "bg-muted text-muted-foreground",
};

const TIER_LABELS: Record<string, string> = {
  full: "Full (σ + calib)",
  sigma_only: "Sigma only",
  fallback: "Fallback",
};

function V2ReadinessTab() {
  const { data, isLoading } = useGetV2Readiness();

  if (isLoading) return <Loading />;
  if (!data) return <p className="text-sm text-muted-foreground px-4 py-4">No data.</p>;

  const s = data.summary;

  return (
    <div className="space-y-4 p-4">
      {/* Summary */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatBox label="Total groups" value={s.totalGroups} sub="city × var × lead × month" />
        <StatBox
          label="Fallback (< 5 obs)"
          value={<span className="text-muted-foreground">{s.fallbackGroups}</span>}
          sub={pct(s.pctFallback)}
        />
        <StatBox
          label="Sigma ready (≥ 5)"
          value={<span className="text-amber-500">{s.sigmaOnlyGroups}</span>}
          sub={pct(s.pctReady)}
        />
        <StatBox
          label="Fully ready (≥ 30)"
          value={<span className="text-green-600">{s.fullGroups}</span>}
          sub={pct(s.pctFull)}
        />
      </div>

      {s.totalGroups === 0 && (
        <div className="rounded border border-border bg-muted/30 px-4 py-3 text-sm text-muted-foreground">
          No verified data yet. Use the "Run Verification" button on the Paper Trading page to fetch
          actual observed temperatures for settled trades and rebuild error statistics.
        </div>
      )}

      {/* City × Variable summary */}
      {data.cityVariableSummary.length > 0 && (
        <Section title="By City × Variable">
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="bg-muted/30 text-muted-foreground text-xs uppercase">
                  <th className="text-left px-4 py-2">City</th>
                  <th className="text-left px-4 py-2">Variable</th>
                  <th className="text-right px-4 py-2">Groups</th>
                  <th className="text-right px-4 py-2">Total obs</th>
                  <th className="text-right px-4 py-2">Sigma ready</th>
                  <th className="text-right px-4 py-2">Fully ready</th>
                </tr>
              </thead>
              <tbody>
                {data.cityVariableSummary.map((cv) => (
                  <tr key={`${cv.city}::${cv.variable}`} className="border-t border-border hover:bg-muted/20">
                    <td className="px-4 py-2 font-medium">{cv.city}</td>
                    <td className="px-4 py-2 text-muted-foreground">{cv.variable}</td>
                    <td className="text-right px-4 py-2">{cv.groupCount}</td>
                    <td className="text-right px-4 py-2">{cv.totalObservations}</td>
                    <td className="text-right px-4 py-2 text-amber-500">{cv.sufficientForSigma}</td>
                    <td className="text-right px-4 py-2 text-green-600">{cv.sufficientForCalib}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      {/* Detail table */}
      {data.detailRows.length > 0 && (
        <Section title="Detail (city × variable × lead time × month)">
          <div className="overflow-x-auto max-h-96">
            <table className="min-w-full text-xs">
              <thead className="sticky top-0 bg-card z-10">
                <tr className="bg-muted/30 text-muted-foreground text-[10px] uppercase">
                  <th className="text-left px-3 py-2">City</th>
                  <th className="text-left px-3 py-2">Var</th>
                  <th className="text-left px-3 py-2">Lead</th>
                  <th className="text-right px-3 py-2">Mo</th>
                  <th className="text-right px-3 py-2">n</th>
                  <th className="text-right px-3 py-2">Bias</th>
                  <th className="text-right px-3 py-2">MAE</th>
                  <th className="text-right px-3 py-2">σ</th>
                  <th className="text-center px-3 py-2">Tier</th>
                </tr>
              </thead>
              <tbody>
                {data.detailRows.map((r: V2ReadinessRow, i: number) => (
                  <tr key={i} className="border-t border-border hover:bg-muted/20">
                    <td className="px-3 py-1.5 font-medium">{r.city}</td>
                    <td className="px-3 py-1.5 text-muted-foreground">{r.variable}</td>
                    <td className="px-3 py-1.5 text-muted-foreground">{r.leadTimeBucket}</td>
                    <td className="text-right px-3 py-1.5 text-muted-foreground">{r.month ?? "—"}</td>
                    <td className="text-right px-3 py-1.5 font-medium">{r.sampleSize}</td>
                    <td className="text-right px-3 py-1.5 text-muted-foreground">
                      {r.meanBias != null ? `${r.meanBias > 0 ? "+" : ""}${r.meanBias.toFixed(2)}°` : "—"}
                    </td>
                    <td className="text-right px-3 py-1.5">{num(r.mae)}°</td>
                    <td className="text-right px-3 py-1.5">{num(r.stdDev)}°</td>
                    <td className="text-center px-3 py-1.5">
                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${TIER_STYLES[r.tier] ?? ""}`}>
                        {TIER_LABELS[r.tier] ?? r.tier}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

const TABS = [
  { id: "differences", label: "1. Strategy Differences" },
  { id: "loss-audit", label: "2. V1 Loss Audit" },
  { id: "long-shot", label: "3. Long-shot Analysis" },
  { id: "settlement", label: "4. Settlement Check" },
  { id: "v2-readiness", label: "5. V2 Readiness" },
];

export default function StrategyAuditPage() {
  const [activeTab, setActiveTab] = useState("differences");

  return (
    <div className="space-y-6">
      <PageHeader />

      <div className="rounded-lg border border-border bg-card overflow-hidden">
        <div className="border-b border-border px-1">
          <TabBar tabs={TABS} active={activeTab} onChange={setActiveTab} />
        </div>

        {activeTab === "differences" && <StrategyDifferencesTab />}
        {activeTab === "loss-audit" && <LossAuditTab />}
        {activeTab === "long-shot" && <LongShotTab />}
        {activeTab === "settlement" && <SettlementCheckTab />}
        {activeTab === "v2-readiness" && <V2ReadinessTab />}
      </div>
    </div>
  );
}
