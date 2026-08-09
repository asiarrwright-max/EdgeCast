/**
 * City Specialization Study section for Audit & Validation.
 * Read-only — pulls live data from GET /api/city-study.
 * No model, trading, or eligibility logic is modified here.
 */
import { useState } from "react";
import {
  MapPin, TrendingUp, Zap, BarChart2, Shield, Star,
  ChevronDown, ChevronUp, AlertTriangle, CheckCircle,
  XCircle, Info,
} from "lucide-react";
import { useGetCityStudy, type CityMetrics } from "@workspace/api-client-react";

// ─── shared card primitives ──────────────────────────────────────────────────
function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-card text-card-foreground shadow-sm">
      {children}
    </div>
  );
}
function CardHeader({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-col space-y-1.5 p-4 pb-2">{children}</div>;
}
function CardBody({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`p-4 pt-0 ${className}`}>{children}</div>;
}

// ─── grade badge ─────────────────────────────────────────────────────────────
function GradeBadge({ grade }: { grade: string }) {
  const colors: Record<string, string> = {
    "VERY LOW": "bg-red-900/40 text-red-400 border-red-800",
    LOW:        "bg-orange-900/40 text-orange-400 border-orange-800",
    MODERATE:   "bg-yellow-900/40 text-yellow-400 border-yellow-800",
    GOOD:       "bg-emerald-900/40 text-emerald-400 border-emerald-800",
    STRONG:     "bg-blue-900/40 text-blue-400 border-blue-800",
  };
  return (
    <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${colors[grade] ?? "bg-secondary text-muted-foreground border-border"}`}>
      {grade}
    </span>
  );
}

// ─── recommendation badge ─────────────────────────────────────────────────────
function RecBadge({ rec }: { rec: string }) {
  const colors: Record<string, string> = {
    "A.": "bg-emerald-900/40 text-emerald-400 border-emerald-700",
    "B.": "bg-blue-900/40 text-blue-400 border-blue-700",
    "C.": "bg-yellow-900/40 text-yellow-400 border-yellow-700",
    "D.": "bg-red-900/40 text-red-400 border-red-700",
  };
  const prefix = rec.slice(0, 2);
  return (
    <span className={`text-[11px] font-mono font-semibold px-2 py-0.5 rounded border ${colors[prefix] ?? "bg-secondary border-border text-muted-foreground"}`}>
      {rec}
    </span>
  );
}

// ─── score bar ───────────────────────────────────────────────────────────────
function ScoreBar({ value, max = 100 }: { value: number; max?: number }) {
  const pct = Math.min(100, (value / max) * 100);
  const color = pct >= 65 ? "bg-emerald-500" : pct >= 45 ? "bg-yellow-500" : "bg-red-500";
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full bg-secondary">
        <div className={`h-1.5 rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] font-mono text-muted-foreground w-8 text-right">
        {value.toFixed(0)}
      </span>
    </div>
  );
}

function fmt(v: number | null | undefined, decimals = 1, suffix = "") {
  if (v === null || v === undefined) return "UNKNOWN";
  return `${v.toFixed(decimals)}${suffix}`;
}

function pct(v: number | null | undefined) {
  if (v === null || v === undefined) return "UNKNOWN";
  return `${v.toFixed(1)}%`;
}

// ─── City detail card (collapsible) ──────────────────────────────────────────
function CityCard({ city, rank }: { city: CityMetrics; rank: number }) {
  const [open, setOpen] = useState(false);
  const s = city.score;
  const isTop = rank === 1;

  return (
    <div className={`rounded-lg border ${isTop ? "border-primary/40 bg-primary/5" : "border-border bg-card"}`}>
      {/* Header row */}
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-3 p-3 text-left hover:bg-secondary/30 transition-colors rounded-lg"
      >
        <span className={`text-sm font-bold font-mono w-6 shrink-0 ${isTop ? "text-primary" : "text-muted-foreground"}`}>
          #{rank}
        </span>
        <span className="text-sm font-semibold flex-1">{city.city}</span>
        {isTop && <Star className="h-3.5 w-3.5 text-yellow-400 shrink-0" />}
        <GradeBadge grade={city.sample_size_grade} />
        <div className="flex items-center gap-2 w-32 shrink-0">
          <ScoreBar value={s.total} />
        </div>
        <span className="text-xs font-mono text-muted-foreground w-20 text-right shrink-0">
          {city.win_rate_pct !== null ? `${city.win_rate_pct}% WR` : "No settled"}
        </span>
        {city.station_verified
          ? <CheckCircle className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
          : <Shield className="h-3.5 w-3.5 text-yellow-500 shrink-0" />}
        {!city.nws_compatible && <XCircle className="h-3.5 w-3.5 text-red-500 shrink-0" />}
        {open ? <ChevronUp className="h-4 w-4 text-muted-foreground shrink-0" />
               : <ChevronDown className="h-4 w-4 text-muted-foreground shrink-0" />}
      </button>

      {open && (
        <div className="px-3 pb-3 space-y-4 border-t border-border/50 pt-3">
          {/* Warnings */}
          {city.sample_warnings.length > 0 && (
            <div className="rounded border border-yellow-800/50 bg-yellow-900/20 p-2 space-y-1">
              {city.sample_warnings.map((w, i) => (
                <div key={i} className="flex items-start gap-2 text-xs text-yellow-400">
                  <AlertTriangle className="h-3 w-3 shrink-0 mt-0.5" />
                  <span>{w}</span>
                </div>
              ))}
            </div>
          )}

          {!city.nws_compatible && (
            <div className="rounded border border-red-800/50 bg-red-900/20 p-2 text-xs text-red-400 flex items-start gap-2">
              <XCircle className="h-3 w-3 shrink-0 mt-0.5" />
              <span>Non-NWS settlement source. EdgeCast explicitly does not trade this city. Shown for completeness only.</span>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
            {/* Score breakdown */}
            <div className="space-y-2">
              <p className="font-semibold text-muted-foreground uppercase tracking-wide text-[10px]">Specialization Score</p>
              {[
                ["Forecast accuracy (30%)", s.forecast],
                ["Trading quality (25%)", s.trading],
                ["Market liquidity (20%)", s.liquidity],
                ["Sample size (15%)", s.sample],
                ["Station integrity (10%)", s.station],
              ].map(([label, val]) => (
                <div key={label as string} className="space-y-0.5">
                  <div className="flex justify-between text-[10px] text-muted-foreground">
                    <span>{label as string}</span>
                    <span className="font-mono">{(val as number).toFixed(0)}</span>
                  </div>
                  <ScoreBar value={val as number} />
                </div>
              ))}
              <div className="flex justify-between font-semibold border-t border-border pt-1 text-[10px]">
                <span>Total</span>
                <span className="font-mono text-primary">{s.total.toFixed(1)}</span>
              </div>
            </div>

            {/* Trading performance */}
            <div className="space-y-1.5">
              <p className="font-semibold text-muted-foreground uppercase tracking-wide text-[10px]">Trading Performance</p>
              {[
                ["Settled total", city.settled_total],
                ["OFFICIAL settled", city.official_settled],
                ["Wins", city.wins],
                ["Losses", city.losses],
                ["Win rate", city.win_rate_pct !== null ? pct(city.win_rate_pct) : "UNKNOWN"],
                ["Avg predicted prob", fmt(city.avg_predicted_prob, 3)],
                ["Calibration gap", fmt(city.calibration_gap, 3)],
                ["Total P&L", city.total_pnl !== null ? `$${city.total_pnl.toFixed(2)}` : "UNKNOWN"],
                ["Avg edge at entry", city.avg_edge !== null ? `${city.avg_edge}pp` : "UNKNOWN"],
                ["Unique market days", city.unique_market_days],
                ["~Opps per day", city.approx_opps_per_day],
              ].map(([label, val]) => (
                <div key={label as string} className="flex justify-between">
                  <span className="text-muted-foreground">{label as string}</span>
                  <span className="font-mono">{val}</span>
                </div>
              ))}
            </div>

            {/* Forecast accuracy + market quality */}
            <div className="space-y-1.5">
              <p className="font-semibold text-muted-foreground uppercase tracking-wide text-[10px]">Forecast Accuracy</p>
              {[
                ["Observations", city.fv_obs || "UNKNOWN"],
                ["MAE", city.mae !== null ? `${city.mae}°F` : "UNKNOWN"],
                ["Median AE", city.median_ae !== null ? `${city.median_ae}°F` : "UNKNOWN"],
                ["Bias", city.mean_bias !== null ? `${city.mean_bias}°F` : "UNKNOWN"],
                ["RMSE", city.rmse !== null ? `${city.rmse}°F` : "UNKNOWN"],
                ["Within ±1°F", pct(city.pct_within_1f)],
                ["Within ±2°F", pct(city.pct_within_2f)],
                ["Within ±3°F", pct(city.pct_within_3f)],
                ["Sources", city.forecast_sources ?? "UNKNOWN"],
              ].map(([label, val]) => (
                <div key={label as string} className="flex justify-between">
                  <span className="text-muted-foreground">{label as string}</span>
                  <span className="font-mono text-right max-w-[120px] truncate">{val}</span>
                </div>
              ))}
              <p className="font-semibold text-muted-foreground uppercase tracking-wide text-[10px] pt-2">Market Quality</p>
              {[
                ["Market scans", city.market_scans || "UNKNOWN"],
                ["% valid ask", pct(city.pct_valid_ask)],
                ["% fresh ≤300s", pct(city.pct_fresh_300s)],
                ["Avg qty", city.avg_qty !== null ? city.avg_qty : "UNKNOWN"],
                ["Volume stored", "No — not in schema"],
              ].map(([label, val]) => (
                <div key={label as string} className="flex justify-between">
                  <span className="text-muted-foreground">{label as string}</span>
                  <span className="font-mono">{val}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Model versions table */}
          {city.model_versions.length > 0 && (
            <div>
              <p className="font-semibold text-muted-foreground uppercase tracking-wide text-[10px] mb-1.5">Model Version Comparison</p>
              <table className="w-full text-[10px] font-mono">
                <thead>
                  <tr className="text-muted-foreground border-b border-border">
                    <th className="text-left pb-1 pr-3">Version</th>
                    <th className="text-right pb-1 pr-3">Settled</th>
                    <th className="text-right pb-1 pr-3">Win Rate</th>
                    <th className="text-right pb-1 pr-3">P&L</th>
                    <th className="text-right pb-1">Avg Edge</th>
                  </tr>
                </thead>
                <tbody>
                  {city.model_versions.filter(v => v.settled > 0).map(v => (
                    <tr key={v.strategy_version} className="border-b border-border/30">
                      <td className="py-0.5 pr-3 text-foreground">{v.strategy_version}</td>
                      <td className="py-0.5 pr-3 text-right">{v.settled}</td>
                      <td className={`py-0.5 pr-3 text-right ${v.win_rate !== null && v.win_rate >= 45 ? "text-emerald-400" : v.win_rate !== null && v.win_rate < 30 ? "text-red-400" : ""}`}>
                        {v.win_rate !== null ? `${v.win_rate}%` : "—"}
                      </td>
                      <td className={`py-0.5 pr-3 text-right ${v.pnl !== null && v.pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                        {v.pnl !== null ? `$${v.pnl.toFixed(0)}` : "—"}
                      </td>
                      <td className="py-0.5 text-right text-muted-foreground">
                        {v.avg_edge !== null ? `${v.avg_edge}pp` : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Station */}
          <div className="text-xs">
            <p className="font-semibold text-muted-foreground uppercase tracking-wide text-[10px] mb-1">Settlement Station</p>
            <div className="flex gap-4 flex-wrap">
              <span className="text-muted-foreground">Station: <span className="font-mono text-foreground">{city.station_name ?? "UNKNOWN"}</span></span>
              <span className="text-muted-foreground">ID: <span className="font-mono text-foreground">{city.station_id ?? "UNKNOWN"}</span></span>
              <span className="text-muted-foreground">Verified: <span className={`font-mono ${city.station_verified ? "text-emerald-400" : "text-yellow-400"}`}>{city.station_verified ? "YES" : "NO"}</span></span>
              <span className="text-muted-foreground">NWS: <span className={`font-mono ${city.nws_compatible ? "text-emerald-400" : "text-red-400"}`}>{city.nws_compatible ? "YES" : "NO"}</span></span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Summary table (top-level ranking) ──────────────────────────────────────
function SummaryTable({ cities }: { cities: CityMetrics[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px] font-mono">
        <thead>
          <tr className="text-muted-foreground border-b border-border text-left">
            <th className="pb-2 pr-3">Rank</th>
            <th className="pb-2 pr-3">City</th>
            <th className="pb-2 pr-3 text-right">Score</th>
            <th className="pb-2 pr-3 text-right">Settled</th>
            <th className="pb-2 pr-3 text-right">Win Rate</th>
            <th className="pb-2 pr-3 text-right">MAE</th>
            <th className="pb-2 pr-3 text-right">Valid Ask%</th>
            <th className="pb-2 pr-3 text-center">Verified</th>
            <th className="pb-2 text-center">NWS</th>
          </tr>
        </thead>
        <tbody>
          {cities.map((c, i) => (
            <tr key={c.city} className={`border-b border-border/30 ${!c.nws_compatible ? "opacity-40" : ""}`}>
              <td className="py-1 pr-3 text-muted-foreground">#{i + 1}</td>
              <td className="py-1 pr-3 font-semibold text-foreground">{c.city}</td>
              <td className={`py-1 pr-3 text-right ${c.score.total >= 65 ? "text-emerald-400" : c.score.total >= 50 ? "text-yellow-400" : "text-muted-foreground"}`}>
                {c.score.total.toFixed(1)}
              </td>
              <td className="py-1 pr-3 text-right">{c.settled_total || "—"}</td>
              <td className={`py-1 pr-3 text-right ${c.win_rate_pct !== null && c.win_rate_pct >= 45 ? "text-emerald-400" : c.win_rate_pct !== null && c.win_rate_pct < 25 ? "text-red-400" : ""}`}>
                {c.win_rate_pct !== null ? `${c.win_rate_pct}%` : "—"}
              </td>
              <td className="py-1 pr-3 text-right">
                {c.mae !== null ? `${c.mae}°F` : "—"}
              </td>
              <td className="py-1 pr-3 text-right">
                {c.pct_valid_ask !== null ? `${c.pct_valid_ask}%` : "—"}
              </td>
              <td className="py-1 pr-3 text-center">
                {c.station_verified ? "✓" : "·"}
              </td>
              <td className={`py-1 text-center ${c.nws_compatible ? "text-emerald-400" : "text-red-400"}`}>
                {c.nws_compatible ? "✓" : "✗"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Main export ─────────────────────────────────────────────────────────────
export function CitySpecializationStudySection() {
  const { data, isLoading, isError, refetch, isFetching } = useGetCityStudy();
  const [showAll, setShowAll] = useState(false);

  const displayCities = showAll ? (data?.cities ?? []) : (data?.cities ?? []).slice(0, 6);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <MapPin className="h-4 w-4 text-primary" />
          <h2 className="text-sm font-semibold">City Specialization Study</h2>
          <div className="ml-auto flex items-center gap-2">
            {isFetching && <span className="text-[10px] text-muted-foreground font-mono">refreshing…</span>}
            <button
              onClick={() => refetch()}
              disabled={isFetching}
              className="text-[11px] font-mono text-muted-foreground hover:text-foreground disabled:opacity-40 transition-colors flex items-center gap-1 px-2 py-1 border border-border rounded"
            >
              Refresh
            </button>
          </div>
        </div>
        <p className="text-xs text-muted-foreground mt-1">
          Read-only analysis. No model logic, eligibility rules, or trading behavior is changed by this study.
        </p>
      </CardHeader>

      <CardBody className="space-y-5">
        {isLoading && (
          <div className="py-8 text-center text-sm text-muted-foreground font-mono">Computing city study…</div>
        )}
        {isError && (
          <div className="py-4 text-center text-sm text-destructive font-mono">Failed to load city study.</div>
        )}

        {data && (
          <>
            {/* ── Should EdgeCast focus on one city? ── */}
            <div className="rounded-lg border border-border bg-secondary/30 p-4 space-y-3">
              <div className="flex items-start gap-3">
                <Info className="h-4 w-4 text-primary mt-0.5 shrink-0" />
                <div className="space-y-2">
                  <p className="text-sm font-semibold">Should EdgeCast focus on one city?</p>
                  <RecBadge rec={data.recommendation} />
                  <div className="space-y-1 mt-2">
                    {data.recommendation_reasons.map((r, i) => (
                      <p key={i} className="text-xs text-muted-foreground leading-relaxed">{r}</p>
                    ))}
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3 text-xs">
                <div>
                  <p className="text-[10px] text-muted-foreground uppercase tracking-wide mb-1">Best single city</p>
                  <p className="font-semibold text-foreground">{data.top_single_city ?? "—"}</p>
                  <p className="text-[10px] text-muted-foreground">#{2}: {data.top_two_city ?? "—"}</p>
                  <p className="text-[10px] text-muted-foreground">#{3}: {data.top_three_city_individual ?? "—"}</p>
                </div>
                <div>
                  <p className="text-[10px] text-muted-foreground uppercase tracking-wide mb-1">Best 3-city set</p>
                  <p className="font-semibold text-foreground">{data.best_3_city_set.join(", ") || "—"}</p>
                  <p className="text-[10px] text-muted-foreground mt-1">Cities analyzed: {data.cities_analyzed.length}</p>
                </div>
              </div>
            </div>

            {/* ── Summary ranking table ── */}
            <div>
              <div className="flex items-center gap-2 mb-2">
                <BarChart2 className="h-3.5 w-3.5 text-muted-foreground" />
                <p className="text-xs font-semibold">City Ranking</p>
              </div>
              <SummaryTable cities={data.cities} />
              <p className="text-[10px] text-muted-foreground mt-1.5">
                Strikeout rows: non-NWS settlement (excluded from recommendations).
                Score = 30% forecast · 25% trading · 20% liquidity · 15% sample · 10% station.
              </p>
            </div>

            {/* ── Score legend ── */}
            <div className="rounded border border-border p-3 text-xs space-y-1">
              <p className="font-semibold text-[10px] text-muted-foreground uppercase tracking-wide">Score weight legend</p>
              <div className="grid grid-cols-2 gap-1 text-[10px] font-mono text-muted-foreground">
                {Object.entries(data.score_weights).map(([k, v]) => (
                  <span key={k}>{k}: {Math.round(v * 100)}%</span>
                ))}
              </div>
            </div>

            {/* ── FTB impact (for #1 city) ── */}
            {data.ftb_impact && (
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <TrendingUp className="h-3.5 w-3.5 text-muted-foreground" />
                  <p className="text-xs font-semibold">FTB Impact — If Focused on {data.ftb_impact.city}</p>
                </div>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-xs font-mono">
                  {[
                    ["v2.3 evaluated", data.ftb_impact.v23_total_evaluated],
                    ["v2.3 OFFICIAL", data.ftb_impact.v23_official],
                    ["v2.3 RESEARCH_ONLY", data.ftb_impact.v23_research_only],
                    ["Scan days", data.ftb_impact.v23_scan_days],
                    ["Est. OFFICIAL/week", `${data.ftb_impact.est_official_per_week_lo}–${data.ftb_impact.est_official_per_week_hi}`],
                    ["Time to 10 settled", data.ftb_impact.est_weeks_to_10_settled],
                    ["Time to 25 settled", data.ftb_impact.est_weeks_to_25_settled],
                    ["Time to 50 settled", data.ftb_impact.est_weeks_to_50_settled],
                  ].map(([label, val]) => (
                    <div key={label as string} className="flex flex-col">
                      <span className="text-[10px] text-muted-foreground">{label as string}</span>
                      <span className="text-foreground">{val}</span>
                    </div>
                  ))}
                </div>
                {data.ftb_impact.top_rejection_reasons.length > 0 && (
                  <div className="mt-2 text-[10px] text-muted-foreground">
                    <span className="text-foreground font-medium">Top FTB rejections: </span>
                    {data.ftb_impact.top_rejection_reasons.join(" · ")}
                  </div>
                )}
                <p className="text-[10px] text-muted-foreground mt-1.5 italic">{data.ftb_impact.note}</p>
              </div>
            )}

            {/* ── City detail cards ── */}
            <div>
              <div className="flex items-center gap-2 mb-3">
                <Zap className="h-3.5 w-3.5 text-muted-foreground" />
                <p className="text-xs font-semibold">City Detail</p>
                <span className="text-[10px] text-muted-foreground">— click any row to expand</span>
              </div>
              <div className="space-y-2">
                {displayCities.map((city, i) => (
                  <CityCard key={city.city} city={city} rank={i + 1} />
                ))}
              </div>
              {(data.cities.length > 6) && (
                <button
                  onClick={() => setShowAll(!showAll)}
                  className="mt-2 text-xs text-muted-foreground hover:text-foreground flex items-center gap-1"
                >
                  {showAll ? <><ChevronUp className="h-3 w-3" /> Show fewer</> : <><ChevronDown className="h-3 w-3" /> Show all {data.cities.length} cities</>}
                </button>
              )}
            </div>

            {/* ── Data notes ── */}
            <div className="rounded border border-border p-3 space-y-1 text-[10px] text-muted-foreground">
              <p className="text-foreground font-medium text-xs">Data notes</p>
              <p>Generated at: <span className="font-mono">{new Date(data.generated_at).toLocaleString()}</span></p>
              <p>Volume / open interest are not stored in paper_trades — shown as UNKNOWN.</p>
              <p>Washington DC is non-NWS settlement (The Weather Company) — excluded from all recommendations.</p>
              <p>Forecast verifications use era5_reanalysis source; GHCND observations not yet linked.</p>
              <p>Outcome data uses uppercase WIN/LOSS values. Model v2.3 (FTB era) started 2026-08-09.</p>
            </div>

            {/* ── Safety attestation ── */}
            <div className="rounded border border-border/40 p-2 flex flex-wrap gap-3 text-[10px] font-mono text-muted-foreground">
              <span className="text-emerald-400">✓ trading_state_modified: false</span>
              <span className="text-emerald-400">✓ ftb_untouched: true</span>
              <span className="text-emerald-400">✓ read_only: true</span>
              <span className="text-emerald-400">✓ no model logic changed</span>
            </div>
          </>
        )}
      </CardBody>
    </Card>
  );
}
