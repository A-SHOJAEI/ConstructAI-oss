"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { Users, Clock, AlertTriangle, TrendingUp, Zap, UserX } from "lucide-react";

interface TradeHours {
  trade: string;
  headcount: number;
  hours_this_week: number;
  overtime_hours: number;
  productivity_index: number;
}

interface FatigueAlert {
  id: string;
  worker_name: string;
  trade: string;
  consecutive_days: number;
  avg_hours_per_day: number;
  risk_level: "low" | "medium" | "high";
}

interface AvailabilityGap {
  trade: string;
  needed: number;
  available: number;
  gap: number;
  start_date: string;
  end_date: string;
}

interface WorkforceData {
  total_headcount: number;
  total_hours_this_week: number;
  total_overtime_hours: number;
  avg_productivity_index: number;
  trades: TradeHours[];
  fatigue_alerts: FatigueAlert[];
  availability_gaps: AvailabilityGap[];
}

const isValidUUID = (id: string) =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);

const riskColors: Record<string, string> = {
  low: "bg-green-100 text-green-800",
  medium: "bg-yellow-100 text-yellow-800",
  high: "bg-red-100 text-red-800",
};

export default function WorkforcePage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);

  const { data, isLoading, error } = useQuery<WorkforceData>({
    queryKey: ["workforce", projectId],
    queryFn: () => apiClient.get<WorkforceData>(`/api/v1/projects/${projectId}/workforce`),
    enabled: !!projectId && isValidUUID(projectId),
  });

  if (!projectId) return <NoProjectSelected />;

  const maxHours = Math.max(...(data?.trades?.map((t) => t.hours_this_week) ?? [1]));

  return (
    <div className="p-4 md:p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Workforce</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Headcount, productivity, overtime alerts, and craft availability
        </p>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <Users className="h-5 w-5 text-blue-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Total Headcount</p>
          </div>
          <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">
            {isLoading ? "..." : (data?.total_headcount ?? 0)}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <Clock className="h-5 w-5 text-indigo-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Hours This Week</p>
          </div>
          <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">
            {isLoading ? "..." : (data?.total_hours_this_week?.toLocaleString() ?? 0)}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-orange-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Overtime Hours</p>
          </div>
          <p className="text-3xl font-bold text-orange-600 mt-1">
            {isLoading ? "..." : (data?.total_overtime_hours?.toLocaleString() ?? 0)}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <TrendingUp className="h-5 w-5 text-green-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Avg Productivity</p>
          </div>
          <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">
            {isLoading
              ? "..."
              : data
                ? `${(data.avg_productivity_index * 100).toFixed(0)}%`
                : "N/A"}
          </p>
        </div>
      </div>

      {isLoading && (
        <div className="p-8 text-center text-gray-500 dark:text-gray-400">
          Loading workforce data...
        </div>
      )}
      {error && (
        <div className="p-4 text-red-800 bg-red-50 rounded-lg">Failed to load workforce data</div>
      )}

      {!isLoading && !error && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Hours by Trade Chart */}
          <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
              Hours by Trade
            </h2>
            {!data?.trades || data.trades.length === 0 ? (
              <p className="text-sm text-gray-500 dark:text-gray-400 text-center py-4">
                No trade data available.
              </p>
            ) : (
              <div className="space-y-3">
                {data.trades.map((trade) => (
                  <div key={trade.trade} className="flex items-center gap-3">
                    <div className="w-28 text-sm text-gray-700 dark:text-gray-300 truncate">
                      {trade.trade}
                    </div>
                    <div className="flex-1 bg-gray-100 dark:bg-gray-700 rounded-full h-6 overflow-hidden relative">
                      <div
                        className="bg-blue-500 h-6 rounded-full"
                        style={{ width: `${(trade.hours_this_week / maxHours) * 100}%` }}
                      />
                      {trade.overtime_hours > 0 && (
                        <div
                          className="bg-orange-400 h-6 rounded-r-full absolute top-0"
                          style={{
                            left: `${((trade.hours_this_week - trade.overtime_hours) / maxHours) * 100}%`,
                            width: `${(trade.overtime_hours / maxHours) * 100}%`,
                          }}
                        />
                      )}
                    </div>
                    <div className="w-20 text-sm text-right text-gray-900 dark:text-white font-medium">
                      {trade.hours_this_week}h
                    </div>
                    <div className="w-12 text-xs text-right text-gray-500 dark:text-gray-400">
                      {trade.headcount} ppl
                    </div>
                  </div>
                ))}
                <div className="flex gap-4 mt-2 text-xs text-gray-500 dark:text-gray-400">
                  <span className="flex items-center gap-1">
                    <span className="w-3 h-3 bg-blue-500 rounded-sm inline-block" /> Regular
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="w-3 h-3 bg-orange-400 rounded-sm inline-block" /> Overtime
                  </span>
                </div>
              </div>
            )}
          </div>

          {/* Productivity Metrics */}
          <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
              Productivity by Trade
            </h2>
            {!data?.trades || data.trades.length === 0 ? (
              <p className="text-sm text-gray-500 dark:text-gray-400 text-center py-4">
                No productivity data.
              </p>
            ) : (
              <div className="space-y-3">
                {data.trades.map((trade) => {
                  const pct = trade.productivity_index * 100;
                  const color =
                    pct >= 95 ? "text-green-600" : pct >= 80 ? "text-yellow-600" : "text-red-600";
                  const barColor =
                    pct >= 95 ? "bg-green-500" : pct >= 80 ? "bg-yellow-500" : "bg-red-500";
                  return (
                    <div key={trade.trade} className="flex items-center gap-3">
                      <div className="w-28 text-sm text-gray-700 dark:text-gray-300 truncate">
                        {trade.trade}
                      </div>
                      <div className="flex-1 bg-gray-100 dark:bg-gray-700 rounded-full h-4 overflow-hidden">
                        <div
                          className={`${barColor} h-4 rounded-full`}
                          style={{ width: `${Math.min(pct, 100)}%` }}
                        />
                      </div>
                      <div className={`w-16 text-sm text-right font-medium ${color}`}>
                        {pct.toFixed(0)}%
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Fatigue Risk Alerts */}
          <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <div className="flex items-center gap-2 mb-4">
              <Zap className="h-5 w-5 text-orange-500" />
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
                Fatigue Risk Alerts
              </h2>
            </div>
            {!data?.fatigue_alerts || data.fatigue_alerts.length === 0 ? (
              <p className="text-sm text-gray-500 dark:text-gray-400 text-center py-4">
                No fatigue alerts.
              </p>
            ) : (
              <div className="space-y-3">
                {data.fatigue_alerts.map((alert) => (
                  <div
                    key={alert.id}
                    className="flex items-center justify-between border-b border-gray-100 dark:border-gray-700 pb-2"
                  >
                    <div>
                      <p className="text-sm font-medium text-gray-900 dark:text-white">
                        {alert.worker_name}
                      </p>
                      <p className="text-xs text-gray-500 dark:text-gray-400">
                        {alert.trade} &middot; {alert.consecutive_days} consecutive days &middot;
                        avg {alert.avg_hours_per_day.toFixed(1)}h/day
                      </p>
                    </div>
                    <span
                      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${riskColors[alert.risk_level]}`}
                    >
                      {alert.risk_level}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Craft Availability Gaps */}
          <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <div className="flex items-center gap-2 mb-4">
              <UserX className="h-5 w-5 text-red-500" />
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
                Craft Availability Gaps
              </h2>
            </div>
            {!data?.availability_gaps || data.availability_gaps.length === 0 ? (
              <p className="text-sm text-gray-500 dark:text-gray-400 text-center py-4">
                No availability gaps detected.
              </p>
            ) : (
              <div className="space-y-3">
                {data.availability_gaps.map((gap, idx) => (
                  <div
                    key={idx}
                    className="flex items-center justify-between border-b border-gray-100 dark:border-gray-700 pb-2"
                  >
                    <div>
                      <p className="text-sm font-medium text-gray-900 dark:text-white">
                        {gap.trade}
                      </p>
                      <p className="text-xs text-gray-500 dark:text-gray-400">
                        {new Date(gap.start_date).toLocaleDateString()} -{" "}
                        {new Date(gap.end_date).toLocaleDateString()}
                      </p>
                    </div>
                    <div className="text-right">
                      <p className="text-sm font-medium text-red-600">-{gap.gap} workers</p>
                      <p className="text-xs text-gray-500 dark:text-gray-400">
                        {gap.available} / {gap.needed} available
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
