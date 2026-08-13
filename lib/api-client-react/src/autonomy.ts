import { useMutation, useQuery } from "@tanstack/react-query";
import type { UseMutationResult, UseQueryResult } from "@tanstack/react-query";

import { customFetch } from "./custom-fetch";

export interface AutonomyHealthSummary {
  state: "ok" | "error";
  checkedAt: string;
  message: string;
}

export interface AutonomySummary {
  greenInProgress: number;
  greenReady: number;
  pullRequestsWaitingReview: number;
  yellowNeedsDecision: number;
  redSafetyBlocks: number;
  systemHealth: AutonomyHealthSummary | null;
}

export interface AutonomyWorkItem {
  number: number;
  title: string;
  summary: string;
  statusText: string;
  htmlUrl: string;
  updatedAt: string;
  isPullRequest: boolean;
}

export interface YellowProposal {
  number: number;
  title: string;
  summary: string;
  whyYellow: string;
  affectedArea: string;
  expectedImpact: string | null;
  ciState: string;
  riskState: string;
  ownerApproved: boolean;
  htmlUrl: string;
  updatedAt: string;
}

export interface AutonomySnapshot {
  integrationConfigured: boolean;
  readOnly: boolean;
  readOnlyReason: string | null;
  summary: AutonomySummary;
  yellowProposals: YellowProposal[];
  greenWork: AutonomyWorkItem[];
  redWork: AutonomyWorkItem[];
}

export interface YellowApprovalResult {
  number: number;
  ownerApproved: boolean;
  message: string;
}

export const autonomyQueryKey = ["/api/autonomy"] as const;

export const getAutonomySnapshot = (
  options?: Parameters<typeof customFetch>[1],
): Promise<AutonomySnapshot> =>
  customFetch<AutonomySnapshot>("/api/autonomy", {
    ...options,
    method: "GET",
  });

export const approveYellowProposal = (
  pullRequestNumber: number,
  options?: Parameters<typeof customFetch>[1],
): Promise<YellowApprovalResult> =>
  customFetch<YellowApprovalResult>(`/api/autonomy/yellow-proposals/${pullRequestNumber}/approve`, {
    ...options,
    method: "POST",
  });

export const rejectYellowProposal = (
  pullRequestNumber: number,
  options?: Parameters<typeof customFetch>[1],
): Promise<YellowApprovalResult> =>
  customFetch<YellowApprovalResult>(`/api/autonomy/yellow-proposals/${pullRequestNumber}/reject`, {
    ...options,
    method: "POST",
  });

export function useGetAutonomySnapshot(): UseQueryResult<AutonomySnapshot, unknown> {
  return useQuery({
    queryKey: autonomyQueryKey,
    queryFn: ({ signal }) => getAutonomySnapshot({ signal }),
    staleTime: 15_000,
    refetchInterval: 30_000,
  }) as UseQueryResult<AutonomySnapshot, unknown>;
}

export function useApproveYellowProposal(): UseMutationResult<
  YellowApprovalResult,
  unknown,
  number
> {
  return useMutation({
    mutationFn: (pullRequestNumber: number) => approveYellowProposal(pullRequestNumber),
  });
}

export function useRejectYellowProposal(): UseMutationResult<
  YellowApprovalResult,
  unknown,
  number
> {
  return useMutation({
    mutationFn: (pullRequestNumber: number) => rejectYellowProposal(pullRequestNumber),
  });
}
