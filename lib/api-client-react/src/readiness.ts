/**
 * Real-Money Readiness Dashboard — API hooks for the /api/readiness endpoint.
 *
 * Safety note: this module is read-only. The endpoint always returns
 * trading_state_modified=false and realMoneyExecutionEnabled=false.
 */
import { useQuery } from "@tanstack/react-query";
import type { UseQueryOptions, UseQueryResult } from "@tanstack/react-query";
import { customFetch } from "./custom-fetch";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ReadinessState {
  status: "NOT_READY" | "NEEDS_EVIDENCE";
  reason: string;
  thresholdsActivated: false;
  realMoneyExecutionEnabled: false;
  trading_state_modified: false;
}

export interface ReadinessEvidence {
  officialTradeCount: number;
  settledCount: number;
  openCount: number;
  wins: number;
  losses: number;
  winRate: number | null;
  roi: number | null;
  netProfitLoss: number | null;
  brierScore: number | null;
  avgEntryEdgePp: number | null;
  maxDrawdown: number | null;
  longestLosingStreak: number | null;
  cityCount: number;
  smallSampleWarning: boolean;
  populationNote: string;
}

export interface SettlementCoverage {
  total: number;
  settled: number;
  open: number;
  void: number;
  pendingSettlement: number;
  settlementCoveragePct: number | null;
  regimeBreakdown: Record<string, number>;
}

export interface CityBreakdownRow {
  city: string;
  total: number;
  settled: number;
  wins: number;
  winRate: number | null;
  smallSample: boolean;
}

export interface StrategyBreakdownRow {
  strategy: string;
  total: number;
  settled: number;
  wins: number;
  winRate: number | null;
  brierScore: number | null;
  avgEdgePp: number | null;
  smallSample: boolean;
}

export interface EdgeBucketRow {
  bucket: string;
  total: number;
  settled: number;
  winRate: number | null;
  smallSample: boolean;
}

export interface ConfidenceBreakdownRow {
  confidenceLabel: string;
  total: number;
  settled: number;
  winRate: number | null;
  smallSample: boolean;
}

export interface QuoteQuality {
  total: number;
  missingQuoteCount: number;
  staleQuoteCount: number;
  missingQuoteRate: number | null;
  staleQuoteRate: number | null;
}

export interface AbstentionAnalysis {
  researchOnlyCount: number;
  reasonBreakdown: Record<string, number>;
}

export interface SettlementIntegrityException {
  tradeId: number;
  ticker: string;
  city: string | null;
  flags: string[];
  settledAt: string | null;
}

export interface ReadinessDashboard {
  trading_state_modified: false;
  realMoneyExecutionEnabled: false;
  readiness: ReadinessState;
  evidence: ReadinessEvidence;
  settlementCoverage: SettlementCoverage;
  cityBreakdown: CityBreakdownRow[];
  strategyBreakdown: StrategyBreakdownRow[];
  edgeBucketBreakdown: EdgeBucketRow[];
  confidenceBreakdown: ConfidenceBreakdownRow[];
  quoteQuality: QuoteQuality;
  abstentionAnalysis: AbstentionAnalysis;
  settlementIntegrityExceptions: SettlementIntegrityException[];
  evidenceGaps: string[];
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export const READINESS_QUERY_KEY = ["/api/readiness"] as const;

export const getReadiness = async (
  options?: Parameters<typeof customFetch>[1]
): Promise<ReadinessDashboard> => {
  return customFetch<ReadinessDashboard>("/api/readiness", {
    ...options,
    method: "GET",
  });
};

export function useGetReadiness<TData = ReadinessDashboard, TError = unknown>(
  options?: UseQueryOptions<ReadinessDashboard, TError, TData>
): UseQueryResult<TData, TError> {
  return useQuery({
    queryKey: READINESS_QUERY_KEY,
    queryFn: ({ signal }) => getReadiness({ signal }),
    ...options,
  }) as UseQueryResult<TData, TError>;
}
