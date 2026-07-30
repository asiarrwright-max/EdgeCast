/**
 * V2.1 Analytics API hooks
 * ========================
 * Covers: retrospective comparison, calibration, readiness panel,
 * station coverage, OKC explanation, consensus guard backtest.
 */
import { useQuery } from "@tanstack/react-query";
import type { UseQueryOptions, UseQueryResult } from "@tanstack/react-query";
import { customFetch } from "./custom-fetch";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface RetroTrade {
  tradeId: number;
  marketTicker: string;
  city: string;
  forecastTimestamp: string | null;
  settlementDate: string | null;
  settlementStation: string;
  stationVerifiedForV21: boolean;
  // V2.0
  sigmaV20: number | null;
  ecYesProbV20: number | null;
  ecSideProbV20: number | null;
  edgePpV20: number | null;
  confidenceLabelV20: string | null;
  fallbackLevelV20: string | null;
  // V2.1 recomputed
  sigmaV21: number;
  sigmaChanged: boolean;
  ecYesProbV21: number | null;
  newEdgePp: number | null;
  v21WouldTrade: boolean;
  v21SkipReason: string | null;
  // Outcome
  direction: string;
  sideMarketPrice: number | null;
  kalshiResult: string | null;
  outcome: string | null;
  plActual: number;
  plHypotheticalV21: number;
}

export interface RetroSummary {
  totalInSample: number;
  v21WouldTake: number;
  v21WouldSkip: number;
  lossesAvoided: number;
  winsSkipped: number;
  v21WinRate: number | null;
  v21Roi: number | null;
  v20Roi: number | null;
  v21BrierScore: number | null;
  avgEdgeV20Pp: number | null;
  avgEdgeV21Pp: number | null;
  avgSigmaV20: number | null;
  avgSigmaV21: number | null;
}

export interface RetrospectiveData {
  disclaimer: string;
  sampleSize: number;
  trades: RetroTrade[];
  summary: RetroSummary;
}

export interface V21CalibrationBucket {
  bucket: string;
  bucketLo?: number;
  bucketHi?: number;
  count: number;
  avgPredictedProb: number | null;
  actualWinRate: number | null;
  calibrationDiff: number | null;
  avgBrierScore: number | null;
  avgRoi: number | null;
  lowSample: boolean;
}

export interface CalibrationData {
  v20: V21CalibrationBucket[];
  v21: V21CalibrationBucket[];
  lowSampleThreshold: number;
  note: string;
}

export interface ReadinessData {
  totalPredictions: number;
  openTrades: number;
  settledTrades: number;
  wins: number;
  losses: number;
  winRate: number | null;
  roi: number | null;
  brierScore: number | null;
  avgSigma: number | null;
  verifiedCities: string[];
  verifiedCityCount: number;
  unverifiedCities: string[];
  unverifiedCityCount: number;
  pctLearnedSigma: number;
  pctFallbackSigma: number;
  bucketsWithMinSample: number;
  minSampleThreshold: number;
  readinessStage: string;
  unresolvedActiveStations: string[];
  criteria: {
    settledNeeded: number;
    bucketsNeeded: number;
    currentSettled: number;
    currentBuckets: number;
  };
}

export type CityStatus = "active" | "inactive" | "blocked";

export interface StationEntry {
  city: string;
  stationName: string;
  ghcndStationId: string;
  lat: number;
  lon: number;
  timezone: string;
  verified: boolean;
  nwsSettlement: boolean;
  source: string | null;
  notes: string | null;
  cityStatus: CityStatus;
  cityStatusReason: string | null;
  lastMarketSeenAt: string | null;
  v21TradingEnabled: boolean;
  v21TradeCount: number;
  observationCount: number;
}

export interface StationCoverageData {
  stations: StationEntry[];
  verifiedCount: number;
  unverifiedCount: number;
  activeCount: number;
  inactiveCount: number;
  blockedCount: number;
  totalCount: number;
  note: string;
}

export interface CityStatusEntry {
  city: string;
  status: CityStatus;
  reason: string | null;
  verified: boolean;
  nwsSettlement: boolean;
  lastMarketSeenAt: string | null;
}

export interface CityAvailabilityData {
  cities: CityStatusEntry[];
  activeCount: number;
  inactiveCount: number;
  blockedCount: number;
  totalCount: number;
  activeCities: string[];
  inactiveCities: string[];
  blockedCities: string[];
  discoveryNote: string;
}

export interface OkcForecastSource {
  source: string;
  forecastTimestamp: string;
  forecastedValue: number | null;
  forecastCoordinates: string;
  settlementStation: string;
  actualOfficialValue: number | null;
  absoluteError: number | null;
  notes: string;
}

export interface OkcExplanationData {
  event: {
    city: string;
    tradeDate: string;
    marketTicker: string;
    direction: string;
    contractDescription: string;
    actualOfficialHigh: number;
    actualNote: string;
  };
  forecastSources: OkcForecastSource[];
  rootCauseAssessment: {
    verdict: string;
    primaryCause: string;
    secondaryCause: string;
    sigmaFailure: string;
    fixes: string[];
  };
  dataQualityNote: string;
}

export interface ConsensusBacktestData {
  threshold: number;
  total_settled: number;
  guard_would_block: number;
  guard_would_allow: number;
  wins_avoided_by_guard: number;
  losses_avoided_by_guard: number;
  pnl_with_guard: number;
  pnl_without_guard: number;
  roi_with_guard_pct: number;
  roi_without_guard_pct: number;
  roi_delta_pp: number;
  note: string;
  guardStatus: string;
  experimentalWarning: string;
}

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

export function useGetV21Retrospective<TData = RetrospectiveData, TError = unknown>(
  options?: UseQueryOptions<RetrospectiveData, TError, TData>
): UseQueryResult<TData, TError> {
  return useQuery({
    queryKey: ["/api/analytics/v21/retrospective"],
    queryFn: ({ signal }) =>
      customFetch<RetrospectiveData>("/api/analytics/v21/retrospective", { signal, method: "GET" }),
    ...options,
  }) as UseQueryResult<TData, TError>;
}

export function useGetV21Calibration<TData = CalibrationData, TError = unknown>(
  options?: UseQueryOptions<CalibrationData, TError, TData>
): UseQueryResult<TData, TError> {
  return useQuery({
    queryKey: ["/api/analytics/v21/calibration"],
    queryFn: ({ signal }) =>
      customFetch<CalibrationData>("/api/analytics/v21/calibration", { signal, method: "GET" }),
    ...options,
  }) as UseQueryResult<TData, TError>;
}

export function useGetV21Readiness<TData = ReadinessData, TError = unknown>(
  options?: UseQueryOptions<ReadinessData, TError, TData>
): UseQueryResult<TData, TError> {
  return useQuery({
    queryKey: ["/api/analytics/v21/readiness"],
    queryFn: ({ signal }) =>
      customFetch<ReadinessData>("/api/analytics/v21/readiness", { signal, method: "GET" }),
    ...options,
  }) as UseQueryResult<TData, TError>;
}

export function useGetStationCoverage<TData = StationCoverageData, TError = unknown>(
  options?: UseQueryOptions<StationCoverageData, TError, TData>
): UseQueryResult<TData, TError> {
  return useQuery({
    queryKey: ["/api/analytics/v21/stations"],
    queryFn: ({ signal }) =>
      customFetch<StationCoverageData>("/api/analytics/v21/stations", { signal, method: "GET" }),
    ...options,
  }) as UseQueryResult<TData, TError>;
}

export function useGetOkcExplanation<TData = OkcExplanationData, TError = unknown>(
  options?: UseQueryOptions<OkcExplanationData, TError, TData>
): UseQueryResult<TData, TError> {
  return useQuery({
    queryKey: ["/api/analytics/v21/okc-explanation"],
    queryFn: ({ signal }) =>
      customFetch<OkcExplanationData>("/api/analytics/v21/okc-explanation", { signal, method: "GET" }),
    ...options,
  }) as UseQueryResult<TData, TError>;
}

export function useGetConsensusBacktest<TData = ConsensusBacktestData, TError = unknown>(
  options?: UseQueryOptions<ConsensusBacktestData, TError, TData>
): UseQueryResult<TData, TError> {
  return useQuery({
    queryKey: ["/api/analytics/v21/consensus-backtest"],
    queryFn: ({ signal }) =>
      customFetch<ConsensusBacktestData>("/api/analytics/v21/consensus-backtest", { signal, method: "GET" }),
    ...options,
  }) as UseQueryResult<TData, TError>;
}

export function useGetCityAvailability<TData = CityAvailabilityData, TError = unknown>(
  options?: UseQueryOptions<CityAvailabilityData, TError, TData>
): UseQueryResult<TData, TError> {
  return useQuery({
    queryKey: ["/api/analytics/v21/city-availability"],
    queryFn: ({ signal }) =>
      customFetch<CityAvailabilityData>("/api/analytics/v21/city-availability", { signal, method: "GET" }),
    staleTime: 5 * 60 * 1000, // 5 min — collection job runs every 3h, no need to hammer
    ...options,
  }) as UseQueryResult<TData, TError>;
}
