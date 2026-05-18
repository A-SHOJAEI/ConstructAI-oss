"use client";

import { useEffect, useState } from "react";
import { AgentMetricsTable } from "@/components/evaluation/agent-metrics-table";
import { EvaluationTrendChart } from "@/components/evaluation/evaluation-trend-chart";
import { evaluationApi } from "@/lib/evaluation-api";

interface AgentMetric {
  agent_name: string;
  accuracy: number | null;
  avg_latency_ms: number | null;
  total_cost_usd: number | null;
  error_rate: number | null;
  total_invocations: number;
}

export default function EvaluationPage() {
  const [metrics, setMetrics] = useState<AgentMetric[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [isStub, setIsStub] = useState(false);

  useEffect(() => {
    evaluationApi
      .getAgentMetrics()
      .then((data) => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const raw = data as any;
        if (raw?.meta?.stub) {
          setIsStub(true);
        } else {
          setMetrics(raw.data ?? []);
        }
      })
      .catch(() => setIsStub(true));
  }, []);

  if (isStub) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <h2 className="text-xl font-semibold text-gray-600">AI Agent Evaluation</h2>
          <p className="text-gray-400 mt-2">Coming soon — this feature is under development.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-6 text-gray-900 dark:text-white">
        AI Agent Performance Dashboard
      </h1>

      <div className="grid grid-cols-3 gap-4 mb-6">
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow p-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">Active Agents</p>
          <p className="text-3xl font-bold text-gray-900 dark:text-white">{metrics.length}</p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow p-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">Avg Accuracy</p>
          <p className="text-3xl font-bold text-gray-900 dark:text-white">
            {metrics.length > 0
              ? (
                  (metrics
                    .filter((m) => m.accuracy !== null)
                    .reduce((sum, m) => sum + (m.accuracy ?? 0), 0) /
                    Math.max(1, metrics.filter((m) => m.accuracy !== null).length)) *
                  100
                ).toFixed(1) + "%"
              : "N/A"}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow p-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">Total Cost (USD)</p>
          <p className="text-3xl font-bold text-gray-900 dark:text-white">
            ${metrics.reduce((sum, m) => sum + (m.total_cost_usd ?? 0), 0).toFixed(2)}
          </p>
        </div>
      </div>

      <div className="bg-white dark:bg-gray-800 rounded-lg shadow p-6 mb-6">
        <h2 className="text-lg font-semibold mb-4 text-gray-900 dark:text-white">Agent Metrics</h2>
        <AgentMetricsTable metrics={metrics} onSelectAgent={setSelectedAgent} />
      </div>

      {selectedAgent && (
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow p-6">
          <h2 className="text-lg font-semibold mb-4 text-gray-900 dark:text-white">
            Trend: {selectedAgent}
          </h2>
          <EvaluationTrendChart agentName={selectedAgent} />
        </div>
      )}
    </div>
  );
}
