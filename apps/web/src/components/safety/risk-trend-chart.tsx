"use client";

import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
} from "recharts";

interface RiskTrendChartProps {
  trends: { date: string; score: number }[];
}

export function RiskTrendChart({ trends }: RiskTrendChartProps) {
  if (!trends.length) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-400 text-sm">
        No trend data available
      </div>
    );
  }

  const data = trends.map((t) => ({
    date: new Date(t.date).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
    score: t.score,
  }));

  return (
    <div>
      <h4 className="text-sm font-medium text-gray-700 mb-2">Risk Score Trend</h4>
      <ResponsiveContainer width="100%" height={200}>
        <AreaChart data={data} margin={{ top: 5, right: 10, bottom: 5, left: 0 }}>
          <defs>
            <linearGradient id="riskGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#f59e0b" stopOpacity={0.05} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
          <XAxis dataKey="date" tick={{ fontSize: 10 }} />
          <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} />
          <Tooltip />
          <ReferenceLine y={30} stroke="#22c55e" strokeDasharray="3 3" />
          <ReferenceLine y={60} stroke="#ef4444" strokeDasharray="3 3" />
          <Area
            type="monotone"
            dataKey="score"
            stroke="#f59e0b"
            fill="url(#riskGradient)"
            strokeWidth={2}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
