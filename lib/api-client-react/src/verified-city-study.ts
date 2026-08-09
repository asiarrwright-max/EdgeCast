import { customFetch } from "./custom-fetch";
import { useQuery } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface CityVerificationResult {
  city: string;
  verdict: "VERIFIED" | "NOT_VERIFIED" | "CONFLICT" | "NO_AUTHORITATIVE_EVIDENCE";
  api_field: string;
  kalshi_series: string;
  settlement_text: string;
  nws_station: string;
  ghcnd_station: string;
  station_name: string;
  query_date: string;
  source_log: string;
  flag_changed: boolean;
  notes: string | null;
}

export interface VerifiedCityScore {
  total: number;
  forecast: number;
  trading: number;
  liquidity: number;
  sample: number;
  station: number;
}

export interface VerifiedCityFtb {
  total_v23: number;
  official_count: number;
  research_count: number;
  settled_v23: number;
  wins_v23: number;
  scan_days: number;
  unique_tickers_v23: number;
  top_rejections: string[];
}

export interface VerifiedCityMetrics {
  city: string;
  station_name: string;
  station_id: string;
  station_verified: boolean;
  nws_compatible: boolean;
  settled_total: number;
  official_settled: number;
  wins: number;
  losses: number;
  win_rate_pct: number | null;
  avg_predicted_prob: number | null;
  calibration_gap: number | null;
  total_pnl: number | null;
  avg_edge: number | null;
  hist_opps_per_day: number;
  est_official_per_week_lo: number;
  est_official_per_week_hi: number;
  fv_obs: number;
  mae: number | null;
  mean_bias: number | null;
  pct_within_2f: number | null;
  forecast_sources: string | null;
  market_scans: number;
  pct_valid_ask: number | null;
  ftb: VerifiedCityFtb | null;
  score: VerifiedCityScore;
  sample_size_grade: string;
  sample_warnings: string[];
}

export interface BetWatchGuidance {
  primary_cities: string[];
  informational_cities: string[];
  best_bet_restriction: string;
  implementation_status: string;
}

export interface VerifiedCityStudyResult {
  generated_at: string;
  trading_state_modified: boolean;
  ftb_untouched: boolean;
  station_flags_changed: boolean;
  read_only: boolean;
  verified_nws_city_count: number;
  verification_results: CityVerificationResult[];
  newly_verified: string[];
  still_unverified: string[];
  conflicts: string[];
  cities: VerifiedCityMetrics[];
  cities_in_ranking: string[];
  shortlist: string[];
  shortlist_verdict: string;
  shortlist_reasons: string[];
  bet_watch_guidance: BetWatchGuidance;
  score_weights: Record<string, number>;
}

// ---------------------------------------------------------------------------
// Fetch + hook
// ---------------------------------------------------------------------------

export async function getVerifiedCityStudy(): Promise<VerifiedCityStudyResult> {
  return customFetch<VerifiedCityStudyResult>("/api/verified-city-study");
}

export function useGetVerifiedCityStudy() {
  return useQuery<VerifiedCityStudyResult>({
    queryKey: ["verified-city-study"],
    queryFn: getVerifiedCityStudy,
    staleTime: 5 * 60 * 1000,
  });
}
