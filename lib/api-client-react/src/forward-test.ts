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
  forwardTestStartDate: string;
  startingCodeVersion: string;
  officialSettledCount: number;
  officialOpenCount: number;
  researchOnlyCount: number;
  legacyExcludedCount: number;
  progressPct: number;
  progressTarget: number;
  readinessLabel: string;
  nextMilestone: string;
  currentReadiness: string;
  whyNoOfficialBet: Record<string, ForwardTestReasonEntry>;
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
