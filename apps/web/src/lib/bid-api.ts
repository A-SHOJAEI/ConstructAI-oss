import { apiClient } from "./api-client";

export interface BidOpportunity {
  id: string;
  org_id: string;
  project_name: string;
  owner_name: string | null;
  location: string | null;
  project_type: string;
  delivery_method: string;
  estimated_value: number;
  bid_due_date: string | null;
  status: string;
  description: string | null;
  created_at: string;
}

export interface BidDecision {
  id: string;
  opportunity_id: string;
  ai_score: number;
  ai_recommendation: string;
  ai_reasoning: string | null;
  human_decision: string | null;
  human_notes: string | null;
  factor_scores: Record<string, number>;
  win_probability: number;
  created_at: string;
}

export interface BidWithDecision extends BidOpportunity {
  latest_decision: BidDecision | null;
}

export interface BidAnalytics {
  total_opportunities: number;
  win_rate: number;
  avg_ai_score: number;
  by_type: Record<string, { count: number; wins: number; rate: number }>;
  by_method: Record<string, { count: number; wins: number; rate: number }>;
}

export const bidApi = {
  list: (orgId: string, status?: string) => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    const qs = params.toString();
    return apiClient.get<{
      data: BidWithDecision[];
      meta: { cursor: string | null; has_more: boolean };
    }>(`/api/v1/orgs/${orgId}/bid-opportunities${qs ? "?" + qs : ""}`);
  },

  get: (orgId: string, id: string) =>
    apiClient.get<BidWithDecision>(`/api/v1/orgs/${orgId}/bid-opportunities/${id}`),

  score: (orgId: string, id: string) =>
    apiClient.post<BidDecision>(`/api/v1/orgs/${orgId}/bid-opportunities/${id}/score`, {}),

  decide: (orgId: string, id: string, decision: string, notes?: string) =>
    apiClient.post<BidDecision>(`/api/v1/orgs/${orgId}/bid-opportunities/${id}/decide`, {
      human_decision: decision,
      human_notes: notes,
    }),

  analytics: (orgId: string) => apiClient.get<BidAnalytics>(`/api/v1/orgs/${orgId}/bid-analytics`),
};
