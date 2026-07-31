/**
 * Strategy Comparison
 * ====================
 * Unified dashboard comparing V2.1, V2.2, and V3 across shared markets.
 *
 * Sections:
 *   1. Smoke-test status banner
 *   2. Per-strategy summary cards (executable = official)
 *   3. Market-level comparison table with filters
 */
import { useState, ReactNode, useMemo } from "react";
import {
  useGetMultiStrategyComparison as useGetStrategyComparison,
  type StrategySummary,
  type StrategySection,
  type MarketComparisonRow,
  type TradeSlot,
  type ReadinessTracker,
  type PairingStats,
  type PreliminaryLeader,
  type StrategyRankEntry,
} from "@workspace/api-client-react";
import { AlertTriangle, CheckCircle, Info, Link2, Link2Off } from "lucide-react";

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

const pct  = (v: number | null | undefined, d = 1) => v == null ? "—" : `${v.toFixed(d)}%`;
const pp   = (v: number | null | undefined, d = 1) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(d)}pp`;
const money = (v: number | null | undefined) =>
  v == null ? "—" : `${v >= 0 ? "+" : "−"}$${Math.abs(v).toFixed(2)}`;
const num  = (v: number | null | undefined, d = 3) => v == null ? "—" : v.toFixed(d);
const int  = (v: number | null | undefined)        => v == null ? "—" : String(v);

// ---------------------------------------------------------------------------
// Layout primitives
// ---------------------------------------------------------------------------

function SectionCard({ title, subtitle, children, badge }: {
  title: string; subtitle?: string; children: ReactNode; badge?: ReactNode;
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

function TabBar({ tabs, active, onChange }: {
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

function Badge({ children, variant = "default" }: {
  children: ReactNode;
  variant?: "default" | "ok" | "warn" | "muted" | "blue";
}) {
  const cls = {
    default: "bg-secondary text-secondary-foreground",
    ok:      "bg-emerald-500/10 text-emerald-400 border border-emerald-500/30",
    warn:    "bg-amber-500/10  text-amber-400  border border-amber-500/30",
    muted:   "bg-muted text-muted-foreground",
    blue:    "bg-blue-500/10   text-blue-400   border border-blue-500/30",
  }[variant];
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${cls}`}>
      {children}
    </span>
  );
}

// ---------------------------------------------------------------------------
// 0. Preliminary Leader panel
// ---------------------------------------------------------------------------

const RANK_MEDAL = ["🥇", "🥈", "🥉"] as const;

const TIER_BADGE_VARIANT: Record<string, "ok" | "warn" | "blue" | "muted"> = {
  strong:       "ok",
  meaningful:   "ok",
  emerging:     "blue",
  preliminary:  "blue",
  very_early:   "warn",
  insufficient: "muted",
};

const STRATEGY_RANK_COLOR: Record<string, string> = {
  v21: "text-violet-300 border-violet-500/30 bg-violet-500/10",
  v22: "text-amber-300  border-amber-500/30  bg-amber-500/10",
  v3:  "text-cyan-300   border-cyan-500/30   bg-cyan-500/10",
};

function RankRow({ entry, isLeader }: { entry: StrategyRankEntry; isLeader: boolean }) {
  const medal   = RANK_MEDAL[entry.rank - 1] ?? `#${entry.rank}`;
  const color   = STRATEGY_RANK_COLOR[entry.strategy] ?? "";
  const hasData = entry.n > 0;

  return (
    <div className={`rounded-lg border px-4 py-3 ${
      isLeader ? "border-border bg-card" : "border-border/50 bg-muted/10"
    }`}>
      <div className="flex items-start gap-3">
        {/* Medal + strategy pill */}
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <span className="text-lg leading-none" aria-label={`Rank ${entry.rank}`}>{medal}</span>
          <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-bold ${color}`}>
            {entry.label}
          </span>
          {isLeader && entry.n >= 10 && (
            <span className="text-xs text-muted-foreground italic">current leader</span>
          )}
        </div>

        {/* Metric chips */}
        {hasData && (
          <div className="flex flex-wrap gap-2 justify-end text-[11px] font-mono">
            {entry.net_roi_pct != null && (
              <span className={`${entry.net_roi_pct >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                ROI {entry.net_roi_pct >= 0 ? "+" : ""}{entry.net_roi_pct.toFixed(1)}%
              </span>
            )}
            {entry.win_rate_pct != null && (
              <span className="text-muted-foreground">
                win {entry.win_rate_pct.toFixed(0)}%
              </span>
            )}
            {entry.brier_score != null && (
              <span className="text-muted-foreground">
                Brier {entry.brier_score.toFixed(3)}
              </span>
            )}
            {entry.city_consistency_pct != null && (
              <span className="text-muted-foreground">
                city {entry.city_consistency_pct.toFixed(0)}%
              </span>
            )}
          </div>
        )}
      </div>

      {/* Reasons */}
      {entry.reasons.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {entry.reasons.map((r) => (
            <span
              key={r}
              className="text-[10px] text-muted-foreground bg-muted/40 rounded px-1.5 py-0.5"
            >
              {r}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function PreliminaryLeaderPanel({ leader }: { leader: PreliminaryLeader }) {
  const { n_paired_settled_exec: n, confidence_tier, confidence_label,
          next_milestone, next_milestone_remaining, headline_reason,
          ranked, caveats } = leader;

  const tierVariant = TIER_BADGE_VARIANT[confidence_tier] ?? "muted";
  const hasData     = ranked != null && ranked.length > 0;

  // Progress toward next milestone (capped to show current progress)
  const progressTarget  = next_milestone ?? 500;
  const progressPct     = Math.min(100, (n / progressTarget) * 100);

  return (
    <SectionCard
      title="Preliminary Leader"
      subtitle="Ranked by net ROI, probability accuracy, win rate, and city consistency — strictly paired settled trades only."
      badge={<Badge variant={tierVariant}>{confidence_label}</Badge>}
    >
      <div className="p-4 space-y-4">

        {/* ── No data state ─────────────────────────────────────────── */}
        {!hasData && (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              No ranked strategies yet. Waiting for strictly paired, executable,
              settled trades shared across all three strategies.
            </p>

            {/* Progress bar toward first milestone */}
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>Strictly paired settled executable trades</span>
                <span className="font-mono">
                  {n} / {progressTarget}
                </span>
              </div>
              <div className="w-full bg-muted rounded-full h-2">
                <div
                  className="bg-sky-500 rounded-full h-2 transition-all"
                  style={{ width: `${Math.max(progressPct, n > 0 ? 2 : 0)}%` }}
                />
              </div>
              <p className="text-[10px] text-muted-foreground">
                {next_milestone_remaining} more needed to begin ranking
              </p>
            </div>
          </div>
        )}

        {/* ── Ranked state ──────────────────────────────────────────── */}
        {hasData && (
          <>
            {/* Headline */}
            {headline_reason && (
              <div className="rounded-md bg-muted/30 border border-border/50 px-3 py-2 text-xs text-muted-foreground">
                <span className="text-foreground font-medium">Why the leader is ahead: </span>
                {headline_reason}.
              </div>
            )}

            {/* Rank rows */}
            <div className="space-y-2">
              {ranked!.map((entry) => (
                <RankRow key={entry.strategy} entry={entry} isLeader={entry.rank === 1} />
              ))}
            </div>

            {/* Sample-size context */}
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>
                Based on{" "}
                <span className="font-mono font-medium text-foreground">{n}</span>
                {" "}strictly paired settled executable trades
              </span>
              {next_milestone && (
                <span>
                  Next review at{" "}
                  <span className="font-mono font-medium text-foreground">{next_milestone}</span>
                  {" "}({next_milestone_remaining} away)
                </span>
              )}
            </div>
          </>
        )}

        {/* ── Caveats (always visible) ──────────────────────────────── */}
        <div className="rounded-md border border-amber-500/20 bg-amber-500/5 px-3 py-2 space-y-0.5">
          {caveats.map((c) => (
            <p key={c} className="text-[11px] text-amber-300/80">
              {c}
            </p>
          ))}
          <p className="text-[11px] text-muted-foreground/60 pt-0.5">
            Ranking uses a composite score: 35% net ROI · 25% Brier score ·
            25% win rate · 15% city consistency.
          </p>
        </div>
      </div>
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// 1. Smoke-test status banner
// ---------------------------------------------------------------------------

function SmokeTestBanner({ data }: { data: ReturnType<typeof useGetStrategyComparison>["data"] }) {
  if (!data) return null;
  const { smoke_test, flags } = data;

  return (
    <div className={`rounded-lg border px-4 py-3 text-sm flex gap-3 ${
      smoke_test.v22_paper_trading_enabled
        ? "bg-amber-500/10 border-amber-500/30 text-amber-300"
        : "bg-blue-500/10  border-blue-500/30  text-blue-300"
    }`}>
      <Info className="h-4 w-4 shrink-0 mt-0.5" />
      <div className="space-y-1">
        <p className="font-medium">
          {smoke_test.v22_paper_trading_enabled
            ? "V2.2 paper trading ENABLED"
            : "V2.2 prediction-only smoke test in progress"}
        </p>
        <p className="text-xs opacity-80">{smoke_test.note}</p>
        <div className="flex flex-wrap gap-2 pt-1">
          <Badge variant={flags.v22.predictions_enabled   ? "ok" : "muted"}>
            {flags.v22.predictions_enabled ? "✓" : "✗"} V2.2 predictions
          </Badge>
          <Badge variant={flags.v22.paper_trading_enabled ? "warn" : "muted"}>
            {flags.v22.paper_trading_enabled ? "✓" : "✗"} V2.2 paper trading
          </Badge>
          <Badge variant={flags.v3.predictions_enabled    ? "ok" : "muted"}>
            {flags.v3.predictions_enabled ? "✓" : "✗"} V3 predictions
          </Badge>
          <Badge variant={flags.v3.paper_trading_enabled  ? "ok" : "muted"}>
            {flags.v3.paper_trading_enabled ? "✓" : "✗"} V3 paper trading
          </Badge>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 2. Per-strategy summary cards
// ---------------------------------------------------------------------------

const STRATEGY_META = {
  v21: {
    label:       "V2.1",
    sublabel:    "Original learning version",
    color:       "border-violet-500/40",
    headerColor: "bg-violet-500/10",
    textColor:   "text-violet-300",
  },
  v22: {
    label:       "V2.2",
    sublabel:    "Corrected-bias version",
    color:       "border-amber-500/40",
    headerColor: "bg-amber-500/10",
    textColor:   "text-amber-300",
  },
  v3: {
    label:       "V3",
    sublabel:    "Historical-preload version",
    color:       "border-cyan-500/40",
    headerColor: "bg-cyan-500/10",
    textColor:   "text-cyan-300",
  },
} as const;

type StrategyKey = keyof typeof STRATEGY_META;

function StatRow({
  label, value, dim, highlight,
}: {
  label: string; value: string; dim?: boolean; highlight?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-2 py-1.5 border-b border-border/40 last:border-0">
      <span className={`text-xs truncate ${highlight ? "text-foreground font-semibold" : "text-muted-foreground"}`}>
        {label}
      </span>
      <span className={`text-xs font-mono font-medium ${dim ? "text-muted-foreground" : highlight ? "text-foreground" : ""}`}>
        {value}
      </span>
    </div>
  );
}

function SectionBlock({ sec, title }: { sec: StrategySection; title: string }) {
  const hasSettled = sec.settled > 0;
  const hasOpen    = sec.open > 0;
  return (
    <div className="space-y-0">
      <p className="text-xs text-muted-foreground font-semibold px-4 pt-3 pb-1 uppercase tracking-wider">
        {title}
        {sec.is_official && (
          <span className="ml-2 text-emerald-400 normal-case tracking-normal font-normal">
            ← official metrics
          </span>
        )}
        {!sec.is_official && sec.count > 0 && (
          <span className="ml-2 text-muted-foreground normal-case tracking-normal font-normal">
            (excluded from ROI)
          </span>
        )}
      </p>
      <div className="px-4 pb-1">
        <StatRow label="Predictions" value={int(sec.count)} />
        <StatRow label="Open"        value={int(sec.open)}  />
        <StatRow label="Settled"     value={int(sec.settled)} />
        <StatRow label="Wins / Losses" value={`${sec.wins} / ${sec.losses}`} />
        <StatRow label="Win rate"    value={sec.win_rate_pct != null ? pct(sec.win_rate_pct) : "—"} dim={!hasSettled} />
      </div>

      {/* ── Settled P/L block ─────────────────────────────────────────────── */}
      <p className="text-[10px] text-muted-foreground/60 font-medium px-4 pt-2 pb-0.5 uppercase tracking-wider">
        Settled P/L
      </p>
      <div className="px-4 pb-1">
        <StatRow label="Settled stake"    value={hasSettled ? `$${sec.settled_stake.toFixed(2)}` : "—"} dim={!hasSettled} />
        <StatRow label="Gross P/L"        value={hasSettled ? money(sec.gross_pl) : "—"} dim={!hasSettled} />
        <StatRow label="Est. fees (settled)" value={hasSettled && sec.estimated_fees > 0 ? `−$${sec.estimated_fees.toFixed(4)}` : "—"} dim />
        <StatRow label="Net P/L"          value={hasSettled ? money(sec.net_pl) : "—"} dim={!hasSettled} />
        <StatRow label="Gross ROI"        value={sec.gross_roi_pct != null ? pct(sec.gross_roi_pct) : "—"} dim={!hasSettled} />
        <StatRow label="Net ROI"          value={sec.net_roi_pct != null ? pct(sec.net_roi_pct) : "—"} dim={!hasSettled} highlight={hasSettled} />
        {sec.is_official && (
          <StatRow label="Brier score" value={sec.brier_score != null ? num(sec.brier_score) : "—"} dim={!hasSettled} />
        )}
      </div>

      {/* ── Open capital block (informational) ────────────────────────────── */}
      <p className="text-[10px] text-muted-foreground/60 font-medium px-4 pt-2 pb-0.5 uppercase tracking-wider">
        Open capital
      </p>
      <div className="px-4 pb-3">
        <StatRow label="Open stake"       value={hasOpen ? `$${sec.open_stake.toFixed(2)}` : "—"} dim={!hasOpen} />
        <StatRow
          label="Est. fees (open)"
          value={hasOpen && sec.open_fees > 0 ? `~$${sec.open_fees.toFixed(4)}` : "—"}
          dim
        />
        <StatRow label="Avg edge"  value={sec.avg_edge_pp != null ? pp(sec.avg_edge_pp) : "—"} />
        <StatRow label="Avg sigma" value={sec.avg_sigma != null ? `${sec.avg_sigma.toFixed(3)}°F` : "—"} />
      </div>
    </div>
  );
}

function StrategyCard({ k, summary }: { k: StrategyKey; summary: StrategySummary }) {
  const meta = STRATEGY_META[k];
  return (
    <div className={`rounded-lg border ${meta.color} bg-card flex flex-col`}>
      <div className={`px-4 py-3 rounded-t-lg ${meta.headerColor} border-b ${meta.color}`}>
        <div className="flex items-center gap-2">
          <span className={`text-sm font-bold ${meta.textColor}`}>{meta.label}</span>
          <span className="text-xs text-muted-foreground">{meta.sublabel}</span>
        </div>
        <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
          {summary.description}
        </p>
      </div>
      <div className="flex-1 divide-y divide-border/40">
        <SectionBlock sec={summary.executable}   title="Executable (official)" />
        <SectionBlock sec={summary.non_executable} title="Non-executable (signal research)" />
        {summary.excluded_count > 0 && (
          <div className="px-4 py-2">
            <p className="text-xs text-muted-foreground">
              {summary.excluded_count} V2_EXCLUDED (research log only)
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function StrategyCards({ data }: { data: ReturnType<typeof useGetStrategyComparison>["data"] }) {
  if (!data) return null;
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      {(["v21", "v22", "v3"] as StrategyKey[]).map((k) => (
        <StrategyCard key={k} k={k} summary={data.strategies[k]} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 3. Market-level comparison table
// ---------------------------------------------------------------------------

type FilterMode =
  | "all"
  | "shared"
  | "v21-only"
  | "v22-only"
  | "v3-only"
  | "agreed"
  | "disagreed"
  | "settled"
  | "open"
  | "executable"
  | "non-executable"
  | "traded-all"
  | "paired"
  | "unpaired";

const FILTER_TABS: { id: FilterMode; label: string }[] = [
  { id: "all",            label: "All markets" },
  { id: "shared",         label: "Shared (≥2 strategies)" },
  { id: "paired",         label: "Strictly paired" },
  { id: "unpaired",       label: "Timing-mismatched" },
  { id: "agreed",         label: "Versions agreed" },
  { id: "disagreed",      label: "Versions disagreed" },
  { id: "traded-all",     label: "All-3 traded" },
  { id: "v21-only",       label: "V2.1 only" },
  { id: "v22-only",       label: "V2.2 only" },
  { id: "v3-only",        label: "V3 only" },
  { id: "settled",        label: "Settled" },
  { id: "open",           label: "Open" },
  { id: "executable",     label: "Executable" },
  { id: "non-executable", label: "Non-executable" },
];

function isSettled(row: MarketComparisonRow) {
  return (
    row.v21?.status === "SETTLED" ||
    row.v22?.status === "SETTLED" ||
    row.v3?.status  === "SETTLED"
  );
}
function isOpen(row: MarketComparisonRow) {
  return (
    row.v21?.status === "OPEN" ||
    row.v22?.status === "OPEN" ||
    row.v3?.status  === "OPEN"
  );
}
function isExecutable(row: MarketComparisonRow) {
  return (
    row.v21?.is_executable === true ||
    row.v22?.is_executable === true ||
    row.v3?.is_executable  === true
  );
}

function applyFilter(rows: MarketComparisonRow[], filter: FilterMode, city: string): MarketComparisonRow[] {
  let r = rows;
  if (city !== "all") r = r.filter((row) => row.city === city);
  switch (filter) {
    case "shared":         return r.filter((row) => row.versions_present.length >= 2);
    case "traded-all":     return r.filter((row) => row.v21 && row.v22 && row.v3);
    case "agreed":         return r.filter((row) => row.versions_present.length >= 2 && row.versions_agreed);
    case "disagreed":      return r.filter((row) => row.versions_present.length >= 2 && !row.versions_agreed);
    case "v21-only":       return r.filter((row) => row.v21 && !row.v22 && !row.v3);
    case "v22-only":       return r.filter((row) => !row.v21 && row.v22 && !row.v3);
    case "v3-only":        return r.filter((row) => !row.v21 && !row.v22 && row.v3);
    case "settled":        return r.filter(isSettled);
    case "open":           return r.filter(isOpen);
    case "executable":     return r.filter(isExecutable);
    case "non-executable": return r.filter((row) => !isExecutable(row));
    case "paired":         return r.filter((row) => row.is_paired);
    case "unpaired":       return r.filter((row) => row.versions_present.length >= 2 && !row.is_paired);
    default:               return r;
  }
}

function ProbCell({ slot, mktProb }: { slot?: TradeSlot | null; mktProb: number | null }) {
  if (!slot) return <td className="px-3 py-2 text-center text-muted-foreground/30 text-xs">—</td>;
  const edge = slot.edge_pp;
  const edgeColor =
    edge == null ? "" :
    edge >= 3 ? "text-emerald-400" :
    edge >= 1 ? "text-sky-400" :
    "text-muted-foreground";

  return (
    <td className="px-3 py-2 text-xs font-mono whitespace-nowrap">
      <div className="flex flex-col gap-0.5">
        <span>{slot.ec_prob != null ? pct(slot.ec_prob * 100, 1) : "—"}</span>
        <span className={`text-[10px] ${edgeColor}`}>
          {edge != null ? pp(edge) : ""}
        </span>
      </div>
    </td>
  );
}

function DecisionCell({ slot }: { slot?: TradeSlot | null }) {
  if (!slot) return <td className="px-3 py-2 text-center text-muted-foreground/30 text-xs">—</td>;
  const exec = slot.is_executable;
  const status = slot.status;
  const outcome = slot.outcome;

  const label =
    status === "SETTLED" ? outcome ?? status :
    status === "V2_EXCLUDED" ? "excluded" :
    "OPEN";

  const color =
    outcome === "WIN"  ? "text-emerald-400" :
    outcome === "LOSS" ? "text-red-400" :
    status  === "V2_EXCLUDED" ? "text-muted-foreground" :
    "text-foreground";

  return (
    <td className="px-3 py-2 text-xs">
      <div className="flex flex-col gap-0.5">
        <span className={color}>{label}</span>
        {exec === false && (
          <span className="text-[10px] text-muted-foreground">non-exec</span>
        )}
        {slot.profit_loss != null && (
          <span className={`text-[10px] font-mono ${slot.profit_loss >= 0 ? "text-emerald-400" : "text-red-400"}`}>
            {money(slot.profit_loss)}
          </span>
        )}
      </div>
    </td>
  );
}

function SigmaBiasCell({ slot }: { slot?: TradeSlot | null }) {
  if (!slot) return <td className="px-3 py-2 text-center text-muted-foreground/30 text-xs">—</td>;
  return (
    <td className="px-3 py-2 text-xs font-mono">
      <div className="flex flex-col gap-0.5">
        <span>{slot.sigma != null ? `σ=${slot.sigma.toFixed(2)}°` : "—"}</span>
        <span className="text-[10px] text-muted-foreground">
          {slot.bias != null && slot.bias !== 0 ? `b=${slot.bias > 0 ? "+" : ""}${slot.bias.toFixed(2)}°` :
           slot.bias === 0 ? "b=0" : ""}
        </span>
      </div>
    </td>
  );
}

function DeltaCell({ value, invert }: { value?: number | null; invert?: boolean }) {
  if (value == null) return <td className="px-3 py-2 text-center text-muted-foreground/30 text-xs">—</td>;
  const sign = value > 0 ? "+" : "";
  const abs  = Math.abs(value);
  const color =
    abs < 0.1  ? "text-muted-foreground" :
    value > 0  ? (invert ? "text-red-400" : "text-emerald-400") :
                 (invert ? "text-emerald-400" : "text-red-400");
  return (
    <td className={`px-3 py-2 text-xs font-mono text-center ${color}`}>
      {value === 0 ? "0pp" : `${sign}${value.toFixed(2)}pp`}
    </td>
  );
}

function MarketTable({
  rows,
  filter,
  city,
}: {
  rows: MarketComparisonRow[];
  filter: FilterMode;
  city: string;
}) {
  const filtered = useMemo(() => applyFilter(rows, filter, city), [rows, filter, city]);

  if (filtered.length === 0) {
    return (
      <p className="text-sm text-muted-foreground px-4 py-8 text-center">
        No markets match this filter.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border bg-muted/30">
            <th className="px-3 py-2 text-left font-semibold text-muted-foreground">Ticker</th>
            <th className="px-3 py-2 text-left font-semibold text-muted-foreground">City / Contract</th>
            <th className="px-3 py-2 text-left font-semibold text-muted-foreground">Mkt prob</th>
            {/* V2.1 */}
            <th className="px-3 py-2 text-left font-semibold text-violet-400">V2.1 prob / edge</th>
            <th className="px-3 py-2 text-left font-semibold text-violet-400">V2.1 σ / bias</th>
            <th className="px-3 py-2 text-left font-semibold text-violet-400">V2.1 result</th>
            {/* V2.2 */}
            <th className="px-3 py-2 text-left font-semibold text-amber-400">V2.2 prob / edge</th>
            <th className="px-3 py-2 text-left font-semibold text-amber-400">V2.2 σ / bias</th>
            <th className="px-3 py-2 text-left font-semibold text-amber-400">V2.2 result</th>
            {/* Delta */}
            <th className="px-3 py-2 text-center font-semibold text-muted-foreground">V2.2−V2.1</th>
            {/* V3 */}
            <th className="px-3 py-2 text-left font-semibold text-cyan-400">V3 prob / edge</th>
            <th className="px-3 py-2 text-left font-semibold text-cyan-400">V3 σ / bias</th>
            <th className="px-3 py-2 text-left font-semibold text-cyan-400">V3 result</th>
            {/* Delta */}
            <th className="px-3 py-2 text-center font-semibold text-muted-foreground">V3−V2.1</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/40">
          {filtered.map((row) => (
            <tr
              key={row.ticker}
              className="hover:bg-muted/10 transition-colors"
            >
              <td className="px-3 py-2 font-mono text-[10px] text-muted-foreground max-w-[160px]">
                <div className="truncate" title={row.ticker}>{row.ticker}</div>
                <div className="flex flex-wrap gap-0.5 mt-0.5">
                  {row.versions_present.map((v) => (
                    <span
                      key={v}
                      className={`inline-block px-1 rounded text-[9px] font-medium ${
                        v === "v2.1" ? "bg-violet-500/20 text-violet-300" :
                        v === "v2.2" ? "bg-amber-500/20 text-amber-300" :
                        "bg-cyan-500/20 text-cyan-300"
                      }`}
                    >
                      {v}
                    </span>
                  ))}
                  {row.versions_present.length >= 2 && (
                    <span className={`inline-block px-1 rounded text-[9px] ${
                      row.versions_agreed
                        ? "bg-emerald-500/10 text-emerald-400"
                        : "bg-amber-500/10 text-amber-400"
                    }`}>
                      {row.versions_agreed ? "agreed" : "disagreed"}
                    </span>
                  )}
                  {row.versions_present.length === 3 && (
                    <span className={`inline-block px-1 rounded text-[9px] ${
                      row.is_paired
                        ? "bg-sky-500/10 text-sky-400"
                        : "bg-muted text-muted-foreground"
                    }`} title={row.is_paired ? "All 3 strategies used identical frozen inputs (same collection cycle)" : "Timing mismatch — inputs may differ across strategies"}>
                      {row.is_paired ? "⊕ paired" : "~ async"}
                    </span>
                  )}
                </div>
              </td>

              <td className="px-3 py-2 text-xs">
                <div className="font-medium truncate">{row.city ?? "—"}</div>
                <div className="text-muted-foreground text-[10px]">
                  {row.weather_variable} · {row.contract_type ?? "—"}
                </div>
              </td>

              <td className="px-3 py-2 text-xs font-mono">
                {row.market_prob != null ? pct(row.market_prob * 100, 1) : "—"}
              </td>

              <ProbCell    slot={row.v21} mktProb={row.market_prob} />
              <SigmaBiasCell slot={row.v21} />
              <DecisionCell  slot={row.v21} />

              <ProbCell    slot={row.v22} mktProb={row.market_prob} />
              <SigmaBiasCell slot={row.v22} />
              <DecisionCell  slot={row.v22} />

              <DeltaCell value={row.v21_v22_delta_pp} />

              <ProbCell    slot={row.v3} mktProb={row.market_prob} />
              <SigmaBiasCell slot={row.v3} />
              <DecisionCell  slot={row.v3} />

              <DeltaCell value={row.v21_v3_delta_pp} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// City filter
// ---------------------------------------------------------------------------

function CityFilter({ rows, value, onChange }: {
  rows: MarketComparisonRow[];
  value: string;
  onChange: (c: string) => void;
}) {
  const cities = useMemo(() => {
    const s = new Set<string>();
    rows.forEach((r) => { if (r.city) s.add(r.city); });
    return Array.from(s).sort();
  }, [rows]);

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-muted-foreground">City:</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="text-xs bg-background border border-border rounded px-2 py-1"
      >
        <option value="all">All cities</option>
        {cities.map((c) => (
          <option key={c} value={c}>{c}</option>
        ))}
      </select>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Readiness tracker
// ---------------------------------------------------------------------------

function ReadinessTrackerPanel({ tracker, pairing }: {
  tracker: ReadinessTracker;
  pairing: PairingStats;
}) {
  const { shared_settled_executable, milestones } = tracker;

  return (
    <div className="space-y-3">
      {/* Pairing stat row */}
      <div className="flex flex-wrap gap-3">
        <div className="flex items-center gap-1.5 text-xs">
          <Link2 className="h-3.5 w-3.5 text-sky-400" />
          <span className="text-foreground font-medium">{pairing.strictly_paired}</span>
          <span className="text-muted-foreground">strictly paired</span>
        </div>
        <div className="flex items-center gap-1.5 text-xs">
          <Link2Off className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="text-foreground font-medium">{pairing.timing_mismatched}</span>
          <span className="text-muted-foreground">timing-mismatched</span>
        </div>
        <p className="text-xs text-muted-foreground ml-auto italic">{pairing.note}</p>
      </div>

      {/* Progress toward milestones */}
      <div className="space-y-2">
        <p className="text-xs text-muted-foreground">
          Shared settled executable trades:{" "}
          <span className="text-foreground font-mono font-medium">{shared_settled_executable}</span>
        </p>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          {milestones.map((m) => (
            <div
              key={m.target}
              className={`rounded-md border px-3 py-2 ${
                m.reached
                  ? "border-emerald-500/40 bg-emerald-500/10"
                  : "border-border bg-muted/20"
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className={`text-xs font-semibold ${m.reached ? "text-emerald-400" : "text-muted-foreground"}`}>
                  {m.target} trades
                </span>
                {m.reached && <CheckCircle className="h-3 w-3 text-emerald-400" />}
              </div>
              {m.reached ? (
                <p className="text-[10px] text-emerald-400">✓ reached</p>
              ) : (
                <>
                  <div className="w-full bg-muted rounded-full h-1.5 mb-1">
                    <div
                      className="bg-sky-500 rounded-full h-1.5 transition-all"
                      style={{ width: `${Math.min(100, m.pct)}%` }}
                    />
                  </div>
                  <p className="text-[10px] text-muted-foreground">
                    {m.remaining} remaining ({m.pct.toFixed(0)}%)
                  </p>
                </>
              )}
            </div>
          ))}
        </div>
      </div>
      <p className="text-xs text-muted-foreground italic">{tracker.note}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Identical-probability explanation
// ---------------------------------------------------------------------------

function BiasExplanationBox() {
  return (
    <div className="rounded-lg border border-border bg-muted/20 px-4 py-3 text-xs text-muted-foreground space-y-1">
      <p className="font-semibold text-foreground">
        Why are V2.1 and V2.2 probabilities identical?
      </p>
      <p>
        V2.2's bias correction activates only when a city/variable bucket crosses
        <strong className="text-foreground"> MIN_SAMPLE = 30 observations</strong> in{" "}
        <code className="bg-muted px-1 rounded">forecast_error_stats</code>.
        The current maximum is ~17 — the correction has never fired for any row.
      </p>
      <p>
        V2.1 formula: <code className="bg-muted px-1 rounded">μ = forecast − mean_error</code>&nbsp;
        (inverted — preserved by design).
        <br />
        V2.2 formula: <code className="bg-muted px-1 rounded">μ = forecast + mean_error</code>&nbsp;
        (corrected sign).
      </p>
      <p>
        When both formulas receive <code className="bg-muted px-1 rounded">mean_error = 0</code>,
        they produce identical probabilities and <strong className="text-foreground">prob_delta_pp = 0</strong>&nbsp;
        across all markets. <em>This is expected, not an error.</em>
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sample calculation table
// ---------------------------------------------------------------------------

function SampleCalculations({ rows }: { rows: MarketComparisonRow[] }) {
  const samples = rows
    .filter((r) => r.v21 && r.v3)
    .slice(0, 6);

  if (samples.length === 0) {
    return (
      <p className="text-sm text-muted-foreground px-4 py-4">
        No shared V2.1 + V3 markets yet.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border bg-muted/30">
            <th className="px-3 py-2 text-left font-semibold text-muted-foreground">City</th>
            <th className="px-3 py-2 text-left font-semibold text-muted-foreground">Variable</th>
            <th className="px-3 py-2 text-left font-semibold text-muted-foreground">Market</th>
            <th className="px-3 py-2 text-right font-semibold text-violet-400">V2.1</th>
            <th className="px-3 py-2 text-right font-semibold text-amber-400">V2.2</th>
            <th className="px-3 py-2 text-center font-semibold text-muted-foreground">Δpp</th>
            <th className="px-3 py-2 text-right font-semibold text-cyan-400">V3</th>
            <th className="px-3 py-2 text-left font-semibold text-muted-foreground">V2.1 σ</th>
            <th className="px-3 py-2 text-left font-semibold text-muted-foreground">V3 σ</th>
            <th className="px-3 py-2 text-left font-semibold text-muted-foreground">V2.1 bias</th>
            <th className="px-3 py-2 text-left font-semibold text-muted-foreground">V3 bias</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/40">
          {samples.map((row) => (
            <tr key={row.ticker} className="hover:bg-muted/10">
              <td className="px-3 py-2">{row.city}</td>
              <td className="px-3 py-2">{row.weather_variable}</td>
              <td className="px-3 py-2 font-mono text-muted-foreground text-[10px] max-w-[100px] truncate">
                {row.market_prob != null ? pct(row.market_prob * 100, 1) : "—"}
              </td>
              <td className="px-3 py-2 text-right font-mono text-violet-300">
                {row.v21?.ec_prob != null ? pct(row.v21.ec_prob * 100, 1) : "—"}
              </td>
              <td className="px-3 py-2 text-right font-mono text-amber-300">
                {row.v22?.ec_prob != null ? pct(row.v22.ec_prob * 100, 1) :
                 <span className="text-muted-foreground">=V2.1</span>}
              </td>
              <td className="px-3 py-2 text-center font-mono text-muted-foreground">
                {row.v21_v22_delta_pp != null
                  ? (row.v21_v22_delta_pp === 0 ? "0pp" : pp(row.v21_v22_delta_pp))
                  : "—"}
              </td>
              <td className="px-3 py-2 text-right font-mono text-cyan-300">
                {row.v3?.ec_prob != null ? pct(row.v3.ec_prob * 100, 1) : "—"}
              </td>
              <td className="px-3 py-2 font-mono text-muted-foreground">
                {row.v21?.sigma != null ? `${row.v21.sigma.toFixed(2)}°F` : "—"}
              </td>
              <td className="px-3 py-2 font-mono text-muted-foreground">
                {row.v3?.sigma != null ? `${row.v3.sigma.toFixed(2)}°F` : "—"}
              </td>
              <td className="px-3 py-2 font-mono text-muted-foreground">
                {row.v21?.bias != null ? `${row.v21.bias > 0 ? "+" : ""}${row.v21.bias.toFixed(4)}°F` : "0°F"}
              </td>
              <td className="px-3 py-2 font-mono text-muted-foreground">
                {row.v3?.bias != null ? `${row.v3.bias > 0 ? "+" : ""}${row.v3.bias.toFixed(4)}°F` : "0°F"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function StrategyComparisonPage() {
  const { data, isLoading, error } = useGetStrategyComparison();
  const [tab, setTab] = useState<FilterMode>("all");
  const [city, setCity] = useState("all");

  if (isLoading) {
    return (
      <div className="p-6 space-y-4">
        <h1 className="text-2xl font-bold tracking-tight">Strategy Comparison</h1>
        <Loading />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="p-6 space-y-4">
        <h1 className="text-2xl font-bold tracking-tight">Strategy Comparison</h1>
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 flex gap-2 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
          {error instanceof Error ? error.message : "Failed to load comparison data."}
        </div>
      </div>
    );
  }

  const { shared_count, total_markets, market_rows } = data;

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Strategy Comparison</h1>
          <p className="text-sm text-muted-foreground mt-1">
            V2.1 · V2.2 · V3 — side-by-side performance and market-level signals.
            Official metrics use <strong>executable</strong> trades only.
          </p>
        </div>
        <div className="flex items-center gap-3 text-xs text-muted-foreground flex-wrap">
          <span>{total_markets} total markets</span>
          <span>·</span>
          <span>{shared_count} shared across ≥2 strategies</span>
        </div>
      </div>

      {/* Smoke-test banner */}
      <SmokeTestBanner data={data} />

      {/* Preliminary leader */}
      {data.preliminary_leader && (
        <PreliminaryLeaderPanel leader={data.preliminary_leader} />
      )}

      {/* Strategy summary cards */}
      <SectionCard
        title="Performance Summary"
        subtitle="Official ROI, win rate, and Brier score are executable trades only."
      >
        <div className="p-4">
          <StrategyCards data={data} />
        </div>
      </SectionCard>

      {/* Readiness tracker */}
      {data.readiness_tracker && data.pairing_stats && (
        <SectionCard
          title="Comparison Readiness"
          subtitle="Progress toward sufficient shared settled executable trades for paired model-vs-model analysis."
        >
          <div className="p-4">
            <ReadinessTrackerPanel
              tracker={data.readiness_tracker}
              pairing={data.pairing_stats}
            />
          </div>
        </SectionCard>
      )}

      {/* V2.1 vs V2.2 identical probability explanation */}
      <SectionCard
        title="V2.1 vs V2.2: Identical Probabilities (Expected)"
        subtitle="The bias correction is inactive until MIN_SAMPLE = 30 is met."
      >
        <div className="p-4 space-y-4">
          <BiasExplanationBox />
          <SampleCalculations rows={market_rows} />
        </div>
      </SectionCard>

      {/* Market-level table */}
      <SectionCard
        title="Market-Level Comparison"
        subtitle="All markets across all strategies. Use filters and city selector to focus."
        badge={
          <Badge variant="muted">{total_markets} markets</Badge>
        }
      >
        {/* Filters row */}
        <div className="px-4 pt-3 pb-2 flex flex-wrap items-center gap-3 border-b border-border">
          <CityFilter rows={market_rows} value={city} onChange={setCity} />
        </div>
        <div className="overflow-x-auto">
          <TabBar
            tabs={FILTER_TABS}
            active={tab}
            onChange={(id) => setTab(id as FilterMode)}
          />
        </div>
        <MarketTable rows={market_rows} filter={tab} city={city} />
        <div className="px-4 py-2 border-t border-border text-xs text-muted-foreground">
          prob = EC yes-side probability · edge = EC − market · σ = sigma used · b = bias correction
          · <span className="text-emerald-400">green edge ≥ 3pp</span>
          · <span className="text-sky-400">blue edge ≥ 1pp</span>
        </div>
      </SectionCard>
    </div>
  );
}
