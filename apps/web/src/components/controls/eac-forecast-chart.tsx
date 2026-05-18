"use client";

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
import type { EVMSnapshot } from "@/lib/controls-api";

const formatCurrency = (value: number) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    notation: "compact",
  }).format(value);

interface EACForecastChartProps {
  latest: EVMSnapshot | null;
}

export function EACForecastChart({ latest }: EACForecastChartProps) {
  if (!latest) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-400 text-sm">
        No forecast data available
      </div>
    );
  }

  const bac = Number(latest.bac);
  const cpi = Number(latest.cpi) || 1;
  const spi = Number(latest.spi) || 1;
  const eacCpi = bac / cpi;
  const eacSpiCpi = bac / (spi * cpi);

  const data = [
    { name: "BAC", value: bac, type: "baseline" },
    { name: "EAC (CPI)", value: eacCpi, type: "forecast" },
    { name: "EAC (SPI×CPI)", value: eacSpiCpi, type: "forecast" },
    { name: "EAC (Actual)", value: Number(latest.eac), type: "forecast" },
  ];

  const colors: Record<string, string> = { baseline: "#6366f1", forecast: "#f59e0b" };

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <h3 className="text-sm font-semibold text-gray-900 mb-4">EAC Forecast Comparison</h3>
      <ResponsiveContainer width="100%" height={250}>
        <BarChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
          <XAxis dataKey="name" tick={{ fontSize: 11 }} />
          <YAxis tickFormatter={formatCurrency} tick={{ fontSize: 11 }} width={70} />
          <Tooltip formatter={(value: number) => formatCurrency(value)} />
          <ReferenceLine y={bac} stroke="#6366f1" strokeDasharray="4 4" />
          <Bar dataKey="value" radius={[4, 4, 0, 0]}>
            {data.map((entry, index) => (
              <Cell key={index} fill={colors[entry.type]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
