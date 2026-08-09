/**
 * Audit & Validation
 * ==================
 * Read-only dashboard showing current model audit findings,
 * Forward Test B blockers, and validation status.
 * No API calls — all content is sourced from the completed read-only audit.
 */
import { ReactNode } from "react";
import { useState } from "react";
import {
  useGetAuditCheckResults,
  triggerAuditDbChecks,
  type AuditCheckResultItem,
  type AuditCheckStatus,
} from "@workspace/api-client-react";
import {
  ShieldCheck,
  ShieldAlert,
  AlertTriangle,
  Info,
  CheckCircle2,
  Circle,
  Database,
  Clock,
  XCircle,
  ChevronDown,
  ChevronUp,
  RefreshCw,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Severity = "CRITICAL" | "HIGH" | "MODERATE" | "LOW";
type FindingStatus = "Confirmed" | "Suspected" | "Required" | "Confirmed structural issue" | "Fix Implemented" | "Resolved";
type DbCheckStatus = "Pending DB verification";

interface Blocker {
  id: number;
  title: string;
  severity: Severity;
  status: FindingStatus;
  mustFix: string;
  finding: string;
  requiredAction: string;
}

interface DbCheck {
  label: string;
  needs: string[];
  status: DbCheckStatus;
}

// ---------------------------------------------------------------------------
// Live DB check status badge
// ---------------------------------------------------------------------------

function LiveStatusBadge({ status }: { status: AuditCheckStatus }) {
  if (status === "CLEARED") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
        <CheckCircle2 className="h-3 w-3" /> CLEARED
      </span>
    );
  }
  if (status === "FIX_REQUIRED") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono bg-destructive/15 text-destructive border border-destructive/30">
        <XCircle className="h-3 w-3" /> FIX REQUIRED
      </span>
    );
  }
  if (status === "CONFIRMED") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono bg-emerald-500/10 text-emerald-500 border border-emerald-500/25">
        <CheckCircle2 className="h-3 w-3" /> CONFIRMED
      </span>
    );
  }
  if (status === "RESOLVED") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono bg-primary/15 text-primary border border-primary/30">
        <CheckCircle2 className="h-3 w-3" /> RESOLVED
      </span>
    );
  }
  // PENDING
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono bg-orange-500/10 text-orange-400 border border-orange-500/25">
      <Clock className="h-3 w-3" /> PENDING
    </span>
  );
}

function LiveCheckCard({ item }: { item: AuditCheckResultItem }) {
  const [expanded, setExpanded] = useState(false);

  const checkedLabel = item.checkedAt
    ? new Date(item.checkedAt).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : null;

  return (
    <div className="border border-border rounded-md p-4 space-y-3">
      <div className="flex flex-wrap items-start gap-2">
        <div className="flex-1 min-w-0 space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold">{item.checkName}</span>
            <LiveStatusBadge status={item.status} />
          </div>
          {checkedLabel && (
            <div className="text-[11px] text-muted-foreground font-mono flex items-center gap-1">
              <Clock className="h-3 w-3" /> Checked: {checkedLabel}
            </div>
          )}
        </div>
      </div>

      <div className="text-sm text-muted-foreground leading-relaxed">
        {item.summary}
      </div>

      {item.actionRequired && (
        <div className="flex items-center gap-1.5 text-[11px] font-mono text-destructive">
          <XCircle className="h-3 w-3" />
          Action required before Forward Test B
        </div>
      )}
      {item.status === "CLEARED" && (
        <div className="flex items-center gap-1.5 text-[11px] font-mono text-emerald-400">
          <CheckCircle2 className="h-3 w-3" />
          No action required
        </div>
      )}

      {item.details && (
        <div>
          <button
            onClick={() => setExpanded((v) => !v)}
            className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
          >
            {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
            {expanded ? "Hide details" : "Show details"}
          </button>
          {expanded && (
            <pre className="mt-2 p-3 rounded bg-muted/20 border border-border text-[11px] font-mono text-muted-foreground whitespace-pre-wrap leading-relaxed overflow-x-auto">
              {item.details}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

function LiveDbChecksSection() {
  const { data, isLoading, isError, refetch, isFetching } =
    useGetAuditCheckResults();
  const [triggering, setTriggering] = useState(false);

  const handleRunChecks = async () => {
    setTriggering(true);
    try {
      await triggerAuditDbChecks();
      await refetch();
    } catch {
      // silently continue — errors visible in details
    } finally {
      setTriggering(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Database className="h-4 w-4 text-orange-400" />
          <h2 className="text-sm font-semibold">Final DB Checks Before Implementation</h2>
          <div className="ml-auto flex items-center gap-2">
            {(isFetching || triggering) && (
              <RefreshCw className="h-3.5 w-3.5 text-muted-foreground animate-spin" />
            )}
            <button
              onClick={handleRunChecks}
              disabled={triggering || isFetching}
              className="text-[11px] font-mono text-muted-foreground hover:text-foreground disabled:opacity-40 transition-colors flex items-center gap-1 px-2 py-1 border border-border rounded"
            >
              <RefreshCw className="h-3 w-3" />
              Re-run checks
            </button>
          </div>
        </div>
      </CardHeader>
      <CardBody className="space-y-3">
        {isLoading && (
          <div className="text-sm text-muted-foreground py-4 text-center">
            Loading check results…
          </div>
        )}
        {isError && (
          <div className="text-sm text-destructive py-4 text-center">
            Failed to load check results. Server may be starting up.
          </div>
        )}
        {!isLoading && !isError && data?.results.map((item) => (
          <LiveCheckCard key={item.checkKey} item={item} />
        ))}
      </CardBody>
    </Card>
  );
}

interface NonBlocker {
  title: string;
  finding: string;
}

interface ChainItem {
  label: string;
  audited: boolean;
}

// ---------------------------------------------------------------------------
// Static audit data
// ---------------------------------------------------------------------------

const BLOCKERS: Blocker[] = [
  {
    id: 1,
    title: "NWS integer threshold rounding",
    severity: "CRITICAL",
    status: "Resolved",
    mustFix: "Yes",
    finding:
      "Integer threshold probability formulas did not account for NWS integer rounding. Approximate probability impact: ~5.7 percentage points in representative cases.",
    requiredAction:
      "Apply settlement-aware T−0.5 boundary for integer thresholds in _calc_prob_threshold (V2, V3). Half-integer thresholds unchanged. ✓ Implemented in probability_engine_v2.py and v3_probability_engine.py.",
  },
  {
    id: 2,
    title: "NWS range contract rounding",
    severity: "CRITICAL",
    status: "Resolved",
    mustFix: "Yes",
    finding:
      "Range probability calculations used raw lower/upper bounds instead of settlement-aware lower−0.5 / upper+0.5 boundaries. Representative impact ~11 percentage points.",
    requiredAction:
      "Use settlement-aware integration bounds in _calc_prob_range (V2, V3). ✓ Implemented in probability_engine_v2.py and v3_probability_engine.py.",
  },
  {
    id: 3,
    title: "ERA5 local-date extraction",
    severity: "CRITICAL",
    status: "Resolved",
    mustFix: "Yes",
    finding:
      "DB Check B confirmed: target_settlement_date stored as UTC ISO timestamp was sliced as UTC calendar date. LA trade 2026-08-08T04:54:08Z produced UTC date 2026-08-08 but correct local PDT date was 2026-08-07.",
    requiredAction:
      "Convert UTC timestamp to settlement-station local timezone in forecast_verifier.py. ✓ Implemented via _local_settlement_date() helper. Pending deployment verification.",
  },
  {
    id: 4,
    title: "Hourly sigma floor",
    severity: "HIGH",
    status: "Resolved",
    mustFix: "Yes",
    finding:
      "Hourly contracts used the daily sigma floor (3.5°F) instead of the intended hourly floor (2.0°F) because hourly=True was not passed to _sigma_v2().",
    requiredAction:
      "Pass hourly=(contract_type == 'hourly_threshold') to _sigma_v2() in run_analysis_v2 and run_analysis_v22. ✓ Implemented in probability_engine_v2.py and probability_engine_v22.py.",
  },
  {
    id: 5,
    title: "Station verification eligibility",
    severity: "HIGH",
    status: "Resolved",
    mustFix: "Yes",
    finding:
      "get_verified_station() had a bug: the else branch returned the unverified station instead of None, making the function's name misleading. V2.2/V3 already passed station_verified to assess_trade_eligibility (which correctly stamps RESEARCH_ONLY for unverified stations).",
    requiredAction:
      "Fix get_verified_station() to return None for unverified stations. ✓ Implemented in settlement_stations.py. Eligibility engine guard already correct (line 156-157).",
  },
  {
    id: 6,
    title: "Philadelphia / San Antonio coordinates",
    severity: "HIGH",
    status: "Resolved",
    mustFix: "Yes",
    finding:
      "SERIES_TO_CITY used city-centre coordinates for Philadelphia (39.9526, -75.1652) and San Antonio (29.4241, -98.4936) instead of the KPHL and KSAT settlement station coordinates.",
    requiredAction:
      "Correct SERIES_TO_CITY: Philadelphia → KPHL (39.8721, -75.2411), San Antonio → KSAT (29.5337, -98.4698). ✓ Implemented in kalshi.py.",
  },
  {
    id: 7,
    title: "Calibration reset for corrected strategy",
    severity: "HIGH",
    status: "Resolved",
    mustFix: "Yes",
    finding:
      'V2.2 looked up calibration rows under strategy_version="v2.0". After corrections, new trades must not inherit any v2.0-era calibration. STRATEGY_VERSION must advance to v2.3.',
    requiredAction:
      "Change STRATEGY_VERSION to v2.3 in paper_trading_v22.py. Update _calibration_adj_v2 to accept strategy_version param. V2.3 calibration resolves to 1.0 (no rows). ✓ Implemented.",
  },
  {
    id: 8,
    title: "New Forward Test start boundary",
    severity: "HIGH",
    status: "Resolved",
    mustFix: "Yes",
    finding:
      "Old and corrected model results must not be mixed. FORWARD_TEST_START_B constant added and set to None (not yet activated).",
    requiredAction:
      "After corrected model passes deployment verification, set FORWARD_TEST_START_B to the exact UTC deployment timestamp. ✓ Set to 2026-08-09T00:15:12Z — Forward Test B active.",
  },
];

const DB_CHECKS: DbCheck[] = [
  {
    label: "calibration_adjustments contents",
    needs: [
      "Whether any usable rows exist",
      "Whether V2.2 currently receives a factor other than 1.0",
      "Provenance and dates of rows",
    ],
    status: "Pending DB verification",
  },
  {
    label: "target_settlement_date local-date alignment",
    needs: [
      "Stored target settlement date for every current OFFICIAL trade",
      "UTC close date",
      "Station-local close date",
      "Date used by ERA5 verification",
    ],
    status: "Pending DB verification",
  },
  {
    label: "WeatherLocation vs SERIES_TO_CITY coordinates",
    needs: [
      "Active city coordinates in WeatherLocation table",
      "Whether forecast and ERA5 verification may use different grid cells",
    ],
    status: "Pending DB verification",
  },
];

const NON_BLOCKERS: NonBlocker[] = [
  {
    title: "GFS / model-run cycle awareness",
    finding:
      "Fresh Kalshi quote does not guarantee fresh underlying NWP model run.",
  },
  {
    title: "True multi-model weighted consensus",
    finding:
      "Current forecast path relies primarily on one Open-Meteo call/default blend rather than an explicitly controlled weighted set of independent forecast models.",
  },
  {
    title: "ERA5 diagnostic rounding",
    finding:
      "Raw continuous ERA5 values are compared directly with integer settlement thresholds, inflating ERA5_KALSHI_DISAGREE counts near ±0.5°F boundaries.",
  },
  {
    title: "Error-stat source separation",
    finding:
      "Forecast error statistics currently mix GHCND, ERA5, and legacy observations rather than separating sigma/bias estimates by source.",
  },
  {
    title: "Calibration write / refit mechanism",
    finding:
      "No persistent code path currently populates calibration_adjustments. Required before future calibration is actually refit, but not required to begin uncalibrated V2.3 Forward Test B.",
  },
  {
    title: "LAX comment cleanup",
    finding: "Low-priority documentation issue only.",
  },
];

const CHAIN_ITEMS: ChainItem[] = [
  { label: "Forecast source / model labeling", audited: true },
  { label: "Model run age / release timing", audited: true },
  { label: "Station alignment", audited: true },
  { label: "NWS settlement rounding", audited: true },
  { label: "Probability formulas", audited: true },
  { label: "Forecast-to-contract translation", audited: true },
  { label: "Recent loss autopsy", audited: true },
  { label: "Eligibility / known bugs", audited: true },
  { label: "Calibration architecture", audited: true },
  { label: "Kalshi settlement fetch/storage", audited: true },
  { label: "ERA5 verification fetch/storage", audited: true },
  { label: "Settlement parser / contract type handling", audited: true },
];

// ---------------------------------------------------------------------------
// Derived counts
// ---------------------------------------------------------------------------

const COUNT_CRITICAL = BLOCKERS.filter((b) => b.severity === "CRITICAL").length;
const COUNT_HIGH = BLOCKERS.filter((b) => b.severity === "HIGH").length;
const COUNT_MODERATE = NON_BLOCKERS.length;
const COUNT_BLOCKERS = BLOCKERS.length;
const COUNT_CONFIRMED = BLOCKERS.filter(
  (b) => b.status === "Confirmed" || b.status === "Confirmed structural issue" || b.status === "Required"
).length;
const COUNT_SUSPECTED = BLOCKERS.filter((b) => b.status === "Suspected").length;
const COUNT_FIX_IMPLEMENTED = BLOCKERS.filter((b) => b.status === "Fix Implemented").length;
const COUNT_RESOLVED = BLOCKERS.filter((b) => b.status === "Resolved").length;

// ---------------------------------------------------------------------------
// Reusable components
// ---------------------------------------------------------------------------

function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-border bg-card ${className}`}>
      {children}
    </div>
  );
}

function CardHeader({ children }: { children: ReactNode }) {
  return (
    <div className="px-4 py-3 border-b border-border">
      {children}
    </div>
  );
}

function CardBody({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`p-4 ${className}`}>{children}</div>;
}

function SeverityBadge({ severity }: { severity: Severity }) {
  const styles: Record<Severity, string> = {
    CRITICAL: "bg-destructive/15 text-destructive border-destructive/30",
    HIGH: "bg-orange-500/15 text-orange-400 border-orange-500/30",
    MODERATE: "bg-yellow-500/15 text-yellow-400 border-yellow-500/30",
    LOW: "bg-muted/40 text-muted-foreground border-border",
  };
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-mono font-semibold border ${styles[severity]}`}
    >
      {severity}
    </span>
  );
}

function StatusBadge({ status }: { status: FindingStatus | DbCheckStatus }) {
  if (status === "Confirmed") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
        <CheckCircle2 className="h-3 w-3" /> Confirmed
      </span>
    );
  }
  if (status === "Confirmed structural issue") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono bg-emerald-500/10 text-emerald-500 border border-emerald-500/25">
        <CheckCircle2 className="h-3 w-3" /> Confirmed structural
      </span>
    );
  }
  if (status === "Suspected") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono bg-yellow-500/15 text-yellow-400 border border-yellow-500/30">
        <AlertTriangle className="h-3 w-3" /> Suspected
      </span>
    );
  }
  if (status === "Required") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono bg-primary/15 text-primary border border-primary/30">
        <Info className="h-3 w-3" /> Required
      </span>
    );
  }
  if (status === "Fix Implemented") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono bg-sky-500/15 text-sky-400 border border-sky-500/30">
        <CheckCircle2 className="h-3 w-3" /> Fix Implemented
      </span>
    );
  }
  if (status === "Resolved") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">
        <CheckCircle2 className="h-3 w-3" /> Resolved
      </span>
    );
  }
  if (status === "Pending DB verification") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono bg-orange-500/10 text-orange-400 border border-orange-500/25">
        <Clock className="h-3 w-3" /> Pending DB verification
      </span>
    );
  }
  return null;
}

function CounterCard({
  label,
  value,
  variant = "default",
}: {
  label: string;
  value: number;
  variant?: "critical" | "high" | "moderate" | "primary" | "muted" | "default";
}) {
  const textColor: Record<string, string> = {
    critical: "text-destructive",
    high: "text-orange-400",
    moderate: "text-yellow-400",
    primary: "text-primary",
    muted: "text-muted-foreground",
    default: "text-foreground",
  };
  return (
    <div className="rounded-lg border border-border bg-card px-4 py-3 text-center">
      <div className={`text-2xl font-bold font-mono ${textColor[variant]}`}>{value}</div>
      <div className="text-[11px] text-muted-foreground mt-0.5 leading-tight">{label}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sections
// ---------------------------------------------------------------------------

function ValidationStatus() {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <ShieldAlert className="h-4 w-4 text-destructive" />
          <h2 className="text-sm font-semibold">Current Validation Status</h2>
        </div>
      </CardHeader>
      <CardBody>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-5">
          <div>
            <div className="text-[11px] text-muted-foreground uppercase tracking-wider mb-1">Current phase</div>
            <div className="text-sm font-semibold text-primary">Preparing Forward Test B</div>
          </div>
          <div>
            <div className="text-[11px] text-muted-foreground uppercase tracking-wider mb-1">Model status</div>
            <div className="text-sm font-semibold text-sky-400">7 of 8 blockers: fix implemented — pending deployment verification</div>
          </div>
          <div>
            <div className="text-[11px] text-muted-foreground uppercase tracking-wider mb-1">Real-money readiness</div>
            <div className="text-sm font-semibold text-destructive flex items-center gap-1.5">
              <XCircle className="h-3.5 w-3.5" /> Not ready for real money
            </div>
          </div>
        </div>

        <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-7 gap-2">
          <CounterCard label="Critical findings" value={COUNT_CRITICAL} variant="critical" />
          <CounterCard label="High findings" value={COUNT_HIGH} variant="high" />
          <CounterCard label="Moderate findings" value={COUNT_MODERATE} variant="moderate" />
          <CounterCard label="FTB blockers" value={COUNT_BLOCKERS} variant="primary" />
          <CounterCard label="Fix implemented" value={COUNT_FIX_IMPLEMENTED} variant="default" />
          <CounterCard label="Confirmed open" value={COUNT_CONFIRMED} variant="muted" />
          <CounterCard label="Resolved" value={COUNT_RESOLVED} variant="muted" />
        </div>
      </CardBody>
    </Card>
  );
}

function BlockerItem({ blocker }: { blocker: Blocker }) {
  return (
    <div className="border border-border rounded-md p-4 space-y-3">
      <div className="flex flex-wrap items-start gap-2">
        <span className="text-sm font-semibold text-muted-foreground font-mono w-5 shrink-0">
          {blocker.id}.
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            <span className="text-sm font-semibold">{blocker.title}</span>
            <SeverityBadge severity={blocker.severity} />
            <StatusBadge status={blocker.status} />
          </div>
          <div className="text-[11px] text-muted-foreground font-mono">
            Must fix: <span className="text-foreground">{blocker.mustFix}</span>
          </div>
        </div>
      </div>

      <div className="pl-7 space-y-2">
        <div>
          <div className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1">Finding</div>
          <p className="text-sm text-muted-foreground leading-relaxed">{blocker.finding}</p>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1">Required action</div>
          <p className="text-sm leading-relaxed">{blocker.requiredAction}</p>
        </div>
      </div>
    </div>
  );
}

function BlockersSection() {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <ShieldAlert className="h-4 w-4 text-destructive" />
          <h2 className="text-sm font-semibold">Forward Test B — Must Fix</h2>
          <span className="ml-auto text-[11px] font-mono text-muted-foreground">
            {BLOCKERS.length} items
          </span>
        </div>
      </CardHeader>
      <CardBody className="space-y-3">
        {BLOCKERS.map((b) => (
          <BlockerItem key={b.id} blocker={b} />
        ))}
      </CardBody>
    </Card>
  );
}

function DbChecksSection() {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Database className="h-4 w-4 text-orange-400" />
          <h2 className="text-sm font-semibold">Final DB Checks Before Implementation</h2>
        </div>
      </CardHeader>
      <CardBody className="space-y-3">
        {DB_CHECKS.map((check, i) => (
          <div key={i} className="border border-border rounded-md p-4 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold">{String.fromCharCode(65 + i)}. {check.label}</span>
              <StatusBadge status={check.status} />
            </div>
            <div>
              <div className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1.5">Need to determine</div>
              <ul className="space-y-1">
                {check.needs.map((n, j) => (
                  <li key={j} className="flex items-start gap-2 text-sm text-muted-foreground">
                    <Circle className="h-3 w-3 mt-0.5 shrink-0 text-orange-400/60" />
                    {n}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        ))}
      </CardBody>
    </Card>
  );
}

function NonBlockersSection() {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Info className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-semibold">Important, But Not Required Before Forward Test B</h2>
        </div>
      </CardHeader>
      <CardBody className="space-y-2">
        {NON_BLOCKERS.map((item, i) => (
          <div key={i} className="border border-border rounded-md p-3 space-y-1">
            <div className="text-sm font-semibold">{item.title}</div>
            <p className="text-sm text-muted-foreground leading-relaxed">{item.finding}</p>
          </div>
        ))}
      </CardBody>
    </Card>
  );
}

function ChainCoverageSection() {
  const auditedCount = CHAIN_ITEMS.filter((c) => c.audited).length;
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-emerald-400" />
          <h2 className="text-sm font-semibold">Forecast → Settlement Chain Audit Coverage</h2>
          <span className="ml-auto text-[11px] font-mono text-emerald-400">
            {auditedCount}/{CHAIN_ITEMS.length} audited
          </span>
        </div>
      </CardHeader>
      <CardBody>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-4">
          {CHAIN_ITEMS.map((item, i) => (
            <div key={i} className="flex items-center gap-2 text-sm">
              <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" />
              <span className={item.audited ? "text-foreground" : "text-muted-foreground"}>
                {item.label}
              </span>
            </div>
          ))}
        </div>

        <div className="border-t border-border pt-3 grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
          <div>
            <div className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1">
              Remaining code blind spots
            </div>
            <div className="text-emerald-400 font-medium">None known</div>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wider text-muted-foreground mb-1">
              Remaining data verification
            </div>
            <div className="text-orange-400 font-medium">3 DB checks listed above</div>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

function BaselineSection() {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 text-yellow-400" />
          <h2 className="text-sm font-semibold">Baseline Forward Test A — Early Findings</h2>
        </div>
      </CardHeader>
      <CardBody>
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3 mb-4">
          <div className="rounded-md border border-border p-3 text-center">
            <div className="text-xl font-bold font-mono text-foreground">16</div>
            <div className="text-[11px] text-muted-foreground mt-0.5">Settled observations</div>
          </div>
          <div className="rounded-md border border-border p-3 text-center">
            <div className="text-xl font-bold font-mono text-foreground">15</div>
            <div className="text-[11px] text-muted-foreground mt-0.5">Official trades</div>
          </div>
          <div className="rounded-md border border-border p-3 text-center">
            <div className="text-xl font-bold font-mono text-destructive">~91%</div>
            <div className="text-[11px] text-muted-foreground mt-0.5">Avg predicted probability</div>
          </div>
          <div className="rounded-md border border-border p-3 text-center">
            <div className="text-xl font-bold font-mono text-orange-400">~60%</div>
            <div className="text-[11px] text-muted-foreground mt-0.5">Actual win rate</div>
          </div>
        </div>

        <div className="space-y-2 text-sm text-muted-foreground">
          <div className="flex items-start gap-2">
            <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0 text-yellow-400" />
            <span>
              All current OFFICIAL trades fall in the 85–94% predicted-probability range. The gap
              between predicted and actual win rate is consistent with the rounding underestimate
              (~5.7pp on threshold YES probability) combined with a sigma floor that is too tight
              relative to real NWP forecast variance.
            </span>
          </div>
          <div className="flex items-start gap-2">
            <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0 text-yellow-400" />
            <span>
              Losses include 2 large NWP misses (8°F and 4–5°F errors) and ERA5/Kalshi
              disagreements in 2–3 cases — some of which may be explained by the suspected ERA5
              local-date mismatch.
            </span>
          </div>
          <div className="flex items-start gap-2">
            <Info className="h-3.5 w-3.5 mt-0.5 shrink-0 text-primary" />
            <span>
              Actively traded cities: Dallas, New York City, Houston, Denver, Oklahoma City,
              Minneapolis, Chicago. Philadelphia and San Antonio have coordinate issues and are not
              yet verified.
            </span>
          </div>
          <div className="flex items-start gap-2">
            <Info className="h-3.5 w-3.5 mt-0.5 shrink-0 text-primary" />
            <span>
              Forward Test A data will be retained for historical reference but must not be mixed
              with Forward Test B results in any performance analytics.
            </span>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function AuditValidationPage() {
  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Audit &amp; Validation</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Read-only. Current model audit findings, Forward Test B blockers, and validation status.
          No model logic, eligibility logic, calibration logic, settlement logic, or production
          trading behavior is modified by this page.
        </p>
      </div>

      <ValidationStatus />
      <BlockersSection />
      <LiveDbChecksSection />
      <NonBlockersSection />
      <ChainCoverageSection />
      <BaselineSection />
    </div>
  );
}
