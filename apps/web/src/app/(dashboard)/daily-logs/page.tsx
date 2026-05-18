"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { apiClient } from "@/lib/api-client";
import {
  Plus,
  Cloud,
  Users,
  Clock,
  CheckCircle2,
  Download,
  Copy,
  Sun,
  CloudRain,
} from "lucide-react";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { toast } from "sonner";

interface DailyLogItem {
  id: string;
  project_id: string;
  log_date: string;
  status: string;
  weather: Record<string, unknown>;
  crew_count: number;
  work_hours: number;
  work_narrative: string | null;
  manpower_by_trade: { trade: string; headcount: number; hours: number }[];
  created_at: string;
}

interface DailyLogListResponse {
  data: DailyLogItem[];
  meta: { cursor: string | null; has_more: boolean };
}

const statusColors: Record<string, string> = {
  draft: "bg-gray-100 text-gray-800",
  submitted: "bg-yellow-100 text-yellow-800",
  approved: "bg-green-100 text-green-800",
};

const statusIcons: Record<string, typeof Clock> = {
  draft: Clock,
  submitted: Clock,
  approved: CheckCircle2,
};

export default function DailyLogsPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const router = useRouter();
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState("");
  const [creating, setCreating] = useState(false);

  const queryParams = new URLSearchParams();
  if (statusFilter) queryParams.set("status", statusFilter);

  const { data, isLoading, error } = useQuery<DailyLogListResponse>({
    queryKey: ["daily-logs", projectId, statusFilter],
    queryFn: () =>
      apiClient.get<DailyLogListResponse>(
        `/api/v1/projects/${projectId}/daily-logs?${queryParams.toString()}`,
      ),
    enabled: !!projectId,
  });

  const logs = data?.data ?? [];

  if (!projectId) return <NoProjectSelected />;

  const handleCreateToday = async () => {
    setCreating(true);
    try {
      const today = new Date().toISOString().split("T")[0];
      await apiClient.post(`/api/v1/projects/${projectId}/daily-logs`, {
        log_date: today,
      });
      queryClient.invalidateQueries({ queryKey: ["daily-logs"] });
    } catch {
      toast.error("Failed to create daily log. Please try again.");
    } finally {
      setCreating(false);
    }
  };

  const handleCopyPrevious = async () => {
    setCreating(true);
    try {
      const today = new Date().toISOString().split("T")[0];
      await apiClient.post(
        `/api/v1/projects/${projectId}/daily-logs/copy-previous?target_date=${today}`,
        {},
      );
      queryClient.invalidateQueries({ queryKey: ["daily-logs"] });
    } catch {
      toast.error("Failed to copy previous log. Please try again.");
    } finally {
      setCreating(false);
    }
  };

  const handleExport = async () => {
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const response = await fetch(`${baseUrl}/api/v1/projects/${projectId}/daily-logs/export`, {
        credentials: "include",
      });
      if (!response.ok) throw new Error(`Export failed: ${response.status}`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "daily_logs_export.csv";
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error("Failed to export daily logs. Please try again.");
    }
  };

  const getWeatherIcon = (weather: Record<string, unknown>) => {
    const conditions = String(weather?.conditions || "").toLowerCase();
    if (conditions.includes("rain") || conditions.includes("storm"))
      return <CloudRain className="h-5 w-5 text-blue-500" />;
    return <Sun className="h-5 w-5 text-yellow-500" />;
  };

  return (
    <div className="p-4 md:p-6 max-w-4xl mx-auto">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-6 gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Daily Logs</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Field reports and daily activity tracking
          </p>
        </div>
        <div className="flex gap-2 flex-wrap">
          <button
            onClick={handleExport}
            className="flex items-center gap-2 px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700"
          >
            <Download className="h-4 w-4" />
            Export
          </button>
          <button
            onClick={handleCopyPrevious}
            disabled={creating}
            className="flex items-center gap-2 px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
          >
            <Copy className="h-4 w-4" />
            Copy Previous
          </button>
          <button
            onClick={handleCreateToday}
            disabled={creating}
            className="flex items-center gap-2 px-3 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            <Plus className="h-4 w-4" />
            New Log
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex gap-3 mb-4">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm dark:bg-gray-700 dark:text-gray-200"
        >
          <option value="">All Statuses</option>
          <option value="draft">Draft</option>
          <option value="submitted">Submitted</option>
          <option value="approved">Approved</option>
        </select>
      </div>

      {/* Loading / Error */}
      {isLoading && (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-16 bg-gray-200 dark:bg-gray-700 rounded animate-pulse" />
          ))}
        </div>
      )}
      {error && <div className="p-4 text-red-800 bg-red-50 rounded">Failed to load daily logs</div>}
      {!isLoading && logs.length === 0 && (
        <div className="text-center text-gray-500 dark:text-gray-400 py-12">
          No daily logs found. Create one to get started.
        </div>
      )}

      {/* Card List — mobile-friendly */}
      <div className="space-y-3">
        {logs.map((log) => {
          const StatusIcon = statusIcons[log.status] || Clock;
          const totalManpower = (log.manpower_by_trade || []).reduce(
            (sum, m) => sum + m.headcount,
            0,
          );
          return (
            <div
              key={log.id}
              onClick={() => router.push(`/daily-logs/${log.id}`)}
              className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 hover:shadow-md cursor-pointer transition-shadow"
            >
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <h3 className="text-base font-semibold text-gray-900 dark:text-white">
                      {new Date(log.log_date).toLocaleDateString("en-US", {
                        weekday: "short",
                        month: "short",
                        day: "numeric",
                        year: "numeric",
                      })}
                    </h3>
                    <span
                      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${
                        statusColors[log.status] ?? "bg-gray-100 text-gray-800"
                      }`}
                    >
                      <StatusIcon className="h-3 w-3" />
                      {log.status}
                    </span>
                  </div>
                  {log.work_narrative && (
                    <p className="text-sm text-gray-600 dark:text-gray-300 line-clamp-2 mt-1">
                      {log.work_narrative}
                    </p>
                  )}
                </div>
                <div className="ml-3 flex-shrink-0">{getWeatherIcon(log.weather)}</div>
              </div>

              <div className="flex items-center gap-4 mt-3 text-sm text-gray-500 dark:text-gray-400">
                <div className="flex items-center gap-1">
                  <Users className="h-4 w-4" />
                  <span>{totalManpower || log.crew_count} crew</span>
                </div>
                <div className="flex items-center gap-1">
                  <Clock className="h-4 w-4" />
                  <span>{log.work_hours}h</span>
                </div>
                {log.weather?.temperature_high != null && (
                  <div className="flex items-center gap-1">
                    <Cloud className="h-4 w-4" />
                    <span>{String(log.weather.temperature_high)}°</span>
                  </div>
                )}
              </div>

              {/* Manpower by trade chips */}
              {log.manpower_by_trade.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-2">
                  {log.manpower_by_trade.slice(0, 4).map((m, i) => (
                    <span
                      key={i}
                      className="inline-flex items-center px-2 py-0.5 rounded bg-blue-50 text-blue-700 text-xs"
                    >
                      {m.trade}: {m.headcount}
                    </span>
                  ))}
                  {log.manpower_by_trade.length > 4 && (
                    <span className="text-xs text-gray-400 dark:text-gray-500">
                      +{log.manpower_by_trade.length - 4} more
                    </span>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
