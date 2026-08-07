/**
 * Forward Test Diagnostics — API hook for
 * GET /api/paper-trades/forward-test-diagnostics.
 *
 * Returns calibration metrics, probability-band breakdown, false-confidence
 * losses, and settlement-integrity flags for settled OFFICIAL forward-test
 * trades.  READ-ONLY — nothing is mutated.
 */
import { useQuery } from "@tanstack/react-query";
import type { UseQueryOptions, UseQueryResult, QueryKey } from "@tanstack/react-query";
import { customFetch } from "./custom-fetch";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface FtCalibrationBand {
  band: string;
  numBets: number;
  wins: number;
  losses: number;
  observedWinRatePct: number | null;
  avgPredictedProbPct: number | null;
  calibrationErrorPp: number | null;
  avgEntryPrice: number | null;
  avgClaimedEdgePp: number | null;
  totalPl: number | null;
  roiPct: number | null;
}

export interface FtGroupRow {
  label: string;
  n: number;
  wins: number;
  losses: number;
  winRatePct: number | null;
  avgPredictedProbPct: number | null;
  totalPl: number | null;
  roiPct: number | null;
  brierScore: number | null;
  logLoss: number | null;
  calibrationErrorPp: number | null;
}

export interface FtFalseConfidenceLoss {
  marketTicker: string;
  city: string;
  weatherVariable: string;
  contractType: string;
  direction: string;
  strategyVersion: string;
  modelProbabilityPct: number;
  marketEntryPrice: number;
  claimedEdgePp: number;
  forecastValueF: number | null;
  era5ActualF: number | null;
  decisionForecastError: number | null;
  thresholdOrRange: string;
  distanceFromThreshold: number | null;
  sigmaUsed: number | null;
  lossCategory: string;
  integrityFlag: string | null;
  hypothesis: string;
}

export interface FtIntegrityFlag {
  marketTicker: string;
  city: string;
  weatherVariable: string;
  targetDate: string;
  direction: string;
  outcome: string;
  kalshiResult: string;
  era5ActualF: number | null;
  era5PredictedResult: string | null;
  flag: string;
  detail: string;
  sourceLabel: string | null;
}

export interface FtChartPoint {
  predictedProbPct: number;
  isWin: boolean;
  strategyVersion: string;
}

export interface ForwardTestDiagnostics {
  sampleWarning: string;
  asOf: string;
  forwardTestStart: string;
  settledCount: number;
  wins: number;
  losses: number;
  winRatePct: number;
  totalStake: number;
  totalPl: number;
  roiPct: number;
  avgPredictedProbPct: number;
  avgEntryPrice: number;
  avgClaimedEdgePp: number;
  brierScore: number;
  logLoss: number;
  expectedCalibrationErrorPct: number;
  meanAbsCalibrationErrorPct: number;
  calibrationBands: FtCalibrationBand[];
  byStrategy: FtGroupRow[];
  byDirection: FtGroupRow[];
  byEdgeBucket: FtGroupRow[];
  byEntryPriceBucket: FtGroupRow[];
  falseConfidenceLosses: FtFalseConfidenceLoss[];
  settlementIntegrityFlags: FtIntegrityFlag[];
  chartPoints: FtChartPoint[];
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export const FORWARD_TEST_DIAGNOSTICS_QUERY_KEY: QueryKey = [
  "/api/paper-trades/forward-test-diagnostics",
];

export const getForwardTestDiagnostics = async (
  options?: Parameters<typeof customFetch>[1]
): Promise<ForwardTestDiagnostics> =>
  customFetch<ForwardTestDiagnostics>(
    "/api/paper-trades/forward-test-diagnostics",
    { ...options, method: "GET" }
  );

export function useGetForwardTestDiagnostics<
  TData = ForwardTestDiagnostics,
  TError = unknown
>(
  options?: UseQueryOptions<ForwardTestDiagnostics, TError, TData>
): UseQueryResult<TData, TError> {
  return useQuery({
    queryKey: options?.queryKey ?? FORWARD_TEST_DIAGNOSTICS_QUERY_KEY,
    queryFn: ({ signal }) => getForwardTestDiagnostics({ signal }),
    staleTime: 120_000, // 2 min — settled counts change slowly
    ...options,
  }) as UseQueryResult<TData, TError>;
}
