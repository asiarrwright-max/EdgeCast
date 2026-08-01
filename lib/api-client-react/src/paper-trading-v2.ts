/**
 * Strategy v2+ — additional API hooks not yet in the generated client.
 * These complement the orval-generated hooks for the comparison,
 * agreement, and analytics endpoints.
 */
import { useQuery, useMutation } from "@tanstack/react-query";
import type {
  UseQueryOptions,
  UseQueryResult,
  QueryKey,
  UseMutationOptions,
  UseMutationResult,
} from "@tanstack/react-query";
import { customFetch } from "./custom-fetch";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface StrategyComparison {
  v1: Record<string, unknown> & { calibration: Record<string, unknown> };
  v2: Record<string, unknown> & { calibration: Record<string, unknown> };
  v2Settings: {
    enabled: boolean;
    min_edge_pct: number;
    min_confidence: string;
    stake: number;
  };
}

export interface AgreementSample {
  ticker: string;
  v1Direction: string;
  v2Direction: string;
  v1EcProb: number;
  v2EcProb: number;
  probDiff: number;
  agree: boolean;
  v2FallbackLevel: string | null;
  v2BiasCorrection: number | null;
}

export interface StrategyAgreement {
  bothTrade: number;
  onlyV1: number;
  onlyV2: number;
  differentSides: number;
  sameSides: number;
  probDivergenceGt10pp: number;
  samples: AgreementSample[];
}

export interface VerificationResult {
  verifications: { created: number; updated: number; skipped: number; errors: number };
  errorStats: { groups_computed: number };
}

// ---------------------------------------------------------------------------
// Segment summary — full per-version × executability breakdown
// ---------------------------------------------------------------------------

export interface SegmentSummaryRow {
  /** e.g. "v1.0", "v2.1", "v3.0" */
  version: string;
  /** "legacy" | "current_exec" | "current_nonexec" | "v3" */
  group: "legacy" | "current_exec" | "current_nonexec" | "v3";
  isExecutable: boolean | null;
  total: number;
  open: number;
  pending: number;
  settled: number;
  wins: number;
  losses: number;
  /** V2_EXCLUDED records (not counted in any performance metric) */
  excluded: number;
  winRate: number | null;
  settledStake: number;
  settledPl: number;
  settledRoi: number | null;
  avgEdge: number | null;
  brierScore: number | null;
}

export interface SegmentSummary {
  rows: SegmentSummaryRow[];
}

// ---------------------------------------------------------------------------
// Comparison
// ---------------------------------------------------------------------------

export const getStrategyComparisonUrl = () => `/api/paper-trades/comparison`;

export const getStrategyComparison = async (
  options?: Parameters<typeof customFetch>[1]
): Promise<StrategyComparison> => {
  return customFetch<StrategyComparison>(getStrategyComparisonUrl(), {
    ...options,
    method: "GET",
  });
};

export const getGetStrategyComparisonQueryKey = (): QueryKey => [
  `/api/paper-trades/comparison`,
];

export function useGetStrategyComparison<
  TData = StrategyComparison,
  TError = unknown
>(
  options?: UseQueryOptions<StrategyComparison, TError, TData>
): UseQueryResult<TData, TError> {
  const queryKey = options?.queryKey ?? getGetStrategyComparisonQueryKey();
  return useQuery({
    queryKey,
    queryFn: ({ signal }) => getStrategyComparison({ signal }),
    ...options,
  }) as UseQueryResult<TData, TError>;
}

// ---------------------------------------------------------------------------
// Agreement
// ---------------------------------------------------------------------------

export const getStrategyAgreementUrl = () => `/api/paper-trades/agreement`;

export const getStrategyAgreement = async (
  options?: Parameters<typeof customFetch>[1]
): Promise<StrategyAgreement> => {
  return customFetch<StrategyAgreement>(getStrategyAgreementUrl(), {
    ...options,
    method: "GET",
  });
};

export const getGetStrategyAgreementQueryKey = (): QueryKey => [
  `/api/paper-trades/agreement`,
];

export function useGetStrategyAgreement<
  TData = StrategyAgreement,
  TError = unknown
>(
  options?: UseQueryOptions<StrategyAgreement, TError, TData>
): UseQueryResult<TData, TError> {
  const queryKey = options?.queryKey ?? getGetStrategyAgreementQueryKey();
  return useQuery({
    queryKey,
    queryFn: ({ signal }) => getStrategyAgreement({ signal }),
    ...options,
  }) as UseQueryResult<TData, TError>;
}

// ---------------------------------------------------------------------------
// Run verification (manual trigger)
// ---------------------------------------------------------------------------

export const runVerification = async (
  options?: Parameters<typeof customFetch>[1]
): Promise<VerificationResult> => {
  return customFetch<VerificationResult>(`/api/paper-trades/run-verification`, {
    ...options,
    method: "POST",
  });
};

export function useRunVerification<TError = unknown>(
  options?: UseMutationOptions<VerificationResult, TError, void>
): UseMutationResult<VerificationResult, TError, void> {
  return useMutation({
    mutationFn: () => runVerification(),
    ...options,
  }) as UseMutationResult<VerificationResult, TError, void>;
}

// ---------------------------------------------------------------------------
// Segment summary
// ---------------------------------------------------------------------------

export const getSegmentSummaryUrl = () => `/api/paper-trades/segment-summary`;

export const getSegmentSummary = async (
  options?: Parameters<typeof customFetch>[1]
): Promise<SegmentSummary> => {
  return customFetch<SegmentSummary>(getSegmentSummaryUrl(), {
    ...options,
    method: "GET",
  });
};

export const getGetSegmentSummaryQueryKey = (): QueryKey => [
  `/api/paper-trades/segment-summary`,
];

export function useGetPaperTradeSegmentSummary<
  TData = SegmentSummary,
  TError = unknown
>(
  options?: UseQueryOptions<SegmentSummary, TError, TData>
): UseQueryResult<TData, TError> {
  const queryKey = options?.queryKey ?? getGetSegmentSummaryQueryKey();
  return useQuery({
    queryKey,
    queryFn: ({ signal }) => getSegmentSummary({ signal }),
    staleTime: 2 * 60 * 1000,
    ...options,
  }) as UseQueryResult<TData, TError>;
}
