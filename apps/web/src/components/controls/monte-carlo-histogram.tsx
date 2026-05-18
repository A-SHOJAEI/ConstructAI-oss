"use client";

import { useMemo } from "react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  Cell,
} from "recharts";

interface MonteCarloHistogramProps {
  histogramData: number[];
  p50: number;
  p80: number;
  p90: number;
  meanDuration: number;
}

export function MonteCarloHistogram({
  histogramData,
  p50,
  p80,
  p90,
  meanDuration,
}: MonteCarloHistogramProps) {
  const bins = useMemo(() => {
    if (!histogramData.length) return [];
    const min = Math.floor(Math.min(...histogramData));
    const max = Math.ceil(Math.max(...histogramData));
    const binCount = 25;
    const binWidth = (max - min) / binCount || 1;

    const buckets = Array.from({ length: binCount }, (_, i) => ({
      range: Math.round(min + i * binWidth),
      count: 0,
      label: `${Math.round(min + i * binWidth)}d`,
    }));

    for (const val of histogramData) {
      const idx = Math.min(Math.floor((val - min) / binWidth), binCount - 1);
      if (idx >= 0 && idx < binCount) buckets[idx].count++;
    }
    return buckets;
  }, [histogramData]);

  if (!bins.length) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-400 text-sm">
        No Monte Carlo data available
      </div>
    );
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-gray-900">
          Schedule Risk — Monte Carlo Simulation
        </h3>
        <div className="flex gap-4 text-xs">
          <span className="text-blue-600">P50: {Math.round(p50)}d</span>
          <span className="text-amber-600">P80: {Math.round(p80)}d</span>
          <span className="text-red-600">P90: {Math.round(p90)}d</span>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={bins} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
          <XAxis dataKey="label" tick={{ fontSize: 10 }} interval={4} />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip
            formatter={(value: number) => [`${value} iterations`, "Frequency"]}
            labelFormatter={(label: string) => `Duration: ${label}`}
          />
          <ReferenceLine
            x={`${Math.round(p50)}d`}
            stroke="#3b82f6"
            strokeWidth={2}
            label={{ value: "P50", position: "top", fontSize: 10, fill: "#3b82f6" }}
          />
          <ReferenceLine
            x={`${Math.round(p90)}d`}
            stroke="#ef4444"
            strokeWidth={2}
            label={{ value: "P90", position: "top", fontSize: 10, fill: "#ef4444" }}
          />
          <Bar dataKey="count" radius={[2, 2, 0, 0]}>
            {bins.map((entry, index) => (
              <Cell
                key={index}
                fill={entry.range <= p50 ? "#22c55e" : entry.range <= p80 ? "#f59e0b" : "#ef4444"}
                opacity={0.8}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <p className="text-xs text-gray-500 mt-2 text-center">
        Mean: {Math.round(meanDuration)} days | {histogramData.length.toLocaleString()} iterations
      </p>
    </div>
  );
}
