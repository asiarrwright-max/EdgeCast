/**
 * ForwardTestStatus — primary dashboard card showing the forward-test
 * progress, readiness stage, and per-group trade breakdowns.
 *
 * Data rules enforced by the backend:
 *  - Only OFFICIAL trades created ≥ 2026-08-04 count toward readiness.
 *  - RESEARCH_ONLY and legacy trades are tracked separately, never mixed in.
 *  - No historical rows are altered.
 */
import { useState } from "react";
import {
  useGetForwardTestStatus,
  type ForwardTestStatus,
} from "@workspace/api-client-react";
import {
  ShieldCheck, AlertCircle, Clock, FlaskConical,
  BarChart2, HelpCircle, History, TrendingUp,
  ChevronRight, Info,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

// ── Color maps ────────────────────────────────────────────────────────────────

const READINESS_COLOR: Record<string, string> = {
  "Not enough data":            "bg-slate-500/10 text-slate-400 border-slate-500/30",
  "Early signal":               "bg-blue-500/10 text-blue-400 border-blue-500/30",
  "Promising but unproven":     "bg-amber-500/10 text-amber-400 border-amber-500/30",
  "Ready for tiny manual testing": "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
  "Strong forward-test evidence":  "bg-emerald-500/20 text-emerald-300 border-emerald-500/50",
};

const READINESS_TEXT: Record<string, string> = {
  "Not enough data":            "text-slate-400",
  "Early signal":               "text-blue-400",
  "Promising but unproven":     "text-amber-400",
  "Ready for tiny manual testing": "text-emerald-400",
  "Strong forward-test evidence":  "text-emerald-300",
};

const READINESS_BAR: Record<string, string> = {
  "Not enough data":            "bg-slate-500",
  "Early signal":               "bg-blue-500",
  "Promising but unproven":     "bg-amber-500",
  "Ready for tiny manual testing": "bg-emerald-500",
  "Strong forward-test evidence":  "bg-emerald-400",
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function InfoTip({ text }: { text: string }) {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Info className="h-3 w-3 text-muted-foreground hover:text-foreground cursor-help transition-colors shrink-0" />
        </TooltipTrigger>
        <TooltipContent className="font-sans text-xs leading-relaxed max-w-[280px]">
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function StatPill({
  label,
  value,
  color = "gray",
  tip,
}: {
  label: string;
  value: string | number;
  color?: "gray" | "green" | "blue" | "amber" | "red";
  tip?: string;
}) {
  const colors = {
    gray:  "text-slate-300",
    green: "text-emerald-400",
    blue:  "text-blue-400",
    amber: "text-amber-400",
    red:   "text-red-400",
  };
  return (
    <div className="flex flex-col items-center justify-center p-3 bg-muted/20 rounded-lg border border-border/40 text-center min-w-[80px]">
      <div className={`text-xl sm:text-2xl font-mono font-bold ${colors[color]}`}>
        {value}
      </div>
      <div className="text-[9px] font-mono uppercase tracking-wider text-muted-foreground mt-0.5 flex items-center gap-1">
        {label}
        {tip && <InfoTip text={tip} />}
      </div>
    </div>
  );
}

// ── Tab content ───────────────────────────────────────────────────────────────

function OfficialTab({ data }: { data: ForwardTestStatus }) {
  const { byStrategy, officialSettledCount, officialOpenCount } = data;
  const v23 = byStrategy.v23;
  const v3  = byStrategy.v3;
  const v22 = byStrategy.v22;

  return (
    <div className="space-y-4">
      <p className="text-[11px] font-mono text-muted-foreground leading-relaxed">
        Only trades stamped <span className="text-emerald-400 font-semibold">OFFICIAL</span> and
        created after {data.forwardTestStartDate} count toward the readiness score.
        <span className="text-foreground font-semibold"> V2.3 (current model) + V3 feed the primary milestone.</span>{" "}
        V2.2 is historical reference only — its results do not affect the progress bar.
      </p>

      {/* ── Current model validation (V2.3 + V3) ── */}
      <div>
        <div className="text-[9px] font-mono uppercase tracking-widest text-emerald-400/70 mb-1.5 px-1">
          Current Model Validation
        </div>
        <div className="overflow-x-auto rounded-lg border border-emerald-500/20">
          <table className="w-full text-xs font-mono">
            <thead className="bg-emerald-500/5 border-b border-border/40">
              <tr className="text-[10px] uppercase tracking-wider text-muted-foreground">
                <th className="px-4 py-2.5 text-left font-medium">Strategy</th>
                <th className="px-4 py-2.5 text-right font-medium">Settled ✓</th>
                <th className="px-4 py-2.5 text-right font-medium">Open</th>
                <th className="px-4 py-2.5 text-right font-medium">Total</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/30">
              {v23 && (
                <tr className="hover:bg-muted/10 transition-colors">
                  <td className="px-4 py-3 text-foreground font-semibold">V2.3 <span className="text-[9px] text-emerald-400/70 font-normal ml-1">current</span></td>
                  <td className="px-4 py-3 text-right text-emerald-400">{v23.officialSettled}</td>
                  <td className="px-4 py-3 text-right text-blue-400">{v23.officialOpen}</td>
                  <td className="px-4 py-3 text-right text-muted-foreground">{v23.officialSettled + v23.officialOpen}</td>
                </tr>
              )}
              <tr className="hover:bg-muted/10 transition-colors">
                <td className="px-4 py-3 text-foreground font-semibold">V3.0 <span className="text-[9px] text-muted-foreground font-normal ml-1">predictive</span></td>
                <td className="px-4 py-3 text-right text-emerald-400">{v3.officialSettled}</td>
                <td className="px-4 py-3 text-right text-blue-400">{v3.officialOpen}</td>
                <td className="px-4 py-3 text-right text-muted-foreground">{v3.officialSettled + v3.officialOpen}</td>
              </tr>
              <tr className="bg-muted/10 font-semibold border-t border-emerald-500/20">
                <td className="px-4 py-3 text-foreground">Milestone total</td>
                <td className="px-4 py-3 text-right text-emerald-400">{officialSettledCount}</td>
                <td className="px-4 py-3 text-right text-blue-400">{officialOpenCount}</td>
                <td className="px-4 py-3 text-right text-foreground">{officialSettledCount + officialOpenCount}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Historical evidence (V2.2) ── */}
      <div>
        <div className="text-[9px] font-mono uppercase tracking-widest text-muted-foreground/50 mb-1.5 px-1">
          Historical Evidence (excluded from milestone)
        </div>
        <div className="overflow-x-auto rounded-lg border border-border/30 opacity-60">
          <table className="w-full text-xs font-mono">
            <thead className="bg-muted/20 border-b border-border/30">
              <tr className="text-[10px] uppercase tracking-wider text-muted-foreground">
                <th className="px-4 py-2 text-left font-medium">Strategy</th>
                <th className="px-4 py-2 text-right font-medium">Settled ✓</th>
                <th className="px-4 py-2 text-right font-medium">Open</th>
                <th className="px-4 py-2 text-right font-medium">Total</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="px-4 py-2.5 text-muted-foreground font-semibold">V2.2 <span className="text-[9px] font-normal ml-1">inverted bias — superseded</span></td>
                <td className="px-4 py-2.5 text-right text-muted-foreground">{v22.officialSettled}</td>
                <td className="px-4 py-2.5 text-right text-muted-foreground">{v22.officialOpen}</td>
                <td className="px-4 py-2.5 text-right text-muted-foreground">{v22.officialSettled + v22.officialOpen}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="text-[10px] font-mono text-muted-foreground/50 mt-1 px-1">
          V2.2 used an inverted bias sign and was superseded by V2.3. Retained for record integrity; never mixed into the current model score.
        </p>
      </div>

      {officialSettledCount === 0 && (
        <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg bg-slate-500/10 border border-slate-500/20 text-[11px] font-mono text-slate-400">
          <Clock className="h-3.5 w-3.5 shrink-0" />
          No official trades have settled yet — the forward test started {data.forwardTestStartDate}.
          Results will appear here as markets settle.
        </div>
      )}
    </div>
  );
}

function ResearchOnlyTab({ data }: { data: ForwardTestStatus }) {
  const { whyNoOfficialBet, researchOnlyCount } = data;
  const reasons = Object.entries(whyNoOfficialBet).filter(([, v]) => v.count > 0);
  const allZero = reasons.length === 0;

  return (
    <div className="space-y-4">
      <p className="text-[11px] font-mono text-muted-foreground leading-relaxed">
        Signals classified <span className="text-amber-400 font-semibold">RESEARCH_ONLY</span> since
        the forward-test start. These are never entered as official trades and never affect the
        readiness score — they exist for analysis only.
        {researchOnlyCount > 0 && (
          <span> <strong className="text-foreground">{researchOnlyCount}</strong> cumulative signal{researchOnlyCount !== 1 ? "s" : ""} recorded since {data.forwardTestStartDate}.</span>
        )}
      </p>

      {allZero ? (
        <div className="flex items-center gap-2 px-3 py-2.5 rounded-lg bg-slate-500/10 border border-slate-500/20 text-[11px] font-mono text-slate-400">
          <ShieldCheck className="h-3.5 w-3.5 shrink-0" />
          No RESEARCH_ONLY signals since {data.forwardTestStartDate}.
        </div>
      ) : (
        <div className="space-y-2">
          {reasons.map(([code, { label, count }]) => (
            <div
              key={code}
              className="flex items-center justify-between px-3 py-2.5 rounded-lg bg-muted/20 border border-border/40 hover:bg-muted/30 transition-colors"
            >
              <div className="flex items-center gap-2 min-w-0">
                <AlertCircle className="h-3.5 w-3.5 text-amber-400 shrink-0" />
                <span className="text-xs font-mono text-foreground truncate">{label}</span>
                <span className="text-[9px] font-mono text-muted-foreground/60 truncate hidden sm:inline">
                  ({code})
                </span>
              </div>
              <span className="text-xs font-mono font-bold text-amber-400 shrink-0 ml-3">
                {count}
              </span>
            </div>
          ))}

          {/* Zero-count reasons, collapsed */}
          {Object.entries(whyNoOfficialBet)
            .filter(([, v]) => v.count === 0)
            .map(([code, { label }]) => (
              <div
                key={code}
                className="flex items-center justify-between px-3 py-2 rounded-lg bg-muted/5 border border-border/20 opacity-40"
              >
                <span className="text-[11px] font-mono text-muted-foreground">{label}</span>
                <span className="text-[11px] font-mono text-muted-foreground ml-3">0</span>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}

function LegacyTab({ data }: { data: ForwardTestStatus }) {
  return (
    <div className="space-y-4">
      <p className="text-[11px] font-mono text-muted-foreground leading-relaxed">
        Trades created <em>before</em> the forward-test start date ({data.forwardTestStartDate}).
        These were recorded under earlier, less-hardened rules and are permanently excluded from
        the official readiness score.
      </p>

      <div className="flex items-center justify-between px-4 py-4 rounded-lg bg-muted/20 border border-border/40">
        <div className="flex items-center gap-3">
          <History className="h-5 w-5 text-slate-400 shrink-0" />
          <div>
            <div className="text-[11px] font-mono uppercase tracking-wider text-muted-foreground">
              Legacy trades on record
            </div>
            <div className="text-[11px] font-mono text-slate-400 mt-0.5">
              Available in Paper Trades for historical research
            </div>
          </div>
        </div>
        <div className="text-2xl font-mono font-bold text-slate-400">
          {data.legacyExcludedCount}
        </div>
      </div>

      <div className="flex items-start gap-2 px-3 py-2.5 rounded-lg bg-slate-500/5 border border-slate-500/20 text-[11px] font-mono text-slate-400">
        <Info className="h-3.5 w-3.5 shrink-0 mt-0.5" />
        Legacy trades have not been modified. Their historical data remains intact and accessible —
        they are simply excluded from the forward-test score.
      </div>
    </div>
  );
}

// ── "Why no official bet?" panel ──────────────────────────────────────────────

function WhyNoBetPanel({ data }: { data: ForwardTestStatus }) {
  const { whyNoOfficialBet, officialOpenCount, reasonBreakdownWindow } = data;
  if (officialOpenCount > 0) return null;

  const reasons = Object.entries(whyNoOfficialBet).filter(([, v]) => v.count > 0);
  if (reasons.length === 0) return null;

  return (
    <div className="mt-5 pt-5 border-t border-border/40">
      <div className="flex items-center justify-between gap-2 mb-3">
        <div className="flex items-center gap-2">
          <HelpCircle className="h-3.5 w-3.5 text-amber-400" />
          <span className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
            Why no official bet?
          </span>
        </div>
        <span className="text-[9px] font-mono px-2 py-0.5 rounded border bg-amber-500/5 border-amber-500/20 text-amber-400/70">
          {reasonBreakdownWindow}
        </span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {reasons.map(([code, { label, count }]) => (
          <div
            key={code}
            className="flex items-center justify-between px-3 py-2 rounded-lg bg-amber-500/5 border border-amber-500/20 text-xs font-mono"
          >
            <span className="text-muted-foreground truncate">{label}</span>
            <span className="font-bold text-amber-400 ml-2 shrink-0">{count}</span>
          </div>
        ))}
      </div>
      <p className="text-[10px] font-mono text-muted-foreground mt-2.5 opacity-70">
        Counts reflect the <span className="text-foreground">{reasonBreakdownWindow.toLowerCase()}</span> only —
        not cumulative totals since the forward-test start.
      </p>
    </div>
  );
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function ForwardTestSkeleton() {
  return (
    <div className="animate-pulse space-y-4">
      <div className="h-6 w-48 bg-muted/20 rounded" />
      <div className="grid grid-cols-4 gap-3">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="h-16 bg-muted/20 rounded-lg" />
        ))}
      </div>
      <div className="h-3 bg-muted/20 rounded-full" />
      <div className="h-32 bg-muted/20 rounded-lg" />
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

const TABS = [
  { id: "official",  label: "Official Forward Test", icon: ShieldCheck },
  { id: "research",  label: "Research Only",          icon: FlaskConical },
  { id: "legacy",    label: "Legacy Research",        icon: History },
] as const;
type TabId = (typeof TABS)[number]["id"];

export function ForwardTestStatus() {
  const { data, isLoading, error } = useGetForwardTestStatus();
  const [activeTab, setActiveTab] = useState<TabId>("official");

  if (isLoading) {
    return (
      <Card className="border-border/50 bg-card/40 backdrop-blur shadow-sm">
        <CardContent className="p-6">
          <ForwardTestSkeleton />
        </CardContent>
      </Card>
    );
  }

  if (error || !data) {
    return (
      <Card className="border-border/50 bg-card/40 backdrop-blur shadow-sm">
        <CardContent className="p-5 flex items-center gap-3 text-xs font-mono text-red-400">
          <AlertCircle className="h-4 w-4 shrink-0" />
          Unable to load forward-test status.
        </CardContent>
      </Card>
    );
  }

  const barColor     = READINESS_BAR[data.readinessLabel]  ?? "bg-slate-500";
  const labelColor   = READINESS_COLOR[data.readinessLabel] ?? READINESS_COLOR["Not enough data"];
  const labelText    = READINESS_TEXT[data.readinessLabel]  ?? "text-slate-400";
  const progressClamped = Math.max(data.progressPct, data.officialSettledCount > 0 ? 1.5 : 0);

  return (
    <Card className="border-border/50 bg-card/40 backdrop-blur shadow-sm overflow-hidden">
      {/* ── Header ── */}
      <CardHeader className="flex flex-row items-center justify-between pb-4 border-b border-border/50 bg-muted/5">
        <CardTitle className="text-xs font-mono tracking-widest text-muted-foreground uppercase flex items-center gap-2">
          <TrendingUp className="h-4 w-4 text-emerald-400" />
          Forward Test Status
        </CardTitle>
        <div className={`text-[10px] font-mono px-2.5 py-1 rounded border ${labelColor}`}>
          {data.readinessLabel}
        </div>
      </CardHeader>

      <CardContent className="p-5 sm:p-6 space-y-5">

        {/* ── Meta row ── */}
        <div className="flex flex-wrap gap-x-6 gap-y-2 text-[11px] font-mono text-muted-foreground">
          <span>
            <span className="text-foreground/60">Phase:</span>{" "}
            <span className="text-foreground">{data.phase}</span>
          </span>
          <span>
            <span className="text-foreground/60">Started:</span>{" "}
            <span className="text-foreground">{data.forwardTestStartDate}</span>
          </span>
          <span>
            <span className="text-foreground/60">Code version:</span>{" "}
            <code className="text-primary font-mono">{data.startingCodeVersion}</code>
          </span>
          <span className="flex items-center gap-1">
            <span className="text-foreground/60">Current readiness:</span>{" "}
            <span className="text-amber-400 font-semibold">{data.currentReadiness}</span>
          </span>
        </div>

        {/* ── Stats row ── */}
        <div className="flex flex-wrap gap-3">
          <StatPill
            label="Settled official"
            value={`${data.officialSettledCount} / ${data.progressTarget}`}
            color={data.officialSettledCount > 0 ? "green" : "gray"}
            tip={`Official trades that have settled since ${data.forwardTestStartDate}. Only these count toward the readiness score.`}
          />
          <StatPill
            label="Open official"
            value={data.officialOpenCount}
            color="blue"
            tip="Official-quality trades currently open and awaiting settlement."
          />
          <StatPill
            label="Research-only signals"
            value={data.researchOnlyCount}
            color="amber"
            tip={`Signals that passed the model but failed one of the eight safety guards since ${data.forwardTestStartDate}. Never counted in readiness metrics.`}
          />
          <StatPill
            label="Legacy excluded"
            value={data.legacyExcludedCount}
            color="gray"
            tip={`Trades created before ${data.forwardTestStartDate} — recorded under earlier, less-hardened rules. Excluded from the forward-test score.`}
          />
        </div>

        {/* ── Progress bar ── */}
        <div className="space-y-2">
          <div className="flex items-center justify-between text-[10px] font-mono text-muted-foreground">
            <span className={data.officialSettledCount > 0 ? labelText : ""}>
              {data.officialSettledCount} settled
            </span>
            <span className="flex items-center gap-1">
              Next: <span className="text-foreground">{data.nextMilestone}</span>
              <InfoTip text="Readiness is conservative and count-based only. Positive ROI with a small sample does not trigger a stage change." />
            </span>
          </div>
          <div className="h-2.5 bg-muted/50 rounded-full overflow-hidden border border-border/50">
            <div
              className={`h-full ${barColor} transition-all duration-700 ease-out rounded-full shadow-[0_0_8px_currentColor]`}
              style={{ width: `${progressClamped}%` }}
            />
          </div>
          <div className="flex justify-between text-[9px] font-mono text-muted-foreground opacity-70">
            <span>0</span>
            <span className="flex items-center gap-1">
              <ChevronRight className="h-2.5 w-2.5" /> Minimum review: 50 settled
            </span>
            <span className="flex items-center gap-1">
              <ChevronRight className="h-2.5 w-2.5" /> Stronger confidence: 100–200
            </span>
          </div>
        </div>

        {/* ── Explanation ── */}
        <p className="text-[11px] font-mono text-muted-foreground leading-relaxed border-l-2 border-primary/30 pl-3">
          {data.explanation}
        </p>

        {/* ── Tab bar ── */}
        <div className="space-y-4">
          <div className="flex gap-1 p-1 bg-muted/30 rounded-lg border border-border/40">
            {TABS.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                onClick={() => setActiveTab(id)}
                className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-md text-[10px] font-mono uppercase tracking-wider transition-all ${
                  activeTab === id
                    ? "bg-card text-foreground border border-border/60 shadow-sm"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted/30"
                }`}
              >
                <Icon className="h-3 w-3 shrink-0" />
                <span className="hidden sm:inline">{label}</span>
                <span className="sm:hidden">
                  {id === "official" ? "Official" : id === "research" ? "Research" : "Legacy"}
                </span>
              </button>
            ))}
          </div>

          <div className="min-h-[120px]">
            {activeTab === "official" && <OfficialTab data={data} />}
            {activeTab === "research" && <ResearchOnlyTab data={data} />}
            {activeTab === "legacy"   && <LegacyTab data={data} />}
          </div>
        </div>

        {/* ── Why no official bet? ── */}
        <WhyNoBetPanel data={data} />

      </CardContent>
    </Card>
  );
}
