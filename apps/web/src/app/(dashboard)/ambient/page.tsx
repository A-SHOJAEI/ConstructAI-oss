"use client";

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { Users, Truck, MapPin, ScanLine, FileText } from "lucide-react";
import { toast } from "sonner";

interface DailySnapshot {
  date: string;
  total_headcount: number;
  equipment_count: number;
  active_zones: number;
  weather_summary: string;
  safety_incidents: number;
}

interface BadgeEvent {
  id: string;
  worker_name: string;
  trade: string;
  event_type: "check_in" | "check_out" | "zone_enter" | "zone_exit";
  zone: string;
  timestamp: string;
}

interface AmbientData {
  today_snapshot: DailySnapshot | null;
  recent_snapshots: DailySnapshot[];
  badge_events: BadgeEvent[];
}

const isValidUUID = (id: string) =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);

const eventTypeColors: Record<string, string> = {
  check_in: "bg-green-100 text-green-800",
  check_out: "bg-gray-100 text-gray-800",
  zone_enter: "bg-blue-100 text-blue-800",
  zone_exit: "bg-yellow-100 text-yellow-800",
};

export default function AmbientPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const [selectedDate, setSelectedDate] = useState(new Date().toISOString().split("T")[0]);

  const { data, isLoading, error } = useQuery<AmbientData>({
    queryKey: ["ambient", projectId, selectedDate],
    queryFn: () =>
      apiClient.get<AmbientData>(`/api/v1/projects/${projectId}/ambient?date=${selectedDate}`),
    enabled: !!projectId && isValidUUID(projectId),
  });

  const reportMutation = useMutation({
    mutationFn: () =>
      apiClient.post(`/api/v1/projects/${projectId}/ambient/generate-report`, {
        date: selectedDate,
      }),
    onSuccess: () => toast.success("Daily report generated"),
    onError: () => toast.error("Failed to generate report"),
  });

  if (!projectId) return <NoProjectSelected />;

  const snapshot = data?.today_snapshot;
  const events = data?.badge_events ?? [];

  return (
    <div className="p-4 md:p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Ambient Field</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Daily site snapshots, badge events, and automated field reports
          </p>
        </div>
        <div className="flex items-center gap-3">
          <input
            type="date"
            value={selectedDate}
            onChange={(e) => setSelectedDate(e.target.value)}
            className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm dark:bg-gray-700 dark:text-gray-200"
          />
          <button
            onClick={() => reportMutation.mutate()}
            disabled={reportMutation.isPending}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            <FileText className="h-4 w-4" />
            {reportMutation.isPending ? "Generating..." : "Generate Report"}
          </button>
        </div>
      </div>

      {/* Daily Snapshot Summary */}
      {snapshot && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-2">
              <Users className="h-5 w-5 text-blue-500" />
              <p className="text-sm text-gray-500 dark:text-gray-400">Headcount</p>
            </div>
            <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">
              {snapshot.total_headcount}
            </p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-2">
              <Truck className="h-5 w-5 text-indigo-500" />
              <p className="text-sm text-gray-500 dark:text-gray-400">Equipment</p>
            </div>
            <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">
              {snapshot.equipment_count}
            </p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-2">
              <MapPin className="h-5 w-5 text-green-500" />
              <p className="text-sm text-gray-500 dark:text-gray-400">Active Zones</p>
            </div>
            <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">
              {snapshot.active_zones}
            </p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <p className="text-sm text-gray-500 dark:text-gray-400">Weather</p>
            <p className="text-sm font-medium text-gray-900 dark:text-white mt-1">
              {snapshot.weather_summary}
            </p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <p className="text-sm text-gray-500 dark:text-gray-400">Safety Incidents</p>
            <p
              className={`text-3xl font-bold mt-1 ${snapshot.safety_incidents > 0 ? "text-red-600" : "text-green-600"}`}
            >
              {snapshot.safety_incidents}
            </p>
          </div>
        </div>
      )}

      {isLoading && (
        <div className="p-8 text-center text-gray-500 dark:text-gray-400">
          Loading ambient data...
        </div>
      )}
      {error && (
        <div className="p-4 text-red-800 bg-red-50 rounded-lg">Failed to load ambient data</div>
      )}
      {!isLoading && !snapshot && !error && (
        <div className="text-center py-12 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
          <ScanLine className="mx-auto h-12 w-12 text-gray-400" />
          <h3 className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">
            No snapshot for {selectedDate}
          </h3>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            No ambient data captured for this date.
          </p>
        </div>
      )}

      {/* Badge Event Timeline */}
      {!isLoading && !error && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
          <div className="flex items-center gap-2 mb-4">
            <ScanLine className="h-5 w-5 text-blue-500" />
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
              Badge Event Timeline
            </h2>
          </div>
          {events.length === 0 ? (
            <p className="text-sm text-gray-500 dark:text-gray-400 text-center py-4">
              No badge events for this date.
            </p>
          ) : (
            <div className="space-y-3 max-h-[500px] overflow-y-auto">
              {events.map((evt) => (
                <div
                  key={evt.id}
                  className="flex items-center gap-4 border-b border-gray-100 dark:border-gray-700 pb-3"
                >
                  <div className="w-16 text-xs text-gray-500 dark:text-gray-400">
                    {new Date(evt.timestamp).toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </div>
                  <span
                    className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${eventTypeColors[evt.event_type]}`}
                  >
                    {evt.event_type.replace("_", " ")}
                  </span>
                  <div className="flex-1">
                    <p className="text-sm font-medium text-gray-900 dark:text-white">
                      {evt.worker_name}
                    </p>
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {evt.trade} &middot; Zone: {evt.zone}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Recent Snapshots */}
      {!isLoading && !error && data?.recent_snapshots && data.recent_snapshots.length > 1 && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
              Recent Snapshots
            </h2>
          </div>
          <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
            <thead className="bg-gray-50 dark:bg-gray-900">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Date
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Headcount
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Equipment
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Zones
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Incidents
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {data.recent_snapshots.map((s) => (
                <tr
                  key={s.date}
                  className="hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer"
                  onClick={() => setSelectedDate(s.date)}
                >
                  <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white">
                    {s.date}
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                    {s.total_headcount}
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                    {s.equipment_count}
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                    {s.active_zones}
                  </td>
                  <td
                    className={`px-6 py-4 text-sm font-medium ${s.safety_incidents > 0 ? "text-red-600" : "text-green-600"}`}
                  >
                    {s.safety_incidents}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
