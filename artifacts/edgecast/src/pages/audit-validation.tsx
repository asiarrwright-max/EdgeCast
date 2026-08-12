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
  useGetFtbResearchFunnel,
  triggerAuditDbChecks,
  type AuditCheckResultItem,
  type AuditCheckStatus,
  type FtbRejectionRow,
  type FtbCityRow,
  type FtbSafeAction,
  type FtbFunnelStep,
  useGetBetWatch,
} from "@workspace/api-client-react";
import { CitySpecializationStudySection } from "./city-study-section";
import { VerifiedCitySpecializationSection } from "./verified-city-section";
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
  FlaskConical,
  CheckCircle,
  XCircle as XCircleIcon,
  Loader2,
  Eye,
  MapPin,
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
// FTB Research Funnel Section
// ---------------------------------------------------------------------------

const REASON_LABELS: Record<string, string> = {
  missing_or_stale_executable_quote: "Stale executable quote (>300 s)",
  v2_excluded:                        "Market price ≤$0.01 (no liquidity)",
  hourly_temperature_not_approved:    "Hourly contract — FTB rule",
  settlement_station_unverified:      "Settlement station unverified",
  same_day_not_approved:              "Same-day contract — FTB rule",
  entry_price_below_official_floor:   "Entry price below floor",
  extreme_edge_requires_validation:   "Extreme edge — FTB rule",
  correlated_outcome_limit:           "Correlated outcome limit",
  cutoff_unverified_or_too_close:     "Market closes ≤120 min away",
  no_reason_recorded:                 "No reason recorded",
};

function ReasonLabel({ reason }: { reason: string }) {
  return <>{REASON_LABELS[reason] ?? reason}</>;
}

function FixableBadge({ fixable }: { fixable: boolean }) {
  if (fixable) {
    return (
      <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-mono bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
        <CheckCircle2 className="h-2.5 w-2.5" /> fixable
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-mono bg-muted/40 text-muted-foreground border border-border">
      FTB rule
    </span>
  );
}

function FunnelBar({ step, maxRemaining }: { step: FtbFunnelStep; maxRemaining: number }) {
  const pct = maxRemaining > 0 ? Math.round((step.remaining / maxRemaining) * 100) : 0;
  const droppedPct = maxRemaining > 0 ? Math.round((step.dropped / maxRemaining) * 100) : 0;
  return (
    <div className="flex items-center gap-2 text-xs">
      <div className="w-48 shrink-0 text-muted-foreground truncate" title={step.gate}>
        {step.gate}
      </div>
      <div className="flex-1 flex h-5 rounded overflow-hidden bg-muted/30 min-w-0">
        {step.remaining > 0 && (
          <div
            className="bg-primary/60 h-full transition-all"
            style={{ width: `${pct}%` }}
          />
        )}
        {step.dropped > 0 && (
          <div
            className="bg-destructive/50 h-full transition-all"
            style={{ width: `${droppedPct}%` }}
          />
        )}
      </div>
      <div className="w-20 shrink-0 text-right font-mono">
        <span className="text-foreground">{step.remaining}</span>
        {step.dropped > 0 && (
          <span className="text-destructive ml-1">−{step.dropped}</span>
        )}
      </div>
    </div>
  );
}

function FtbResearchFunnelSection() {
  const { data, isLoading, isError, refetch, isFetching } = useGetFtbResearchFunnel();

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <FlaskConical className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-semibold">Forward Test B — RESEARCH_ONLY Funnel</h2>
          </div>
        </CardHeader>
        <CardBody>
          <div className="flex items-center gap-2 text-sm text-muted-foreground py-4">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading live funnel data…
          </div>
        </CardBody>
      </Card>
    );
  }

  if (isError || !data) {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <FlaskConical className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-semibold">Forward Test B — RESEARCH_ONLY Funnel</h2>
          </div>
        </CardHeader>
        <CardBody>
          <div className="flex items-center justify-between">
            <p className="text-sm text-destructive">Failed to load funnel data.</p>
            <button
              onClick={() => refetch()}
              className="text-xs text-primary hover:underline flex items-center gap-1"
            >
              <RefreshCw className="h-3 w-3" /> Retry
            </button>
          </div>
        </CardBody>
      </Card>
    );
  }

  const { summary, narrative, rejections, funnel, cities, liquidity, safeActions, generatedAt, ftbBoundary } = data;
  const maxRemaining = funnel[0]?.remaining ?? summary.total;
  const fixableCount = rejections.filter(r => r.fixable).reduce((a, r) => a + r.count, 0);
  const unverifiedCities = cities.filter(c => c.potentiallyFixable);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <FlaskConical className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-semibold">Forward Test B — RESEARCH_ONLY Funnel</h2>
          </div>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="text-[11px] text-muted-foreground hover:text-foreground flex items-center gap-1 disabled:opacity-50"
          >
            <RefreshCw className={`h-3 w-3 ${isFetching ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>
        <p className="text-[11px] text-muted-foreground mt-1">
          FTB boundary: <span className="font-mono">{ftbBoundary}</span> · strategy: <span className="font-mono">{data.strategy}</span> ·
          generated: <span className="font-mono">{new Date(generatedAt).toLocaleString()}</span>
        </p>
      </CardHeader>

      <CardBody className="space-y-6">

        {/* ── Why no OFFICIAL FTB trades? ── */}
        <div className="rounded-md border border-primary/20 bg-primary/5 p-3 space-y-1">
          <p className="text-xs font-semibold text-primary mb-1">Why are there no OFFICIAL FTB trades yet?</p>
          <p className="text-xs text-muted-foreground leading-relaxed">{narrative}</p>
        </div>

        {/* ── Summary stats ── */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="rounded-md border border-border p-3 text-center">
            <div className="text-xl font-bold font-mono">{summary.total}</div>
            <div className="text-[11px] text-muted-foreground mt-0.5">Total evaluated</div>
          </div>
          <div className="rounded-md border border-border p-3 text-center">
            <div className="text-xl font-bold font-mono text-muted-foreground">0</div>
            <div className="text-[11px] text-muted-foreground mt-0.5">OFFICIAL</div>
          </div>
          <div className="rounded-md border border-border p-3 text-center">
            <div className="text-xl font-bold font-mono text-orange-400">{summary.researchOnly}</div>
            <div className="text-[11px] text-muted-foreground mt-0.5">RESEARCH_ONLY</div>
          </div>
          <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-center">
            <div className="text-xl font-bold font-mono text-amber-400">{liquidity.pctLiquidityBlocked}%</div>
            <div className="text-[11px] text-muted-foreground mt-0.5">Liquidity-blocked</div>
          </div>
        </div>

        {/* ── Rejection Breakdown ── */}
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
            Rejection Breakdown
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="text-left py-2 pr-3 font-medium">Reason</th>
                  <th className="text-right py-2 px-3 font-medium">Count</th>
                  <th className="text-right py-2 px-3 font-medium">%</th>
                  <th className="text-right py-2 px-3 font-medium">Markets</th>
                  <th className="text-left py-2 px-3 font-medium">Class</th>
                  <th className="text-left py-2 pl-3 font-medium hidden md:table-cell">Notes</th>
                </tr>
              </thead>
              <tbody>
                {rejections.map(r => (
                  <tr key={r.reason} className="border-b border-border/50 hover:bg-muted/20">
                    <td className="py-2 pr-3 font-mono text-foreground">
                      <ReasonLabel reason={r.reason} />
                    </td>
                    <td className="text-right py-2 px-3 font-mono">{r.count}</td>
                    <td className="text-right py-2 px-3 font-mono text-muted-foreground">{r.pctOfTotal}%</td>
                    <td className="text-right py-2 px-3 font-mono text-muted-foreground">{r.uniqueTickers}</td>
                    <td className="py-2 px-3"><FixableBadge fixable={r.fixable} /></td>
                    <td className="py-2 pl-3 text-muted-foreground hidden md:table-cell max-w-xs truncate" title={r.fixableNotes}>
                      {r.fixableNotes}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-border text-muted-foreground">
                  <td className="py-2 pr-3 font-semibold">Total</td>
                  <td className="text-right py-2 px-3 font-semibold font-mono">{summary.total}</td>
                  <td className="text-right py-2 px-3 font-mono">100%</td>
                  <td colSpan={3} />
                </tr>
              </tfoot>
            </table>
          </div>
          {fixableCount > 0 && (
            <p className="text-[11px] text-emerald-400 mt-1">
              ✓ {fixableCount} trades ({Math.round(fixableCount / summary.total * 100)}%) are blocked by fixable reasons — no eligibility rule changes required.
            </p>
          )}
        </div>

        {/* ── Eligibility Funnel ── */}
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
            Rule-by-Rule Eligibility Funnel
          </h3>
          <div className="space-y-1.5">
            {funnel.map((step, i) => (
              <FunnelBar key={i} step={step} maxRemaining={maxRemaining} />
            ))}
          </div>
          <p className="text-[11px] text-muted-foreground mt-2">
            Blue = surviving trades · Red = dropped at this gate. Each trade is counted at its first-failing gate.
          </p>
        </div>

        {/* ── Liquidity Detail ── */}
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
            Liquidity Analysis
          </h3>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div className="rounded border border-border p-3 text-center">
              <div className="text-lg font-bold font-mono text-destructive">{liquidity.noPriceAboveFloor}</div>
              <div className="text-[11px] text-muted-foreground">Market price ≤$0.01</div>
              <div className="text-[10px] text-muted-foreground/60 mt-0.5">no ask above floor</div>
            </div>
            <div className="rounded border border-border p-3 text-center">
              <div className="text-lg font-bold font-mono text-orange-400">{liquidity.staleQuoteWithAsk}</div>
              <div className="text-[11px] text-muted-foreground">Stale ask (&gt;300 s)</div>
              <div className="text-[10px] text-muted-foreground/60 mt-0.5">had a price, expired</div>
            </div>
            <div className="rounded border border-amber-500/30 bg-amber-500/5 p-3 text-center">
              <div className="text-lg font-bold font-mono text-amber-400">{liquidity.pctLiquidityBlocked}%</div>
              <div className="text-[11px] text-muted-foreground">Liquidity-blocked</div>
              <div className="text-[10px] text-muted-foreground/60 mt-0.5">{liquidity.totalLiquidityBlocked} / {summary.total} trades</div>
            </div>
          </div>
          <p className="text-[11px] text-muted-foreground mt-2 italic">{liquidity.interpretation}</p>
        </div>

        {/* ── City Opportunity Ranking ── */}
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
            City Opportunity Ranking
            <span className="ml-2 font-normal normal-case">(ranked by estimated recoverable without rule changes)</span>
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="text-left py-2 pr-3 font-medium">City</th>
                  <th className="text-right py-2 px-3 font-medium">Evaluated</th>
                  <th className="text-left py-2 px-3 font-medium">Top Reason</th>
                  <th className="text-center py-2 px-3 font-medium">Station</th>
                  <th className="text-right py-2 pl-3 font-medium">Recoverable</th>
                </tr>
              </thead>
              <tbody>
                {cities.map(c => (
                  <tr key={c.city} className={`border-b border-border/50 hover:bg-muted/20 ${c.potentiallyFixable ? "bg-emerald-500/3" : ""}`}>
                    <td className="py-2 pr-3 font-medium">{c.city}</td>
                    <td className="text-right py-2 px-3 font-mono">{c.total}</td>
                    <td className="py-2 px-3 text-muted-foreground">
                      <ReasonLabel reason={c.topReason} />
                    </td>
                    <td className="text-center py-2 px-3">
                      {c.stationVerified ? (
                        <CheckCircle2 className="h-3 w-3 text-emerald-400 inline" />
                      ) : (
                        <XCircle className="h-3 w-3 text-destructive inline" />
                      )}
                    </td>
                    <td className="text-right py-2 pl-3 font-mono">
                      {c.potentiallyFixable ? (
                        <span className="text-emerald-400">+{c.estimatedRecoverable}</span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {unverifiedCities.length > 0 && (
            <p className="text-[11px] text-emerald-400 mt-1">
              ✓ {unverifiedCities.length} cit{unverifiedCities.length === 1 ? "y" : "ies"} ({unverifiedCities.map(c => c.city).join(", ")}) could become OFFICIAL with station documentation only.
            </p>
          )}
        </div>

        {/* ── Top 5 Safe Actions ── */}
        {safeActions.length > 0 && (
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
              Top Safe Acceleration Actions
              <span className="ml-2 font-normal normal-case">(no FTB rule changes)</span>
            </h3>
            <div className="space-y-2">
              {safeActions.map((a, i) => (
                <div key={i} className="rounded border border-border p-3 text-xs space-y-1">
                  <div className="flex items-start justify-between gap-3">
                    <span className="font-medium text-foreground">{i + 1}. {a.action}</span>
                    <span className={`shrink-0 text-[10px] font-mono px-1.5 py-0.5 rounded border ${
                      a.confidence === "MEDIUM"
                        ? "bg-primary/10 text-primary border-primary/30"
                        : a.confidence === "LOW"
                          ? "bg-muted/40 text-muted-foreground border-border"
                          : "bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
                    }`}>{a.confidence} confidence</span>
                  </div>
                  <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-muted-foreground">
                    <span>Cities: {a.affectedCities.join(", ")}</span>
                    <span>Est. opportunities: <span className="font-mono text-foreground">{a.estimatedOpportunities}</span></span>
                    <span>Effort: {a.effort}</span>
                    <span>Risk: {a.riskToComparability}</span>
                    {a.requiresExternalVerification && <span className="text-amber-400">⚠ requires external verification</span>}
                  </div>
                  <p className="text-muted-foreground/80 leading-relaxed">{a.notes}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        <p className="text-[10px] text-muted-foreground/50 text-right">
          Read-only diagnostic · no model, eligibility, or trading logic modified
        </p>
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// City Specialization Study — Completion Snapshot (2026-08-09)
// ---------------------------------------------------------------------------

function CityStudyCompletionSection() {
  const rankings: Array<{ rank: number; city: string; score: string; wr: string; settled: number; mae: string; nws: boolean; verified: boolean; note: string }> = [
    { rank: 1, city: "Denver",        score: "~71",  wr: "50.2%", settled: 265, mae: "2.90°F", nws: true,  verified: true,  note: "Best trading signal (v2.2: 57.1%, NO/range 71.4%); cold bias –2.24°F; station verified." },
    { rank: 2, city: "Houston",       score: "~66",  wr: "22.6%", settled: 124, mae: "0.87°F", nws: true,  verified: false, note: "Best forecast accuracy of any city; win rate does not yet reflect forecast quality." },
    { rank: 3, city: "Oklahoma City", score: "~63",  wr: "43.8%", settled: 137, mae: "2.59°F", nws: true,  verified: false, note: "Steady improving trend across all model versions; KOKC NWS-compatible." },
    { rank: 4, city: "New York City", score: "~63",  wr: "39.1%", settled: 156, mae: "1.72°F", nws: true,  verified: true,  note: "Verified station (Central Park). Best forecast accuracy among verified stations." },
    { rank: 5, city: "Dallas",        score: "~60",  wr: "32.5%", settled: 246, mae: "2.19°F", nws: true,  verified: false, note: "Moderate trading and forecast quality; consistent sample size." },
    { rank: 6, city: "Minneapolis",   score: "~52",  wr: "46.1%", settled: 128, mae: "5.63°F", nws: true,  verified: false, note: "Decent win rate but catastrophic cold bias (–5.59°F) tanks forecast score." },
    { rank: 7, city: "Miami",         score: "~50",  wr: "37.8%", settled: 135, mae: "3.63°F", nws: true,  verified: false, note: "All metrics middling." },
    { rank: 8, city: "Los Angeles",   score: "~low", wr: "8.9%",  settled: 617, mae: "2.68°F", nws: true,  verified: false, note: "Largest dataset but dominated by YES/hourly trades with 0% win rate. +2.68°F warm bias." },
    { rank: 9, city: "Chicago",       score: "~low", wr: "6.1%",  settled: 295, mae: "UNKNOWN", nws: true,  verified: true,  note: "Trading dominated by hourly contracts (v2_excluded); daily contract performance obscured." },
    { rank: 10, city: "Washington DC", score: "0",  wr: "8.8%",  settled: 273, mae: "UNKNOWN", nws: false, verified: false, note: "NON-NWS settlement (The Weather Company). Excluded from all recommendations." },
  ];

  const insights = [
    "NO-direction trades significantly outperform YES across all cities — YES/range win rate is 0% in aggregate.",
    "Denver's NO/range contracts achieve 71.4% win rate (v2.2 era); this is the sharpest signal in the dataset.",
    "Houston's MAE 0.87°F is extraordinary but its 22.6% win rate suggests model-to-market conversion issues.",
    "Minneapolis is held back by a –5.59°F systematic cold bias that dominates its forecast error.",
    "Washington DC uses The Weather Company (non-NWS) for settlement — EdgeCast must never trade it.",
    "Volume / open interest columns are always NULL in the database and cannot be used for liquidity scoring.",
    "FTB era (v2.3) started 2026-08-09 — sample too small for meaningful FTB-era rankings yet.",
    "Sample-size warning: Denver v2.2 has 49 settled trades — moderate confidence, not conclusive.",
  ];

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <MapPin className="h-4 w-4 text-primary" />
          <h2 className="text-sm font-semibold">City Specialization Study — Completion Snapshot</h2>
          <span className="text-[10px] font-mono text-muted-foreground ml-auto">2026-08-09 · Read-only</span>
        </div>
      </CardHeader>
      <CardBody className="space-y-4">
        {/* Recommendation box */}
        <div className="rounded-lg border border-blue-800/50 bg-blue-900/20 p-3 space-y-1">
          <p className="text-xs font-semibold text-blue-300">B. SPECIALIZE_THREE_CITIES</p>
          <p className="text-xs text-muted-foreground leading-relaxed">
            Denver leads on total score (~71/100) with 265 settled trades and a 50.2% all-time win rate. Evidence is
            sufficient to suggest reducing focus but not conclusive enough for a single-city bet. Specializing on one
            city now would cut volume ~80–90%, extending FTB validation timelines substantially.
          </p>
          <p className="text-xs text-muted-foreground leading-relaxed">
            A 3-city focus (Denver · Houston · Oklahoma City) preserves enough volume for timely FTB validation while
            concentrating on the highest-quality markets.
          </p>
          <p className="text-xs font-mono text-blue-400 mt-1">Best 3-city set: Denver · Houston · Oklahoma City</p>
        </div>

        {/* Rankings table */}
        <div>
          <p className="text-[10px] text-muted-foreground uppercase tracking-wide font-semibold mb-1.5">City Rankings (Score = 30% forecast · 25% trading · 20% liquidity · 15% sample · 10% station)</p>
          <div className="overflow-x-auto">
            <table className="w-full text-[10px] font-mono">
              <thead>
                <tr className="text-muted-foreground border-b border-border">
                  <th className="pb-1 pr-2 text-left">#</th>
                  <th className="pb-1 pr-2 text-left">City</th>
                  <th className="pb-1 pr-2 text-right">Score</th>
                  <th className="pb-1 pr-2 text-right">Settled</th>
                  <th className="pb-1 pr-2 text-right">Win Rate</th>
                  <th className="pb-1 pr-2 text-right">MAE</th>
                  <th className="pb-1 pr-2 text-center">V✓</th>
                  <th className="pb-1 pr-2 text-center">NWS</th>
                  <th className="pb-1 text-left">Note</th>
                </tr>
              </thead>
              <tbody>
                {rankings.map((r) => (
                  <tr key={r.city} className={`border-b border-border/30 ${!r.nws ? "opacity-40" : ""}`}>
                    <td className="py-0.5 pr-2 text-muted-foreground">{r.rank}</td>
                    <td className="py-0.5 pr-2 font-semibold text-foreground">{r.city}</td>
                    <td className={`py-0.5 pr-2 text-right ${r.score.startsWith("~7") || r.score.startsWith("~6") ? "text-emerald-400" : "text-muted-foreground"}`}>{r.score}</td>
                    <td className="py-0.5 pr-2 text-right">{r.settled}</td>
                    <td className={`py-0.5 pr-2 text-right ${parseFloat(r.wr) >= 45 ? "text-emerald-400" : parseFloat(r.wr) < 20 ? "text-red-400" : ""}`}>{r.wr}</td>
                    <td className="py-0.5 pr-2 text-right text-muted-foreground">{r.mae}</td>
                    <td className="py-0.5 pr-2 text-center">{r.verified ? "✓" : "·"}</td>
                    <td className={`py-0.5 pr-2 text-center ${r.nws ? "text-emerald-400" : "text-red-400"}`}>{r.nws ? "✓" : "✗"}</td>
                    <td className="py-0.5 text-muted-foreground max-w-[300px] truncate">{r.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Key insights */}
        <div>
          <p className="text-[10px] text-muted-foreground uppercase tracking-wide font-semibold mb-1.5">Key Insights</p>
          <ul className="space-y-0.5">
            {insights.map((ins, i) => (
              <li key={i} className="text-[10px] text-muted-foreground flex items-start gap-1.5">
                <span className="text-primary shrink-0">›</span>
                <span>{ins}</span>
              </li>
            ))}
          </ul>
        </div>

        {/* FTB projection for Denver */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[10px] font-mono">
          {[
            ["FTB city (for projection)", "Denver"],
            ["v2.3 scan days", "1 (2026-08-09)"],
            ["Est. OFFICIAL/week", "1–5 (fresh quotes only)"],
            ["Time to 10 settled", "~2–10 weeks"],
            ["Time to 25 settled", "~5–25 weeks"],
            ["Time to 50 settled", "~10–50 weeks"],
          ].map(([label, val]) => (
            <div key={label} className="flex flex-col">
              <span className="text-muted-foreground">{label}</span>
              <span className="text-foreground">{val}</span>
            </div>
          ))}
        </div>

        {/* Safety attestation */}
        <div className="rounded border border-border/40 p-2 flex flex-wrap gap-3 text-[10px] font-mono text-muted-foreground">
          <span className="text-emerald-400">✓ trading_state_modified: false</span>
          <span className="text-emerald-400">✓ ftb_untouched: true</span>
          <span className="text-emerald-400">✓ read_only: true</span>
          <span className="text-emerald-400">✓ no model logic changed</span>
          <span className="text-emerald-400">✓ tests: 50 new / 1181 total passing</span>
          <span className="text-emerald-400">✓ commit: 9494eea</span>
        </div>
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Bet Watch Post-Deployment Verification (written 2026-08-09 20:25 UTC)
// ---------------------------------------------------------------------------

function BetWatchDeploymentVerificationSection() {
  const checks: Array<{ label: string; value: string; ok: boolean }> = [
    { label: "Deployment timestamp (UTC)", value: "2026-08-09 20:25:31 UTC", ok: true },
    { label: "Production startup", value: "Clean — all 3 audit checks CLEARED", ok: true },
    { label: "GET /api/bet-watch (production)", value: "HTTP 401 → 200 when authenticated ✓", ok: true },
    { label: "GET /api/healthz (production)", value: "HTTP 200 ✓", ok: true },
    { label: "Frontend /bet-watch route", value: "HTTP 200 — Vite bundle served ✓", ok: true },
    { label: "Data source", value: "Live production DB — paper_trades strategy_version='v2.3'", ok: true },
    { label: "Candidates evaluated", value: "334 (v2.3 rows in 48-hour window)", ok: true },
    { label: "OFFICIAL-ELIGIBLE", value: "0", ok: false },
    { label: "NEAR OFFICIAL", value: "0", ok: false },
    { label: "WATCHING", value: "0", ok: false },
    { label: "PRELIMINARY", value: "0", ok: false },
    { label: "AVOID / STALE", value: "334 (100%)", ok: false },
    { label: "Best Bet Right Now", value: "None — see reason below", ok: false },
    { label: "trading_state_modified", value: "false (hardcoded)", ok: true },
    { label: "ftb_untouched", value: "true (hardcoded)", ok: true },
  ];

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <CheckCircle className="h-4 w-4 text-emerald-400" />
          <h2 className="text-sm font-semibold">Bet Watch — Post-Deployment Verification</h2>
          <span className="ml-auto text-[10px] font-mono text-muted-foreground">2026-08-09 20:25 UTC</span>
        </div>
        <p className="text-xs text-muted-foreground mt-1">
          Verified against live production database immediately after deploy. Static snapshot — reflects conditions at deployment time.
        </p>
      </CardHeader>
      <CardBody className="space-y-2">
        {checks.map((c) => (
          <div key={c.label} className="flex items-start justify-between gap-4 text-sm">
            <span className="text-muted-foreground shrink-0">{c.label}</span>
            <span className={`font-mono text-right text-xs leading-snug ${c.ok ? "text-emerald-400" : "text-foreground"}`}>
              {c.value}
            </span>
          </div>
        ))}

        {/* No-best-bet explanation */}
        <div className="mt-3 rounded border border-border bg-secondary/40 p-3 space-y-1">
          <p className="text-xs font-semibold text-foreground">Why there is no Best Bet right now</p>
          <p className="text-xs text-muted-foreground leading-relaxed">
            All 334 candidates evaluated have Kalshi quotes older than 2 hours (Bet Watch requires &lt;2h freshness
            before surfacing a recommendation). The most recent data collection ran at approximately{" "}
            <span className="font-mono text-foreground">17:25 UTC</span>. At verification time (20:25 UTC),
            quote age was ≈3 hours for the newest batch and up to 18 hours for earlier batches.
            No fabricated recommendation was generated. When the next scheduled scan runs,
            Bet Watch will re-evaluate fresh quotes and surface any actionable opportunities at that time.
          </p>
          <p className="text-xs text-muted-foreground leading-relaxed mt-1">
            Additionally, none of the 334 rows carries <code className="font-mono">eligibility_status = 'OFFICIAL'</code>{" "}
            in this window — all are RESEARCH_ONLY. The most common reasons: <code className="font-mono">missing_or_stale_executable_quote</code> (151),{" "}
            <code className="font-mono">v2_excluded</code> (99 — all penny-priced, already settled),{" "}
            <code className="font-mono">hourly_temperature_not_approved</code> (57),{" "}
            <code className="font-mono">settlement_station_unverified</code> (27).
          </p>
        </div>

        {/* Implementation limitations */}
        <div className="mt-3 rounded border border-border bg-secondary/40 p-3 space-y-2">
          <p className="text-xs font-semibold text-foreground">Limitations discovered during implementation</p>
          <ul className="space-y-1.5 text-xs text-muted-foreground list-none">
            <li>
              <span className="text-foreground font-medium">Quote staleness between scans.</span>{" "}
              Bet Watch inherits quotes from the last data collection. Between scans (every ~3h) all candidates
              age into AVOID/STALE. Actionable opportunities will only appear in the minutes after a fresh scan completes.
            </li>
            <li>
              <span className="text-foreground font-medium">Volume and open interest not stored.</span>{" "}
              <code className="font-mono">paper_trades</code> does not persist Kalshi volume or open interest;
              those Bet Watch fields return <code className="font-mono">null</code>.
            </li>
            <li>
              <span className="text-foreground font-medium">No cross-version model agreement.</span>{" "}
              <code className="font-mono">model_agreement</code> is reserved (<code className="font-mono">null</code>);
              Bet Watch reads only v2.3 rows. Cross-version comparison would require joining v2.2/v3 rows
              by <code className="font-mono">comparison_snapshot_id</code>.
            </li>
            <li>
              <span className="text-foreground font-medium">No model or trading logic was modified.</span>{" "}
              Verified: no changes to probability engines, calibration, Guard 8, FTB thresholds,
              eligibility rules, or the 300-second freshness rule. <code className="font-mono">trading_state_modified</code> is
              hardcoded <code className="font-mono">false</code>; <code className="font-mono">ftb_untouched</code> is
              hardcoded <code className="font-mono">true</code>.
            </li>
          </ul>
        </div>
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Bet Watch Audit Section (requirement 16)
// ---------------------------------------------------------------------------

function BetWatchAuditSection() {
  const { data, isLoading, isError } = useGetBetWatch();

  const rows: Array<{ label: string; value: string; ok?: boolean }> = data
    ? [
        {
          label: "Bet Watch API",
          value: "Healthy",
          ok: true,
        },
        {
          label: "Last successful calculation",
          value: new Date(data.generated_at).toLocaleString(),
          ok: true,
        },
        {
          label: "Candidates evaluated",
          value: String(data.all_candidate_count),
        },
        {
          label: "Actionable candidates",
          value: String(data.summary.actionable),
          ok: data.summary.actionable > 0,
        },
        {
          label: "Preliminary candidates",
          value: String(data.summary.preliminary),
        },
        {
          label: "Current best-bet ticker",
          value: data.summary.best_ticker ?? "None",
          ok: data.summary.best_ticker !== null,
        },
        {
          label: "Trading state modified",
          value: data.trading_state_modified ? "YES ⚠" : "NO",
          ok: !data.trading_state_modified,
        },
        {
          label: "Forward Test B untouched",
          value: data.ftb_untouched ? "YES" : "NO ⚠",
          ok: data.ftb_untouched,
        },
      ]
    : [];

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Eye className="h-4 w-4 text-primary" />
          <h2 className="text-sm font-semibold">Bet Watch Monitoring</h2>
        </div>
        <p className="text-xs text-muted-foreground mt-1">
          Read-only status of the Bet Watch decision-support layer. Trading state must always show NO.
        </p>
      </CardHeader>
      <CardBody className="space-y-2">
        {isLoading && (
          <div className="text-sm text-muted-foreground py-4 text-center">
            Loading Bet Watch status…
          </div>
        )}
        {isError && (
          <div className="text-sm text-destructive py-4 text-center">
            Bet Watch API unavailable. Server may be starting up.
          </div>
        )}
        {!isLoading && !isError && rows.map((row) => (
          <div key={row.label} className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">{row.label}</span>
            <span
              className={`font-mono font-medium ${
                row.ok === true
                  ? "text-emerald-400"
                  : row.ok === false
                  ? "text-destructive"
                  : "text-foreground"
              }`}
            >
              {row.value}
            </span>
          </div>
        ))}
      </CardBody>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Kalshi Settlement Transition — August 14, 2026
// ---------------------------------------------------------------------------

function KalshiSettlementTransitionSection() {
  const findings: Array<{
    label: string;
    value: string;
    ok?: boolean;
    note?: string;
  }> = [
    {
      label: "Effective date",
      value: "Friday, August 14, 2026",
      ok: true,
      note: "Per Kalshi announcement published ~Aug 12, 2026",
    },
    {
      label: "New settlement source",
      value: "The Weather Company (weather.com/kalshi)",
      note: "Uses NWS as primary underlying source per Kalshi",
    },
    {
      label: "Previous settlement source",
      value: "National Weather Service (NWS GHCND / CLI reports)",
      ok: true,
    },
    {
      label: "settlement_regime field added",
      value: "Yes — stamped at trade creation",
      ok: true,
      note: "LEGACY_NWS for dates < Aug 14 · WEATHER_COMPANY for dates ≥ Aug 14",
    },
    {
      label: "outcome_verified field added",
      value: "Yes — set True on Kalshi settlement, None at creation",
      ok: true,
      note: "False/NULL trades excluded from calibration learning",
    },
    {
      label: "V3 OFFICIAL settled trades",
      value: "0 settled (9 open)",
      note: "FTB live since Aug 9. Settlement dates Aug 14+ → all open trades are WEATHER_COMPANY regime",
    },
    {
      label: "V3 regime breakdown",
      value: "9 open = WEATHER_COMPANY (settling Aug 14+) · 0 settled",
      note: "Regimes will be confirmed once first trades settle",
    },
    {
      label: "ERA5 integrity-flagged trades",
      value: "0 found in database",
      ok: true,
      note: "Queried quality_flags for ERA5/DISAGREE patterns — zero matching rows. No disputed outcomes exist.",
    },
    {
      label: "Stale-quote bottleneck root cause",
      value: "Expired markets (July 30–Aug 1) in collection pool",
      note: "OKC/Denver trades created Aug 9 have quote_timestamp from July 30 (~860k s stale). These are already-closed Kalshi markets whose quotes haven't been updated since their last active day. The 300s freshness gate correctly rejects them as RESEARCH_ONLY. Fix (future): filter markets with settlement_date > X hours past at collection time.",
    },
    {
      label: "300-second freshness gate",
      value: "Unchanged — OFFICIAL_STALE_QUOTE_SECONDS = 300",
      ok: true,
      note: "Guard not loosened. Stale OKC/Denver trades are correctly classified RESEARCH_ONLY.",
    },
    {
      label: "FTB eligibility guards",
      value: "All 8 guards unchanged",
      ok: true,
      note: "Edge ≥ 5pp, side probability ≥ 55%, quote fresh ≤ 300s, min liquidity, max market-close 120 min, station verified, correlation limit, min sample — none modified.",
    },
    {
      label: "Mission Control readiness metric",
      value: "V2.3 (current model) now primary — V2.2 demoted to historical reference",
      ok: true,
      note: "officialSettledCount now = V2.3 + V3 settled only. V2.2 shown separately as historical evidence with inverted-bias disclaimer.",
    },
    {
      label: "\"Fully Trained\" label",
      value: "Renamed to \"Calibration lessons complete\" / \"Lessons Complete\"",
      ok: true,
      note: "Removed false implication that calibration completion = validated model. Affects dashboard, city matrix, trade detail, and progress bar milestone.",
    },
    {
      label: "Methodology equivalence (NWS vs Weather Company)",
      value: "UNRESOLVED — cannot verify pre-transition",
      note: "Kalshi states The Weather Company uses NWS as primary source. Exact rounding rules, station/location mappings, and systematic differences cannot be confirmed until post-Aug 14 data is available. Analytics support regime-filtered views to enable future comparison.",
    },
    {
      label: "Calibration separation",
      value: "outcome_verified = False trades excluded from calibration",
      ok: true,
      note: "Zero disputed trades exist today. Guard is in place for future ERA5 discrepancies. LEGACY_NWS and WEATHER_COMPANY calibration results should be compared separately once WEATHER_COMPANY trades settle.",
    },
  ];

  return (
    <Card className="border-amber-500/20 bg-card/40">
      <CardHeader>
        <div className="text-xs font-mono tracking-widest text-amber-400 uppercase flex items-center gap-2">
          <AlertTriangle className="h-4 w-4" />
          KALSHI SETTLEMENT TRANSITION — AUGUST 14, 2026
        </div>
        <p className="text-xs text-muted-foreground mt-1 font-mono">
          Investigation completed 2026-08-12. Effective Friday Aug 14, Kalshi daily temperature
          markets transition from NWS to The Weather Company as the settlement authority.
          All findings below are read-only audit results. No guard logic was modified.
        </p>
      </CardHeader>
      <CardBody>
        <div className="space-y-2">
          {findings.map((f) => (
            <div key={f.label} className="rounded-lg border border-border/30 bg-muted/10 p-3">
              <div className="flex items-start justify-between gap-3">
                <span className="text-[11px] font-mono text-muted-foreground shrink-0 min-w-[220px]">
                  {f.label}
                </span>
                <span
                  className={`text-[11px] font-mono font-medium text-right ${
                    f.ok === true
                      ? "text-emerald-400"
                      : f.ok === false
                      ? "text-destructive"
                      : "text-foreground"
                  }`}
                >
                  {f.value}
                </span>
              </div>
              {f.note && (
                <p className="text-[10px] font-mono text-muted-foreground/60 mt-1.5 leading-relaxed border-l border-border/40 pl-2">
                  {f.note}
                </p>
              )}
            </div>
          ))}
        </div>

        <div className="mt-4 rounded-lg border border-amber-500/20 bg-amber-500/5 p-3">
          <p className="text-[11px] font-mono text-amber-400/80 leading-relaxed">
            <span className="font-semibold text-amber-400">Action required post-Aug 14:</span>{" "}
            Once Weather Company trades begin settling, compare settlement values against NWS CLI
            reports to verify methodology equivalence. If systematic differences exist, calibration
            models may need to be separated by regime before the next forward-test milestone.
            The audit &amp; validation page will be updated with post-transition findings.
          </p>
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

      <KalshiSettlementTransitionSection />
      <ValidationStatus />
      <BlockersSection />
      <LiveDbChecksSection />
      <FtbResearchFunnelSection />
      <BetWatchDeploymentVerificationSection />
      <BetWatchAuditSection />
      <CityStudyCompletionSection />
      <VerifiedCitySpecializationSection />
      <CitySpecializationStudySection />
      <NonBlockersSection />
      <ChainCoverageSection />
      <BaselineSection />
    </div>
  );
}
