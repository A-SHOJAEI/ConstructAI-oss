import { apiClient } from "./api-client";

export interface EVMSnapshot {
  id: string;
  project_id: string;
  snapshot_date: string;
  bac: number;
  pv: number;
  ev: number;
  ac: number;
  sv: number;
  cv: number;
  spi: number;
  cpi: number;
  eac: number;
  etc: number;
  vac: number;
  tcpi: number;
  percent_complete: number;
  data_date: string;
  created_at: string;
}

export interface SCurveDataPoint {
  date: string;
  pv: number;
  ev: number;
  ac: number;
}

export interface SCurveResponse {
  project_id: string;
  data_points: SCurveDataPoint[];
  bac: number;
  forecast_completion: string | null;
}

export interface MonteCarloResult {
  id: string;
  project_id: string;
  num_iterations: number;
  p10_duration: number;
  p50_duration: number;
  p80_duration: number;
  p90_duration: number;
  mean_duration: number;
  std_dev: number;
  critical_risk_drivers: Record<string, number>[];
  histogram_data: number[];
  created_at: string;
}

export interface ChangeOrderFull {
  id: string;
  project_id: string;
  co_number: number;
  title: string;
  description: string;
  status: string;
  change_type?: string;
  original_amount: number;
  approved_amount: number;
  cost_impact?: number;
  schedule_impact_days: number;
  risk_score: number | null;
  labor_cost?: number;
  material_cost?: number;
  equipment_cost?: number;
  subcontractor_cost?: number;
  overhead_cost?: number;
  markup_pct?: number;
  ai_analysis?: Record<string, unknown>;
  created_at: string;
}

export interface ScopeAnalysisEvidence {
  type: "spec" | "rfi" | "drawing";
  ref: string;
  quote: string;
}

export interface ScopeAnalysisSpecSource {
  document_title: string;
  page_number: number | null;
  section: string | null;
}

export interface ScopeAnalysisRFISource {
  rfi_number: string;
  subject: string;
  similarity_score: number | null;
}

export interface ScopeAnalysisResult {
  verdict:
    | "additional_work"
    | "covered_by_contract"
    | "covered_by_rfi"
    | "needs_clarification";
  summary: string;
  evidence: ScopeAnalysisEvidence[];
  recommendation: string;
  confidence: number;
  spec_sources: ScopeAnalysisSpecSource[];
  rfi_sources: ScopeAnalysisRFISource[];
  model: string | null;
  error?: string;
}

export interface CumulativeImpact {
  total_approved_amount: number;
  total_pending_amount: number;
  total_schedule_impact_days: number;
  original_contract_sum: number;
  revised_contract_sum: number;
  pco_count: number;
  cor_count: number;
  co_count: number;
}

export interface WeatherForecastDay {
  date: string;
  temperature_high: number;
  temperature_low: number;
  conditions: string;
  precipitation_inches: number;
  wind_mph: number;
  impact_score?: number;
}

export interface WeatherForecastResponse {
  forecast: WeatherForecastDay[];
  project_id: string;
}

export const controlsApi = {
  evmSnapshots: (projectId: string) =>
    apiClient.get<{ data: EVMSnapshot[]; meta: { cursor: string | null; has_more: boolean } }>(
      `/api/v1/controls/evm-snapshots?project_id=${projectId}&limit=100`,
    ),

  scurve: (projectId: string) =>
    apiClient.get<SCurveResponse>(`/api/v1/controls/s-curve/${projectId}`),

  scheduleRisk: (projectId: string, baselineId: string, iterations = 1000) =>
    apiClient.post<MonteCarloResult>("/api/v1/controls/schedule-risk", {
      project_id: projectId,
      baseline_id: baselineId,
      num_iterations: iterations,
    }),

  changeOrders: (projectId: string) =>
    apiClient.get<{ data: ChangeOrderFull[]; meta: { cursor: string | null; has_more: boolean } }>(
      `/api/v1/controls/change-orders?project_id=${projectId}&limit=100`,
    ),

  changeOrder: (coId: string) =>
    apiClient.get<ChangeOrderFull>(`/api/v1/controls/change-orders/${coId}`),

  scopeAnalysis: (coId: string) =>
    apiClient.post<ScopeAnalysisResult>(
      `/api/v1/controls/change-orders/${coId}/scope-analysis`,
      {},
      { timeoutMs: 180_000 },
    ),

  cumulativeImpact: (projectId: string) =>
    apiClient.get<CumulativeImpact>(`/api/v1/controls/cumulative-impact?project_id=${projectId}`),

  weatherForecast: (projectId: string, startDate: string, endDate: string) =>
    apiClient.post<WeatherForecastResponse>("/api/v1/scheduling/weather-impact", {
      project_id: projectId,
      start_date: startDate,
      end_date: endDate,
      location: "", // Server resolves from project
    }),
};
