"use client";

import { useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { Camera, Upload, TrendingUp, TrendingDown, Minus } from "lucide-react";
import { toast } from "sonner";

interface ProgressSnapshot {
  id: string;
  photo_url: string | null;
  captured_at: string;
  ai_summary: string | null;
  overall_progress_pct: number;
  activities_detected: number;
}

interface ActivityProgress {
  id: string;
  activity_name: string;
  planned_pct: number;
  ai_detected_pct: number;
  variance: number;
  status: "ahead" | "on_track" | "behind";
  last_updated: string;
}

interface ProgressData {
  latest_snapshot: ProgressSnapshot | null;
  snapshots: ProgressSnapshot[];
  activities: ActivityProgress[];
  overall_planned_pct: number;
  overall_actual_pct: number;
}

const isValidUUID = (id: string) =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);

const statusConfig: Record<string, { label: string; color: string; bg: string }> = {
  ahead: { label: "Ahead", color: "text-green-600", bg: "bg-green-100 text-green-800" },
  on_track: { label: "On Track", color: "text-blue-600", bg: "bg-blue-100 text-blue-800" },
  behind: { label: "Behind", color: "text-red-600", bg: "bg-red-100 text-red-800" },
};

export default function ProgressPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const queryClient = useQueryClient();

  const { data, isLoading, error } = useQuery<ProgressData>({
    queryKey: ["progress", projectId],
    queryFn: () => apiClient.get<ProgressData>(`/api/v1/projects/${projectId}/progress`),
    enabled: !!projectId && isValidUUID(projectId),
  });

  const uploadMutation = useMutation({
    mutationFn: (formData: FormData) => {
      if (!projectId || !isValidUUID(projectId))
        return Promise.reject(new Error("Invalid project ID"));
      return apiClient.upload<ProgressSnapshot>(
        `/api/v1/projects/${projectId}/progress/photos`,
        formData,
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["progress", projectId] });
      toast.success("Photo uploaded for analysis");
    },
    onError: () => toast.error("Failed to upload photo"),
  });

  const handleUploadPhoto = useCallback(() => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.multiple = true;
    input.onchange = () => {
      if (input.files) {
        Array.from(input.files).forEach((file) => {
          const formData = new FormData();
          formData.append("file", file);
          uploadMutation.mutate(formData);
        });
      }
    };
    input.click();
  }, [uploadMutation]);

  if (!projectId) return <NoProjectSelected />;

  const activities = data?.activities ?? [];
  const snapshot = data?.latest_snapshot;

  return (
    <div className="p-4 md:p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Progress Tracking</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            AI-powered photo analysis and activity progress comparison
          </p>
        </div>
        <button
          onClick={handleUploadPhoto}
          disabled={uploadMutation.isPending}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
        >
          <Upload className="h-4 w-4" />
          {uploadMutation.isPending ? "Uploading..." : "Upload Photo"}
        </button>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">Overall Planned</p>
          <p className="text-3xl font-bold text-blue-600 mt-1">
            {isLoading ? "..." : `${(data?.overall_planned_pct ?? 0).toFixed(1)}%`}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">Overall Actual (AI)</p>
          <p className="text-3xl font-bold text-green-600 mt-1">
            {isLoading ? "..." : `${(data?.overall_actual_pct ?? 0).toFixed(1)}%`}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">Snapshots</p>
          <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">
            {isLoading ? "..." : (data?.snapshots?.length ?? 0)}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">Activities Tracked</p>
          <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">
            {isLoading ? "..." : activities.length}
          </p>
        </div>
      </div>

      {isLoading && (
        <div className="p-8 text-center text-gray-500 dark:text-gray-400">
          Loading progress data...
        </div>
      )}
      {error && (
        <div className="p-4 text-red-800 bg-red-50 rounded-lg">Failed to load progress data</div>
      )}

      {/* Latest Snapshot Summary */}
      {!isLoading && snapshot && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
          <div className="flex items-center gap-2 mb-3">
            <Camera className="h-5 w-5 text-blue-500" />
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Latest Snapshot</h2>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <p className="text-xs text-gray-500 dark:text-gray-400 uppercase">Captured</p>
              <p className="text-sm font-medium text-gray-900 dark:text-white">
                {new Date(snapshot.captured_at).toLocaleString()}
              </p>
            </div>
            <div>
              <p className="text-xs text-gray-500 dark:text-gray-400 uppercase">Overall Progress</p>
              <p className="text-sm font-medium text-gray-900 dark:text-white">
                {snapshot.overall_progress_pct.toFixed(1)}%
              </p>
            </div>
            <div>
              <p className="text-xs text-gray-500 dark:text-gray-400 uppercase">
                Activities Detected
              </p>
              <p className="text-sm font-medium text-gray-900 dark:text-white">
                {snapshot.activities_detected}
              </p>
            </div>
          </div>
          {snapshot.ai_summary && (
            <p className="text-sm text-gray-600 dark:text-gray-300 mt-3 bg-gray-50 dark:bg-gray-700 p-3 rounded">
              {snapshot.ai_summary}
            </p>
          )}
        </div>
      )}

      {/* Activity Progress Table */}
      {!isLoading && !error && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
              Activity Progress
            </h2>
          </div>
          {activities.length === 0 ? (
            <div className="text-center py-12">
              <Camera className="mx-auto h-12 w-12 text-gray-400" />
              <h3 className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">
                No activity data
              </h3>
              <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                Upload progress photos for AI analysis.
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                <thead className="bg-gray-50 dark:bg-gray-900">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Activity
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Planned %
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      AI Detected %
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Variance
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Status
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Updated
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                  {activities.map((act) => {
                    const cfg = statusConfig[act.status] ?? statusConfig.on_track;
                    return (
                      <tr key={act.id} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                        <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white">
                          {act.activity_name}
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                          {act.planned_pct.toFixed(1)}%
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-900 dark:text-white font-medium">
                          {act.ai_detected_pct.toFixed(1)}%
                        </td>
                        <td className="px-6 py-4">
                          <span
                            className={`inline-flex items-center gap-1 text-sm font-medium ${cfg.color}`}
                          >
                            {act.variance > 0 ? (
                              <TrendingUp className="h-4 w-4" />
                            ) : act.variance < 0 ? (
                              <TrendingDown className="h-4 w-4" />
                            ) : (
                              <Minus className="h-4 w-4" />
                            )}
                            {act.variance > 0 ? "+" : ""}
                            {act.variance.toFixed(1)}%
                          </span>
                        </td>
                        <td className="px-6 py-4">
                          <span
                            className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${cfg.bg}`}
                          >
                            {cfg.label}
                          </span>
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                          {new Date(act.last_updated).toLocaleDateString()}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
