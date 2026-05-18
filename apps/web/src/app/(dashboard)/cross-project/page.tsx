"use client";

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { BarChart3, TrendingUp, MessageCircleQuestion, Search, Loader2 } from "lucide-react";

interface CostPattern {
  building_type: string;
  avg_cost_per_sf: number;
  min_cost_per_sf: number;
  max_cost_per_sf: number;
  project_count: number;
  trend_pct: number;
}

interface ScheduleAccuracy {
  project_type: string;
  avg_planned_duration_days: number;
  avg_actual_duration_days: number;
  accuracy_pct: number;
  project_count: number;
}

interface RFIPattern {
  category: string;
  avg_count_per_project: number;
  avg_resolution_days: number;
  most_common_subject: string;
  unnecessary_pct: number;
}

interface NLQueryResult {
  answer: string;
  supporting_data: Record<string, unknown>[];
  confidence: number;
}

interface CrossProjectData {
  cost_patterns: CostPattern[];
  schedule_accuracy: ScheduleAccuracy[];
  rfi_patterns: RFIPattern[];
}

export default function CrossProjectPage() {
  const [nlQuery, setNlQuery] = useState("");

  const { data, isLoading, error } = useQuery<CrossProjectData>({
    queryKey: ["cross-project-insights"],
    queryFn: () => apiClient.get<CrossProjectData>("/api/v1/insights/cross-project"),
  });

  const nlMutation = useMutation({
    mutationFn: (query: string) =>
      apiClient.post<NLQueryResult>("/api/v1/insights/query", { query }),
  });

  const handleNLQuery = () => {
    if (nlQuery.trim()) {
      nlMutation.mutate(nlQuery.trim());
    }
  };

  function formatCurrency(value: number): string {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 0,
    }).format(value);
  }

  return (
    <div className="p-4 md:p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Cross-Project Insights</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Cost patterns, schedule accuracy, and RFI trends across all projects
        </p>
      </div>

      {/* Natural Language Query */}
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-3">Ask a Question</h2>
        <div className="flex gap-3">
          <input
            type="text"
            value={nlQuery}
            onChange={(e) => setNlQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleNLQuery()}
            placeholder="e.g., What is the average cost per SF for healthcare projects?"
            className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm dark:bg-gray-700 dark:text-gray-200"
          />
          <button
            onClick={handleNLQuery}
            disabled={nlMutation.isPending || !nlQuery.trim()}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            {nlMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Search className="h-4 w-4" />
            )}
            Query
          </button>
        </div>
        {nlMutation.data && (
          <div className="mt-4 p-4 bg-blue-50 dark:bg-blue-900/20 rounded-lg">
            <p className="text-sm text-gray-900 dark:text-white">{nlMutation.data.answer}</p>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-2">
              Confidence: {(nlMutation.data.confidence * 100).toFixed(0)}%
            </p>
          </div>
        )}
        {nlMutation.error && (
          <p className="mt-3 text-sm text-red-600">Failed to process query. Please try again.</p>
        )}
      </div>

      {isLoading && (
        <div className="p-8 text-center text-gray-500 dark:text-gray-400">
          Loading cross-project insights...
        </div>
      )}
      {error && (
        <div className="p-4 text-red-800 bg-red-50 rounded-lg">Failed to load insights</div>
      )}

      {!isLoading && !error && (
        <div className="space-y-6">
          {/* Cost Patterns */}
          <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center gap-2">
              <BarChart3 className="h-5 w-5 text-blue-500" />
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
                Cost Patterns by Building Type
              </h2>
            </div>
            {!data?.cost_patterns || data.cost_patterns.length === 0 ? (
              <p className="text-sm text-gray-500 dark:text-gray-400 py-8 text-center">
                No cost pattern data available.
              </p>
            ) : (
              <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                <thead className="bg-gray-50 dark:bg-gray-900">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Building Type
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Avg $/SF
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Range
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Projects
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Trend
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                  {data.cost_patterns.map((cp) => (
                    <tr key={cp.building_type} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                      <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white capitalize">
                        {cp.building_type.replace("_", " ")}
                      </td>
                      <td className="px-6 py-4 text-sm text-right font-medium text-gray-900 dark:text-white">
                        {formatCurrency(cp.avg_cost_per_sf)}
                      </td>
                      <td className="px-6 py-4 text-sm text-right text-gray-500 dark:text-gray-400">
                        {formatCurrency(cp.min_cost_per_sf)} - {formatCurrency(cp.max_cost_per_sf)}
                      </td>
                      <td className="px-6 py-4 text-sm text-right text-gray-500 dark:text-gray-400">
                        {cp.project_count}
                      </td>
                      <td className="px-6 py-4 text-sm text-right">
                        <span
                          className={`font-medium ${cp.trend_pct >= 0 ? "text-red-600" : "text-green-600"}`}
                        >
                          {cp.trend_pct >= 0 ? "+" : ""}
                          {cp.trend_pct.toFixed(1)}%
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Schedule Accuracy */}
          <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center gap-2">
              <TrendingUp className="h-5 w-5 text-green-500" />
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
                Schedule Accuracy by Project Type
              </h2>
            </div>
            {!data?.schedule_accuracy || data.schedule_accuracy.length === 0 ? (
              <p className="text-sm text-gray-500 dark:text-gray-400 py-8 text-center">
                No schedule accuracy data available.
              </p>
            ) : (
              <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                <thead className="bg-gray-50 dark:bg-gray-900">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Project Type
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Avg Planned
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Avg Actual
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Accuracy
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Projects
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                  {data.schedule_accuracy.map((sa) => (
                    <tr key={sa.project_type} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                      <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white capitalize">
                        {sa.project_type.replace("_", " ")}
                      </td>
                      <td className="px-6 py-4 text-sm text-right text-gray-500 dark:text-gray-400">
                        {sa.avg_planned_duration_days}d
                      </td>
                      <td className="px-6 py-4 text-sm text-right text-gray-500 dark:text-gray-400">
                        {sa.avg_actual_duration_days}d
                      </td>
                      <td className="px-6 py-4 text-sm text-right">
                        <span
                          className={`font-medium ${sa.accuracy_pct >= 90 ? "text-green-600" : sa.accuracy_pct >= 75 ? "text-yellow-600" : "text-red-600"}`}
                        >
                          {sa.accuracy_pct.toFixed(0)}%
                        </span>
                      </td>
                      <td className="px-6 py-4 text-sm text-right text-gray-500 dark:text-gray-400">
                        {sa.project_count}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* RFI Patterns */}
          <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center gap-2">
              <MessageCircleQuestion className="h-5 w-5 text-purple-500" />
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white">RFI Patterns</h2>
            </div>
            {!data?.rfi_patterns || data.rfi_patterns.length === 0 ? (
              <p className="text-sm text-gray-500 dark:text-gray-400 py-8 text-center">
                No RFI pattern data available.
              </p>
            ) : (
              <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                <thead className="bg-gray-50 dark:bg-gray-900">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Category
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Avg / Project
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Avg Resolution
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Common Subject
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Unnecessary %
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                  {data.rfi_patterns.map((rp) => (
                    <tr key={rp.category} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                      <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white">
                        {rp.category}
                      </td>
                      <td className="px-6 py-4 text-sm text-right text-gray-500 dark:text-gray-400">
                        {rp.avg_count_per_project.toFixed(1)}
                      </td>
                      <td className="px-6 py-4 text-sm text-right text-gray-500 dark:text-gray-400">
                        {rp.avg_resolution_days.toFixed(1)}d
                      </td>
                      <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400 max-w-xs truncate">
                        {rp.most_common_subject}
                      </td>
                      <td className="px-6 py-4 text-sm text-right">
                        <span
                          className={`font-medium ${rp.unnecessary_pct > 20 ? "text-red-600" : "text-gray-600 dark:text-gray-400"}`}
                        >
                          {rp.unnecessary_pct.toFixed(0)}%
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
