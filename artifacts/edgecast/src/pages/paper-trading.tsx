import { useState } from "react";
import { Link } from "wouter";
import { TrendingUp, AlertTriangle, ExternalLink, ChevronDown, ChevronUp } from "lucide-react";
import {
  useListPaperTrades,
  useGetPaperTradeMetrics,
  useGetPaperTradeSettings,
  useUpdatePaperTradeSettings,
  getListPaperTradesQueryKey,
  getGetPaperTradeMetricsQueryKey,
  type ListPaperTradesParams,
  type PaperTrade,
  type PaperTradeSummary,
  type PaperTradeSettings,
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

// ── Helpers ──────────────────────────────────────────────────────────────────

function pct(n: number | null | undefined): string {
  if (n == null) return "—";
  return `${(n * 100).toFixed(1)}%`;
}

function pp(n: number | null | undefined, prefix = "+"): string {
  if (n == null) return "—";
  return `${n >= 0 ? prefix : ""}${n.toFixed(1)}pp`;
}

function usd(n: number | null | undefined): string {
  if (n == null) return "—";
  const sign = n >= 0 ? "+" : "-";
  return `${sign}$${Math.abs(n).toFixed(2)}`;
}

function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  return s.slice(0, 10);
}

function ConfidenceBadge({ label }: { label: string | null | undefined }) {
  if (!label) return <span className="text-muted-foreground text-xs">—</span>;
  const variant =
    label === "Very High" ? "outline" :
    label === "High"      ? "outline" :
    label === "Medium"    ? "outline" : "outline";
  const color =
    label === "Very High" ? "text-emerald-400 border-emerald-500/40" :
    label === "High"      ? "text-sky-400 border-sky-500/40" :
    label === "Medium"    ? "text-amber-400 border-amber-500/40" :
                            "text-rose-400 border-rose-500/40";
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded border font-medium ${color}`}>
      {label}
    </span>
  );
}

function DirectionBadge({ direction }: { direction: "YES" | "NO" }) {
  return (
    <span className={`text-xs px-2 py-0.5 rounded border font-bold font-mono ${
      direction === "YES"
        ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
        : "bg-rose-500/10 text-rose-400 border-rose-500/30"
    }`}>
      {direction}
    </span>
  );
}

function OutcomeBadge({ outcome }: { outcome: string | null | undefined }) {
  if (!outcome) return <span className="text-xs text-muted-foreground">—</span>;
  const color =
    outcome === "WIN"  ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30" :
    outcome === "LOSS" ? "bg-rose-500/10 text-rose-400 border-rose-500/30" :
                         "bg-muted/50 text-muted-foreground border-border";
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded border font-bold font-mono ${color}`}>
      {outcome}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  const color =
    status === "OPEN"    ? "bg-sky-500/10 text-sky-400 border-sky-500/30" :
    status === "SETTLED" ? "bg-muted/50 text-muted-foreground border-border" :
    status === "VOID"    ? "bg-muted/50 text-muted-foreground border-border" :
                           "bg-destructive/10 text-destructive border-destructive/30";
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded border font-medium ${color}`}>
      {status}
    </span>
  );
}

// ── Summary cards ─────────────────────────────────────────────────────────────

function MetricCard({
  label, value, sub, accent,
}: {
  label: string; value: string; sub?: string;
  accent?: "green" | "red" | "neutral";
}) {
  const valueColor =
    accent === "green" ? "text-emerald-400" :
    accent === "red"   ? "text-rose-400" :
    "text-foreground";
  return (
    <Card className="border-border">
      <CardContent className="pt-4 pb-4 px-4">
        <p className="text-xs text-muted-foreground uppercase tracking-wide">{label}</p>
        <p className={`text-2xl font-bold font-mono mt-1 ${valueColor}`}>{value}</p>
        {sub && <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>}
      </CardContent>
    </Card>
  );
}

// ── Settings panel ────────────────────────────────────────────────────────────

function SettingsPanel() {
  const queryClient = useQueryClient();
  const { data: settings } = useGetPaperTradeSettings();
  const mutation = useUpdatePaperTradeSettings();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});

  if (!settings) return <Skeleton className="h-32 w-full" />;

  const startEdit = () => {
    setForm({
      min_edge_pct: String(settings.min_edge_pct ?? "10"),
      min_confidence: String(settings.min_confidence ?? "High"),
      stake: String(settings.stake ?? "10"),
      strategy_version: String(settings.strategy_version ?? "v1.0"),
      enabled: String(settings.enabled ?? "true"),
    });
    setEditing(true);
  };

  const save = () => {
    mutation.mutate(
      {
        data: {
          min_edge_pct: parseFloat(form.min_edge_pct),
          min_confidence: form.min_confidence,
          stake: parseFloat(form.stake),
          strategy_version: form.strategy_version,
          enabled: form.enabled === "true",
        },
      },
      {
        onSuccess: () => {
          queryClient.invalidateQueries({ queryKey: getListPaperTradesQueryKey() });
          queryClient.invalidateQueries({ queryKey: getGetPaperTradeMetricsQueryKey() });
          setEditing(false);
        },
      }
    );
  };

  return (
    <Card className="border-border">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm">Strategy Settings</CardTitle>
          {!editing ? (
            <button onClick={startEdit} className="text-xs text-primary hover:underline">
              Edit
            </button>
          ) : (
            <div className="flex gap-3">
              <button onClick={() => setEditing(false)} className="text-xs text-muted-foreground hover:underline">
                Cancel
              </button>
              <button
                onClick={save}
                disabled={mutation.isPending}
                className="text-xs text-primary hover:underline disabled:opacity-50"
              >
                {mutation.isPending ? "Saving…" : "Save"}
              </button>
            </div>
          )}
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        {!editing ? (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-8 gap-y-1 text-xs">
            {[
              ["Enabled",          String(settings.enabled)],
              ["Min Edge",         `${settings.min_edge_pct}pp`],
              ["Min Confidence",   String(settings.min_confidence)],
              ["Stake per Trade",  `$${settings.stake}`],
              ["Strategy Version", String(settings.strategy_version)],
            ].map(([k, v]) => (
              <div key={k} className="flex justify-between border-b border-border/40 py-1.5">
                <span className="text-muted-foreground">{k}</span>
                <span className="font-mono font-medium">{v}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
            {[
              { key: "enabled", label: "Enabled", type: "select", opts: ["true", "false"] },
              { key: "min_edge_pct", label: "Min Edge (pp)", type: "number" },
              { key: "min_confidence", label: "Min Confidence", type: "select",
                opts: ["Very High", "High", "Medium", "Low", "Very Low"] },
              { key: "stake", label: "Stake per Trade ($)", type: "number" },
              { key: "strategy_version", label: "Strategy Version", type: "text" },
            ].map(({ key, label, type, opts }) => (
              <label key={key} className="flex items-center justify-between gap-4">
                <span className="text-muted-foreground w-36">{label}</span>
                {type === "select" ? (
                  <select
                    value={form[key]}
                    onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                    className="bg-secondary border border-border rounded px-2 py-1 text-xs flex-1"
                  >
                    {opts?.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                ) : (
                  <input
                    type={type}
                    value={form[key]}
                    onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                    className="bg-secondary border border-border rounded px-2 py-1 text-xs flex-1"
                  />
                )}
              </label>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Filter bar ────────────────────────────────────────────────────────────────

type ActiveTab = "open" | "settled" | "all";

interface LocalFilters {
  direction: string;
  confidence: string;
  city: string;
  contractType: string;
}

const EMPTY: LocalFilters = { direction: "", confidence: "", city: "", contractType: "" };

function FilterBar({ filters, onChange }: { filters: LocalFilters; onChange: (f: LocalFilters) => void }) {
  const up = (k: keyof LocalFilters, v: string) => onChange({ ...filters, [k]: v });
  return (
    <div className="flex flex-wrap gap-2">
      <select value={filters.direction} onChange={(e) => up("direction", e.target.value)}
        className="bg-secondary border border-border rounded px-2 py-1 text-xs">
        <option value="">Both Directions</option>
        <option value="YES">YES only</option>
        <option value="NO">NO only</option>
      </select>
      <select value={filters.confidence} onChange={(e) => up("confidence", e.target.value)}
        className="bg-secondary border border-border rounded px-2 py-1 text-xs">
        <option value="">All Confidence</option>
        <option value="Very High">Very High</option>
        <option value="High">High</option>
        <option value="Medium">Medium</option>
        <option value="Low">Low</option>
      </select>
      <input type="text" placeholder="City…" value={filters.city}
        onChange={(e) => up("city", e.target.value)}
        className="bg-secondary border border-border rounded px-2 py-1 text-xs w-24" />
      <select value={filters.contractType} onChange={(e) => up("contractType", e.target.value)}
        className="bg-secondary border border-border rounded px-2 py-1 text-xs">
        <option value="">All Types</option>
        <option value="threshold">Threshold</option>
        <option value="range">Range</option>
        <option value="hourly_threshold">Hourly</option>
      </select>
      {Object.values(filters).some(Boolean) && (
        <button onClick={() => onChange(EMPTY)}
          className="text-xs text-muted-foreground hover:text-foreground border border-border rounded px-2 py-1">
          Clear
        </button>
      )}
    </div>
  );
}

// ── Trades table ──────────────────────────────────────────────────────────────

function TradesTable({ trades }: { trades: PaperTrade[] }) {
  if (trades.length === 0) {
    return (
      <p className="text-muted-foreground text-sm text-center py-10">
        No trades match the current filters.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-muted-foreground text-xs uppercase tracking-wide">
            <th className="text-left py-2 px-2 font-medium">Date</th>
            <th className="text-left py-2 px-2 font-medium">Ticker</th>
            <th className="text-left py-2 px-2 font-medium">City</th>
            <th className="text-left py-2 px-2 font-medium">Dir</th>
            <th className="text-right py-2 px-2 font-medium">EC%</th>
            <th className="text-right py-2 px-2 font-medium">Mkt%</th>
            <th className="text-right py-2 px-2 font-medium">Edge</th>
            <th className="text-left py-2 px-2 font-medium">Conf</th>
            <th className="text-right py-2 px-2 font-medium">Stake</th>
            <th className="text-left py-2 px-2 font-medium">Status</th>
            <th className="text-left py-2 px-2 font-medium">Outcome</th>
            <th className="text-right py-2 px-2 font-medium">P/L</th>
            <th className="py-2 px-2"></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/50">
          {trades.map((t) => (
            <tr key={t.id} className="hover:bg-secondary/30 transition-colors">
              <td className="py-2 px-2 font-mono text-xs text-muted-foreground">{fmtDate(t.createdAt)}</td>
              <td className="py-2 px-2 font-mono text-xs max-w-[140px] truncate" title={t.marketTicker}>
                {t.marketTicker}
              </td>
              <td className="py-2 px-2 text-xs">{t.city ?? "—"}</td>
              <td className="py-2 px-2"><DirectionBadge direction={t.direction} /></td>
              <td className="py-2 px-2 text-right font-mono text-xs">{pct(t.ecYesProbability)}</td>
              <td className="py-2 px-2 text-right font-mono text-xs">{pct(t.marketYesProbability)}</td>
              <td className="py-2 px-2 text-right font-mono text-xs text-primary">
                {t.edgePctPoints != null ? `+${t.edgePctPoints.toFixed(1)}pp` : "—"}
              </td>
              <td className="py-2 px-2"><ConfidenceBadge label={t.confidenceLabel} /></td>
              <td className="py-2 px-2 text-right font-mono text-xs">${t.stake.toFixed(2)}</td>
              <td className="py-2 px-2"><StatusBadge status={t.status} /></td>
              <td className="py-2 px-2"><OutcomeBadge outcome={t.outcome} /></td>
              <td className={`py-2 px-2 text-right font-mono text-xs ${
                t.profitLoss == null ? "text-muted-foreground" :
                t.profitLoss >= 0    ? "text-emerald-400" : "text-rose-400"
              }`}>
                {t.profitLoss != null ? usd(t.profitLoss) : "—"}
              </td>
              <td className="py-2 px-2">
                <Link href={`/paper-trading/${t.id}`}
                  className="text-primary hover:text-primary/70 transition-colors">
                  <ExternalLink className="h-3.5 w-3.5" />
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function PaperTradingPage() {
  const [activeTab, setActiveTab] = useState<ActiveTab>("open");
  const [localFilters, setLocalFilters] = useState<LocalFilters>(EMPTY);
  const [showSettings, setShowSettings] = useState(false);

  // Build API query params — status will be applied client-side from activeTab
  const queryParams: ListPaperTradesParams = {
    ...(localFilters.direction    ? { direction:     localFilters.direction }    : {}),
    ...(localFilters.confidence   ? { confidence:    localFilters.confidence }   : {}),
    ...(localFilters.city         ? { city:          localFilters.city }         : {}),
    ...(localFilters.contractType ? { contract_type: localFilters.contractType } : {}),
    limit: 500,
  };

  const { data: tradesData, isLoading: tradesLoading, error: tradesError } =
    useListPaperTrades(queryParams, {
      query: { queryKey: getListPaperTradesQueryKey(queryParams), refetchInterval: 60_000 },
    });

  const { data: metrics, isLoading: metricsLoading } =
    useGetPaperTradeMetrics({
      query: { queryKey: getGetPaperTradeMetricsQueryKey(), refetchInterval: 60_000 },
    });

  const allTrades    = tradesData?.trades ?? [];
  const openTrades   = allTrades.filter((t) => t.status === "OPEN");
  const settledTrades = allTrades.filter((t) => t.status === "SETTLED" || t.status === "VOID");
  const displayTrades =
    activeTab === "open"    ? openTrades :
    activeTab === "settled" ? settledTrades :
    allTrades;

  const m: PaperTradeSummary | undefined = metrics;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div className="flex items-center gap-3">
          <TrendingUp className="h-6 w-6 text-primary" />
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Paper Trading</h1>
            <p className="text-sm text-muted-foreground">Automated simulation — no real trades</p>
          </div>
        </div>
        <button
          onClick={() => setShowSettings((s) => !s)}
          className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground border border-border rounded px-3 py-1.5 transition-colors"
        >
          Settings {showSettings ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
        </button>
      </div>

      {/* Disclaimer */}
      <div className="flex items-start gap-3 bg-amber-500/10 border border-amber-500/30 rounded-lg p-4">
        <AlertTriangle className="h-5 w-5 text-amber-400 shrink-0 mt-0.5" />
        <div>
          <p className="text-sm font-semibold text-amber-300">
            Simulation Only — No Real Trades Placed
          </p>
          <p className="text-xs text-amber-300/80 mt-1 leading-relaxed">
            All trades are hypothetical simulations. No real money is at risk and no Kalshi
            trading credentials are used. Results do not account for fees, spreads, liquidity,
            or slippage and may overstate real-world performance.
          </p>
        </div>
      </div>

      {/* Settings panel (collapsible) */}
      {showSettings && <SettingsPanel />}

      {/* Summary cards */}
      {metricsLoading ? (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-24 w-full" />)}
        </div>
      ) : m ? (
        <>
          {m.sampleSizeWarning && (
            <p className="text-xs text-amber-400/80 bg-amber-500/5 border border-amber-500/20 rounded px-3 py-2">
              ⚠ Sample size too small for reliable conclusions ({m.settledCount} settled trade
              {m.settledCount !== 1 ? "s" : ""}).{m.preliminaryNote ? ` ${m.preliminaryNote}` : ""}
            </p>
          )}
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-5 gap-3">
            <MetricCard label="Open Trades" value={String(m.openCount)} />
            <MetricCard label="Settled" value={String(m.settledCount)} />
            <MetricCard label="Wins" value={String(m.wins)} accent="green" />
            <MetricCard label="Losses" value={String(m.losses)} accent="red" />
            <MetricCard
              label="Win Rate"
              value={m.winRate != null ? pct(m.winRate) : "—"}
              sub={`${m.settledCount} settled`}
            />
            <MetricCard label="Total Staked" value={`$${m.totalStaked.toFixed(2)}`} />
            <MetricCard
              label="Net P/L"
              value={m.netProfitLoss >= 0 ? `+$${m.netProfitLoss.toFixed(2)}` : `-$${Math.abs(m.netProfitLoss).toFixed(2)}`}
              accent={m.netProfitLoss >= 0 ? "green" : "red"}
            />
            <MetricCard
              label="ROI"
              value={m.roi != null ? `${m.roi >= 0 ? "+" : ""}${m.roi.toFixed(1)}%` : "—"}
              accent={m.roi == null ? "neutral" : m.roi >= 0 ? "green" : "red"}
              sub="on settled trades"
            />
            <MetricCard
              label="Avg Entry Edge"
              value={m.avgEntryEdge != null ? `+${m.avgEntryEdge.toFixed(1)}pp` : "—"}
            />
          </div>
        </>
      ) : null}

      {/* Trades table */}
      <Card className="border-border overflow-hidden">
        <CardHeader className="border-b border-border py-3 px-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            {/* Tabs */}
            <div className="flex gap-1">
              {(["open", "settled", "all"] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={`px-3 py-1 text-xs rounded font-medium capitalize transition-colors ${
                    activeTab === tab
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {tab === "open"    ? `Open (${openTrades.length})` :
                   tab === "settled" ? `Settled (${settledTrades.length})` :
                   `All (${allTrades.length})`}
                </button>
              ))}
            </div>
            {/* Filters */}
            <FilterBar filters={localFilters} onChange={setLocalFilters} />
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {tradesLoading ? (
            <div className="p-6 space-y-2">
              {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
            </div>
          ) : tradesError ? (
            <p className="text-destructive text-sm text-center p-8">Failed to load trades.</p>
          ) : (
            <div className="p-4">
              <TradesTable trades={displayTrades} />
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
