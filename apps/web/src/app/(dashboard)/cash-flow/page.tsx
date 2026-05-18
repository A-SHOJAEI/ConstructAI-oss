"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { DollarSign, TrendingUp, Shield, FileCheck } from "lucide-react";

interface MonthlyProjection {
  month: string;
  planned_income: number;
  planned_expense: number;
  actual_income: number;
  actual_expense: number;
  net_planned: number;
  net_actual: number;
  cumulative_planned: number;
  cumulative_actual: number;
}

interface MonteCarloResult {
  p10: number;
  p50: number;
  p90: number;
  mean: number;
  std_dev: number;
}

interface CashFlowData {
  net_cash_position: number;
  retainage_held: number;
  lien_waiver_coverage_pct: number;
  total_contract_value: number;
  total_billed: number;
  total_received: number;
  monthly_projections: MonthlyProjection[];
  monte_carlo: MonteCarloResult | null;
}

const isValidUUID = (id: string) =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);

function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value);
}

export default function CashFlowPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const [showMonteCarlo, setShowMonteCarlo] = useState(false);

  const { data, isLoading, error } = useQuery<CashFlowData>({
    queryKey: ["cash-flow", projectId],
    queryFn: () => apiClient.get<CashFlowData>(`/api/v1/projects/${projectId}/cash-flow`),
    enabled: !!projectId && isValidUUID(projectId),
  });

  if (!projectId) return <NoProjectSelected />;

  const projections = data?.monthly_projections ?? [];
  const maxVal = Math.max(
    ...projections.map((p) =>
      Math.max(Math.abs(p.cumulative_planned), Math.abs(p.cumulative_actual)),
    ),
    1,
  );

  return (
    <div className="p-4 md:p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Cash Flow</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Monthly projections, retainage, and Monte Carlo confidence analysis
          </p>
        </div>
        <button
          onClick={() => setShowMonteCarlo(!showMonteCarlo)}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
        >
          {showMonteCarlo ? "Hide" : "Show"} Monte Carlo
        </button>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <DollarSign className="h-5 w-5 text-green-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Net Cash Position</p>
          </div>
          <p
            className={`text-2xl font-bold mt-1 ${(data?.net_cash_position ?? 0) >= 0 ? "text-green-600" : "text-red-600"}`}
          >
            {isLoading ? "..." : data ? formatCurrency(data.net_cash_position) : "N/A"}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <Shield className="h-5 w-5 text-blue-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Retainage Held</p>
          </div>
          <p className="text-2xl font-bold text-gray-900 dark:text-white mt-1">
            {isLoading ? "..." : data ? formatCurrency(data.retainage_held) : "N/A"}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <FileCheck className="h-5 w-5 text-purple-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Lien Waiver Coverage</p>
          </div>
          <p className="text-2xl font-bold text-gray-900 dark:text-white mt-1">
            {isLoading ? "..." : data ? `${data.lien_waiver_coverage_pct.toFixed(1)}%` : "N/A"}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <TrendingUp className="h-5 w-5 text-indigo-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Billed / Contract</p>
          </div>
          <p className="text-2xl font-bold text-gray-900 dark:text-white mt-1">
            {isLoading
              ? "..."
              : data
                ? `${((data.total_billed / (data.total_contract_value || 1)) * 100).toFixed(0)}%`
                : "N/A"}
          </p>
        </div>
      </div>

      {isLoading && (
        <div className="p-8 text-center text-gray-500 dark:text-gray-400">
          Loading cash flow data...
        </div>
      )}
      {error && (
        <div className="p-4 text-red-800 bg-red-50 rounded-lg">Failed to load cash flow data</div>
      )}

      {/* Projection Chart (bar representation) */}
      {!isLoading && !error && projections.length > 0 && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
            Monthly Projections (Cumulative)
          </h2>
          <div className="space-y-2">
            {projections.map((m) => (
              <div key={m.month} className="flex items-center gap-3">
                <div className="w-20 text-sm text-gray-600 dark:text-gray-400">{m.month}</div>
                <div className="flex-1 relative">
                  <div className="flex gap-1 items-center h-6">
                    <div
                      className="bg-blue-400 h-5 rounded"
                      style={{ width: `${(Math.abs(m.cumulative_planned) / maxVal) * 100}%` }}
                      title={`Planned: ${formatCurrency(m.cumulative_planned)}`}
                    />
                  </div>
                  <div className="flex gap-1 items-center h-6">
                    <div
                      className="bg-green-500 h-5 rounded"
                      style={{ width: `${(Math.abs(m.cumulative_actual) / maxVal) * 100}%` }}
                      title={`Actual: ${formatCurrency(m.cumulative_actual)}`}
                    />
                  </div>
                </div>
                <div className="w-32 text-xs text-right">
                  <div className="text-blue-600">{formatCurrency(m.cumulative_planned)}</div>
                  <div className="text-green-600">{formatCurrency(m.cumulative_actual)}</div>
                </div>
              </div>
            ))}
          </div>
          <div className="flex gap-4 mt-4 text-xs text-gray-500 dark:text-gray-400">
            <span className="flex items-center gap-1">
              <span className="w-3 h-3 bg-blue-400 rounded-sm inline-block" /> Planned
            </span>
            <span className="flex items-center gap-1">
              <span className="w-3 h-3 bg-green-500 rounded-sm inline-block" /> Actual
            </span>
          </div>
        </div>
      )}

      {!isLoading && !error && projections.length === 0 && (
        <div className="text-center py-12 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-2">
            No projection data
          </h3>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Cash flow projections will appear once billing data is available.
          </p>
        </div>
      )}

      {/* Monte Carlo Confidence Bands */}
      {showMonteCarlo && data?.monte_carlo && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
            Monte Carlo Cash Flow Simulation
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="text-center p-4 bg-red-50 dark:bg-red-900/20 rounded-lg">
              <p className="text-xs text-gray-500 dark:text-gray-400 uppercase">
                P10 (Pessimistic)
              </p>
              <p className="text-xl font-bold text-red-600 mt-1">
                {formatCurrency(data.monte_carlo.p10)}
              </p>
            </div>
            <div className="text-center p-4 bg-yellow-50 dark:bg-yellow-900/20 rounded-lg">
              <p className="text-xs text-gray-500 dark:text-gray-400 uppercase">P50 (Median)</p>
              <p className="text-xl font-bold text-yellow-600 mt-1">
                {formatCurrency(data.monte_carlo.p50)}
              </p>
            </div>
            <div className="text-center p-4 bg-green-50 dark:bg-green-900/20 rounded-lg">
              <p className="text-xs text-gray-500 dark:text-gray-400 uppercase">P90 (Optimistic)</p>
              <p className="text-xl font-bold text-green-600 mt-1">
                {formatCurrency(data.monte_carlo.p90)}
              </p>
            </div>
            <div className="text-center p-4 bg-blue-50 dark:bg-blue-900/20 rounded-lg">
              <p className="text-xs text-gray-500 dark:text-gray-400 uppercase">Mean</p>
              <p className="text-xl font-bold text-blue-600 mt-1">
                {formatCurrency(data.monte_carlo.mean)}
              </p>
            </div>
          </div>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-4 text-center">
            Standard deviation: {formatCurrency(data.monte_carlo.std_dev)} &middot; Based on 10,000
            simulations
          </p>
        </div>
      )}

      {/* Monthly Detail Table */}
      {!isLoading && !error && projections.length > 0 && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Monthly Detail</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
              <thead className="bg-gray-50 dark:bg-gray-900">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Month
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Planned In
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Actual In
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Planned Out
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Actual Out
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Net
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                {projections.map((m) => (
                  <tr key={m.month} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                    <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white">
                      {m.month}
                    </td>
                    <td className="px-6 py-4 text-sm text-right text-gray-500 dark:text-gray-400">
                      {formatCurrency(m.planned_income)}
                    </td>
                    <td className="px-6 py-4 text-sm text-right text-green-600">
                      {formatCurrency(m.actual_income)}
                    </td>
                    <td className="px-6 py-4 text-sm text-right text-gray-500 dark:text-gray-400">
                      {formatCurrency(m.planned_expense)}
                    </td>
                    <td className="px-6 py-4 text-sm text-right text-red-600">
                      {formatCurrency(m.actual_expense)}
                    </td>
                    <td
                      className={`px-6 py-4 text-sm text-right font-medium ${m.net_actual >= 0 ? "text-green-600" : "text-red-600"}`}
                    >
                      {formatCurrency(m.net_actual)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
