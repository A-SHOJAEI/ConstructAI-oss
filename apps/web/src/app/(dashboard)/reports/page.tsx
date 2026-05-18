"use client";

import { useState, Suspense } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";

interface DailyReport {
  id: string;
  project_id: string;
  report_date: string;
  narrative_markdown: string | null;
  status: string;
  generated_by: string | null;
  reviewed_by: string | null;
  approved_at: string | null;
  created_at: string;
  updated_at: string;
}

interface DailyReportListResponse {
  data: DailyReport[];
  meta: { cursor?: string | null; has_more?: boolean };
}

const statusColors: Record<string, string> = {
  draft: "bg-yellow-100 text-yellow-800",
  reviewed: "bg-blue-100 text-blue-800",
  approved: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
};

export default function ReportsPage() {
  return (
    <Suspense fallback={<div className="p-6 text-gray-400">Loading...</div>}>
      <ReportsPageContent />
    </Suspense>
  );
}

function ReportsPageContent() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery<DailyReportListResponse>({
    queryKey: ["daily-reports", projectId],
    queryFn: () =>
      apiClient.get<DailyReportListResponse>(
        `/api/v1/projects/${projectId}/daily-reports?limit=50`,
      ),
    enabled: !!projectId,
  });

  const generateMutation = useMutation({
    mutationFn: () => {
      const today = new Date().toISOString().slice(0, 10);
      return apiClient.post<DailyReport>(
        `/api/v1/projects/${projectId}/daily-reports/generate`,
        { report_date: today },
        // Daily report aggregation + LLM narrative can take 30-90s.
        { timeoutMs: 180_000 },
      );
    },
    onSuccess: (report) => {
      queryClient.invalidateQueries({ queryKey: ["daily-reports", projectId] });
      setSelectedId(report.id);
    },
  });

  const reports = data?.data ?? [];
  const selected = reports.find((r) => r.id === selectedId) ?? reports[0] ?? null;

  if (!projectId) return <NoProjectSelected />;

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Daily Reports</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            AI-generated daily project reports
          </p>
        </div>
        <button
          onClick={() => generateMutation.mutate()}
          disabled={generateMutation.isPending}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {generateMutation.isPending ? "Generating..." : "Generate Today's Report"}
        </button>
      </div>

      {generateMutation.isError && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-800">
          <p className="text-sm">
            Failed to generate report: {(generateMutation.error as Error).message}
          </p>
        </div>
      )}

      {isLoading && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-8 text-center">
          <p className="text-gray-500 dark:text-gray-400">Loading reports...</p>
        </div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-800">
          <p className="font-medium">Failed to load reports</p>
          <p className="text-sm mt-1">{(error as Error).message}</p>
        </div>
      )}

      {!isLoading && !error && reports.length === 0 && (
        <div className="text-center py-12 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-2">No reports yet</h3>
          <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
            Click &quot;Generate Today&apos;s Report&quot; to create the first one.
          </p>
        </div>
      )}

      {!isLoading && !error && reports.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-1 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900">
              <p className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                {reports.length} reports
              </p>
            </div>
            <ul className="divide-y divide-gray-200 dark:divide-gray-700 max-h-[70vh] overflow-y-auto">
              {reports.map((r) => {
                const active = selected?.id === r.id;
                return (
                  <li key={r.id}>
                    <button
                      onClick={() => setSelectedId(r.id)}
                      className={`w-full text-left px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-700 ${
                        active ? "bg-blue-50 dark:bg-blue-900/30" : ""
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium text-gray-900 dark:text-gray-100">
                          {r.report_date}
                        </span>
                        <span
                          className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                            statusColors[r.status] ?? "bg-gray-100 text-gray-800"
                          }`}
                        >
                          {r.status}
                        </span>
                      </div>
                      <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                        {new Date(r.created_at).toLocaleString()}
                      </p>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>

          <div className="lg:col-span-2">
            {selected ? (
              <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
                      Daily Report — {selected.report_date}
                    </h2>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                      Generated {new Date(selected.created_at).toLocaleString()}
                    </p>
                  </div>
                  <span
                    className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                      statusColors[selected.status] ?? "bg-gray-100 text-gray-800"
                    }`}
                  >
                    {selected.status}
                  </span>
                </div>
                <div className="prose prose-sm dark:prose-invert max-w-none whitespace-pre-wrap text-gray-800 dark:text-gray-200">
                  {selected.narrative_markdown ?? "(no narrative generated)"}
                </div>
              </div>
            ) : (
              <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-8 text-center text-gray-500">
                Select a report to view its narrative.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
