/**
 * Strategy Comparison API hooks
 * ==============================
 * Unified V2.1 / V2.2 / V3 cross-strategy comparison data.
 */
import { useQuery } from "@tanstack/react-query";
import type { UseQueryOptions, UseQueryResult } from "@tanstack/react-query";
import { customFetch } from "./custom-fetch";

// ---------------------------------------------------------------------------
// Per-strategy section (executable or non-executable subset)
// ---------------------------------------------------------------------------

export interface StrategySection {
  count: number;
  open: number;
  settled: number;
  wins: number;
  losses: number;
  win_rate_pct: number | null;
  /** Stake from settled trades only — the ROI denominator. */
  settled_stake: number;
  gross_pl: number;
  /** Exchange fees on settled trades only — deducted from gross_pl to form net_pl. */
  estimated_fees: number;
  net_pl: number;
  /** Gross P/L / settled stake (before fees). */
  gross_roi_pct: number | null;
  /** Net P/L / settled stake (after settled fees); may fall below −100 %. */
  net_roi_pct: number | null;
  brier_score: number | null;
  /** Capital still deployed in open trades — informational, not mixed into settled P/L. */
  open_stake: number;
  /** Estimated exchange fees for open trades — informational only. */
  open_fees: number;
  /** total_stake = settled_stake + open_stake (retained for display). */
  total_stake: number;
  avg_edge_pp: number | null;
  avg_sigma: number | null;
  is_official: boolean;
}

export interface StrategySummary {
  label: string;
  description: string;
  total_predictions: number;
  executable: StrategySection;
  non_executable: StrategySection;
  excluded_count: number;
  official_note: string;
}

// ---------------------------------------------------------------------------
// Market-level trade slot (one per strategy per ticker)
// ---------------------------------------------------------------------------

export interface TradeSlot {
  ticker: string;
  city: string | null;
  weather_variable: string | null;
  contract_type: string | null;
  market_prob: number | null;
  ec_prob: number | null;
  edge_pp: number | null;
  direction: string | null;
  is_executable: boolean | null;
  sigma: number | null;
  bias: number | null;
  fallback: string | null;
  status: string | null;
  outcome: string | null;
  profit_loss: number | null;
  hist_sample_count?: number | null;
  comparison_snapshot_id: string | null;
  collection_batch_id: string | null;
}

// ---------------------------------------------------------------------------
// Full market row (all three strategies side-by-side)
// ---------------------------------------------------------------------------

export interface MarketComparisonRow {
  ticker: string;
  city: string | null;
  weather_variable: string | null;
  contract_type: string | null;
  market_prob: number | null;
  versions_present: string[];
  versions_agreed: boolean;
  /** True when V2.1, V2.2, and V3 all share the same comparison_snapshot_id
   *  (same collection cycle, identical frozen quote + forecast inputs). */
  is_paired: boolean;
  v21?: TradeSlot;
  v22?: TradeSlot;
  v3?: TradeSlot;
  v21_v22_delta_pp?: number | null;
  v21_v3_delta_pp?: number | null;
}

// ---------------------------------------------------------------------------
// Flag state
// ---------------------------------------------------------------------------

export interface StrategyFlagState {
  predictions_enabled: boolean;
  paper_trading_enabled: boolean;
}

export interface ComparisonFlags {
  v21: StrategyFlagState;
  v22: StrategyFlagState;
  v3: StrategyFlagState;
}

// ---------------------------------------------------------------------------
// Smoke-test status
// ---------------------------------------------------------------------------

export interface SmokeTestStatus {
  phase: string;
  v22_predictions_enabled: boolean;
  v22_paper_trading_enabled: boolean;
  v22_paper_trade_count: number;
  v22_min_sample_met: boolean;
  expected_prob_delta_pp: number;
  note: string;
}

// ---------------------------------------------------------------------------
// Pairing stats
// ---------------------------------------------------------------------------

export interface PairingStats {
  strictly_paired: number;
  timing_mismatched: number;
  note: string;
}

// ---------------------------------------------------------------------------
// Readiness tracker
// ---------------------------------------------------------------------------

export interface ReadinessMilestone {
  target: number;
  reached: boolean;
  remaining: number;
  pct: number;
}

export interface ReadinessTracker {
  shared_settled_executable: number;
  milestones: ReadinessMilestone[];
  note: string;
}

// ---------------------------------------------------------------------------
// Full response
// ---------------------------------------------------------------------------

export interface StrategyComparisonData {
  flags: ComparisonFlags;
  strategies: {
    v21: StrategySummary;
    v22: StrategySummary;
    v3: StrategySummary;
  };
  shared_count: number;
  total_markets: number;
  market_rows: MarketComparisonRow[];
  pairing_stats: PairingStats;
  readiness_tracker: ReadinessTracker;
  smoke_test: SmokeTestStatus;
}

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

export function useGetMultiStrategyComparison<
  TData = StrategyComparisonData,
  TError = unknown,
>(
  options?: UseQueryOptions<StrategyComparisonData, TError, TData>
): UseQueryResult<TData, TError> {
  return useQuery({
    queryKey: ["/api/analytics/strategy-comparison"],
    queryFn: ({ signal }) =>
      customFetch<StrategyComparisonData>(
        "/api/analytics/strategy-comparison",
        { signal, method: "GET" }
      ),
    staleTime: 60_000,
    ...options,
  }) as UseQueryResult<TData, TError>;
}
