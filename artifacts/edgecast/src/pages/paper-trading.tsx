import { useState } from "react";
import { Link } from "wouter";
import {
  useListPaperTrades,
  useGetPaperTradeMetrics,
  useGetPaperTradeSettings,
  useUpdatePaperTradeSettings,
  useGetPaperTradeAnalytics,
  useGetPaperTradeCalibration,
  useSettleNow,
  useRunVerification,
  useGetPaperTradeSegmentSummary,
  type SegmentSummaryRow,
} from "@workspace/api-client-react";

// ── small helpers ─────────────────────────────────────────────────────────────
const pct = (n: number | null | undefined, dec = 1) =>
  n == null ? "—" : `${(n * 100).toFixed(dec)}%`;
const pp = (n: number | null | undefined, dec = 1) =>
  n == null ? "—" : `${n.toFixed(dec)}pp`;
const money = (n: number | null | undefined) =>
  n == null ? "—" : `$${n.toFixed(2)}`;
const fmt = (n: number | null | undefined, dec = 3) =>
  n == null ? "—" : n.toFixed(dec);

const PROGRESS_MILESTONES = [100, 300, 500];

function SampleBar({ settled }: { settled: number }) {
  const next = PROGRESS_MILESTONES.find((m) => m > settled) ?? 500;
  const pctProgress = Math.min(settled / next, 1);
  return (
    <div className="space-y-1">
      <p className="text-xs text-gray-500">
        {settled} settled · progress to {next} trades
      </p>
      <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
        <div
          className="h-full bg-blue-500 rounded-full transition-all"
          style={{ width: `${pctProgress * 100}%` }}
        />
      </div>
      {settled < 30 && (
        <p className="text-xs text-amber-600">
          ⚠ Fewer than 30 settled trades — results are preliminary.
        </p>
      )}
    </div>
  );
}

function MetricCard({
  label,
  value,
  sub,
  highlight,
}: {
  label: string;
  value: string;
  sub?: string;
  highlight?: "green" | "red" | "amber";
}) {
  const colors = {
    green: "text-green-600",
    red: "text-red-600",
    amber: "text-amber-600",
  };
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">{label}</p>
      <p className={`mt-1 text-2xl font-semibold ${highlight ? colors[highlight] : "text-gray-900"}`}>
        {value}
      </p>
      {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
    </div>
  );
}

function Section({
  title,
  children,
  defaultOpen = false,
}: {
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-semibold text-gray-800 hover:bg-gray-50 transition-colors"
      >
        <span>{title}</span>
        <span className="text-gray-400">{open ? "▲" : "▼"}</span>
      </button>
      {open && <div className="border-t border-gray-100">{children}</div>}
    </div>
  );
}

function BreakdownTable({ rows, adjMode }: { rows: any[]; adjMode: boolean }) {
  if (!rows || rows.length === 0)
    return <p className="text-sm text-gray-500 px-4 py-3">No settled trades in this breakdown.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="bg-gray-50 text-xs text-gray-500 uppercase">
            <th className="text-left px-4 py-2">Label</th>
            <th className="text-right px-4 py-2">Settled</th>
            <th className="text-right px-4 py-2">Win Rate</th>
            <th className="text-right px-4 py-2">Stake</th>
            <th className="text-right px-4 py-2">{adjMode ? "Adj P/L" : "P/L"}</th>
            <th className="text-right px-4 py-2">{adjMode ? "Adj ROI" : "ROI"}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label} className="border-t border-gray-100 hover:bg-gray-50">
              <td className="px-4 py-2 font-medium text-gray-800">{r.label}</td>
              <td className="text-right px-4 py-2 text-gray-600">{r.settledCount}</td>
              <td className="text-right px-4 py-2">{pct(r.winRate)}</td>
              <td className="text-right px-4 py-2 text-gray-600">{money(r.totalStake)}</td>
              <td className={`text-right px-4 py-2 font-medium ${
                (adjMode ? r.adjProfitLoss ?? r.profitLoss : r.profitLoss) >= 0
                  ? "text-green-600"
                  : "text-red-600"
              }`}>
                {money(adjMode ? (r.adjProfitLoss ?? r.profitLoss) : r.profitLoss)}
              </td>
              <td className={`text-right px-4 py-2 ${
                (adjMode ? r.adjRoi ?? r.roi : r.roi) != null &&
                (adjMode ? r.adjRoi ?? r.roi : r.roi) >= 0
                  ? "text-green-600"
                  : "text-red-600"
              }`}>
                {pct((adjMode ? r.adjRoi ?? r.roi : r.roi) == null ? null : (adjMode ? r.adjRoi ?? r.roi : r.roi) / 100)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── main page ─────────────────────────────────────────────────────────────────

export default function PaperTradingPage() {
  // Filter state
  const [statusF, setStatusF] = useState("");
  const [directionF, setDirectionF] = useState("");
  const [cityF, setCityF] = useState("");
  const [contractTypeF, setContractTypeF] = useState("");
  const [dateFromF, setDateFromF] = useState("");
  const [dateToF, setDateToF] = useState("");
  const [stratVerF, setStratVerF] = useState("");
  const [edgeBucketF, setEdgeBucketF] = useState("");
  const [priceBucketF, setPriceBucketF] = useState("");
  const [isFlaggedF, setIsFlaggedF] = useState<string>("");
  const [outcomeF, setOutcomeF] = useState("");

  // Analytics/calibration state
  const [includeFlagged, setIncludeFlagged] = useState(true);
  const [feePct, setFeePct] = useState(0);
  const [slippagePct, setSlippagePct] = useState(0);
  const [spreadAdj, setSpreadAdj] = useState(0);
  const [adjMode, setAdjMode] = useState(false);
  const [segment, setSegment] = useState("current_exp");

  // Settings edit state
  const [editSettings, setEditSettings] = useState(false);
  const [settingsForm, setSettingsForm] = useState<Record<string, any>>({});

  // Settlement state
  const [settling, setSettling] = useState(false);
  const [settleResult, setSettleResult] = useState<any>(null);

  // Verification state
  const [verifying, setVerifying] = useState(false);
  const [verifyResult, setVerifyResult] = useState<any>(null);

  // Build query params for list
  const listParams: Record<string, any> = {};
  if (statusF) listParams.status = statusF;
  if (directionF) listParams.direction = directionF;
  if (cityF) listParams.city = cityF;
  if (contractTypeF) listParams.contract_type = contractTypeF;
  if (dateFromF) listParams.date_from = dateFromF;
  if (dateToF) listParams.date_to = dateToF;
  if (stratVerF) listParams.strategy_version = stratVerF;
  if (edgeBucketF) listParams.edge_bucket = edgeBucketF;
  if (priceBucketF) listParams.price_bucket = priceBucketF;
  if (isFlaggedF !== "") listParams.is_flagged = isFlaggedF === "true";
  if (outcomeF) listParams.outcome = outcomeF;

  // Build analytics params — segment drives the filter, not a standalone version picker
  const analyticsParams: Record<string, any> = {
    segment,
    include_flagged: includeFlagged,
    fee_pct: adjMode ? feePct : 0,
    slippage_pct: adjMode ? slippagePct : 0,
    spread_adj: adjMode ? spreadAdj : 0,
  };

  const { data: listData } = useListPaperTrades(listParams);
  const { data: metrics, refetch: refetchMetrics } = useGetPaperTradeMetrics({ segment });
  const { data: segmentSummary } = useGetPaperTradeSegmentSummary();
  const { data: settings } = useGetPaperTradeSettings();
  const { data: analytics } = useGetPaperTradeAnalytics(analyticsParams);
  const { data: calibration } = useGetPaperTradeCalibration({ segment });
  const updateSettings = useUpdatePaperTradeSettings();
  const settleNowMutation = useSettleNow();
  const runVerificationMutation = useRunVerification();

  const trades = listData?.trades ?? [];
  const total = listData?.total ?? 0;

  // CSV export URL
  const buildExportUrl = () => {
    const params = new URLSearchParams();
    Object.entries(listParams).forEach(([k, v]) => params.set(k, String(v)));
    const base = import.meta.env.BASE_URL?.replace(/\/$/, "") ?? "";
    return `${base}/api/paper-trades/export.csv?${params.toString()}`;
  };

  const handleSettleNow = async () => {
    setSettling(true);
    setSettleResult(null);
    try {
      const r = await settleNowMutation.mutateAsync();
      setSettleResult(r);
      refetchMetrics();
    } catch (e: any) {
      setSettleResult({ error: e?.message ?? "Unknown error" });
    } finally {
      setSettling(false);
    }
  };

  const handleRunVerification = async () => {
    setVerifying(true);
    setVerifyResult(null);
    try {
      const r = await runVerificationMutation.mutateAsync();
      setVerifyResult(r);
    } catch (e: any) {
      setVerifyResult({ error: e?.message ?? "Unknown error" });
    } finally {
      setVerifying(false);
    }
  };

  const handleSaveSettings = async () => {
    try {
      await updateSettings.mutateAsync({ data: settingsForm });
      setEditSettings(false);
    } catch (e) {
      console.error("Failed to save settings", e);
    }
  };

  const settledCount = metrics?.settledCount ?? 0;

  return (
    <div className="max-w-7xl mx-auto px-4 py-8 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Paper Trading</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Strategy validation — simulated positions, real market prices
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap justify-end">
          {/* Segment selector */}
          <select
            data-segment-select
            value={segment}
            onChange={(e) => setSegment(e.target.value)}
            className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 bg-white text-gray-700"
            style={{ maxWidth: "280px" }}
          >
            <option value="current_exp">Current Experiment — V2.1 + V2.2 + V3</option>
            <option value="v21_only">V2.1 only</option>
            <option value="v22_only">V2.2 only</option>
            <option value="v3_challenger">V3 only</option>
            <option value="paired">Strictly Paired Head-to-Head</option>
            <option value="legacy">Legacy V1/V2</option>
            <option value="all">All Versions Unfiltered ⚠</option>
          </select>
          <button
            onClick={handleSettleNow}
            disabled={settling}
            className="text-sm bg-blue-600 text-white px-3 py-1.5 rounded hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {settling ? "Running…" : "Run Settlement Check"}
          </button>
        </div>
      </div>

      {settleResult && (
        <div
          className={`rounded-lg border px-4 py-3 text-sm ${
            settleResult.error
              ? "border-red-300 bg-red-50 text-red-800"
              : "border-green-300 bg-green-50 text-green-800"
          }`}
        >
          {settleResult.error ? (
            <>Settlement failed: {settleResult.error}</>
          ) : (
            <>
              Settlement complete — checked {settleResult.checked}, settled{" "}
              {settleResult.settled}, voided {settleResult.voided}, errors{" "}
              {settleResult.errors}, still open {settleResult.stillOpen}.
            </>
          )}
        </div>
      )}

      {/* Closing today banner */}
      {(metrics?.closingTodayTotal ?? 0) > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 flex items-start gap-3">
          <span className="text-lg leading-none mt-0.5" aria-hidden>⏳</span>
          <div className="space-y-0.5">
            <p className="text-sm font-semibold text-amber-800">
              {metrics!.closingTodayTotal} position{metrics!.closingTodayTotal !== 1 ? "s" : ""} closing today
              {(metrics!.closingTodayUniqueMarkets ?? 0) > 0 && (
                <span className="text-amber-600 font-normal ml-2">
                  across {metrics!.closingTodayUniqueMarkets} unique market{metrics!.closingTodayUniqueMarkets !== 1 ? "s" : ""}
                </span>
              )}
            </p>
            <p className="text-xs text-amber-700">
              {metrics!.pendingSettlementCount! > 0 && (
                <>{metrics!.pendingSettlementCount} awaiting Kalshi results</>
              )}
              {metrics!.pendingSettlementCount! > 0 && metrics!.closingTodayCount! > 0 && " · "}
              {metrics!.closingTodayCount! > 0 && (
                <>{metrics!.closingTodayCount} open position{metrics!.closingTodayCount !== 1 ? "s" : ""} settling today</>
              )}
              {(metrics!.closingTodayUniqueMarkets ?? 0) > 0 && (metrics!.closingTodayTotal ?? 0) > (metrics!.closingTodayUniqueMarkets ?? 0) && (
                <> · {metrics!.closingTodayTotal! - metrics!.closingTodayUniqueMarkets!} extra positions from multi-strategy overlap</>
              )}
              {" — "}includes V2.1, V2.2, and V3
            </p>
          </div>
        </div>
      )}

      {/* Sample size progress */}
      <div className="space-y-1">
        <p className="text-xs text-gray-400">
          Metrics below:{" "}
          <span className="font-medium text-gray-600">
            {segment === "current_exp"   && "Current Experiment — V2.1 + V2.2 + V3 executable"}
            {segment === "v21_only"      && "V2.1 executable only"}
            {segment === "v22_only"      && "V2.2 executable only"}
            {segment === "v3_challenger" && "V3 executable only — open positions (settling in progress)"}
            {segment === "paired"        && "Strictly Paired Head-to-Head — V2.1 + V2.2 + V3 on shared opportunities"}
            {segment === "legacy"        && "Legacy V1/V2 baseline — pre-station/sigma corrections, not comparable to current"}
            {segment === "all"           && "All Versions Unfiltered ⚠ — legacy + current contaminated"}
          </span>
        </p>
        <SampleBar settled={settledCount} />
      </div>

      {/* Contamination warning — only shown for "all" */}
      {segment === "all" && (
        <div className="rounded-lg border border-orange-300 bg-orange-50 px-4 py-4 text-sm">
          <p className="font-semibold text-orange-800">⚠ Contaminated view — not for experiment evaluation</p>
          <p className="text-sm text-orange-700 mt-1">
            These results combine legacy strategies, current strategies, and repeated exposure to the
            same Kalshi markets. They should not be used to evaluate the current EdgeCast experiment.{" "}
            <button
              className="underline font-medium text-orange-800"
              onClick={() => setSegment("current_exp")}
            >
              Switch to Current Experiment
            </button>
          </p>
        </div>
      )}

      {/* Metric cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        <MetricCard label="Open" value={String(metrics?.openCount ?? "—")} />
        <MetricCard label="Settled" value={String(metrics?.settledCount ?? "—")} />
        <MetricCard label="Wins" value={String(metrics?.wins ?? "—")} />
        <MetricCard
          label="Win Rate"
          value={pct(metrics?.winRate)}
          highlight={
            metrics?.winRate == null
              ? undefined
              : metrics.winRate >= 0.55
              ? "green"
              : metrics.winRate < 0.45
              ? "red"
              : "amber"
          }
        />
        <MetricCard
          label="Net P/L"
          value={money(metrics?.netProfitLoss)}
          highlight={
            metrics?.netProfitLoss == null
              ? undefined
              : metrics.netProfitLoss > 0
              ? "green"
              : metrics.netProfitLoss < 0
              ? "red"
              : undefined
          }
        />
        <MetricCard
          label="Settled Stake"
          value={money(metrics?.settledStake ?? metrics?.totalStaked)}
          sub="ROI denominator"
        />
        <MetricCard
          label="Open Capital"
          value={money(metrics?.openCapital ?? 0)}
          sub={`Total exposure: ${money((metrics?.settledStake ?? 0) + (metrics?.openCapital ?? 0))}`}
        />
        <MetricCard
          label="ROI"
          value={pct(metrics?.roi == null ? null : metrics.roi / 100, 2)}
          sub="on settled stake only"
          highlight={
            metrics?.roi == null
              ? undefined
              : metrics.roi > 0
              ? "green"
              : metrics.roi < 0
              ? "red"
              : undefined
          }
        />
        <MetricCard label="Avg Edge" value={pp(metrics?.avgEntryEdge)} />
        <MetricCard label="Avg Entry Price" value={pct(metrics?.avgEntryPrice)} />
        <MetricCard label="Avg Win Edge" value={pp(metrics?.avgWinEdge)} sub={`Loss: ${pp(metrics?.avgLossEdge)}`} />
      </div>

      {/* Strategy Breakdown — full per-version × executability audit table */}
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-gray-800">Strategy Breakdown</h3>
            <p className="text-xs text-gray-500 mt-0.5">
              All versions × executability. Official headline metrics use settled executable rows only (✓).
            </p>
          </div>
        </div>
        {segmentSummary?.rows && segmentSummary.rows.length > 0 ? (
          <>
            <div className="overflow-x-auto">
              <table className="min-w-full text-xs">
                <thead>
                  <tr className="bg-gray-50 text-xs text-gray-400 uppercase">
                    <th className="text-left px-4 py-2">Version</th>
                    <th className="text-left px-3 py-2">Group</th>
                    <th className="text-center px-3 py-2">Exec?</th>
                    <th className="text-right px-3 py-2">Open</th>
                    <th className="text-right px-3 py-2">Settled</th>
                    <th className="text-right px-3 py-2">Wins</th>
                    <th className="text-right px-3 py-2">Win Rate</th>
                    <th className="text-right px-3 py-2">Stake</th>
                    <th className="text-right px-3 py-2">Net P/L</th>
                    <th className="text-right px-3 py-2">ROI</th>
                    <th className="text-right px-3 py-2">Avg Edge</th>
                    <th className="text-right px-3 py-2">Brier</th>
                  </tr>
                </thead>
                <tbody>
                  {(segmentSummary.rows as SegmentSummaryRow[]).map((row) => {
                    const isOfficial    = row.group === "current_exec" || row.group === "v3";
                    const isV3          = row.group === "v3";
                    const isLegacy      = row.group === "legacy";
                    const isResearch    = row.group === "current_nonexec";
                    const groupLabel =
                      row.group === "current_exec"    ? "current experiment" :
                      row.group === "current_nonexec" ? "research signal"    :
                      row.group === "v3"              ? "V3 challenger"       :
                      row.group === "legacy"          ? "legacy baseline"    :
                      (row.group as string).replace(/_/g, " ");
                    return (
                      <tr
                        key={`${row.version}-${row.isExecutable}`}
                        className={`border-t border-gray-100 ${
                          isV3       ? "bg-cyan-50/40"   :
                          isOfficial ? "bg-blue-50/60"   :
                          isLegacy   ? "bg-amber-50/40"  :
                          isResearch ? "bg-gray-50"      : ""
                        }`}
                      >
                        <td className="px-4 py-2 font-medium text-gray-800">
                          {row.version}
                          {isV3       && <span className="ml-1.5 text-cyan-600 font-normal">✓ official (V3)</span>}
                          {!isV3 && isOfficial && <span className="ml-1.5 text-blue-600 font-normal">✓ official</span>}
                          {isLegacy   && <span className="ml-1.5 text-amber-600 font-normal">legacy</span>}
                          {isResearch && <span className="ml-1.5 text-gray-400 font-normal">signal</span>}
                        </td>
                        <td className="px-3 py-2 text-gray-400">{groupLabel}</td>
                        <td className="text-center px-3 py-2">
                          {row.isExecutable === true  ? <span className="text-green-600 font-bold">✓</span> :
                           row.isExecutable === false ? <span className="text-red-400">✗</span> :
                           <span className="text-gray-300">—</span>}
                        </td>
                        <td className="text-right px-3 py-2 text-gray-600">{row.open}</td>
                        <td className="text-right px-3 py-2 text-gray-600">{row.settled}</td>
                        <td className="text-right px-3 py-2 text-gray-600">{row.wins}</td>
                        <td className={`text-right px-3 py-2 font-medium ${
                          row.winRate == null    ? "text-gray-300" :
                          row.winRate >= 0.55   ? "text-green-600" :
                          row.winRate < 0.45    ? "text-red-600"   : "text-amber-600"
                        }`}>
                          {row.winRate != null ? pct(row.winRate) : "—"}
                        </td>
                        <td className="text-right px-3 py-2 text-gray-600">
                          {row.settled > 0 ? money(row.settledStake) : "—"}
                        </td>
                        <td className={`text-right px-3 py-2 font-medium ${
                          row.settled === 0   ? "text-gray-300" :
                          row.settledPl > 0   ? "text-green-600" :
                          row.settledPl < 0   ? "text-red-600"   : "text-gray-400"
                        }`}>
                          {row.settled > 0 ? money(row.settledPl) : "—"}
                        </td>
                        <td className={`text-right px-3 py-2 ${
                          row.settledRoi == null ? "text-gray-300" :
                          row.settledRoi > 0    ? "text-green-600" : "text-red-600"
                        }`}>
                          {row.settledRoi != null ? `${row.settledRoi.toFixed(1)}%` : "—"}
                        </td>
                        <td className="text-right px-3 py-2 text-gray-600">
                          {row.avgEdge != null ? `${row.avgEdge.toFixed(1)}pp` : "—"}
                        </td>
                        <td className="text-right px-3 py-2 text-gray-600">
                          {row.brierScore != null ? row.brierScore.toFixed(4) : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="px-4 py-2.5 border-t border-gray-100 flex flex-wrap gap-x-5 gap-y-1 text-xs text-gray-400">
              <span><span className="inline-block w-2.5 h-2.5 rounded bg-blue-100 mr-1" />✓ official — settled executable (V2.1 + V2.2) — current performance benchmark</span>
              <span><span className="inline-block w-2.5 h-2.5 rounded bg-amber-100 mr-1" />legacy — pre-station/sigma corrections — not comparable to current experiment</span>
              <span>Research signals: non-executable; recorded for calibration, not real positions</span>
              <span>Fees: paper trades incur no real Kalshi charges — not recorded</span>
            </div>
          </>
        ) : (
          <p className="text-sm text-gray-500 px-4 py-3">Loading breakdown…</p>
        )}
      </div>

      {/* Strategy Comparison: V2.1 vs V2.2 vs V3 */}
      <Section title="Strategy Comparison — V2.1 vs V2.2 vs V3">
        <div className="px-4 py-3 space-y-4">
          {segment !== "current_exp" && (
            <p className="text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded px-3 py-2">
              ⚠ Select <button className="underline font-medium" onClick={() => setSegment("current_exp")}>Current Experiment</button> to see the full three-strategy side-by-side comparison.
            </p>
          )}

          {segment === "current_exp" && (metrics as any)?.reconciliation ? (
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 text-xs text-gray-500 uppercase">
                    <th className="text-left px-4 py-2">Metric</th>
                    <th className="text-right px-4 py-2 text-blue-700">V2.1</th>
                    <th className="text-right px-4 py-2 text-indigo-700">V2.2</th>
                    <th className="text-right px-4 py-2 text-cyan-700">V3</th>
                    <th className="text-right px-4 py-2 text-gray-600">Combined</th>
                  </tr>
                </thead>
                <tbody>
                  {(["Open", "Settled", "Wins"] as const).map((label) => {
                    const key = label.toLowerCase() as "open" | "settled" | "wins";
                    return (
                      <tr key={label} className="border-t border-gray-100">
                        <td className="px-4 py-2 text-gray-700">{label}</td>
                        {(["v21", "v22", "v3", "combined"] as const).map((v) => (
                          <td key={v} className="text-right px-4 py-2 text-gray-600">
                            {(metrics as any).reconciliation[v]?.[key] ?? "—"}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                  <tr className="border-t border-gray-100">
                    <td className="px-4 py-2 text-gray-700">Win Rate</td>
                    {(["v21", "v22", "v3", "combined"] as const).map((v) => {
                      const val = (metrics as any).reconciliation[v]?.winRate;
                      return (
                        <td key={v} className={`text-right px-4 py-2 font-medium ${
                          val == null ? "text-gray-400" :
                          val >= 0.55 ? "text-green-600" :
                          val < 0.45  ? "text-red-600"   : "text-amber-600"
                        }`}>
                          {pct(val)}
                        </td>
                      );
                    })}
                  </tr>
                  <tr className="border-t border-gray-100">
                    <td className="px-4 py-2 text-gray-700">Net P/L</td>
                    {(["v21", "v22", "v3", "combined"] as const).map((v) => (
                      <td key={v} className="text-right px-4 py-2 text-gray-600">
                        {money((metrics as any).reconciliation[v]?.netPl)}
                      </td>
                    ))}
                  </tr>
                  <tr className="border-t border-gray-100">
                    <td className="px-4 py-2 text-gray-700">ROI</td>
                    {(["v21", "v22", "v3", "combined"] as const).map((v) => {
                      const roi = (metrics as any).reconciliation[v]?.roi;
                      return (
                        <td key={v} className={`text-right px-4 py-2 ${
                          roi == null ? "text-gray-400" :
                          roi > 0 ? "text-green-600" : "text-red-600"
                        }`}>
                          {roi == null ? "—" : pct(roi / 100, 2)}
                        </td>
                      );
                    })}
                  </tr>
                </tbody>
              </table>
            </div>
          ) : segment === "current_exp" ? (
            <p className="text-sm text-gray-500">Loading comparison…</p>
          ) : null}

          <div className="flex items-start justify-between gap-4 flex-wrap">
            <p className="text-xs text-gray-400 flex-1">
              V3 settled metrics appear once V3 positions begin to settle. V2.1 and V2.2 share
              the same Kalshi markets — use "Strictly Paired Head-to-Head" to compare them on
              identical opportunities only. ROI is computed on settled stake.
            </p>
            <div className="flex flex-col items-end gap-1">
              <button
                onClick={handleRunVerification}
                disabled={verifying}
                className="text-xs bg-indigo-600 text-white px-3 py-1.5 rounded hover:bg-indigo-700 disabled:opacity-50 transition-colors whitespace-nowrap"
              >
                {verifying ? "Running…" : "Run Verification"}
              </button>
              <p className="text-xs text-gray-400">Rebuilds V2 σ / bias error stats</p>
            </div>
          </div>

          {verifyResult && (
            <div className={`rounded border px-3 py-2 text-xs ${
              verifyResult.error
                ? "border-red-300 bg-red-50 text-red-800"
                : "border-indigo-200 bg-indigo-50 text-indigo-800"
            }`}>
              {verifyResult.error ? (
                <>Verification failed: {verifyResult.error}</>
              ) : (
                <>
                  Verifications: {verifyResult.verifications?.created ?? 0} created,{" "}
                  {verifyResult.verifications?.updated ?? 0} updated,{" "}
                  {verifyResult.verifications?.skipped ?? 0} skipped · Error stats:{" "}
                  {verifyResult.errorStats?.groups_computed ?? 0} groups recomputed
                </>
              )}
            </div>
          )}
        </div>
      </Section>

      {/* Settings */}
      <Section title="Settings">
        <div className="px-4 py-3 space-y-3">
          {settings && !editSettings && (
            <div className="flex flex-wrap gap-4 text-sm text-gray-700">
              <span>
                Enabled:{" "}
                <span className={settings.enabled ? "text-green-600 font-medium" : "text-red-600 font-medium"}>
                  {settings.enabled ? "Yes" : "No"}
                </span>
              </span>
              <span>Min edge: <strong>{settings.min_edge_pct}pp</strong></span>
              <span>Min confidence: <strong>{settings.min_confidence}</strong></span>
              <span>Stake: <strong>${settings.stake}</strong></span>
              <span>Strategy version: <strong>{settings.strategy_version}</strong></span>
              <button
                onClick={() => {
                  setSettingsForm({
                    enabled: settings.enabled,
                    min_edge_pct: settings.min_edge_pct,
                    min_confidence: settings.min_confidence,
                    stake: settings.stake,
                    strategy_version: settings.strategy_version,
                  });
                  setEditSettings(true);
                }}
                className="ml-auto text-blue-600 hover:underline text-xs"
              >
                Edit
              </button>
            </div>
          )}
          {editSettings && (
            <div className="space-y-3">
              <div className="flex flex-wrap gap-4 text-sm">
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={!!settingsForm.enabled}
                    onChange={(e) => setSettingsForm({ ...settingsForm, enabled: e.target.checked })}
                  />
                  Enabled
                </label>
                <label className="flex items-center gap-2">
                  Min edge (pp):
                  <input
                    type="number"
                    value={settingsForm.min_edge_pct ?? ""}
                    onChange={(e) => setSettingsForm({ ...settingsForm, min_edge_pct: parseFloat(e.target.value) })}
                    className="border border-gray-300 rounded px-2 py-1 w-20 text-sm"
                  />
                </label>
                <label className="flex items-center gap-2">
                  Stake ($):
                  <input
                    type="number"
                    value={settingsForm.stake ?? ""}
                    onChange={(e) => setSettingsForm({ ...settingsForm, stake: parseFloat(e.target.value) })}
                    className="border border-gray-300 rounded px-2 py-1 w-20 text-sm"
                  />
                </label>
                <label className="flex items-center gap-2">
                  Strategy version:
                  <input
                    type="text"
                    value={settingsForm.strategy_version ?? ""}
                    onChange={(e) => setSettingsForm({ ...settingsForm, strategy_version: e.target.value })}
                    className="border border-gray-300 rounded px-2 py-1 w-24 text-sm"
                  />
                </label>
              </div>
              <p className="text-xs text-amber-600">
                ⚠ Changing strategy version causes future trades to be recorded under the new version.
                All existing trades are permanently preserved under their original version.
              </p>
              <div className="flex gap-2">
                <button
                  onClick={handleSaveSettings}
                  className="text-sm bg-blue-600 text-white px-3 py-1.5 rounded hover:bg-blue-700"
                >
                  Save
                </button>
                <button
                  onClick={() => setEditSettings(false)}
                  className="text-sm text-gray-600 hover:underline px-2"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      </Section>

      {/* Analytics */}
      <Section title="Performance Analytics" defaultOpen>
        <div className="px-4 py-3 space-y-4">
          {/* Segment context + contamination warning */}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-gray-500">
              Showing:{" "}
              <span className="font-medium text-gray-700">
                {segment === "current_exp"   && "Current Experiment — V2.1 + V2.2 executable (analytics from V2.1+V2.2 settled trades)"}
                {segment === "v21_only"      && "V2.1 executable only"}
                {segment === "v22_only"      && "V2.2 executable only"}
                {segment === "v3_challenger" && "V3 executable only — no settled trades yet; breakdowns empty until settlement"}
                {segment === "paired"        && "Strictly Paired Head-to-Head — V2.1 + V2.2 executable on shared opportunities"}
                {segment === "legacy"        && "Legacy V1/V2 — pre-station/sigma corrections, not comparable to current"}
                {segment === "all"           && "All Versions Unfiltered ⚠"}
              </span>
              {" · "}
              <button
                className="underline text-blue-500 hover:text-blue-700"
                onClick={() => document.querySelector<HTMLSelectElement>("[data-segment-select]")?.focus()}
              >
                change segment
              </button>
            </p>
          </div>

          {segment === "all" && (
            <div className="rounded-lg border border-orange-300 bg-orange-50 px-4 py-3 text-sm">
              <p className="font-semibold text-orange-800">⚠ Contaminated analytics — not for experiment evaluation</p>
              <p className="text-xs text-orange-700 mt-1">
                These results combine legacy strategies, current strategies, and repeated exposure to the
                same Kalshi markets. They should not be used to evaluate the current EdgeCast experiment.{" "}
                <button className="underline font-medium text-orange-800" onClick={() => setSegment("current_exp")}>
                  Switch to Current Experiment
                </button>
              </p>
            </div>
          )}

          {/* Controls */}
          <div className="flex flex-wrap gap-3 items-end text-sm">
            <label className="flex items-center gap-2 text-gray-600">
              <input
                type="checkbox"
                checked={includeFlagged}
                onChange={(e) => setIncludeFlagged(e.target.checked)}
              />
              Include flagged trades
            </label>
            <label className="flex items-center gap-2 text-gray-600">
              <input type="checkbox" checked={adjMode} onChange={(e) => setAdjMode(e.target.checked)} />
              Realistic adjustments
            </label>
            {adjMode && (
              <>
                <label className="flex items-center gap-1 text-xs text-gray-500">
                  Fee%:
                  <input
                    type="number"
                    step={0.1}
                    min={0}
                    value={feePct}
                    onChange={(e) => setFeePct(parseFloat(e.target.value) || 0)}
                    className="border border-gray-300 rounded px-1.5 py-1 w-16 text-xs"
                  />
                </label>
                <label className="flex items-center gap-1 text-xs text-gray-500">
                  Slippage%:
                  <input
                    type="number"
                    step={0.1}
                    min={0}
                    value={slippagePct}
                    onChange={(e) => setSlippagePct(parseFloat(e.target.value) || 0)}
                    className="border border-gray-300 rounded px-1.5 py-1 w-16 text-xs"
                  />
                </label>
                <label className="flex items-center gap-1 text-xs text-gray-500">
                  Spread%:
                  <input
                    type="number"
                    step={0.1}
                    min={0}
                    value={spreadAdj}
                    onChange={(e) => setSpreadAdj(parseFloat(e.target.value) || 0)}
                    className="border border-gray-300 rounded px-1.5 py-1 w-16 text-xs"
                  />
                </label>
              </>
            )}
          </div>
          {adjMode && (
            <p className="text-xs text-gray-400">
              Realistic adjustments are simplified model approximations (fee + slippage + spread as % of stake per trade).
              These are not guaranteed real-world performance figures.
            </p>
          )}

          {/* Breakdown tables */}
          {analytics ? (
            <div className="space-y-4">
              {[
                { key: "byDirection", label: "By Direction" },
                { key: "byEdgeBucket", label: "By Edge Bucket" },
                { key: "byPriceBucket", label: "By Price Bucket" },
                { key: "byLeadTime", label: "By Lead Time" },
                { key: "byCity", label: "By City" },
                { key: "byContractType", label: "By Contract Type" },
              ].map(({ key, label }) => (
                <div key={key}>
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide px-1 mb-1">
                    {label}
                  </p>
                  <BreakdownTable rows={(analytics as any)[key] ?? []} adjMode={adjMode} />
                </div>
              ))}

              {/* Cumulative P/L chart (simple text series) */}
              {analytics.cumulativePl.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide px-1 mb-2">
                    Cumulative P/L ({analytics.cumulativePl.length} trades)
                  </p>
                  <div className="bg-gray-50 rounded p-3 text-xs text-gray-600 font-mono overflow-x-auto">
                    {analytics.cumulativePl.slice(-10).map((d: any, i: number) => (
                      <div key={i} className="flex justify-between gap-4">
                        <span>{d.date?.slice(0, 10)}</span>
                        <span
                          className={
                            d.cumulativePl >= 0 ? "text-green-600" : "text-red-600"
                          }
                        >
                          {money(d.cumulativePl)}
                          {adjMode && d.adjCumulativePl != null && (
                            <> → {money(d.adjCumulativePl)} adj</>
                          )}
                        </span>
                      </div>
                    ))}
                    {analytics.cumulativePl.length > 10 && (
                      <p className="text-gray-400 mt-1">
                        (showing last 10 of {analytics.cumulativePl.length})
                      </p>
                    )}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <p className="text-sm text-gray-500">Loading analytics…</p>
          )}
        </div>
      </Section>

      {/* Calibration */}
      <Section title="Probability Calibration">
        <div className="px-4 py-3 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
            <p className="text-xs text-gray-500">
              Showing:{" "}
              <span className="font-medium text-gray-700">
                {segment === "current_exp"   && "Current Experiment — V2.1 + V2.2 executable"}
                {segment === "v21_only"      && "V2.1 executable only"}
                {segment === "v22_only"      && "V2.2 executable only"}
                {segment === "v3_challenger" && "V3 executable only — no settled trades yet"}
                {segment === "paired"        && "Strictly Paired Head-to-Head — V2.1 + V2.2 executable"}
                {segment === "legacy"        && "Legacy V1/V2"}
                {segment === "all"           && "All Versions ⚠ — not for experiment evaluation"}
              </span>
            </p>
            {calibration?.brierScore != null && (
              <span className="text-xs text-gray-500">
                Brier score:{" "}
                <span className="font-semibold text-gray-800">
                  {calibration.brierScore.toFixed(4)}
                </span>{" "}
                ({calibration.totalSettled} settled)
              </span>
            )}
          </div>

          {segment === "all" && (
            <div className="rounded-lg border border-orange-300 bg-orange-50 px-4 py-2 text-xs">
              <span className="font-semibold text-orange-800">⚠ Contaminated calibration — not for experiment evaluation: </span>
              <span className="text-orange-700">
                These results combine legacy strategies, current strategies, and repeated exposure to the
                same Kalshi markets.{" "}
              </span>
              <button className="underline font-medium text-orange-800" onClick={() => setSegment("current_exp")}>
                Switch to Current Experiment
              </button>
            </div>
          )}

          {calibration ? (
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 text-xs text-gray-500 uppercase">
                    <th className="text-left px-4 py-2">EC Prob Bucket</th>
                    <th className="text-right px-4 py-2">Trades</th>
                    <th className="text-right px-4 py-2">Avg EC Prob</th>
                    <th className="text-right px-4 py-2">Actual YES Rate</th>
                    <th className="text-right px-4 py-2">Diff</th>
                  </tr>
                </thead>
                <tbody>
                  {calibration.buckets.map((b: any) => (
                    <tr key={b.bucket} className="border-t border-gray-100 hover:bg-gray-50">
                      <td className="px-4 py-2 font-medium text-gray-800">{b.bucket}</td>
                      <td className="text-right px-4 py-2 text-gray-600">{b.count}</td>
                      <td className="text-right px-4 py-2">{b.avgEcProb != null ? pct(b.avgEcProb) : "—"}</td>
                      <td className="text-right px-4 py-2">{b.actualYesRate != null ? pct(b.actualYesRate) : "—"}</td>
                      <td
                        className={`text-right px-4 py-2 font-medium ${
                          b.calibrationDiff == null
                            ? "text-gray-400"
                            : Math.abs(b.calibrationDiff) < 0.05
                            ? "text-green-600"
                            : "text-amber-600"
                        }`}
                      >
                        {b.calibrationDiff != null
                          ? `${b.calibrationDiff > 0 ? "+" : ""}${(b.calibrationDiff * 100).toFixed(1)}pp`
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="text-xs text-gray-400 px-4 py-2">
                Brier score closer to 0 = better calibration (perfect = 0, always-wrong = 1).
              </p>
            </div>
          ) : (
            <p className="text-sm text-gray-500">Loading calibration…</p>
          )}
        </div>
      </Section>

      {/* V2 Research View (excluded trades) */}
      <Section title="v2 Research View — Excluded Trades">
        <div className="px-4 py-3 space-y-3">
          <p className="text-xs text-gray-500">
            Markets that v2 declined to trade due to quality rules (1-cent price, zero volume, or
            no liquidity). These entries help assess what v2 is filtering out and why.
          </p>
          {(() => {
            const excludedTrades = trades.filter((t: any) => t.status === "V2_EXCLUDED");
            if (excludedTrades.length === 0) {
              return (
                <p className="text-sm text-gray-400 py-2">
                  No excluded v2 trades yet — set the strategy filter below to "v2.0" to see
                  V2_EXCLUDED entries in the main trade list.
                </p>
              );
            }
            return (
              <div className="overflow-x-auto">
                <table className="min-w-full text-xs">
                  <thead>
                    <tr className="bg-gray-50 text-xs text-gray-400 uppercase">
                      <th className="text-left px-3 py-2">Market</th>
                      <th className="text-left px-3 py-2">City</th>
                      <th className="text-center px-3 py-2">Dir</th>
                      <th className="text-right px-3 py-2">Price</th>
                      <th className="text-right px-3 py-2">EC Prob</th>
                      <th className="text-left px-3 py-2">Exclusion Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {excludedTrades.slice(0, 50).map((t: any) => (
                      <tr key={t.id} className="border-t border-gray-100 hover:bg-gray-50">
                        <td className="px-3 py-2 max-w-[160px] truncate text-gray-600 font-mono">{t.marketTicker}</td>
                        <td className="px-3 py-2 text-gray-600">{t.city ?? "—"}</td>
                        <td className="text-center px-3 py-2">
                          {t.direction ? (
                            <span className={`text-xs font-medium px-1 py-0.5 rounded ${t.direction === "YES" ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}`}>
                              {t.direction}
                            </span>
                          ) : "—"}
                        </td>
                        <td className="text-right px-3 py-2 text-gray-500">{pct(t.sideMarketPrice)}</td>
                        <td className="text-right px-3 py-2 text-gray-500">{pct(t.ecYesProbability)}</td>
                        <td className="px-3 py-2">
                          {(t.qualityFlags ?? []).map((f: string) => (
                            <span key={f} title={t.qualityFlagDescriptions?.[f] ?? f} className="inline-block text-xs bg-gray-100 text-gray-500 border border-gray-200 rounded px-1.5 py-0.5 mr-1">
                              {f}
                            </span>
                          ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          })()}
        </div>
      </Section>

      {/* Trade List */}
      <Section title={`Trades (${total})`} defaultOpen>
        <div className="px-4 py-3 space-y-3">
          {/* Filter bar */}
          <div className="flex flex-wrap gap-2 items-end text-xs">
            <select
              value={statusF}
              onChange={(e) => setStatusF(e.target.value)}
              className="border border-gray-300 rounded px-2 py-1"
            >
              <option value="">All statuses</option>
              <option value="OPEN">Open</option>
              <option value="SETTLED">Settled</option>
              <option value="VOID">Void</option>
              <option value="ERROR">Error</option>
              <option value="PENDING_SETTLEMENT">Pending Settlement</option>
              <option value="V2_EXCLUDED">v2 Excluded</option>
            </select>
            <select
              value={directionF}
              onChange={(e) => setDirectionF(e.target.value)}
              className="border border-gray-300 rounded px-2 py-1"
            >
              <option value="">All directions</option>
              <option value="YES">YES</option>
              <option value="NO">NO</option>
            </select>
            <select
              value={outcomeF}
              onChange={(e) => setOutcomeF(e.target.value)}
              className="border border-gray-300 rounded px-2 py-1"
            >
              <option value="">All outcomes</option>
              <option value="WIN">WIN</option>
              <option value="LOSS">LOSS</option>
              <option value="VOID">VOID</option>
            </select>
            <select
              value={edgeBucketF}
              onChange={(e) => setEdgeBucketF(e.target.value)}
              className="border border-gray-300 rounded px-2 py-1"
            >
              <option value="">All edge buckets</option>
              <option value="&lt;10pp">&lt;10pp</option>
              <option value="10-20pp">10-20pp</option>
              <option value="20-30pp">20-30pp</option>
              <option value="30-40pp">30-40pp</option>
              <option value="≥40pp">≥40pp</option>
            </select>
            <select
              value={priceBucketF}
              onChange={(e) => setPriceBucketF(e.target.value)}
              className="border border-gray-300 rounded px-2 py-1"
            >
              <option value="">All price buckets</option>
              <option value="1-5¢">1-5¢</option>
              <option value="6-15¢">6-15¢</option>
              <option value="16-30¢">16-30¢</option>
              <option value="31-50¢">31-50¢</option>
              <option value=">50¢">&gt;50¢</option>
            </select>
            <select
              value={isFlaggedF}
              onChange={(e) => setIsFlaggedF(e.target.value)}
              className="border border-gray-300 rounded px-2 py-1"
            >
              <option value="">All (incl. flagged)</option>
              <option value="false">Clean only</option>
              <option value="true">Flagged only</option>
            </select>
            <input
              type="text"
              placeholder="City…"
              value={cityF}
              onChange={(e) => setCityF(e.target.value)}
              className="border border-gray-300 rounded px-2 py-1 w-28"
            />
            <input
              type="text"
              placeholder="Strategy ver…"
              value={stratVerF}
              onChange={(e) => setStratVerF(e.target.value)}
              className="border border-gray-300 rounded px-2 py-1 w-28"
            />
            <input
              type="date"
              value={dateFromF}
              onChange={(e) => setDateFromF(e.target.value)}
              className="border border-gray-300 rounded px-2 py-1 text-xs"
            />
            <input
              type="date"
              value={dateToF}
              onChange={(e) => setDateToF(e.target.value)}
              className="border border-gray-300 rounded px-2 py-1 text-xs"
            />
            <a
              href={buildExportUrl()}
              download
              className="ml-auto text-xs text-blue-600 border border-blue-300 rounded px-2 py-1 hover:bg-blue-50 transition-colors"
            >
              ↓ Export CSV
            </a>
          </div>

          {/* Table */}
          {trades.length === 0 ? (
            <p className="text-sm text-gray-500 py-4 text-center">No trades match filters.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 text-xs text-gray-500 uppercase">
                    <th className="text-left px-3 py-2">ID</th>
                    <th className="text-left px-3 py-2">Market</th>
                    <th className="text-left px-3 py-2">City</th>
                    <th className="text-left px-3 py-2">Dir</th>
                    <th className="text-right px-3 py-2">Edge</th>
                    <th className="text-right px-3 py-2">Price</th>
                    <th className="text-right px-3 py-2">Stake</th>
                    <th className="text-left px-3 py-2">Status</th>
                    <th className="text-left px-3 py-2">Outcome</th>
                    <th className="text-right px-3 py-2">P/L</th>
                    <th className="text-left px-3 py-2">Ver</th>
                    <th className="text-left px-3 py-2">Flags</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t: any) => (
                    <tr key={t.id} className="border-t border-gray-100 hover:bg-gray-50">
                      <td className="px-3 py-2">
                        <Link
                          href={`/paper-trading/${t.id}`}
                          className="text-blue-600 hover:underline font-medium"
                        >
                          #{t.id}
                        </Link>
                      </td>
                      <td className="px-3 py-2 max-w-[160px] truncate text-gray-700 text-xs">
                        {t.marketTicker}
                      </td>
                      <td className="px-3 py-2 text-gray-700">{t.city ?? "—"}</td>
                      <td className="px-3 py-2">
                        <span
                          className={`inline-block text-xs font-medium px-1.5 py-0.5 rounded ${
                            t.direction === "YES"
                              ? "bg-green-100 text-green-700"
                              : "bg-red-100 text-red-700"
                          }`}
                        >
                          {t.direction}
                        </span>
                      </td>
                      <td className="text-right px-3 py-2 text-gray-600">{pp(t.edgePctPoints)}</td>
                      <td className="text-right px-3 py-2 text-gray-600">{pct(t.sideMarketPrice)}</td>
                      <td className="text-right px-3 py-2 text-gray-600">{money(t.stake)}</td>
                      <td className="px-3 py-2">
                        <span
                          className={`text-xs font-medium px-1.5 py-0.5 rounded ${
                            t.status === "SETTLED"
                              ? "bg-blue-100 text-blue-700"
                              : t.status === "OPEN"
                              ? "bg-yellow-100 text-yellow-700"
                              : t.status === "PENDING_SETTLEMENT"
                              ? "bg-purple-100 text-purple-700"
                              : t.status === "VOID"
                              ? "bg-gray-100 text-gray-500"
                              : "bg-red-100 text-red-700"
                          }`}
                        >
                          {t.status}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        {t.outcome ? (
                          <span
                            className={`text-xs font-medium px-1.5 py-0.5 rounded ${
                              t.outcome === "WIN"
                                ? "bg-green-100 text-green-700"
                                : t.outcome === "LOSS"
                                ? "bg-red-100 text-red-700"
                                : "bg-gray-100 text-gray-500"
                            }`}
                          >
                            {t.outcome}
                          </span>
                        ) : (
                          <span className="text-gray-400">—</span>
                        )}
                      </td>
                      <td
                        className={`text-right px-3 py-2 font-medium ${
                          t.profitLoss == null
                            ? "text-gray-400"
                            : t.profitLoss >= 0
                            ? "text-green-600"
                            : "text-red-600"
                        }`}
                      >
                        {money(t.profitLoss)}
                      </td>
                      <td className="px-3 py-2 text-xs text-gray-500">{t.strategyVersion}</td>
                      <td className="px-3 py-2">
                        {t.isFlagged ? (
                          <span
                            title={(t.qualityFlags ?? []).join(", ")}
                            className="text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded px-1.5 py-0.5"
                          >
                            ⚑ {(t.qualityFlags ?? []).length}
                          </span>
                        ) : (
                          <span className="text-gray-300 text-xs">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Section>
    </div>
  );
}
