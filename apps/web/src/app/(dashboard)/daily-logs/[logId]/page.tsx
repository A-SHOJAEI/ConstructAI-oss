"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { apiClient } from "@/lib/api-client";
import {
  ArrowLeft,
  Cloud,
  Users,
  Truck,
  Camera,
  UserCheck,
  Send,
  CheckCircle2,
  RotateCcw,
  RefreshCw,
} from "lucide-react";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";

interface DailyLogDetail {
  id: string;
  project_id: string;
  log_date: string;
  status: string;
  weather: Record<string, unknown>;
  crew_count: number;
  work_hours: number;
  work_narrative: string | null;
  manpower_by_trade: { trade: string; headcount: number; hours: number }[];
  equipment_entries: { equipment_type: string; hours_used: number; notes?: string }[];
  deliveries: { description: string; supplier?: string; tracking_number?: string }[];
  visitors: { name: string; company?: string; purpose?: string }[];
  photos: { file_name?: string; caption?: string; gps_lat?: number; gps_lon?: number }[];
  activities_completed: Record<string, unknown>[];
  delays: Record<string, unknown>[];
  notes: string | null;
  location_lat: number | null;
  location_lon: number | null;
  approved_by: string | null;
  approved_at: string | null;
  submitted_at: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

const statusColors: Record<string, string> = {
  draft: "bg-gray-100 text-gray-800",
  submitted: "bg-yellow-100 text-yellow-800",
  approved: "bg-green-100 text-green-800",
};

export default function DailyLogDetailPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const params = useParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const logId = params.logId as string;

  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fetchingWeather, setFetchingWeather] = useState(false);

  const {
    data: log,
    isLoading,
    error: loadError,
  } = useQuery<DailyLogDetail>({
    queryKey: ["daily-log-detail", projectId, logId],
    queryFn: () =>
      apiClient.get<DailyLogDetail>(`/api/v1/projects/${projectId}/daily-logs/${logId}`),
    enabled: !!logId && !!projectId,
  });

  if (!projectId) return <NoProjectSelected />;

  const handleAction = async (action: "submit" | "approve" | "reject") => {
    setActionLoading(true);
    setError(null);
    try {
      await apiClient.post(`/api/v1/projects/${projectId}/daily-logs/${logId}/${action}`, {});
      queryClient.invalidateQueries({ queryKey: ["daily-log-detail", logId] });
      queryClient.invalidateQueries({ queryKey: ["daily-logs"] });
    } catch {
      setError(`Failed to ${action} daily log.`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleFetchWeather = async () => {
    if (!log?.location_lat || !log?.location_lon) return;
    setFetchingWeather(true);
    try {
      const weather = await apiClient.get(
        `/api/v1/projects/${projectId}/daily-logs/weather?log_date=${log.log_date}&lat=${log.location_lat}&lon=${log.location_lon}`,
      );
      // Update the log with weather data
      await apiClient.patch(`/api/v1/projects/${projectId}/daily-logs/${logId}`, { weather });
      queryClient.invalidateQueries({ queryKey: ["daily-log-detail", logId] });
    } catch {
      setError("Failed to fetch weather data.");
    } finally {
      setFetchingWeather(false);
    }
  };

  if (isLoading) {
    return (
      <div className="p-6">
        <div className="text-center text-gray-500 py-12">Loading daily log...</div>
      </div>
    );
  }

  if (loadError || !log) {
    return (
      <div className="p-6">
        <div className="p-4 text-red-800 bg-red-50 rounded">Failed to load daily log</div>
      </div>
    );
  }

  const totalManpower = (log.manpower_by_trade || []).reduce((sum, m) => sum + m.headcount, 0);

  return (
    <div className="p-4 md:p-6 max-w-4xl mx-auto">
      {/* Back + Header */}
      <button
        onClick={() => router.back()}
        className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-4"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to Daily Logs
      </button>

      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between mb-6 gap-3">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-gray-900">
              {new Date(log.log_date).toLocaleDateString("en-US", {
                weekday: "long",
                month: "long",
                day: "numeric",
                year: "numeric",
              })}
            </h1>
            <span
              className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                statusColors[log.status] ?? "bg-gray-100 text-gray-800"
              }`}
            >
              {log.status}
            </span>
          </div>
        </div>
        <div className="flex gap-2 flex-wrap">
          {log.status === "draft" && (
            <button
              onClick={() => handleAction("submit")}
              disabled={actionLoading}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
            >
              <Send className="h-4 w-4" />
              {actionLoading ? "Submitting..." : "Submit"}
            </button>
          )}
          {log.status === "submitted" && (
            <>
              <button
                onClick={() => handleAction("approve")}
                disabled={actionLoading}
                className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50"
              >
                <CheckCircle2 className="h-4 w-4" />
                Approve
              </button>
              <button
                onClick={() => handleAction("reject")}
                disabled={actionLoading}
                className="flex items-center gap-2 px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              >
                <RotateCcw className="h-4 w-4" />
                Return to Draft
              </button>
            </>
          )}
        </div>
      </div>

      {error && <div className="mb-4 p-3 text-sm text-red-800 bg-red-50 rounded">{error}</div>}

      {/* Weather Card */}
      <div className="bg-white rounded-lg border border-gray-200 p-4 mb-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-medium text-gray-500 uppercase flex items-center gap-2">
            <Cloud className="h-4 w-4" />
            Weather
          </h3>
          {log.status === "draft" && log.location_lat && (
            <button
              onClick={handleFetchWeather}
              disabled={fetchingWeather}
              className="flex items-center gap-1 px-3 py-1 text-xs font-medium text-blue-600 border border-blue-200 rounded hover:bg-blue-50 disabled:opacity-50"
            >
              <RefreshCw className={`h-3 w-3 ${fetchingWeather ? "animate-spin" : ""}`} />
              Auto-fill Weather
            </button>
          )}
        </div>
        {Object.keys(log.weather || {}).length > 0 ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {log.weather.temperature_high != null && (
              <div className="text-center p-2 bg-orange-50 rounded">
                <p className="text-lg font-bold text-orange-600">
                  {String(log.weather.temperature_high)}°
                </p>
                <p className="text-xs text-gray-500">High</p>
              </div>
            )}
            {log.weather.temperature_low != null && (
              <div className="text-center p-2 bg-blue-50 rounded">
                <p className="text-lg font-bold text-blue-600">
                  {String(log.weather.temperature_low)}°
                </p>
                <p className="text-xs text-gray-500">Low</p>
              </div>
            )}
            {log.weather.precipitation_mm != null && (
              <div className="text-center p-2 bg-cyan-50 rounded">
                <p className="text-lg font-bold text-cyan-600">
                  {String(log.weather.precipitation_mm)}mm
                </p>
                <p className="text-xs text-gray-500">Precip</p>
              </div>
            )}
            {log.weather.wind_speed_max != null && (
              <div className="text-center p-2 bg-gray-50 rounded">
                <p className="text-lg font-bold text-gray-600">
                  {String(log.weather.wind_speed_max)}mph
                </p>
                <p className="text-xs text-gray-500">Wind</p>
              </div>
            )}
            {log.weather.conditions ? (
              <div className="col-span-2 md:col-span-4 text-sm text-gray-600">
                Conditions: {String(log.weather.conditions)}
              </div>
            ) : null}
          </div>
        ) : (
          <p className="text-sm text-gray-400">No weather data yet.</p>
        )}
      </div>

      {/* Manpower Table */}
      <div className="bg-white rounded-lg border border-gray-200 p-4 mb-4">
        <h3 className="text-sm font-medium text-gray-500 uppercase mb-3 flex items-center gap-2">
          <Users className="h-4 w-4" />
          Manpower ({totalManpower} crew, {log.work_hours}h total)
        </h3>
        {log.manpower_by_trade.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100">
                  <th className="text-left py-2 text-gray-500 font-medium">Trade</th>
                  <th className="text-right py-2 text-gray-500 font-medium">Headcount</th>
                  <th className="text-right py-2 text-gray-500 font-medium">Hours</th>
                </tr>
              </thead>
              <tbody>
                {log.manpower_by_trade.map((m, i) => (
                  <tr key={i} className="border-b border-gray-50">
                    <td className="py-2 text-gray-900 capitalize">{m.trade}</td>
                    <td className="py-2 text-right text-gray-900">{m.headcount}</td>
                    <td className="py-2 text-right text-gray-900">{m.hours}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-gray-400">No manpower entries.</p>
        )}
      </div>

      {/* Work Narrative */}
      {log.work_narrative && (
        <div className="bg-white rounded-lg border border-gray-200 p-4 mb-4">
          <h3 className="text-sm font-medium text-gray-500 uppercase mb-2">Work Narrative</h3>
          <p className="text-gray-900 whitespace-pre-wrap">{log.work_narrative}</p>
        </div>
      )}

      {/* Equipment / Deliveries / Visitors in 2-col grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        {/* Equipment */}
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <h3 className="text-sm font-medium text-gray-500 uppercase mb-2 flex items-center gap-2">
            <Truck className="h-4 w-4" />
            Equipment ({log.equipment_entries.length})
          </h3>
          {log.equipment_entries.length > 0 ? (
            <ul className="space-y-2">
              {log.equipment_entries.map((e, i) => (
                <li key={i} className="text-sm">
                  <span className="font-medium text-gray-900 capitalize">{e.equipment_type}</span>
                  <span className="text-gray-500"> — {e.hours_used}h</span>
                  {e.notes && <p className="text-xs text-gray-400">{e.notes}</p>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-gray-400">No equipment entries.</p>
          )}
        </div>

        {/* Deliveries */}
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <h3 className="text-sm font-medium text-gray-500 uppercase mb-2 flex items-center gap-2">
            <Truck className="h-4 w-4" />
            Deliveries ({log.deliveries.length})
          </h3>
          {log.deliveries.length > 0 ? (
            <ul className="space-y-2">
              {log.deliveries.map((d, i) => (
                <li key={i} className="text-sm">
                  <span className="font-medium text-gray-900">{d.description}</span>
                  {d.supplier && <span className="text-gray-500"> — {d.supplier}</span>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-gray-400">No deliveries.</p>
          )}
        </div>
      </div>

      {/* Visitors */}
      {log.visitors.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-4 mb-4">
          <h3 className="text-sm font-medium text-gray-500 uppercase mb-2 flex items-center gap-2">
            <UserCheck className="h-4 w-4" />
            Visitors ({log.visitors.length})
          </h3>
          <ul className="space-y-1">
            {log.visitors.map((v, i) => (
              <li key={i} className="text-sm">
                <span className="font-medium text-gray-900">{v.name}</span>
                {v.company && <span className="text-gray-500"> ({v.company})</span>}
                {v.purpose && <span className="text-gray-400"> — {v.purpose}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Photos */}
      {log.photos.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-4 mb-4">
          <h3 className="text-sm font-medium text-gray-500 uppercase mb-2 flex items-center gap-2">
            <Camera className="h-4 w-4" />
            Photos ({log.photos.length})
          </h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            {log.photos.map((p, i) => (
              <div
                key={i}
                className="bg-gray-100 rounded aspect-square flex items-center justify-center"
              >
                <div className="text-center">
                  <Camera className="h-6 w-6 text-gray-400 mx-auto" />
                  <p className="text-xs text-gray-500 mt-1">
                    {p.caption || p.file_name || `Photo ${i + 1}`}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Delays */}
      {log.delays.length > 0 && (
        <div className="bg-white rounded-lg border border-orange-200 p-4 mb-4">
          <h3 className="text-sm font-medium text-orange-600 uppercase mb-2">
            Delays ({log.delays.length})
          </h3>
          <ul className="space-y-1">
            {log.delays.map((d, i) => (
              <li key={i} className="text-sm text-gray-900">
                {String((d as Record<string, unknown>).description || JSON.stringify(d))}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Notes */}
      {log.notes && (
        <div className="bg-white rounded-lg border border-gray-200 p-4 mb-4">
          <h3 className="text-sm font-medium text-gray-500 uppercase mb-2">Notes</h3>
          <p className="text-gray-900 whitespace-pre-wrap">{log.notes}</p>
        </div>
      )}

      {/* Approval info */}
      {log.approved_at && (
        <div className="text-xs text-gray-400 mt-4">
          Approved {new Date(log.approved_at).toLocaleString()}
          {log.submitted_at && <> | Submitted {new Date(log.submitted_at).toLocaleString()}</>}
        </div>
      )}
    </div>
  );
}
