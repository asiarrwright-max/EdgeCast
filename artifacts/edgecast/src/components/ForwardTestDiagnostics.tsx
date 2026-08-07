/**
 * ForwardTestDiagnostics — calibration health card shown below ForwardTestStatus.
 *
 * Sections:
 *  - Small-sample warning banner (shown until ≥50 settled)
 *  - Key metric pills: settled count, win rate, Brier score, ECE, ROI
 *  - Probability-band calibration table
 *  - By-strategy and by-direction breakdowns
 *  - False-confidence losses (model ≥85%, bet lost)
 *  - Settlement integrity flags (ERA5 vs Kalshi disagreements)
 *
 * READ-ONLY diagnostic view — nothing is written or modified here.
 */
import { useState } from "react";
import {
  useGetForwardTestDiagnostics,
  type FtCalibrationBand,
  type FtGroupRow,
  type FtFalseConfidenceLoss,
  type FtIntegrityFlag,
} from "@workspace/api-client-react";
import {
  AlertTriangle, Activity, ShieldAlert, TrendingDown,
  ChevronDown, ChevronRight, Info,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip, TooltipContent, TooltipProvider, TooltipTrigger,
} from "@/components/ui/tooltip";

// ── Helpers ───────────────────────────────────────────────────────────────────

function pct(n: number | null, d = 1) {
  return n == null ? "—" : `${n.toFixed(d)}%`;
}
function usd(n: number | null) {
  if (n == null) return "—";
  const s = Math.abs(n).toFixed(2);
  return `${n < 0 ? "-" : "+"}$${s}`;
}
function num(n: number | null, d = 3) {
  return n == null ? "—" : n.toFixed(d);
}

function Tip({ text }: { text: string }) {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Info className="h-3 w-3 text-slate-500 hover:text-slate-300 cursor-help inline ml-1 shrink-0" />
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs text-xs">
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function Pill({
  label, value, color = "gray", tip,
}: { label: string; value: string; color?: "gray" | "green" | "red" | "amber" | "blue"; tip?: string }) {
  const colors = {
    gray:  "bg-slate-800/60 border-slate-700/40 text-slate-300",
    green: "bg-emerald-900/30 border-emerald-700/40 text-emerald-300",
    red:   "bg-red-900/30 border-red-700/40 text-red-300",
    amber: "bg-amber-900/30 border-amber-700/40 text-amber-300",
    blue:  "bg-blue-900/30 border-blue-700/40 text-blue-300",
  };
  return (
    <div className={`flex flex-col gap-0.5 px-3 py-2 rounded-lg border text-center ${colors[color]}`}>
      <span className="text-[10px] uppercase tracking-wider font-mono opacity-70 flex items-center justify-center gap-0.5">
        {label}{tip && <Tip text={tip} />}
      </span>
      <span className="text-sm font-bold font-mono">{value}</span>
    </div>
  );
}

// ── Calibration band table ────────────────────────────────────────────────────

function calColor(err: number | null) {
  if (err == null) return "text-slate-500";
  if (Math.abs(err) <= 5) return "text-emerald-400";
  if (Math.abs(err) <= 15) return "text-amber-400";
  return "text-red-400";
}

function CalibrationTable({ bands }: { bands: FtCalibrationBand[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono border-collapse">
        <thead>
          <tr className="border-b border-slate-700/50">
            {["Band", "Bets", "Wins", "Obs. WR", "Pred.", "Cal. Error", "Avg Edge", "P/L"].map(h => (
              <th key={h} className="text-left py-1.5 px-2 text-slate-400 font-semibold uppercase tracking-wide text-[10px]">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {bands.map(b => {
            const empty = b.numBets === 0;
            return (
              <tr key={b.band} className="border-b border-slate-800/50 hover:bg-slate-800/20 transition-colors">
                <td className="py-1.5 px-2 text-slate-300 font-semibold">{b.band}</td>
                <td className="py-1.5 px-2 text-slate-400">{empty ? "—" : b.numBets}</td>
                <td className="py-1.5 px-2 text-slate-400">{empty ? "—" : b.wins}</td>
                <td className="py-1.5 px-2">
                  {empty ? <span className="text-slate-600">—</span>
                    : <span className={b.numBets < 5 ? "text-slate-500 italic" : "text-slate-200"}>
                        {pct(b.observedWinRatePct, 0)}
                        {b.numBets < 5 && <span className="text-[9px] ml-1 opacity-60">n&lt;5</span>}
                      </span>}
                </td>
                <td className="py-1.5 px-2 text-slate-400">{empty ? "—" : pct(b.avgPredictedProbPct, 0)}</td>
                <td className={`py-1.5 px-2 font-semibold ${calColor(b.calibrationErrorPp)}`}>
                  {empty ? "—" : `${(b.calibrationErrorPp ?? 0) >= 0 ? "+" : ""}${pct(b.calibrationErrorPp, 1)}`}
                </td>
                <td className="py-1.5 px-2 text-slate-500">{empty ? "—" : `${b.avgClaimedEdgePp?.toFixed(1)}pp`}</td>
                <td className={`py-1.5 px-2 font-semibold ${empty ? "text-slate-600" : (b.totalPl ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                  {empty ? "—" : usd(b.totalPl)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Group mini table ──────────────────────────────────────────────────────────

function GroupTable({ rows }: { rows: FtGroupRow[] }) {
  return (
    <table className="w-full text-xs font-mono">
      <thead>
        <tr className="border-b border-slate-700/50">
          {["Group", "n", "WR", "Avg Prob", "Cal. Err", "ROI", "Brier"].map(h => (
            <th key={h} className="text-left py-1 px-2 text-slate-500 text-[10px] uppercase tracking-wide">{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map(r => (
          <tr key={r.label} className="border-b border-slate-800/40 hover:bg-slate-800/20">
            <td className="py-1.5 px-2 text-slate-300 font-semibold">{r.label}</td>
            <td className="py-1.5 px-2 text-slate-400">{r.n}</td>
            <td className="py-1.5 px-2 text-slate-200">{pct(r.winRatePct, 1)}</td>
            <td className="py-1.5 px-2 text-slate-400">{pct(r.avgPredictedProbPct, 1)}</td>
            <td className={`py-1.5 px-2 font-semibold ${calColor(r.calibrationErrorPp)}`}>
              {r.calibrationErrorPp != null ? `${r.calibrationErrorPp >= 0 ? "+" : ""}${pct(r.calibrationErrorPp, 1)}` : "—"}
            </td>
            <td className={`py-1.5 px-2 font-semibold ${(r.roiPct ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"}`}>
              {pct(r.roiPct, 1)}
            </td>
            <td className={`py-1.5 px-2 ${(r.brierScore ?? 1) > 0.25 ? "text-amber-400" : "text-emerald-400"}`}>
              {num(r.brierScore, 3)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ── False confidence losses ───────────────────────────────────────────────────

function FalseConfidenceList({ items }: { items: FtFalseConfidenceLoss[] }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggle = (k: string) =>
    setExpanded(s => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n; });

  if (!items.length) {
    return <p className="text-slate-500 text-xs font-mono italic">No false-confidence losses — all ≥85% bets won.</p>;
  }
  return (
    <div className="space-y-2">
      {items.map(t => {
        const key = t.marketTicker;
        const open = expanded.has(key);
        return (
          <div key={key} className="rounded-lg border border-slate-700/40 bg-slate-800/30 overflow-hidden">
            <button
              onClick={() => toggle(key)}
              className="w-full flex items-center justify-between px-3 py-2 text-xs font-mono hover:bg-slate-700/20 transition-colors"
            >
              <div className="flex items-center gap-2 min-w-0">
                <TrendingDown className="h-3 w-3 text-red-400 shrink-0" />
                <span className="text-slate-200 truncate">{t.marketTicker}</span>
                <span className="text-slate-500">{t.city}</span>
                <Badge className="text-[9px] px-1 py-0 bg-red-900/40 text-red-300 border-red-700/40">
                  {t.modelProbabilityPct.toFixed(0)}% → LOSS
                </Badge>
                {t.integrityFlag && (
                  <Badge className="text-[9px] px-1 py-0 bg-amber-900/40 text-amber-300 border-amber-700/40">
                    ⚠ integrity
                  </Badge>
                )}
              </div>
              {open ? <ChevronDown className="h-3 w-3 text-slate-500 shrink-0" /> : <ChevronRight className="h-3 w-3 text-slate-500 shrink-0" />}
            </button>
            {open && (
              <div className="px-3 pb-3 pt-1 space-y-2 text-xs font-mono border-t border-slate-700/30">
                <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-1 text-slate-400">
                  <span><span className="text-slate-500">direction:</span> {t.direction}</span>
                  <span><span className="text-slate-500">strategy:</span> {t.strategyVersion}</span>
                  <span><span className="text-slate-500">model prob:</span> <span className="text-red-300">{t.modelProbabilityPct.toFixed(1)}%</span></span>
                  <span><span className="text-slate-500">entry price:</span> {t.marketEntryPrice.toFixed(2)}</span>
                  <span><span className="text-slate-500">claimed edge:</span> +{t.claimedEdgePp.toFixed(1)}pp</span>
                  <span><span className="text-slate-500">range/threshold:</span> {t.thresholdOrRange || "—"}</span>
                  {t.forecastValueF != null && <span><span className="text-slate-500">model forecast:</span> {t.forecastValueF.toFixed(1)}°F</span>}
                  {t.era5ActualF != null && <span><span className="text-slate-500">ERA5 actual:</span> {t.era5ActualF.toFixed(1)}°F</span>}
                  {t.distanceFromThreshold != null && <span><span className="text-slate-500">dist from threshold:</span> {t.distanceFromThreshold.toFixed(2)}°F</span>}
                  <span><span className="text-slate-500">σ used:</span> {t.sigmaUsed?.toFixed(1) ?? "—"}°F</span>
                  <span><span className="text-slate-500">category:</span> <span className="text-amber-300">{t.lossCategory}</span></span>
                </div>
                {t.integrityFlag && (
                  <div className="mt-1 p-2 rounded bg-amber-900/20 border border-amber-700/30 text-amber-300">
                    ⚠ {t.integrityFlag}
                  </div>
                )}
                <div className="mt-1 p-2 rounded bg-slate-900/50 text-slate-400 leading-relaxed">
                  <span className="text-slate-500">hypothesis: </span>{t.hypothesis}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Integrity flags ───────────────────────────────────────────────────────────

function IntegrityFlagList({ flags }: { flags: FtIntegrityFlag[] }) {
  if (!flags.length) {
    return <p className="text-slate-500 text-xs font-mono italic">No ERA5/Kalshi disagreements detected.</p>;
  }
  return (
    <div className="space-y-2">
      {flags.map(f => (
        <div key={f.marketTicker} className="rounded-lg border border-amber-700/30 bg-amber-900/10 px-3 py-2 text-xs font-mono">
          <div className="flex items-start gap-2">
            <ShieldAlert className="h-3.5 w-3.5 text-amber-400 mt-0.5 shrink-0" />
            <div className="space-y-0.5 min-w-0">
              <div className="text-slate-200 font-semibold">{f.marketTicker}</div>
              <div className="text-slate-400">
                {f.city} · {f.weatherVariable} · {f.targetDate} · {f.direction} → outcome: <span className="text-red-300">{f.outcome}</span>
              </div>
              <div className="text-amber-300 mt-1">{f.detail}</div>
              {f.sourceLabel && (
                <div className="text-slate-500 text-[10px]">
                  ERA5 source: {f.sourceLabel} — note: ERA5 grid ≠ NWS point station; verify against NWS Daily CLI report before drawing conclusions.
                </div>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function DiagnosticsSkeleton() {
  return (
    <Card className="bg-transparent border border-slate-700/50">
      <CardHeader className="pb-3 pt-4 px-5">
        <Skeleton className="h-4 w-56 bg-slate-700/40" />
      </CardHeader>
      <CardContent className="px-5 pb-5 space-y-4">
        <Skeleton className="h-10 w-full bg-slate-700/20 rounded-lg" />
        <div className="grid grid-cols-5 gap-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-14 bg-slate-700/20 rounded-lg" />
          ))}
        </div>
        <Skeleton className="h-32 w-full bg-slate-700/20 rounded-lg" />
      </CardContent>
    </Card>
  );
}

// ── Collapsible section ───────────────────────────────────────────────────────

function Section({
  title, defaultOpen = true, count, children,
}: { title: string; defaultOpen?: boolean; count?: number; children: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 py-1.5 text-left group"
      >
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-400 group-hover:text-slate-200 transition-colors font-mono">
          {title}
        </span>
        {count != null && (
          <Badge className="text-[9px] px-1.5 py-0 bg-slate-700/40 text-slate-400 border-slate-600/40">
            {count}
          </Badge>
        )}
        <div className="flex-1 h-px bg-slate-700/30" />
        {open ? <ChevronDown className="h-3 w-3 text-slate-500" /> : <ChevronRight className="h-3 w-3 text-slate-500" />}
      </button>
      {open && <div className="mt-2">{children}</div>}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function ForwardTestDiagnostics() {
  const { data, isLoading, isError } = useGetForwardTestDiagnostics();

  if (isLoading) return <DiagnosticsSkeleton />;
  if (isError || !data) {
    return (
      <Card className="bg-transparent border border-slate-700/50">
        <CardContent className="px-5 py-4">
          <p className="text-xs text-slate-500 font-mono">Diagnostics unavailable.</p>
        </CardContent>
      </Card>
    );
  }

  const briColor = data.brierScore > 0.25 ? "red" : "green";
  const eceColor = data.expectedCalibrationErrorPct > 15 ? "amber" : "green";
  const roiColor = data.roiPct < 0 ? "red" : "green";

  return (
    <Card className="bg-transparent border border-slate-700/50 shadow-[0_0_30px_rgba(0,0,0,0.3)]">
      <CardHeader className="pb-2 pt-4 px-5">
        <CardTitle className="flex items-center gap-2 text-sm font-bold font-mono text-slate-200 uppercase tracking-wider">
          <Activity className="h-4 w-4 text-blue-400" />
          Forward Test Diagnostics
          <Badge className="text-[9px] px-1.5 py-0 bg-blue-900/30 text-blue-400 border-blue-700/40 ml-1">
            READ-ONLY
          </Badge>
        </CardTitle>
      </CardHeader>

      <CardContent className="px-5 pb-5 space-y-5">

        {/* ── Warning banner ── */}
        <div className="flex items-start gap-2 p-3 rounded-lg border border-amber-700/40 bg-amber-900/15 text-amber-300 text-xs font-mono leading-relaxed">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <span>{data.sampleWarning}</span>
        </div>

        {/* ── Stat pills ── */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
          <Pill label="Settled" value={String(data.settledCount)} color="gray" />
          <Pill
            label="Win Rate"
            value={pct(data.winRatePct)}
            color={data.winRatePct >= 55 ? "green" : "red"}
          />
          <Pill
            label="Brier Score"
            value={num(data.brierScore, 3)}
            color={briColor}
            tip="0 = perfect, 0.25 = always-50% baseline. Lower is better."
          />
          <Pill
            label="Cal. Error (ECE)"
            value={pct(data.expectedCalibrationErrorPct)}
            color={eceColor}
            tip="Expected Calibration Error — how far predicted probabilities are from actual win rates on average."
          />
          <Pill label="ROI" value={pct(data.roiPct)} color={roiColor} />
        </div>

        {/* ── Calibration bands ── */}
        <Section title="Calibration by Probability Band" defaultOpen>
          <div className="text-[10px] text-slate-500 font-mono mb-2">
            Cal. Error = observed win rate − avg predicted probability. Negative = model is overconfident.
          </div>
          <CalibrationTable bands={data.calibrationBands} />
        </Section>

        {/* ── By strategy & direction ── */}
        <Section title="Performance by Strategy & Direction" defaultOpen>
          <GroupTable rows={[...data.byStrategy, ...data.byDirection]} />
        </Section>

        {/* ── False confidence losses ── */}
        <Section
          title="False-Confidence Losses (model ≥85%, outcome = LOSS)"
          defaultOpen
          count={data.falseConfidenceLosses.length}
        >
          <FalseConfidenceList items={data.falseConfidenceLosses} />
        </Section>

        {/* ── Settlement integrity ── */}
        <Section
          title="Settlement Integrity Flags (ERA5 vs Kalshi)"
          defaultOpen={data.settlementIntegrityFlags.length > 0}
          count={data.settlementIntegrityFlags.length}
        >
          <IntegrityFlagList flags={data.settlementIntegrityFlags} />
          {data.settlementIntegrityFlags.length > 0 && (
            <p className="mt-2 text-[10px] text-slate-500 font-mono leading-relaxed">
              ERA5 reanalysis uses a grid point, not the NWS station Kalshi settles against. Disagreements
              are investigation leads — verify against the NWS Daily CLI report before concluding there is an error.
            </p>
          )}
        </Section>

      </CardContent>
    </Card>
  );
}
