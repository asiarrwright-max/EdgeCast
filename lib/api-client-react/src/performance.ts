/**
 * Performance Analytics — API hooks for the /analytics/performance endpoint.
 */
import { useQuery } from "@tanstack/react-query";
import type { UseQueryOptions, UseQueryResult, QueryKey } from "@tanstack/react-query";
import { customFetch } from "./custom-fetch";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PerformanceSummary {
  totalCount: number;
  settledCount: number;
  openCount: number;
  voidCount: number;
  wins: number;
  losses: number;
  winRate: number | null;
  roi: number | null;
  netProfitLoss: number;
  brierScore: number | null;
  avgEntryEdgePp: number | null;
  avgConfidenceLabel: string | null;
  confidenceDistribution: Record<string, number>;
  sampleSizeWarning: boolean;
  preliminaryNote: string | null;
}

export interface ChartPoint {
  date: string | null;
  [key: string]: unknown;
}

export interface CumulativeRoiPoint {
  date: string | null;
  roi: number;
  tradeId: number;
}

export interface CumulativePlPoint {
  date: string | null;
  cumulativePl: number;
  tradeId: number;
}

export interface RollingWinRatePoint {
  date: string | null;
  winRate: number;
  tradeNum: number;
}

export interface DailyPlPoint {
  date: string;
  pl: number;
  count: number;
}

export interface DailyTradeCountPoint {
  date: string;
  count: number;
}

export interface BrierOverTimePoint {
  date: string | null;
  brierScore: number;
  tradeCount: number;
}

export interface PerformanceCharts {
  cumulativeRoi: CumulativeRoiPoint[];
  cumulativePl: CumulativePlPoint[];
  rollingWinRate: RollingWinRatePoint[];
  dailyPl: DailyPlPoint[];
  dailyTradeCount: DailyTradeCountPoint[];
  brierOverTime: BrierOverTimePoint[];
}

export interface StrategyVersionStats {
  strategy: string;
  total: number;
  settled: number;
  wins: number;
  losses: number;
  winRate: number | null;
  roi: number | null;
  netPl: number;
  brierScore: number | null;
}

export interface StrategyGroupStats {
  total: number;
  settled: number;
  wins: number;
  losses: number;
  winRate: number | null;
  roi: number | null;
  netPl: number;
}

export interface PerformanceStrategyComparison {
  v1: StrategyVersionStats;
  v2: StrategyVersionStats;
  sharedCount: number;
  v1OnlyCount: number;
  v2OnlyCount: number;
  oppositeSideCount: number;
  /** Separate V1 and V2 stats for each segment. */
  sharedV1: StrategyGroupStats;
  sharedV2: StrategyGroupStats;
  v1OnlyV1: StrategyGroupStats;
  v2OnlyV2: StrategyGroupStats;
  oppositeSideV1: StrategyGroupStats;
  oppositeSideV2: StrategyGroupStats;
}

export interface PerformanceAnalytics {
  period: string;
  strategy: string;
  summary: PerformanceSummary;
  charts: PerformanceCharts;
  strategyComparison: PerformanceStrategyComparison;
}

export type PerformancePeriod = "7d" | "30d" | "all";
export type PerformanceStrategy = "v1.0" | "v2.0" | "all";

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export const getPerformanceAnalyticsUrl = (
  period: PerformancePeriod,
  strategy: PerformanceStrategy
) => `/api/analytics/performance?period=${period}&strategy=${strategy}`;

export const getGetPerformanceAnalyticsQueryKey = (
  period: PerformancePeriod,
  strategy: PerformanceStrategy
): QueryKey => [`/api/analytics/performance`, period, strategy];

export const getPerformanceAnalytics = async (
  period: PerformancePeriod,
  strategy: PerformanceStrategy,
  options?: Parameters<typeof customFetch>[1]
): Promise<PerformanceAnalytics> => {
  return customFetch<PerformanceAnalytics>(
    getPerformanceAnalyticsUrl(period, strategy),
    { ...options, method: "GET" }
  );
};

export function useGetPerformanceAnalytics<
  TData = PerformanceAnalytics,
  TError = unknown
>(
  period: PerformancePeriod,
  strategy: PerformanceStrategy,
  options?: UseQueryOptions<PerformanceAnalytics, TError, TData>
): UseQueryResult<TData, TError> {
  const queryKey =
    options?.queryKey ?? getGetPerformanceAnalyticsQueryKey(period, strategy);
  return useQuery({
    queryKey,
    queryFn: ({ signal }) =>
      getPerformanceAnalytics(period, strategy, { signal }),
    ...options,
  }) as UseQueryResult<TData, TError>;
}
