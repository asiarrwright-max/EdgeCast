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
  useGetStrategyComparison,
  useGetStrategyAgreement,
  useRunVerification,
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
  const [analyticsStratVer, setAnalyticsStratVer] = useState("");
  const [includeFlagged, setIncludeFlagged] = useState(true);
  const [feePct, setFeePct] = useState(0);
  const [slippagePct, setSlippagePct] = useState(0);
  const [spreadAdj, setSpreadAdj] = useState(0);
  const [adjMode, setAdjMode] = useState(false);
  const [calibStratVer, setCalibStratVer] = useState("");
  const [metricsStratVer, setMetricsStratVer] = useState("");

  // Settings edit state
  const [editSettings, setEditSettings] = useState(false);
  const [settingsForm, setSettingsForm] = useState<Record<string, any>>({});

  // Settlement state
  const [settling, setSettling] = useState(false);
  const [settleResult, setSettleResult] = useState<any>(null);

  // V2 comparison state
  const [showComparison, setShowComparison] = useState(false);
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

  // Build analytics params
  const analyticsParams: Record<string, any> = {
    include_flagged: includeFlagged,
    fee_pct: adjMode ? feePct : 0,
    slippage_pct: adjMode ? slippagePct : 0,
    spread_adj: adjMode ? spreadAdj : 0,
  };
  if (analyticsStratVer) analyticsParams.strategy_version = analyticsStratVer;
  if (calibStratVer) analyticsParams.strategy_version_calib = calibStratVer; // won't be used, separate

  const { data: listData } = useListPaperTrades(listParams);
  const { data: metrics, refetch: refetchMetrics } = useGetPaperTradeMetrics(
    metricsStratVer ? { strategy_version: metricsStratVer } : {}
  );
  const { data: settings } = useGetPaperTradeSettings();
  const { data: analytics } = useGetPaperTradeAnalytics(analyticsParams);
  const { data: calibration } = useGetPaperTradeCalibration(
    calibStratVer ? { strategy_version: calibStratVer } : {}
  );
  const { data: comparison } = useGetStrategyComparison();
  const { data: agreement } = useGetStrategyAgreement();
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
        <div className="flex items-center gap-3">
          <select
            value={metricsStratVer}
            onChange={(e) => setMetricsStratVer(e.target.value)}
            className="text-sm border border-gray-300 rounded px-2 py-1.5 text-gray-700"
          >
            <option value="">All versions</option>
            <option value="v1.0">v1.0</option>
            <option value="v2.0">v2.0</option>
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

      {/* Sample size progress */}
      <SampleBar settled={settledCount} />

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
        <MetricCard label="Total Staked" value={money(metrics?.totalStaked)} />
        <MetricCard
          label="ROI"
          value={pct(metrics?.roi == null ? null : metrics.roi / 100, 2)}
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

      {/* V2 Strategy Comparison */}
      <Section title="Strategy v1 vs v2 Comparison">
        <div className="px-4 py-3 space-y-4">
          <div className="flex items-center gap-3 text-sm">
            <p className="text-xs text-gray-500 flex-1">
              v2 uses learned σ (from historical forecast errors), bias correction, and conservative
              calibration adjustments. Initially falls back to v1 fixed σ until enough verified data
              accumulates.
            </p>
            <button
              onClick={handleRunVerification}
              disabled={verifying}
              className="text-xs bg-indigo-600 text-white px-3 py-1.5 rounded hover:bg-indigo-700 disabled:opacity-50 transition-colors"
            >
              {verifying ? "Running…" : "Run Verification"}
            </button>
          </div>

          {verifyResult && (
            <div
              className={`rounded border px-3 py-2 text-xs ${
                verifyResult.error
                  ? "border-red-300 bg-red-50 text-red-800"
                  : "border-indigo-200 bg-indigo-50 text-indigo-800"
              }`}
            >
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

          {/* Agreement summary */}
          {agreement && (
            <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
              {[
                { label: "Both trade", value: agreement.bothTrade },
                { label: "Only v1", value: agreement.onlyV1 },
                { label: "Only v2", value: agreement.onlyV2 },
                { label: "Same side", value: agreement.sameSides },
                { label: "Diff side", value: agreement.differentSides },
                { label: "Prob div >10pp", value: agreement.probDivergenceGt10pp },
              ].map(({ label, value }) => (
                <div key={label} className="bg-gray-50 rounded border border-gray-200 px-3 py-2 text-center">
                  <p className="text-xs text-gray-500">{label}</p>
                  <p className="text-lg font-semibold text-gray-800">{value}</p>
                </div>
              ))}
            </div>
          )}

          {/* Side-by-side metrics */}
          {comparison && (
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 text-xs text-gray-500 uppercase">
                    <th className="text-left px-4 py-2">Metric</th>
                    <th className="text-right px-4 py-2">v1.0</th>
                    <th className="text-right px-4 py-2">v2.0</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    { label: "Total trades", v1: (comparison.v1 as any).totalCount, v2: (comparison.v2 as any).totalCount },
                    { label: "Open", v1: (comparison.v1 as any).openCount, v2: (comparison.v2 as any).openCount },
                    { label: "Settled", v1: (comparison.v1 as any).settledCount, v2: (comparison.v2 as any).settledCount },
                    { label: "Wins", v1: (comparison.v1 as any).wins, v2: (comparison.v2 as any).wins },
                  ].map(({ label, v1, v2 }) => (
                    <tr key={label} className="border-t border-gray-100">
                      <td className="px-4 py-2 text-gray-700">{label}</td>
                      <td className="text-right px-4 py-2 text-gray-600">{v1 ?? "—"}</td>
                      <td className="text-right px-4 py-2 text-gray-600">{v2 ?? "—"}</td>
                    </tr>
                  ))}
                  {[
                    {
                      label: "Win rate",
                      v1: pct((comparison.v1 as any).winRate),
                      v2: pct((comparison.v2 as any).winRate),
                      v1Raw: (comparison.v1 as any).winRate,
                      v2Raw: (comparison.v2 as any).winRate,
                    },
                  ].map(({ label, v1, v2, v1Raw, v2Raw }) => (
                    <tr key={label} className="border-t border-gray-100">
                      <td className="px-4 py-2 text-gray-700">{label}</td>
                      <td className={`text-right px-4 py-2 font-medium ${v1Raw == null ? "text-gray-400" : v1Raw >= 0.55 ? "text-green-600" : v1Raw < 0.45 ? "text-red-600" : "text-amber-600"}`}>{v1}</td>
                      <td className={`text-right px-4 py-2 font-medium ${v2Raw == null ? "text-gray-400" : v2Raw >= 0.55 ? "text-green-600" : v2Raw < 0.45 ? "text-red-600" : "text-amber-600"}`}>{v2}</td>
                    </tr>
                  ))}
                  {[
                    { label: "Net P/L", v1: money((comparison.v1 as any).netProfitLoss), v2: money((comparison.v2 as any).netProfitLoss) },
                    {
                      label: "ROI",
                      v1: pct((comparison.v1 as any).roi == null ? null : (comparison.v1 as any).roi / 100, 2),
                      v2: pct((comparison.v2 as any).roi == null ? null : (comparison.v2 as any).roi / 100, 2),
                    },
                    {
                      label: "Brier score",
                      v1: fmt((comparison.v1 as any).calibration?.brierScore),
                      v2: fmt((comparison.v2 as any).calibration?.brierScore),
                    },
                  ].map(({ label, v1, v2 }) => (
                    <tr key={label} className="border-t border-gray-100">
                      <td className="px-4 py-2 text-gray-700">{label}</td>
                      <td className="text-right px-4 py-2 text-gray-600">{v1}</td>
                      <td className="text-right px-4 py-2 text-gray-600">{v2}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Agreement samples */}
          {agreement && agreement.samples.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
                Agreement samples (up to 20 shared markets)
              </p>
              <div className="overflow-x-auto">
                <table className="min-w-full text-xs">
                  <thead>
                    <tr className="bg-gray-50 text-xs text-gray-400 uppercase">
                      <th className="text-left px-3 py-1.5">Ticker</th>
                      <th className="text-center px-3 py-1.5">v1 Dir</th>
                      <th className="text-center px-3 py-1.5">v2 Dir</th>
                      <th className="text-right px-3 py-1.5">v1 Prob</th>
                      <th className="text-right px-3 py-1.5">v2 Prob</th>
                      <th className="text-right px-3 py-1.5">Δ</th>
                      <th className="text-center px-3 py-1.5">Agree</th>
                      <th className="text-left px-3 py-1.5">σ source</th>
                    </tr>
                  </thead>
                  <tbody>
                    {agreement.samples.map((s: any) => (
                      <tr key={s.ticker} className="border-t border-gray-100 hover:bg-gray-50">
                        <td className="px-3 py-1.5 max-w-[140px] truncate text-gray-600 font-mono">{s.ticker}</td>
                        <td className="text-center px-3 py-1.5">
                          <span className={`text-xs font-medium px-1 py-0.5 rounded ${s.v1Direction === "YES" ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}`}>
                            {s.v1Direction}
                          </span>
                        </td>
                        <td className="text-center px-3 py-1.5">
                          <span className={`text-xs font-medium px-1 py-0.5 rounded ${s.v2Direction === "YES" ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}`}>
                            {s.v2Direction}
                          </span>
                        </td>
                        <td className="text-right px-3 py-1.5 text-gray-600">{pct(s.v1EcProb)}</td>
                        <td className="text-right px-3 py-1.5 text-gray-600">{pct(s.v2EcProb)}</td>
                        <td className={`text-right px-3 py-1.5 font-medium ${s.probDiff > 0.10 ? "text-amber-600" : "text-gray-400"}`}>
                          {(s.probDiff * 100).toFixed(1)}pp
                        </td>
                        <td className="text-center px-3 py-1.5">
                          {s.agree ? (
                            <span className="text-green-600 font-medium">✓</span>
                          ) : (
                            <span className="text-red-600 font-medium">✗</span>
                          )}
                        </td>
                        <td className="px-3 py-1.5 text-gray-400 text-xs">{s.v2FallbackLevel ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <p className="text-xs text-gray-400">
            v2 calibration and bias corrections are conservative — only applied when n ≥ 5 (σ/bias)
            or n ≥ 30 (calibration). Until then, v2 uses the v1 fixed σ table as a fallback.
            Run "Verification" to fetch actual observed temperatures and rebuild error statistics.
          </p>
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
          {/* Controls */}
          <div className="flex flex-wrap gap-3 items-end text-sm">
            <label className="flex items-center gap-2 text-gray-600">
              Strategy version:
              <select
                value={analyticsStratVer}
                onChange={(e) => setAnalyticsStratVer(e.target.value)}
                className="border border-gray-300 rounded px-2 py-1 text-sm"
              >
                <option value="">All</option>
                <option value="v1.0">v1.0</option>
                <option value="v2.0">v2.0</option>
              </select>
            </label>
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
          <div className="flex items-center gap-3 text-sm">
            <label className="flex items-center gap-2 text-gray-600">
              Strategy version:
              <select
                value={calibStratVer}
                onChange={(e) => setCalibStratVer(e.target.value)}
                className="border border-gray-300 rounded px-2 py-1 text-sm"
              >
                <option value="">All</option>
                <option value="v1.0">v1.0</option>
                <option value="v2.0">v2.0</option>
              </select>
            </label>
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
