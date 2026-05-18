import { apiClient } from "./api-client";

export interface IntelligenceBrief {
  id: string;
  project_id: string;
  report_date: string;
  overall_health_score: number;
  project_status: string;
  schedule_health_score: number;
  cost_health_score: number;
  risk_score: number;
  productivity_score: number;
  executive_summary: string;
  schedule_intelligence: string | null;
  cost_intelligence: string | null;
  risk_intelligence: string | null;
  productivity_intelligence: string | null;
  action_items: Record<string, unknown>[];
  metrics_dashboard: Record<string, unknown> | null;
  narrative_report: string | null;
  created_at: string;
}

export const intelligenceApi = {
  getLatest: (projectId: string) =>
    apiClient.get<IntelligenceBrief>(`/api/v1/projects/${projectId}/intelligence-brief/latest`),

  getHistory: (projectId: string) =>
    apiClient.get<{
      data: IntelligenceBrief[];
      meta: { cursor: string | null; has_more: boolean };
    }>(`/api/v1/projects/${projectId}/intelligence-brief/history`),

  generate: (projectId: string) =>
    apiClient.post<IntelligenceBrief>(`/api/v1/projects/${projectId}/intelligence-brief`, {}),
};
