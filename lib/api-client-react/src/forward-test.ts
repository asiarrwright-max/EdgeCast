/**
 * Forward Test Status — API hook for /api/paper-trades/forward-test-status.
 *
 * Returns the forward-test progress card data: official settled/open counts,
 * research-only signals, legacy-excluded count, readiness label, milestone
 * text, and per-strategy breakdowns.
 */
import { useQuery } from "@tanstack/react-query";
import type { UseQueryOptions, UseQueryResult, QueryKey } from "@tanstack/react-query";
import { customFetch } from "./custom-fetch";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ForwardTestStrategyRow {
  officialSettled: number;
  officialOpen: number;
  researchOnly: number;
}

export interface ForwardTestReasonEntry {
  label: string;
  count: number;
}

export interface ForwardTestStatus {
  phase: string;
  /** Always "August 4, 2026" in display; exact cutoff is 2026-08-04T22:21:44Z internally. */
  forwardTestStartDate: string;
  startingCodeVersion: string;
  officialSettledCount: number;
  officialOpenCount: number;
  /** Cumulative RESEARCH_ONLY count since the forward-test start. */
  researchOnlyCount: number;
  legacyExcludedCount: number;
  progressPct: number;
  progressTarget: number;
  /** Automatic stage — caps at "Promising but unproven" without manualReadinessApproval. */
  readinessLabel: string;
  nextMilestone: string;
  currentReadiness: string;
  /** False until an explicit review of ROI, calibration, and drawdown is completed. */
  manualReadinessApproval: boolean;
  whyNoOfficialBet: Record<string, ForwardTestReasonEntry>;
  /** "Latest collection batch" or "Past 24 hours" — describes the whyNoOfficialBet window. */
  reasonBreakdownWindow: string;
  byStrategy: {
    v22: ForwardTestStrategyRow;
    v3: ForwardTestStrategyRow;
  };
  explanation: string;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export const FORWARD_TEST_STATUS_QUERY_KEY: QueryKey = [
  "/api/paper-trades/forward-test-status",
];

export const getForwardTestStatus = async (
  options?: Parameters<typeof customFetch>[1]
): Promise<ForwardTestStatus> =>
  customFetch<ForwardTestStatus>(
    "/api/paper-trades/forward-test-status",
    { ...options, method: "GET" }
  );

export function useGetForwardTestStatus<
  TData = ForwardTestStatus,
  TError = unknown
>(
  options?: UseQueryOptions<ForwardTestStatus, TError, TData>
): UseQueryResult<TData, TError> {
  return useQuery({
    queryKey: options?.queryKey ?? FORWARD_TEST_STATUS_QUERY_KEY,
    queryFn: ({ signal }) => getForwardTestStatus({ signal }),
    staleTime: 60_000, // 1 min — counts change slowly
    ...options,
  }) as UseQueryResult<TData, TError>;
}
