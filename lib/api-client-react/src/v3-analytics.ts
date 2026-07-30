/**
 * V3 Analytics API client
 * ========================
 * Types and hooks for V3 Phase 1: ingestion audit and feature flags.
 * All hooks are read-only; they call GET endpoints only.
 */

import { useQuery } from "@tanstack/react-query";
import { customFetch } from "./custom-fetch";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface V3Flag {
  key: string;
  value: string;
  description: string;
  updated_at: string | null;
}

export interface V3FlagsData {
  flags: V3Flag[];
}

export interface V3CityAuditEntry {
  city: string;
  status: "success" | "partial" | "error" | "skipped" | "no_data" | "unknown";
  sources: Array<{
    provider: string;
    model: string;
    records: number;
    ok_records: number;
  }>;
  earliest_date: string | null;
  latest_date: string | null;
  total_records: number;
  total_ok_records: number;
  total_attempted: number;
  total_accepted: number;
  total_rejected: number;
  rejection_breakdown: Record<string, number>;
  missing_observation_count: number;
  api_errors: string[];
  last_run_at: string | null;
}

export interface V3IngestionAuditData {
  cities: V3CityAuditEntry[];
  summary: {
    cities_with_data: number;
    total_records: number;
    total_ok_records: number;
    total_cities_audited: number;
  };
  note: string;
}

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

export function useGetV3Flags() {
  return useQuery<V3FlagsData>({
    queryKey: ["v3", "flags"],
    queryFn: () => customFetch<V3FlagsData>("/api/analytics/v3/flags"),
    staleTime: 30_000,
  });
}

export function useGetV3IngestionAudit(enabled = true) {
  return useQuery<V3IngestionAuditData>({
    queryKey: ["v3", "ingestion-audit"],
    queryFn: () =>
      customFetch<V3IngestionAuditData>("/api/analytics/v3/ingestion-audit"),
    enabled,
    staleTime: 60_000,
  });
}
