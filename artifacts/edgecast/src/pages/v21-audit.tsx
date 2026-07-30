/**
 * V2.1 Audit & Analytics
 * ======================
 * Before/after retrospective, station coverage, OKC July 28 evidence,
 * and consensus guard backtest.  All sections are read-only.
 */
import { ReactNode, useState } from "react";
import {
  useGetV21Retrospective,
  useGetStationCoverage,
  useGetOkcExplanation,
  useGetConsensusBacktest,
  type RetroTrade,
  type RetroSummary,
  type StationEntry,
} from "@workspace/api-client-react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, ScatterChart, Scatter,
} from "recharts";

// ---------------------------------------------------------------------------
// Shared helpers / components
// ---------------------------------------------------------------------------

const pct = (v: number | null | undefined, d = 1) =>
  v == null ? "—" : `${(v * 100).toFixed(d)}%`;
const pp = (v: number | null | undefined, d = 1) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(d)}pp`;
const money = (v: number | null | undefined) =>
  v == null ? "—" : `${v >= 0 ? "+" : "-"}$${Math.abs(v).toFixed(2)}`;
const roiFmt = (v: number | null | undefined) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
const num = (v: number | null | undefined, d = 2) =>
  v == null ? "—" : v.toFixed(d);

function SectionCard({
  title,
  subtitle,
  children,
  badge,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  badge?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2 flex-wrap">
          <h2 className="text-sm font-semibold">{title}</h2>
          {badge}
        </div>
        {subtitle && <p className="text-xs text-muted-foreground mt-0.5">{subtitle}</p>}
      </div>
      {children}
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

function Disclaimer({ text }: { text: string }) {
  return (
    <div className="rounded border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-xs text-amber-400">
      <span className="font-semibold uppercase tracking-wide">Retrospective only — </span>
      {text}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 1. Before/After Retrospective Table
// ---------------------------------------------------------------------------

function RetroSummaryCards({ s }: { s: RetroSummary }) {
  const cards = [
    { label: "Trades in sample", value: s.totalInSample },
    { label: "V2.1 would take", value: s.v21WouldTake, tone: "neutral" as const },
    { label: "V2.1 would skip", value: s.v21WouldSkip, tone: "neutral" as const },
    { label: "V2.0 losses avoided", value: s.lossesAvoided, tone: "green" as const },
    { label: "V2.0 wins skipped", value: s.winsSkipped, tone: "red" as const },
    { label: "Hyp. V2.1 win rate", value: pct(s.v21WinRate), tone: (s.v21WinRate ?? 0) >= 0.55 ? "green" as const : "neutral" as const },
    { label: "Hyp. V2.1 ROI", value: roiFmt(s.v21Roi), tone: (s.v21Roi ?? 0) >= 0 ? "green" as const : "red" as const },
    { label: "V2.0 ROI (same period)", value: roiFmt(s.v20Roi), tone: (s.v20Roi ?? 0) >= 0 ? "green" as const : "red" as const },
    { label: "Hyp. V2.1 Brier", value: s.v21BrierScore != null ? s.v21BrierScore.toFixed(4) : "—" },
    { label: "Avg σ V2.0 → V2.1", value: `${num(s.avgSigmaV20)}→${num(s.avgSigmaV21)}°F` },
    { label: "Avg edge V2.0 → V2.1", value: `${pp(s.avgEdgeV20Pp)}→${pp(s.avgEdgeV21Pp)}` },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 p-4">
      {cards.map((c) => (
        <div key={c.label} className="bg-muted/40 rounded-lg border border-border p-3">
          <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">{c.label}</p>
          <p className={`mt-1 text-lg font-bold ${
            c.tone === "green" ? "text-emerald-400" : c.tone === "red" ? "text-red-400" : ""
          }`}>
            {typeof c.value === "number" ? c.value : c.value}
          </p>
        </div>
      ))}
    </div>
  );
}

function RetroTable({ trades }: { trades: RetroTrade[] }) {
  const [expanded, setExpanded] = useState<number | null>(null);
  if (!trades.length) return <Empty msg="No V2.0 settled trades to compare." />;

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-xs">
        <thead>
          <tr className="bg-muted/40 text-muted-foreground uppercase text-[10px]">
            <th className="text-left px-3 py-2">Market / City</th>
            <th className="text-right px-3 py-2">Direction</th>
            <th className="text-right px-3 py-2">σ V2.0</th>
            <th className="text-right px-3 py-2">σ V2.1</th>
            <th className="text-right px-3 py-2">Prob V2.0</th>
            <th className="text-right px-3 py-2">Prob V2.1</th>
            <th className="text-right px-3 py-2">Edge V2.0</th>
            <th className="text-right px-3 py-2">Edge V2.1</th>
            <th className="text-center px-3 py-2">V2.1 takes?</th>
            <th className="text-center px-3 py-2">Station ✓</th>
            <th className="text-center px-3 py-2">Outcome</th>
            <th className="text-right px-3 py-2">P/L actual</th>
            <th className="text-right px-3 py-2">Hyp. V2.1</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => (
            <>
              <tr
                key={t.tradeId}
                className={`border-t border-border hover:bg-muted/20 cursor-pointer ${
                  t.outcome === "WIN" ? "bg-emerald-950/10" : t.outcome === "LOSS" ? "bg-red-950/10" : ""
                }`}
                onClick={() => setExpanded(expanded === t.tradeId ? null : t.tradeId)}
              >
                <td className="px-3 py-2">
                  <p className="font-mono text-[10px] text-muted-foreground">{t.marketTicker.slice(-20)}</p>
                  <p className="font-medium">{t.city}</p>
                </td>
                <td className="px-3 py-2 text-right">
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                    t.direction === "YES" ? "bg-emerald-500/20 text-emerald-400" : "bg-blue-500/20 text-blue-400"
                  }`}>{t.direction}</span>
                </td>
                <td className="px-3 py-2 text-right text-muted-foreground">{t.sigmaV20 != null ? `${t.sigmaV20}°F` : "—"}</td>
                <td className={`px-3 py-2 text-right font-medium ${t.sigmaChanged ? "text-amber-400" : ""}`}>
                  {t.sigmaV21}°F
                </td>
                <td className="px-3 py-2 text-right">{pct(t.ecSideProbV20)}</td>
                <td className="px-3 py-2 text-right">{t.ecYesProbV21 != null ? pct(t.direction === "YES" ? t.ecYesProbV21 : 1 - t.ecYesProbV21) : "—"}</td>
                <td className="px-3 py-2 text-right">{t.edgePpV20 != null ? `${t.edgePpV20 >= 0 ? "+" : ""}${t.edgePpV20.toFixed(1)}pp` : "—"}</td>
                <td className="px-3 py-2 text-right">{t.newEdgePp != null ? `${t.newEdgePp >= 0 ? "+" : ""}${t.newEdgePp.toFixed(1)}pp` : "—"}</td>
                <td className="px-3 py-2 text-center">
                  {t.v21WouldTrade ? (
                    <span className="text-emerald-400 font-bold">✓</span>
                  ) : (
                    <span className="text-red-400 font-bold" title={t.v21SkipReason ?? undefined}>✗</span>
                  )}
                </td>
                <td className="px-3 py-2 text-center">
                  {t.stationVerifiedForV21 ? (
                    <span className="text-emerald-400">✓</span>
                  ) : (
                    <span className="text-amber-400">—</span>
                  )}
                </td>
                <td className="px-3 py-2 text-center">
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                    t.outcome === "WIN" ? "bg-emerald-500/20 text-emerald-400" :
                    t.outcome === "LOSS" ? "bg-red-500/20 text-red-400" : "text-muted-foreground"
                  }`}>{t.outcome ?? "—"}</span>
                </td>
                <td className={`px-3 py-2 text-right font-medium ${t.plActual >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                  {money(t.plActual)}
                </td>
                <td className={`px-3 py-2 text-right font-medium ${t.plHypotheticalV21 >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                  {money(t.plHypotheticalV21)}
                </td>
              </tr>
              {expanded === t.tradeId && (
                <tr className="border-t border-border bg-muted/30">
                  <td colSpan={13} className="px-4 py-3">
                    <div className="text-xs space-y-1">
                      <p className="font-medium text-foreground/70">Station: {t.settlementStation}</p>
                      <p className="text-muted-foreground">Settlement date: {t.settlementDate}</p>
                      <p className="text-muted-foreground">Fallback level (V2.0): {t.fallbackLevelV20 ?? "—"}</p>
                      <p className="text-muted-foreground">Confidence (V2.0): {t.confidenceLabelV20 ?? "—"}</p>
                      {t.v21SkipReason && (
                        <p className="text-amber-400">V2.1 skip reason: {t.v21SkipReason}</p>
                      )}
                      {t.sigmaChanged && (
                        <p className="text-amber-300">
                          σ change: {t.sigmaV20}°F → {t.sigmaV21}°F
                          {(t.sigmaV20 ?? 0) < 3.5 ? " (V2.0 sigma was below floor)" : ""}
                        </p>
                      )}
                    </div>
                  </td>
                </tr>
              )}
            </>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 2. Station Coverage Table
// ---------------------------------------------------------------------------

function StationRow({ s }: { s: StationEntry }) {
  return (
    <tr className="border-t border-border hover:bg-muted/20">
      <td className="px-3 py-2.5">
        <p className="font-medium text-sm">{s.city}</p>
      </td>
      <td className="px-3 py-2.5 text-xs">{s.stationName}</td>
      <td className="px-3 py-2.5 text-xs font-mono">{s.ghcndStationId}</td>
      <td className="px-3 py-2.5 text-xs text-muted-foreground">
        {s.lat.toFixed(4)}°, {s.lon.toFixed(4)}°
      </td>
      <td className="px-3 py-2.5 text-xs text-muted-foreground">{s.timezone}</td>
      <td className="px-3 py-2.5 text-center">
        {s.verified ? (
          <span className="inline-block text-[10px] font-semibold px-2 py-0.5 rounded border bg-emerald-500/10 text-emerald-400 border-emerald-500/30">
            Verified
          </span>
        ) : (
          <span className="inline-block text-[10px] font-semibold px-2 py-0.5 rounded border bg-amber-500/10 text-amber-400 border-amber-500/30">
            Unverified
          </span>
        )}
      </td>
      <td className="px-3 py-2.5 text-center">
        {s.v21TradingEnabled ? (
          <span className="text-emerald-400 text-xs font-medium">✓ Active</span>
        ) : (
          <span className="text-muted-foreground text-xs">Excluded</span>
        )}
      </td>
      <td className="px-3 py-2.5 text-right text-xs">{s.v21TradeCount}</td>
      <td className="px-3 py-2.5 text-right text-xs text-muted-foreground">{s.observationCount}</td>
      <td className="px-3 py-2.5 text-xs text-muted-foreground max-w-xs">
        <span className="line-clamp-2">{s.notes ?? "—"}</span>
      </td>
    </tr>
  );
}

function StationCoverageSection() {
  const { data, isLoading } = useGetStationCoverage();
  const [showUnverified, setShowUnverified] = useState(true);

  if (isLoading) return <Loading />;
  if (!data) return <Empty />;

  const shown = showUnverified
    ? data.stations
    : data.stations.filter((s) => s.verified);

  return (
    <SectionCard
      title="Settlement Station Coverage"
      subtitle={`${data.verifiedCount} verified · ${data.unverifiedCount} unverified · V2.1 trades only placed for verified cities`}
    >
      <div className="px-4 py-3 border-b border-border flex items-center gap-3">
        <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer">
          <input
            type="checkbox"
            checked={showUnverified}
            onChange={(e) => setShowUnverified(e.target.checked)}
            className="rounded"
          />
          Show unverified cities
        </label>
        <p className="text-xs text-muted-foreground ml-auto">
          To verify a city: download its Kalshi contract PDF and find "NWS Daily Climate Report for …"
        </p>
      </div>

      <div className="overflow-x-auto">
        <table className="min-w-full text-xs">
          <thead>
            <tr className="bg-muted/40 text-muted-foreground uppercase text-[10px]">
              <th className="text-left px-3 py-2">City</th>
              <th className="text-left px-3 py-2">Station</th>
              <th className="text-left px-3 py-2">GHCND ID</th>
              <th className="text-left px-3 py-2">Coordinates</th>
              <th className="text-left px-3 py-2">Timezone</th>
              <th className="text-center px-3 py-2">Status</th>
              <th className="text-center px-3 py-2">V2.1</th>
              <th className="text-right px-3 py-2">V2.1 Trades</th>
              <th className="text-right px-3 py-2">Obs.</th>
              <th className="text-left px-3 py-2">Notes</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((s) => <StationRow key={s.city} s={s} />)}
          </tbody>
        </table>
      </div>

      <div className="px-4 py-2 border-t border-border">
        <p className="text-xs text-muted-foreground">{data.note}</p>
      </div>
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// 3. OKC July 28 Evidence Table
// ---------------------------------------------------------------------------

function OkcExplanationSection() {
  const { data, isLoading } = useGetOkcExplanation();
  if (isLoading) return <Loading />;
  if (!data) return <Empty />;

  const { event, forecastSources, rootCauseAssessment, dataQualityNote } = data;

  const verdictLabel =
    rootCauseAssessment.verdict === "combination"
      ? "Combination"
      : rootCauseAssessment.verdict === "model_error"
      ? "Primarily Model Error"
      : rootCauseAssessment.verdict === "location_error"
      ? "Primarily Location Error"
      : "Inconclusive";

  return (
    <SectionCard
      title="Oklahoma City — July 28 Miss: Evidence Table"
      subtitle="Root-cause analysis for the 9°F forecast error that caused a catastrophic V2.0 loss"
    >
      {/* Event summary */}
      <div className="px-4 py-3 border-b border-border bg-red-950/10">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          <div>
            <p className="text-muted-foreground">Market</p>
            <p className="font-mono font-medium">{event.marketTicker}</p>
          </div>
          <div>
            <p className="text-muted-foreground">Contract</p>
            <p>{event.contractDescription}</p>
          </div>
          <div>
            <p className="text-muted-foreground">Actual Official Low</p>
            <p className="text-red-400 font-bold">{event.actualOfficialHigh}°F</p>
          </div>
          <div>
            <p className="text-muted-foreground">Note</p>
            <p className="text-muted-foreground">{event.actualNote}</p>
          </div>
        </div>
      </div>

      {/* Forecast source table */}
      <div className="overflow-x-auto">
        <table className="min-w-full text-xs">
          <thead>
            <tr className="bg-muted/40 text-muted-foreground uppercase text-[10px]">
              <th className="text-left px-3 py-2">Forecast Source</th>
              <th className="text-left px-3 py-2">Timestamp</th>
              <th className="text-right px-3 py-2">Forecasted Value</th>
              <th className="text-left px-3 py-2">Coordinates</th>
              <th className="text-right px-3 py-2">Actual</th>
              <th className="text-right px-3 py-2">Abs. Error</th>
              <th className="text-left px-3 py-2">Notes</th>
            </tr>
          </thead>
          <tbody>
            {forecastSources.map((fs, i) => (
              <tr key={i} className="border-t border-border hover:bg-muted/20">
                <td className="px-3 py-2.5 font-medium">{fs.source}</td>
                <td className="px-3 py-2.5 text-muted-foreground font-mono text-[10px]">
                  {fs.forecastTimestamp.slice(0, 16).replace("T", " ")}Z
                </td>
                <td className="px-3 py-2.5 text-right">
                  {fs.forecastedValue != null ? `${fs.forecastedValue}°F` : "—"}
                </td>
                <td className="px-3 py-2.5 text-muted-foreground text-[10px]">{fs.forecastCoordinates}</td>
                <td className="px-3 py-2.5 text-right">{fs.actualOfficialValue != null ? `${fs.actualOfficialValue}°F` : "—"}</td>
                <td className="px-3 py-2.5 text-right">
                  {fs.absoluteError != null ? (
                    <span className="text-red-400 font-bold">{fs.absoluteError.toFixed(1)}°F</span>
                  ) : "—"}
                </td>
                <td className="px-3 py-2.5 text-muted-foreground max-w-xs">
                  <span className="line-clamp-3">{fs.notes}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Root cause */}
      <div className="px-4 py-4 border-t border-border space-y-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-foreground/70 uppercase tracking-wide">Verdict:</span>
          <span className="inline-block text-xs font-semibold px-2.5 py-1 rounded border bg-amber-500/10 text-amber-400 border-amber-500/30">
            {verdictLabel}
          </span>
        </div>
        <div className="grid md:grid-cols-2 gap-3">
          <div className="space-y-2">
            <div className="rounded border border-red-500/20 bg-red-500/5 px-3 py-2">
              <p className="text-[10px] font-semibold text-red-400 uppercase tracking-wide mb-1">Primary cause (≈7–8°F)</p>
              <p className="text-xs text-foreground/80">{rootCauseAssessment.primaryCause}</p>
            </div>
            <div className="rounded border border-amber-500/20 bg-amber-500/5 px-3 py-2">
              <p className="text-[10px] font-semibold text-amber-400 uppercase tracking-wide mb-1">Secondary cause (≈1–2°F)</p>
              <p className="text-xs text-foreground/80">{rootCauseAssessment.secondaryCause}</p>
            </div>
          </div>
          <div className="space-y-2">
            <div className="rounded border border-red-500/30 bg-red-500/5 px-3 py-2">
              <p className="text-[10px] font-semibold text-red-400 uppercase tracking-wide mb-1">Critical compounding failure</p>
              <p className="text-xs text-foreground/80">{rootCauseAssessment.sigmaFailure}</p>
            </div>
            <div className="rounded border border-emerald-500/20 bg-emerald-500/5 px-3 py-2">
              <p className="text-[10px] font-semibold text-emerald-400 uppercase tracking-wide mb-1">V2.1 fixes applied</p>
              <ul className="text-xs text-foreground/80 space-y-0.5">
                {rootCauseAssessment.fixes.map((f, i) => (
                  <li key={i} className="flex gap-1.5">
                    <span className="text-emerald-400 shrink-0">•</span>
                    <span>{f}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
        <p className="text-xs text-muted-foreground border-t border-border pt-2">{dataQualityNote}</p>
      </div>
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// 4. Consensus Guard Backtest
// ---------------------------------------------------------------------------

function ConsensusGuardSection() {
  const { data, isLoading } = useGetConsensusBacktest();
  if (isLoading) return <Loading />;
  if (!data) return <Empty />;

  return (
    <SectionCard
      title="Consensus Guard — Retrospective Backtest"
      subtitle={`Experimental filter: skip trades where market consensus against our side ≥ ${(data.threshold * 100).toFixed(0)}%`}
      badge={
        <span className="inline-block text-[10px] font-semibold px-2 py-0.5 rounded border bg-muted/40 text-muted-foreground border-border">
          {data.guardStatus}
        </span>
      }
    >
      <div className="px-4 py-3 border-b border-border">
        <div className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-400">
          <span className="font-semibold">Experimental — </span>
          {data.experimentalWarning}
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 p-4">
        {[
          { label: "Total settled (V2.0)", value: data.total_settled },
          { label: "Guard would block", value: data.guard_would_block },
          { label: "Guard would allow", value: data.guard_would_allow },
          { label: "Losses avoided", value: data.losses_avoided_by_guard, tone: "green" as const },
          { label: "Wins skipped", value: data.wins_avoided_by_guard, tone: "red" as const },
          { label: "ROI without guard", value: roiFmt(data.roi_without_guard_pct), tone: data.roi_without_guard_pct >= 0 ? "green" as const : "red" as const },
          { label: "ROI with guard", value: roiFmt(data.roi_with_guard_pct), tone: data.roi_with_guard_pct >= 0 ? "green" as const : "red" as const },
          { label: "ROI delta (guard − no guard)", value: pp(data.roi_delta_pp), tone: data.roi_delta_pp >= 0 ? "green" as const : "red" as const },
        ].map((c) => (
          <div key={c.label} className="bg-muted/40 rounded-lg border border-border p-3">
            <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">{c.label}</p>
            <p className={`mt-1 text-xl font-bold ${
              c.tone === "green" ? "text-emerald-400" : c.tone === "red" ? "text-red-400" : ""
            }`}>
              {typeof c.value === "number" ? c.value : c.value}
            </p>
          </div>
        ))}
      </div>

      <div className="px-4 py-2 border-t border-border">
        <p className="text-xs text-muted-foreground">{data.note}</p>
      </div>
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Retrospective section (full)
// ---------------------------------------------------------------------------

function RetrospectiveSection() {
  const { data, isLoading, error } = useGetV21Retrospective();

  return (
    <SectionCard
      title="Before vs After: V2.0 → V2.1 Retrospective Comparison"
      subtitle="What would V2.1 have decided on the same trades? Click any row to expand details."
    >
      {!!error && (
        <div className="px-4 py-3 text-sm text-destructive">
          Failed to load retrospective. {String(error)}
        </div>
      )}
      {!isLoading && data && (
        <div className="px-4 py-3 border-b border-border">
          <Disclaimer text={data.disclaimer} />
        </div>
      )}
      {isLoading ? (
        <Loading />
      ) : data?.trades.length ? (
        <>
          <RetroSummaryCards s={data.summary} />
          <div className="border-t border-border">
            <RetroTable trades={data.trades} />
          </div>
          <div className="px-4 py-2 border-t border-border">
            <p className="text-xs text-muted-foreground">
              Sample: {data.sampleSize} most recent settled V2.0 trades with available snapshot data.
              Hypothetical P/L assumes the same $10 stake per trade as V2.0.
            </p>
          </div>
        </>
      ) : (
        <Empty msg="No settled V2.0 trades found for comparison." />
      )}
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function V21AuditPage() {
  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight">V2.1 Audit &amp; Analysis</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Before/after retrospective, station coverage, root-cause evidence, and experimental filters.
          All views are read-only. Paper trading only — no real trades are placed.
        </p>
      </div>

      {/* Retrospective */}
      <RetrospectiveSection />

      {/* Station coverage */}
      <StationCoverageSection />

      {/* OKC explanation */}
      <OkcExplanationSection />

      {/* Consensus guard */}
      <ConsensusGuardSection />
    </div>
  );
}
