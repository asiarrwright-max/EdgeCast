import { customFetch } from "./custom-fetch";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface CityScoreComponents {
  total: number;
  forecast: number;
  trading: number;
  liquidity: number;
  sample: number;
  station: number;
  weights: {
    forecast: number;
    trading: number;
    liquidity: number;
    sample: number;
    station: number;
  };
}

export interface CityModelVersion {
  strategy_version: string;
  settled: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  pnl: number | null;
  avg_edge: number | null;
  total_evaluated: number;
}

export interface CityDirectionRow {
  direction: string;
  contract_type: string;
  settled: number;
  wins: number;
  pnl: number | null;
}

export interface FesRow {
  weather_variable: string;
  lead_time_bucket: string;
  mean_error: number | null;
  mae: number | null;
  std_dev: number | null;
  sample_size: number;
}

export interface CityFtbData {
  total_v23: number;
  official_count: number;
  research_count: number;
  settled_v23: number;
  wins_v23: number;
  rej_stale: number;
  rej_v2_excl: number;
  rej_hourly: number;
  rej_station: number;
  scan_days: number;
  unique_tickers_v23: number;
}

export interface CityMetrics {
  city: string;
  // station
  station_name: string | null;
  station_id: string | null;
  station_verified: boolean;
  nws_compatible: boolean;
  station_notes: string | null;
  // trading
  settled_total: number;
  official_settled: number;
  research_settled: number;
  wins: number;
  losses: number;
  win_rate_pct: number | null;
  avg_predicted_prob: number | null;
  actual_win_rate: number | null;
  calibration_gap: number | null;
  total_pnl: number | null;
  official_pnl: number | null;
  avg_edge: number | null;
  median_edge: number | null;
  unique_market_days: number;
  unique_tickers: number;
  approx_opps_per_day: number;
  direction_breakdown: CityDirectionRow[];
  model_versions: CityModelVersion[];
  // forecast accuracy
  fv_obs: number;
  mae: number | null;
  median_ae: number | null;
  mean_bias: number | null;
  rmse: number | null;
  sigma: number | null;
  pct_within_1f: number | null;
  pct_within_2f: number | null;
  pct_within_3f: number | null;
  mae_by_lead_time: Record<string, number | null> | null;
  forecast_sources: string | null;
  fes_detail: FesRow[];
  // market quality
  market_scans: number;
  scans_valid_ask: number;
  scans_no_quote: number;
  pct_valid_ask: number | null;
  avg_volume: number | null;
  median_volume: number | null;
  distinct_market_days_kalshi: number;
  // quote freshness
  total_evaluated: number;
  pct_fresh_300s: number | null;
  pct_executable: number | null;
  avg_qty: number | null;
  rej_stale_quote: number;
  rej_station: number;
  rej_v2_excluded: number;
  rej_hourly: number;
  volume_note: string;
  // FTB
  ftb: CityFtbData | null;
  // score
  score: CityScoreComponents;
  sample_size_grade: string;
  sample_warnings: string[];
}

export interface FtbImpact {
  city: string;
  v23_total_evaluated: number;
  v23_official: number;
  v23_research_only: number;
  v23_scan_days: number;
  top_rejection_reasons: string[];
  hist_opps_per_day: number;
  est_official_per_week_lo: number;
  est_official_per_week_hi: number;
  est_weeks_to_10_settled: string;
  est_weeks_to_25_settled: string;
  est_weeks_to_50_settled: string;
  note: string;
}

export interface CityStudyResult {
  generated_at: string;
  trading_state_modified: boolean;
  ftb_untouched: boolean;
  read_only: boolean;
  cities_analyzed: string[];
  top_single_city: string | null;
  top_two_city: string | null;
  top_three_city_individual: string | null;
  best_3_city_set: string[];
  recommendation: string;
  recommendation_reasons: string[];
  ftb_impact: FtbImpact | null;
  score_weights: Record<string, number>;
  cities: CityMetrics[];
}

// ---------------------------------------------------------------------------
// Fetch
// ---------------------------------------------------------------------------

export async function getCityStudy(): Promise<CityStudyResult> {
  return customFetch<CityStudyResult>("/api/city-study");
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

import { useQuery } from "@tanstack/react-query";

export function useGetCityStudy() {
  return useQuery<CityStudyResult>({
    queryKey: ["city-study"],
    queryFn: getCityStudy,
    staleTime: 5 * 60 * 1000,
  });
}
