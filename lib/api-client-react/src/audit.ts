/**
 * EdgeCast Audit API hooks — Strategy Differences & Loss Audit report.
 */
import { useQuery } from "@tanstack/react-query";
import type { UseQueryOptions, UseQueryResult } from "@tanstack/react-query";
import { customFetch } from "./custom-fetch";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ComparisonRow {
  ticker: string;
  title: string | null;
  city: string | null;
  settlementDate: string | null;
  contractType: string | null;
  probDiffPp: number | null;
  bothTraded: boolean;
  onlyV1: boolean;
  onlyV2: boolean;
  diffSide: boolean;
  differenceReason: string;
  v2BiasCorrection: number | null;
  v2SigmaUsed: number | null;
  v2FallbackLevel: string | null;
  v2CalibrationAdj: number | null;
  v2UsedHistorical: boolean;
  // v1 fields
  v1Traded: boolean;
  v1Status: string | null;
  v1Direction: string | null;
  v1EcYesProb: number | null;
  v1EcSideProb: number | null;
  v1EntryPrice: number | null;
  v1Edge: number | null;
  v1Outcome: string | null;
  v1Pl: number | null;
  v1Stake: number | null;
  v1QualityFlags: string[];
  // v2 fields
  v2Traded: boolean;
  v2Status: string | null;
  v2Direction: string | null;
  v2EcYesProb: number | null;
  v2EcSideProb: number | null;
  v2EntryPrice: number | null;
  v2Edge: number | null;
  v2Outcome: string | null;
  v2Pl: number | null;
  v2Stake: number | null;
  v2QualityFlags: string[];
}

export interface StrategyDifferencesResult {
  rows: ComparisonRow[];
  total: number;
}

export interface LossAuditBucket {
  bucket: string;
  settledCount: number;
  wins: number;
  losses: number;
  actualWinRate: number | null;
  avgPredictedProb: number | null;
  avgEntryPrice: number | null;
  totalStake: number | null;
  profitLoss: number | null;
  roi: number | null;
  expectedWins: number | null;
}

export interface LossAuditTrade {
  id: number;
  ticker: string;
  city: string | null;
  settlementDate: string | null;
  direction: string | null;
  ecSideProb: number | null;
  entryPrice: number | null;
  edge: number | null;
  stake: number | null;
  outcome: string;
  pl: number | null;
  settlementTimestamp: string | null;
}

export interface LossAuditResult {
  buckets: LossAuditBucket[];
  summary: {
    totalSettled: number;
    totalWins: number;
    totalLosses: number;
    overallWinRate: number | null;
    expectedWins: number | null;
    actualWins: number;
    expectedVsActualDiff: number | null;
    longestLosingStreak: number;
  };
  trades: LossAuditTrade[];
}

export interface LongShotBucket {
  bucket: string;
  settledCount: number;
  wins: number;
  losses: number;
  avgEcProb: number | null;
  expectedWins: number | null;
  actualWins: number;
  totalStake: number | null;
  profitLoss: number | null;
  roi: number | null;
}

export interface LongShotResult {
  buckets: LongShotBucket[];
  total: number;
  conclusion: string;
}

export interface SettlementTrade {
  id: number;
  ticker: string;
  city: string | null;
  settlementDate: string | null;
  direction: string | null;
  kalshiResult: string | null;
  recordedOutcome: string | null;
  expectedOutcome: string | null;
  classification: string;
  stake: number | null;
  quantity: number | null;
  grossPayout: number | null;
  profitLoss: number | null;
  expectedGrossPayout: number | null;
  expectedProfitLoss: number | null;
  payoutCorrect: boolean | null;
  settlementTimestamp: string | null;
  warnings: string | null;
  apiError: boolean;
}

export interface SettlementCheckResult {
  trades: SettlementTrade[];
  summary: {
    total: number;
    correctlySettled: number;
    incorrectlySettled: number;
    unresolved: number;
    missingResult: number;
    apiError: number;
  };
}

export interface V2ReadinessRow {
  city: string;
  variable: string;
  leadTimeBucket: string;
  month: number | null;
  sampleSize: number;
  meanBias: number | null;
  mae: number | null;
  stdDev: number | null;
  sufficientForSigma: boolean;
  sufficientForCalib: boolean;
  tier: "full" | "sigma_only" | "fallback";
  lastComputedAt: string | null;
}

export interface V2ReadinessResult {
  detailRows: V2ReadinessRow[];
  cityVariableSummary: Array<{
    city: string;
    variable: string;
    totalObservations: number;
    groupCount: number;
    sufficientForSigma: number;
    sufficientForCalib: number;
  }>;
  summary: {
    totalGroups: number;
    fallbackGroups: number;
    sigmaOnlyGroups: number;
    fullGroups: number;
    pctFallback: number | null;
    pctReady: number | null;
    pctFull: number | null;
  };
}

// ---------------------------------------------------------------------------
// Fetch functions
// ---------------------------------------------------------------------------

export interface StrategyDifferencesParams {
  filter?: string;
  min_prob_diff?: number;
  v2_adj?: string;
  status?: string;
}

export const getStrategyDifferences = (
  params: StrategyDifferencesParams = {},
  options?: Parameters<typeof customFetch>[1]
): Promise<StrategyDifferencesResult> => {
  const qs = new URLSearchParams();
  if (params.filter) qs.set("filter", params.filter);
  if (params.min_prob_diff) qs.set("min_prob_diff", String(params.min_prob_diff));
  if (params.v2_adj) qs.set("v2_adj", params.v2_adj);
  if (params.status) qs.set("status", params.status);
  const q = qs.toString();
  return customFetch<StrategyDifferencesResult>(
    `/api/audit/strategy-differences${q ? `?${q}` : ""}`,
    { ...options, method: "GET" }
  );
};

export const getLossAudit = (
  options?: Parameters<typeof customFetch>[1]
): Promise<LossAuditResult> =>
  customFetch<LossAuditResult>("/api/audit/loss-audit", { ...options, method: "GET" });

export const getLongShot = (
  options?: Parameters<typeof customFetch>[1]
): Promise<LongShotResult> =>
  customFetch<LongShotResult>("/api/audit/long-shot", { ...options, method: "GET" });

export const getSettlementCheck = (
  options?: Parameters<typeof customFetch>[1]
): Promise<SettlementCheckResult> =>
  customFetch<SettlementCheckResult>("/api/audit/settlement-check", { ...options, method: "GET" });

export const getV2Readiness = (
  options?: Parameters<typeof customFetch>[1]
): Promise<V2ReadinessResult> =>
  customFetch<V2ReadinessResult>("/api/audit/v2-readiness", { ...options, method: "GET" });

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

export function useGetStrategyDifferences(
  params: StrategyDifferencesParams = {},
  options?: UseQueryOptions<StrategyDifferencesResult, unknown, StrategyDifferencesResult>
): UseQueryResult<StrategyDifferencesResult, unknown> {
  return useQuery({
    queryKey: ["/api/audit/strategy-differences", params],
    queryFn: ({ signal }) => getStrategyDifferences(params, { signal }),
    ...options,
  }) as UseQueryResult<StrategyDifferencesResult, unknown>;
}

export function useGetLossAudit(
  options?: UseQueryOptions<LossAuditResult, unknown, LossAuditResult>
): UseQueryResult<LossAuditResult, unknown> {
  return useQuery({
    queryKey: ["/api/audit/loss-audit"],
    queryFn: ({ signal }) => getLossAudit({ signal }),
    ...options,
  }) as UseQueryResult<LossAuditResult, unknown>;
}

export function useGetLongShot(
  options?: UseQueryOptions<LongShotResult, unknown, LongShotResult>
): UseQueryResult<LongShotResult, unknown> {
  return useQuery({
    queryKey: ["/api/audit/long-shot"],
    queryFn: ({ signal }) => getLongShot({ signal }),
    ...options,
  }) as UseQueryResult<LongShotResult, unknown>;
}

export function useGetSettlementCheck(
  options?: UseQueryOptions<SettlementCheckResult, unknown, SettlementCheckResult>
): UseQueryResult<SettlementCheckResult, unknown> {
  return useQuery({
    queryKey: ["/api/audit/settlement-check"],
    queryFn: ({ signal }) => getSettlementCheck({ signal }),
    ...options,
  }) as UseQueryResult<SettlementCheckResult, unknown>;
}

export function useGetV2Readiness(
  options?: UseQueryOptions<V2ReadinessResult, unknown, V2ReadinessResult>
): UseQueryResult<V2ReadinessResult, unknown> {
  return useQuery({
    queryKey: ["/api/audit/v2-readiness"],
    queryFn: ({ signal }) => getV2Readiness({ signal }),
    ...options,
  }) as UseQueryResult<V2ReadinessResult, unknown>;
}

// ---------------------------------------------------------------------------
// V2 Learning Progress types
// ---------------------------------------------------------------------------

export interface MilestoneProgress {
  current: number;
  milestones: number[];
  reached: boolean[];
  nextMilestone: number | null;
  neededForNext: number | null;
}

export interface CityFesGroup {
  variable: string;
  leadTimeBucket: string;
  month: number | null;
  sampleSize: number;
  fallbackLevel: string | null;
  mae: number | null;
  stdDev: number | null;
  meanBias: number | null;
}

export interface CityLearningRow {
  city: string;
  stationVerified: boolean;
  stationName: string;
  readinessStatus: string;
  readinessLabel: string;
  usableObservations: number;
  totalObservations: number;
  sourceBreakdown: Record<string, number>;
  sourceQualityLabel: "ghcnd" | "era5" | "mixed" | "none";
  cityFesGroupCount: number;
  cityFesReadyCount: number;
  milestoneProgress: MilestoneProgress;
  v2TradesTotal: number;
  v2TradesFallback: number;
  v2TradesHistorical: number;
  latestObservationDate: string | null;
  fesGroups: CityFesGroup[];
}

export interface ErrorGroupRow {
  city: string;
  variable: string;
  leadTimeBucket: string;
  month: number | null;
  sampleSize: number;
  fallbackLevel: string | null;
  mae: number | null;
  stdDev: number | null;
  meanBias: number | null;
  sourceQualityLabel: string;
  lastComputedAt: string | null;
}

export interface LearningProgressSummary {
  totalCities: number;
  citiesLearned: number;
  citiesPartiallyLearned: number;
  citiesCollecting: number;
  citiesNotCollecting: number;
  citiesDataQualityIssue: number;
  totalUsableObservations: number;
  totalFesGroups: number;
  cityFesGroups: number;
  globalFesGroups: number;
  v2TotalTrades: number;
  v2TradesUsingHistorical: number;
  v2TradesUsingFallback: number;
  v1TotalTrades: number;
}

export interface LearningProgressResult {
  summary: LearningProgressSummary;
  cities: CityLearningRow[];
  errorGroups: ErrorGroupRow[];
}

export interface CityDetailVerification {
  id: number;
  targetDate: string;
  weatherVariable: string;
  forecastValue: number | null;
  actualValue: number | null;
  forecastError: number | null;
  sourceLabel: string | null;
  ghcndStationId: string | null;
  leadTimeDays: number | null;
  month: number | null;
  season: string | null;
  createdAt: string | null;
}

export interface CityDetailTrade {
  id: number;
  ticker: string;
  direction: string;
  status: string;
  outcome: string | null;
  fallbackLevel: string | null;
  sigmaUsed: number | null;
  biasCorrection: number | null;
  calibrationAdj: number | null;
  stake: number | null;
  pl: number | null;
  targetDate: string | null;
  createdAt: string | null;
}

export interface CityDetailResult {
  city: string;
  readinessStatus: string;
  readinessLabel: string;
  stationInfo: {
    stationName: string | null;
    ghcndStationId: string | null;
    verified: boolean | null;
    notes: string | null;
  };
  milestoneProgress: MilestoneProgress;
  sourceBreakdown: Record<string, number>;
  sourceQualityLabel: string;
  verifications: CityDetailVerification[];
  fesGroups: CityFesGroup[];
  v2Trades: CityDetailTrade[];
}

// ---------------------------------------------------------------------------
// Fetch functions — V2 Learning Progress
// ---------------------------------------------------------------------------

export const getV2LearningProgress = (
  options?: Parameters<typeof customFetch>[1]
): Promise<LearningProgressResult> =>
  customFetch<LearningProgressResult>("/api/audit/v2-learning-progress", {
    ...options,
    method: "GET",
  });

export const getV2CityDetail = (
  city: string,
  options?: Parameters<typeof customFetch>[1]
): Promise<CityDetailResult> =>
  customFetch<CityDetailResult>(
    `/api/audit/v2-city-detail/${encodeURIComponent(city)}`,
    { ...options, method: "GET" }
  );

// ---------------------------------------------------------------------------
// Hooks — V2 Learning Progress
// ---------------------------------------------------------------------------

export function useGetV2LearningProgress(
  options?: UseQueryOptions<LearningProgressResult, unknown, LearningProgressResult>
): UseQueryResult<LearningProgressResult, unknown> {
  return useQuery({
    queryKey: ["/api/audit/v2-learning-progress"],
    queryFn: ({ signal }) => getV2LearningProgress({ signal }),
    ...options,
  }) as UseQueryResult<LearningProgressResult, unknown>;
}

export function useGetV2CityDetail(
  city: string,
  options?: UseQueryOptions<CityDetailResult, unknown, CityDetailResult>
): UseQueryResult<CityDetailResult, unknown> {
  return useQuery({
    queryKey: ["/api/audit/v2-city-detail", city],
    queryFn: ({ signal }) => getV2CityDetail(city, { signal }),
    enabled: city.length > 0,
    ...options,
  }) as UseQueryResult<CityDetailResult, unknown>;
}
