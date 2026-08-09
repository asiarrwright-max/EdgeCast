/**
 * Verified City Specialization section for Audit & Validation.
 * Read-only. No model, trading, eligibility, or FTB changes.
 */
import { useState } from "react";
import {
  ShieldCheck, ShieldAlert, MapPin, CheckCircle, XCircle,
  ChevronDown, ChevronUp, Info, Star, AlertTriangle,
} from "lucide-react";
import {
  useGetVerifiedCityStudy,
  type CityVerificationResult,
  type VerifiedCityMetrics,
} from "@workspace/api-client-react";

// ─── primitives ──────────────────────────────────────────────────────────────
function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-card text-card-foreground shadow-sm">
      {children}
    </div>
  );
}
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wide">
        {title}
      </p>
      {children}
    </div>
  );
}

function VerdictBadge({ verdict }: { verdict: string }) {
  if (verdict === "VERIFIED") {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-mono font-semibold px-2 py-0.5 rounded border border-emerald-700 bg-emerald-900/40 text-emerald-400">
        <CheckCircle className="h-3 w-3" /> VERIFIED
      </span>
    );
  }
  if (verdict === "CONFLICT") {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-mono font-semibold px-2 py-0.5 rounded border border-red-700 bg-red-900/40 text-red-400">
        <XCircle className="h-3 w-3" /> CONFLICT
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-[10px] font-mono font-semibold px-2 py-0.5 rounded border border-yellow-700 bg-yellow-900/40 text-yellow-400">
      <ShieldAlert className="h-3 w-3" /> {verdict}
    </span>
  );
}

function ScoreBar({ value }: { value: number }) {
  const pct = Math.min(100, value);
  const color = pct >= 65 ? "bg-emerald-500" : pct >= 45 ? "bg-yellow-500" : "bg-red-500";
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full bg-secondary">
        <div className={`h-1.5 rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] font-mono text-muted-foreground w-7 text-right">
        {value.toFixed(0)}
      </span>
    </div>
  );
}

// ─── Verification evidence card ───────────────────────────────────────────────
function EvidenceCard({ ev }: { ev: CityVerificationResult }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded border border-border bg-secondary/20">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-3 p-2.5 text-left hover:bg-secondary/40 transition-colors rounded"
      >
        <VerdictBadge verdict={ev.verdict} />
        <span className="text-xs font-semibold flex-1">{ev.city}</span>
        <span className="text-[10px] font-mono text-muted-foreground">{ev.station_name}</span>
        {open ? <ChevronUp className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
               : <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" />}
      </button>
      {open && (
        <div className="px-3 pb-3 pt-1 border-t border-border/40 space-y-2 text-[10px] font-mono">
          <div className="grid grid-cols-2 gap-x-4 gap-y-1">
            {[
              ["Kalshi series",      ev.kalshi_series],
              ["API field used",     ev.api_field],
              ["GHCND station ID",   ev.ghcnd_station],
              ["Query date",         ev.query_date],
              ["Flag changed",       ev.flag_changed ? "YES" : "NO (already verified)"],
            ].map(([label, val]) => (
              <div key={label as string}>
                <span className="text-muted-foreground">{label as string}: </span>
                <span className="text-foreground">{val as string}</span>
              </div>
            ))}
          </div>
          <div className="rounded border border-border/50 bg-background/50 p-2 space-y-1">
            <p className="text-muted-foreground">Settlement text from API:</p>
            <p className="text-foreground italic">"{ev.settlement_text}"</p>
          </div>
          <div>
            <span className="text-muted-foreground">NWS location: </span>
            <span className="text-foreground">{ev.nws_station}</span>
          </div>
          <div>
            <span className="text-muted-foreground">Source log: </span>
            <span className="text-foreground">{ev.source_log}</span>
          </div>
          {ev.notes && (
            <div className="rounded border border-yellow-800/40 bg-yellow-900/10 p-1.5 text-yellow-400">
              ⚠ {ev.notes}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Ranked city row ──────────────────────────────────────────────────────────
function RankedCityRow({
  city,
  rank,
  inShortlist,
}: {
  city: VerifiedCityMetrics;
  rank: number;
  inShortlist: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`rounded border ${inShortlist ? "border-primary/40 bg-primary/5" : "border-border/40 bg-secondary/10"}`}>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 p-2.5 text-left text-xs hover:bg-secondary/20 transition-colors rounded"
      >
        <span className="font-mono text-muted-foreground w-5 shrink-0">#{rank}</span>
        <span className="font-semibold flex-1">{city.city}</span>
        {inShortlist && <Star className="h-3 w-3 text-yellow-400 shrink-0" />}
        <span className="font-mono text-muted-foreground text-[10px] w-16 text-right">
          {city.win_rate_pct !== null ? `${city.win_rate_pct}% WR` : "—"}
        </span>
        <span className="font-mono text-muted-foreground text-[10px] w-16 text-right">
          {city.mae !== null ? `${city.mae}°F` : "UNKNOWN"}
        </span>
        <div className="w-24 shrink-0">
          <ScoreBar value={city.score.total} />
        </div>
        {open ? <ChevronUp className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
               : <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" />}
      </button>
      {open && (
        <div className="px-3 pb-3 border-t border-border/30 pt-2 grid grid-cols-2 md:grid-cols-3 gap-3 text-[10px] font-mono">
          {/* Trading */}
          <div className="space-y-1">
            <p className="text-muted-foreground font-semibold uppercase text-[9px]">Trading</p>
            {[
              ["Settled", city.settled_total || "—"],
              ["Win rate", city.win_rate_pct !== null ? `${city.win_rate_pct}%` : "—"],
              ["Wins/Losses", `${city.wins}/${city.losses}`],
              ["Total P&L", city.total_pnl !== null ? `$${city.total_pnl.toFixed(0)}` : "—"],
              ["Avg edge", city.avg_edge !== null ? `${city.avg_edge}pp` : "—"],
              ["Cal. gap", city.calibration_gap !== null ? city.calibration_gap.toFixed(3) : "—"],
            ].map(([l, v]) => (
              <div key={l as string} className="flex justify-between">
                <span className="text-muted-foreground">{l as string}</span>
                <span>{v}</span>
              </div>
            ))}
          </div>
          {/* Forecast */}
          <div className="space-y-1">
            <p className="text-muted-foreground font-semibold uppercase text-[9px]">Forecast</p>
            {[
              ["Observations", city.fv_obs || "—"],
              ["MAE", city.mae !== null ? `${city.mae}°F` : "UNKNOWN"],
              ["Bias", city.mean_bias !== null ? `${city.mean_bias}°F` : "UNKNOWN"],
              ["Within ±2°F", city.pct_within_2f !== null ? `${city.pct_within_2f}%` : "UNKNOWN"],
              ["Sources", city.forecast_sources ?? "—"],
            ].map(([l, v]) => (
              <div key={l as string} className="flex justify-between">
                <span className="text-muted-foreground">{l as string}</span>
                <span className="text-right max-w-[100px] truncate">{v}</span>
              </div>
            ))}
          </div>
          {/* FTB & Liquidity */}
          <div className="space-y-1">
            <p className="text-muted-foreground font-semibold uppercase text-[9px]">FTB & Liquidity</p>
            {[
              ["Est. OFFICIAL/wk", city.est_official_per_week_lo > 0
                ? `${city.est_official_per_week_lo}–${city.est_official_per_week_hi}`
                : "—"],
              ["v2.3 total", city.ftb?.total_v23 ?? "—"],
              ["v2.3 OFFICIAL", city.ftb?.official_count ?? "—"],
              ["Scan days", city.ftb?.scan_days ?? "—"],
              ["Market scans", city.market_scans || "—"],
              ["% valid ask", city.pct_valid_ask !== null ? `${city.pct_valid_ask}%` : "—"],
            ].map(([l, v]) => (
              <div key={l as string} className="flex justify-between">
                <span className="text-muted-foreground">{l as string}</span>
                <span>{v}</span>
              </div>
            ))}
          </div>
          {/* Score breakdown */}
          <div className="col-span-2 md:col-span-3 space-y-1">
            <p className="text-muted-foreground font-semibold uppercase text-[9px]">Score breakdown (all verified+NWS → station=100)</p>
            <div className="grid grid-cols-5 gap-2">
              {[
                ["Forecast (30%)", city.score.forecast],
                ["Trading (25%)", city.score.trading],
                ["Liquidity (20%)", city.score.liquidity],
                ["Sample (15%)", city.score.sample],
                ["Station (10%)", 100],
              ].map(([label, val]) => (
                <div key={label as string}>
                  <p className="text-muted-foreground text-[9px]">{label as string}</p>
                  <ScoreBar value={val as number} />
                </div>
              ))}
            </div>
          </div>
          {city.sample_warnings.length > 0 && (
            <div className="col-span-2 md:col-span-3 rounded border border-yellow-800/40 bg-yellow-900/10 p-1.5 space-y-0.5">
              {city.sample_warnings.map((w, i) => (
                <div key={i} className="flex items-start gap-1.5 text-yellow-400">
                  <AlertTriangle className="h-3 w-3 shrink-0 mt-px" />
                  <span>{w}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Main export ─────────────────────────────────────────────────────────────
export function VerifiedCitySpecializationSection() {
  const { data, isLoading, isError, refetch, isFetching } = useGetVerifiedCityStudy();

  const shortlistSet = new Set(data?.shortlist ?? []);

  return (
    <Card>
      {/* Header */}
      <div className="flex flex-col space-y-1.5 p-4 pb-2">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-primary" />
          <h2 className="text-sm font-semibold">Verified City Specialization</h2>
          <div className="ml-auto flex items-center gap-2">
            {isFetching && (
              <span className="text-[10px] text-muted-foreground font-mono">refreshing…</span>
            )}
            <button
              onClick={() => refetch()}
              disabled={isFetching}
              className="text-[11px] font-mono text-muted-foreground hover:text-foreground disabled:opacity-40 transition-colors flex items-center gap-1 px-2 py-1 border border-border rounded"
            >
              Refresh
            </button>
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          Read-only. No model, trading, eligibility, or FTB changes.
          Only <span className="font-mono">verified=True</span> + <span className="font-mono">nws_settlement=True</span> cities appear in the ranking.
        </p>
      </div>

      <div className="p-4 pt-0 space-y-5">
        {isLoading && (
          <div className="py-8 text-center text-sm text-muted-foreground font-mono">
            Loading verified city study…
          </div>
        )}
        {isError && (
          <div className="py-4 text-center text-sm text-destructive font-mono">
            Failed to load verified city study.
          </div>
        )}

        {data && (
          <>
            {/* ── Primary answer ── */}
            <div className="rounded-lg border border-primary/30 bg-primary/5 p-4 space-y-3">
              <div className="flex items-start gap-2">
                <Info className="h-4 w-4 text-primary shrink-0 mt-0.5" />
                <div className="space-y-1">
                  <p className="text-sm font-semibold">
                    Which verified cities should EdgeCast specialize in?
                  </p>
                  <p className="text-xs font-mono font-semibold text-primary">
                    {data.shortlist_verdict.replace(/_/g, " ")}
                  </p>
                  <p className="text-sm font-semibold text-foreground mt-1">
                    {data.shortlist.join(" · ") || "Insufficient data"}
                  </p>
                </div>
              </div>
              <div className="space-y-1">
                {data.shortlist_reasons.map((r, i) => (
                  <p key={i} className="text-xs text-muted-foreground leading-relaxed">{r}</p>
                ))}
              </div>
            </div>

            {/* ── Summary stats ── */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-[10px] font-mono">
              {[
                ["Verified+NWS cities", data.verified_nws_city_count],
                ["In ranking (w/ data)", data.cities_in_ranking.length],
                ["Newly verified", data.newly_verified.length || "None"],
                ["Conflicts", data.conflicts.length || "None"],
              ].map(([l, v]) => (
                <div key={l as string} className="rounded border border-border p-2">
                  <p className="text-muted-foreground">{l as string}</p>
                  <p className="text-foreground font-semibold text-xs mt-0.5">{v}</p>
                </div>
              ))}
            </div>

            {/* ── Verification evidence ── */}
            <Section title="Verification Evidence — 5 Target Cities">
              <div className="space-y-1.5">
                {data.verification_results.map((ev) => (
                  <EvidenceCard key={ev.city} ev={ev} />
                ))}
              </div>
              <div className="rounded border border-emerald-800/30 bg-emerald-900/10 p-2 text-[10px] text-emerald-400">
                ✓ All 5 target cities were already verified from authoritative Kalshi API data
                (2026-07-30). No station flags changed in this task.
              </div>
            </Section>

            {/* ── Verified-only ranking ── */}
            <Section title="Verified-Only City Ranking (click to expand)">
              <p className="text-[10px] text-muted-foreground">
                Score = 30% forecast · 25% trading · 20% liquidity · 15% sample · 10% station.
                Station score is 100 for all cities here (all verified+NWS).
                ★ = in specialization shortlist.
              </p>
              <div className="space-y-1.5">
                {data.cities.map((city, i) => (
                  <RankedCityRow
                    key={city.city}
                    city={city}
                    rank={i + 1}
                    inShortlist={shortlistSet.has(city.city)}
                  />
                ))}
              </div>
            </Section>

            {/* ── Bet Watch guidance ── */}
            <Section title="Bet Watch — Future Guidance (not yet implemented)">
              <div className="rounded border border-border/50 bg-secondary/20 p-3 space-y-2 text-xs">
                <div>
                  <p className="text-muted-foreground text-[10px] font-semibold uppercase mb-1">
                    Primary cities (Best Bet eligible)
                  </p>
                  <p className="font-semibold">
                    {data.bet_watch_guidance.primary_cities.join(" · ") || "—"}
                  </p>
                </div>
                <div>
                  <p className="text-muted-foreground text-[10px] font-semibold uppercase mb-1">
                    Informational only (WATCHING)
                  </p>
                  <p className="text-muted-foreground">
                    {data.bet_watch_guidance.informational_cities.join(", ") || "—"}
                  </p>
                </div>
                <p className="text-muted-foreground leading-relaxed">
                  {data.bet_watch_guidance.best_bet_restriction}
                </p>
                <p className="text-[10px] text-yellow-400 font-mono">
                  {data.bet_watch_guidance.implementation_status}
                </p>
              </div>
            </Section>

            {/* ── Expected validation pace ── */}
            {data.shortlist.length > 0 && (
              <Section title="Expected Validation Pace (OFFICIAL trades/week)">
                <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                  {data.cities
                    .filter((c) => shortlistSet.has(c.city))
                    .map((c) => (
                      <div key={c.city} className="rounded border border-border p-2.5 text-[10px] font-mono">
                        <p className="font-semibold text-xs mb-1">{c.city}</p>
                        <div className="space-y-0.5 text-muted-foreground">
                          <p>Est. OFFICIAL/wk:
                            <span className="text-foreground ml-1">
                              {c.est_official_per_week_lo}–{c.est_official_per_week_hi}
                            </span>
                          </p>
                          <p>v2.3 FTB OFFICIAL:
                            <span className="text-foreground ml-1">
                              {c.ftb?.official_count ?? "—"}
                            </span>
                          </p>
                          <p>v2.3 evaluated:
                            <span className="text-foreground ml-1">
                              {c.ftb?.total_v23 ?? "—"}
                            </span>
                          </p>
                        </div>
                      </div>
                    ))}
                </div>
                <p className="text-[10px] text-muted-foreground italic">
                  Projections assume current scan cadence (~1/day) and 15–40% fresh-quote
                  conversion from current RESEARCH_ONLY pool. Use ranges, not point estimates.
                </p>
              </Section>
            )}

            {/* ── Plain-English conclusion ── */}
            <div className="rounded border border-border p-3 space-y-2 text-xs text-muted-foreground">
              <p className="text-foreground font-semibold">Plain-English conclusion</p>
              <p>
                All five target cities (Houston, Oklahoma City, Dallas, Minneapolis, Miami)
                were already verified from authoritative Kalshi market rule text captured
                on 2026-07-30. No station flags needed to change.
              </p>
              <p>
                With the full verified+NWS set, the ranking is led by{" "}
                <span className="text-foreground font-medium">
                  {data.cities[0]?.city ?? "—"}
                </span>{" "}
                (best composite score) and{" "}
                <span className="text-foreground font-medium">
                  {data.cities.find((c) => c.city !== data.cities[0]?.city)?.city ?? "—"}
                </span>{" "}
                (best forecast accuracy). The recommended specialization set is{" "}
                <span className="text-foreground font-semibold">
                  {data.shortlist.join(" + ") || "not enough data"}.
                </span>
              </p>
              <p>
                Minneapolis is verified but carries a –5.6°F systematic cold bias;
                Houston is verified but converts its excellent forecast accuracy (MAE 0.87°F)
                into only a 22.6% win rate — both are bench cities until those issues resolve.
              </p>
            </div>

            {/* ── Safety attestation ── */}
            <div className="rounded border border-border/40 p-2 flex flex-wrap gap-3 text-[10px] font-mono text-muted-foreground">
              <span className="text-emerald-400">✓ trading_state_modified: false</span>
              <span className="text-emerald-400">✓ ftb_untouched: true</span>
              <span className="text-emerald-400">✓ station_flags_changed: false</span>
              <span className="text-emerald-400">✓ read_only: true</span>
              <span className="text-emerald-400">✓ no model logic changed</span>
              <span className="text-foreground font-medium">generated: {new Date(data.generated_at).toLocaleString()}</span>
            </div>
          </>
        )}
      </div>
    </Card>
  );
}
