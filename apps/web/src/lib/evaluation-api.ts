import { apiClient } from "./api-client";

interface AgentMetrics {
  agent_name: string;
  accuracy: number | null;
  avg_latency_ms: number | null;
  total_cost_usd: number | null;
  error_rate: number | null;
  total_invocations: number;
  metrics: Record<string, number>;
}

interface HistoryPoint {
  date: string;
  metric_name: string;
  metric_value: number;
}

interface EvaluationResult {
  status: string;
  results: Record<string, unknown>;
}

export const evaluationApi = {
  getAgentMetrics: () => apiClient.get<AgentMetrics[]>("/api/v1/evaluation/agents"),

  getAgentHistory: (agentName: string) =>
    apiClient.get<HistoryPoint[]>(`/api/v1/evaluation/agents/${agentName}/history`),

  triggerEvaluation: (agentNames?: string[]) =>
    apiClient.post<EvaluationResult>("/api/v1/evaluation/run", { agent_names: agentNames }),
};
