"use client";

import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";
import type { SCurveDataPoint } from "@/lib/controls-api";

const formatCurrency = (value: number) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    notation: "compact",
  }).format(value);

const formatDate = (dateStr: string) => {
  const d = new Date(dateStr);
  return d.toLocaleDateString("en-US", { month: "short", year: "2-digit" });
};

interface SCurveChartProps {
  data: SCurveDataPoint[];
  bac: number;
}

export function SCurveChart({ data, bac }: SCurveChartProps) {
  if (!data.length) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-400 text-sm">
        No S-curve data available
      </div>
    );
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-gray-900">
          S-Curve — Planned vs Earned vs Actual
        </h3>
        <span className="text-xs text-gray-500">BAC: {formatCurrency(bac)}</span>
      </div>
      <ResponsiveContainer width="100%" height={300}>
        <ComposedChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
          <XAxis dataKey="date" tickFormatter={formatDate} tick={{ fontSize: 11 }} />
          <YAxis tickFormatter={formatCurrency} tick={{ fontSize: 11 }} width={70} />
          <Tooltip
            formatter={(value: number) => formatCurrency(value)}
            labelFormatter={(label: string) => formatDate(label)}
          />
          <Legend />
          <Line
            type="monotone"
            dataKey="pv"
            name="Planned Value"
            stroke="#6366f1"
            strokeDasharray="6 3"
            strokeWidth={2}
            dot={false}
          />
          <Line
            type="monotone"
            dataKey="ev"
            name="Earned Value"
            stroke="#22c55e"
            strokeWidth={2}
            dot={false}
          />
          <Line
            type="monotone"
            dataKey="ac"
            name="Actual Cost"
            stroke="#ef4444"
            strokeWidth={2}
            dot={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
