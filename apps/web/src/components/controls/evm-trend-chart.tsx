"use client";

import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceLine,
} from "recharts";
import type { EVMSnapshot } from "@/lib/controls-api";

interface EVMTrendChartProps {
  snapshots: EVMSnapshot[];
}

export function EVMTrendChart({ snapshots }: EVMTrendChartProps) {
  if (!snapshots.length) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-400 text-sm">
        No EVM trend data available
      </div>
    );
  }

  const data = snapshots
    .sort((a, b) => a.snapshot_date.localeCompare(b.snapshot_date))
    .map((s) => ({
      date: new Date(s.snapshot_date).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
      }),
      spi: Number(s.spi),
      cpi: Number(s.cpi),
    }));

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <h3 className="text-sm font-semibold text-gray-900 mb-4">CPI / SPI Trend</h3>
      <ResponsiveContainer width="100%" height={250}>
        <LineChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
          <XAxis dataKey="date" tick={{ fontSize: 11 }} />
          <YAxis domain={[0.7, 1.3]} tick={{ fontSize: 11 }} />
          <Tooltip />
          <Legend />
          <ReferenceLine
            y={1.0}
            stroke="#9ca3af"
            strokeDasharray="4 4"
            label={{ value: "1.0", position: "right", fontSize: 10 }}
          />
          <Line
            type="monotone"
            dataKey="cpi"
            name="CPI"
            stroke="#22c55e"
            strokeWidth={2}
            dot={{ r: 3 }}
          />
          <Line
            type="monotone"
            dataKey="spi"
            name="SPI"
            stroke="#3b82f6"
            strokeWidth={2}
            dot={{ r: 3 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
