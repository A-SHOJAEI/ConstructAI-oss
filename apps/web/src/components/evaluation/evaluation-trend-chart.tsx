"use client";

import { useEffect, useState } from "react";
import { evaluationApi } from "@/lib/evaluation-api";

interface HistoryPoint {
  date: string;
  metric_name: string;
  metric_value: number;
}

export function EvaluationTrendChart({ agentName }: { agentName: string }) {
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    evaluationApi
      .getAgentHistory(agentName)
      .then((data) => {
        setHistory(Array.isArray(data) ? data : []);
      })
      .catch(() => {
        setHistory([]);
      })
      .finally(() => {
        setLoading(false);
      });
  }, [agentName]);

  if (loading) {
    return (
      <div className="text-gray-400 text-center py-4 animate-pulse">Loading trend data...</div>
    );
  }

  if (history.length === 0) {
    return (
      <p className="text-gray-500 text-center py-4">
        No historical data for {agentName.replace(/_/g, " ")}.
      </p>
    );
  }

  const maxValue = Math.max(...history.map((h) => h.metric_value), 1);

  return (
    <div>
      <div className="flex items-end gap-1 h-40">
        {history.map((point, idx) => (
          <div
            key={idx}
            className="flex-1 bg-blue-500 rounded-t"
            style={{
              height: `${(point.metric_value / maxValue) * 100}%`,
            }}
            title={`${point.date}: ${point.metric_value.toFixed(4)}`}
          />
        ))}
      </div>
      <div className="flex gap-1 mt-1">
        {history.map((point, idx) => (
          <div key={idx} className="flex-1 text-xs text-gray-400 text-center truncate">
            {point.date}
          </div>
        ))}
      </div>
    </div>
  );
}
