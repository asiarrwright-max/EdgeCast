/**
 * EdgeCast Bet Watch API — read-only decision-support layer.
 * Types and hooks for the GET /api/bet-watch endpoint.
 */
import { useQuery } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { customFetch } from "./custom-fetch";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface BetWatchCandidate {
  rank: number;
  city: string;
  ticker: string;
  side: "YES" | "NO";
  contract_question: string;
  contract_type: string | null;
  weather_variable: string | null;
  settlement_date: string | null;
  // Prices — null means unavailable, never fabricated
  kalshi_price: number | null;
  kalshi_bid: number | null;
  kalshi_ask: number | null;
  // Model
  model_probability: number;
  ec_yes_probability: number;
  edge: number;
  model_version: string;
  model_agreement: null; // reserved
  forecast_value: number | null;
  contract_boundary: string | null;
  confidence: string;
  // Market metadata
  quote_timestamp: string | null;
  quote_age_seconds: number | null;
  market_close: string | null;
  minutes_to_close: number | null;
  volume: null;
  open_interest: number | null;
  market_status: string;
  // Quality
  liquidity_status: string;
  data_freshness: string;
  station_verified: boolean | null;
  // FTB status
  watch_status:
    | "OFFICIAL-ELIGIBLE"
    | "NEAR OFFICIAL"
    | "WATCHING"
    | "PRELIMINARY"
    | "AVOID / STALE";
  ftb_status: string;
  ftb_eligible: boolean;
  failed_ftb_guards: string[];
  // Narratives
  why_this_bet: string;
  what_to_watch: string;
  changed_since_previous_scan: string[];
  evaluated_at: string | null;
  // Specialization
  specialization_city: boolean;
}

export interface BetWatchSummary {
  total_evaluated: number;
  actionable: number;
  near_official: number;
  watching: number;
  preliminary: number;
  avoid_stale: number;
  best_ticker: string | null;
  text: string;
}

export interface BetWatchResult {
  generated_at: string;
  trading_state_modified: false;
  ftb_untouched: true;
  summary: BetWatchSummary;
  recommendation: string;
  wait_message: string | null;
  best_opportunity: BetWatchCandidate | null;
  candidates: BetWatchCandidate[];
  all_candidate_count: number;
  // Specialization
  specialization_cities: string[];
  specialization_note: string;
}

// ---------------------------------------------------------------------------
// Fetch function
// ---------------------------------------------------------------------------

export const getBetWatch = (
  options?: Parameters<typeof customFetch>[1]
): Promise<BetWatchResult> =>
  customFetch<BetWatchResult>("/api/bet-watch", {
    ...options,
    method: "GET",
  });

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useGetBetWatch(
  fetchOptions: { refetchInterval?: number } = {}
): UseQueryResult<BetWatchResult, unknown> {
  return useQuery({
    queryKey: ["/api/bet-watch"],
    queryFn: ({ signal }) => getBetWatch({ signal }),
    refetchInterval: fetchOptions.refetchInterval ?? 60_000,
    staleTime: 30_000,
  }) as UseQueryResult<BetWatchResult, unknown>;
}
