/**
 * Audit & Validation
 * ==================
 * Read-only dashboard showing current model audit findings,
 * Forward Test B blockers, and validation status.
 * No API calls — all content is sourced from the completed read-only audit.
 */
import { ReactNode } from "react";
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
} from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Severity = "CRITICAL" | "HIGH" | "MODERATE" | "LOW";
type FindingStatus = "Confirmed" | "Suspected" | "Required" | "Confirmed structural issue";
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
    status: "Confirmed",
    mustFix: "Yes",
    finding:
      "Integer threshold probability formulas do not account for NWS integer rounding. Approximate probability impact identified in audit: ~5.7 percentage points in representative cases.",
    requiredAction:
      "Apply settlement-aware ±0.5°F boundary handling for integer thresholds. Do not apply this correction to half-integer thresholds.",
  },
  {
    id: 2,
    title: "NWS range contract rounding",
    severity: "CRITICAL",
    status: "Confirmed",
    mustFix: "Yes",
    finding:
      "Range probability calculations currently use raw lower/upper bounds instead of settlement-aware lower−0.5 / upper+0.5 boundaries. Representative impact estimated around ~11 percentage points.",
    requiredAction: "Use settlement-aware integration bounds.",
  },
  {
    id: 3,
    title: "ERA5 local-date extraction",
    severity: "CRITICAL",
    status: "Suspected",
    mustFix: "Yes — if confirmed",
    finding:
      "target_settlement_date may be derived from a UTC close/expiration timestamp and sliced as a UTC calendar date. This may cause CDT/MDT/PDT markets to be verified against the following local day.",
    requiredAction:
      "Run DB verification. If confirmed, convert UTC timestamp to settlement-station local timezone before extracting the verification date.",
  },
  {
    id: 4,
    title: "Hourly sigma floor",
    severity: "HIGH",
    status: "Confirmed",
    mustFix: "Yes",
    finding:
      "Hourly contracts can use the daily sigma floor instead of the intended hourly sigma behavior.",
    requiredAction:
      "Pass hourly=True for hourly_threshold contracts in V2.1/V2.2 logic.",
  },
  {
    id: 5,
    title: "Station verification eligibility",
    severity: "HIGH",
    status: "Confirmed",
    mustFix: "Yes",
    finding:
      "get_verified_station can return an unverified station object, and V2.2/V3 paper-trading eligibility does not consistently check .verified.",
    requiredAction:
      "Fix verification behavior and enforce verified stations before OFFICIAL eligibility.",
  },
  {
    id: 6,
    title: "Philadelphia / San Antonio coordinates",
    severity: "HIGH",
    status: "Confirmed",
    mustFix: "Yes",
    finding:
      "Forecast coordinates require correction to align with intended settlement stations.",
    requiredAction:
      "Correct SERIES_TO_CITY coordinates before those cities participate in Forward Test B.",
  },
  {
    id: 7,
    title: "Calibration reset for corrected strategy",
    severity: "HIGH",
    status: "Confirmed structural issue",
    mustFix: "Yes",
    finding:
      'V2.1/V2.2 look up calibration rows under strategy_version="v2.0", but no calibration write mechanism exists in the current codebase. Existing rows, if any, have unknown provenance and will become invalid after settlement-rounding corrections.',
    requiredAction:
      "Audit existing rows, do not inherit them, and start corrected strategy as v2.3 with calibration bypassed (factor 1.0) until clean corrected-version outcomes are available.",
  },
  {
    id: 8,
    title: "New Forward Test start boundary",
    severity: "HIGH",
    status: "Required",
    mustFix: "Yes",
    finding:
      "Old and corrected model results must not be mixed.",
    requiredAction:
      "After corrected model deployment, set a new FORWARD_TEST_START timestamp and clearly label all prior data as Baseline Forward Test A.",
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
const COUNT_RESOLVED = 0;

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
            <div className="text-sm font-semibold text-orange-400">Corrections required before new clean forward test</div>
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
          <CounterCard label="Confirmed" value={COUNT_CONFIRMED} variant="default" />
          <CounterCard label="Suspected" value={COUNT_SUSPECTED} variant="muted" />
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
      <DbChecksSection />
      <NonBlockersSection />
      <ChainCoverageSection />
      <BaselineSection />
    </div>
  );
}
