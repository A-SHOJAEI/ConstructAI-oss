"use client";

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { Plane, Image as ImageIcon, Mountain, Calculator } from "lucide-react";
import { toast } from "sonner";

interface FlightLog {
  id: string;
  flight_name: string;
  drone_model: string;
  pilot: string;
  date: string;
  duration_minutes: number;
  area_covered_acres: number;
  altitude_ft: number;
  capture_count: number;
  status: "completed" | "processing" | "failed";
}

interface DroneCapture {
  id: string;
  flight_id: string;
  thumbnail_url: string;
  capture_type: "photo" | "orthomosaic" | "point_cloud";
  timestamp: string;
}

interface EarthworkResult {
  id: string;
  flight_id: string;
  cut_volume_cy: number;
  fill_volume_cy: number;
  net_volume_cy: number;
  area_sf: number;
  calculated_at: string;
}

interface DronesData {
  flights: FlightLog[];
  recent_captures: DroneCapture[];
  earthwork_results: EarthworkResult[];
}

const isValidUUID = (id: string) =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);

/**
 * Validate that a URL is safe for use in img src attributes.
 * Only allows relative URLs (starting with /) or HTTPS URLs.
 */
const isSafeImageUrl = (url: string): boolean => {
  if (url.startsWith("/")) return true;
  if (url.startsWith("https://")) return true;
  return false;
};

const flightStatusColors: Record<string, string> = {
  completed: "bg-green-100 text-green-800",
  processing: "bg-yellow-100 text-yellow-800",
  failed: "bg-red-100 text-red-800",
};

export default function DronesPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const [selectedFlightId, setSelectedFlightId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery<DronesData>({
    queryKey: ["drones", projectId],
    queryFn: () => apiClient.get<DronesData>(`/api/v1/projects/${projectId}/drones`),
    enabled: !!projectId && isValidUUID(projectId),
  });

  const volumeMutation = useMutation({
    mutationFn: (flightId: string) =>
      apiClient.post(`/api/v1/projects/${projectId}/drones/${flightId}/calculate-volume`),
    onSuccess: () => toast.success("Volume calculation started"),
    onError: () => toast.error("Failed to start volume calculation"),
  });

  if (!projectId) return <NoProjectSelected />;

  const flights = data?.flights ?? [];
  const captures = data?.recent_captures ?? [];
  const earthwork = data?.earthwork_results ?? [];
  const selectedCaptures = selectedFlightId
    ? captures.filter((c) => c.flight_id === selectedFlightId)
    : captures;

  return (
    <div className="p-4 md:p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Drone Data</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Flight logs, aerial captures, and earthwork volume calculations
        </p>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <Plane className="h-5 w-5 text-blue-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Total Flights</p>
          </div>
          <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">
            {isLoading ? "..." : flights.length}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <ImageIcon className="h-5 w-5 text-green-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Total Captures</p>
          </div>
          <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">
            {isLoading ? "..." : captures.length}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <Mountain className="h-5 w-5 text-orange-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Earthwork Calcs</p>
          </div>
          <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">
            {isLoading ? "..." : earthwork.length}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">Total Area Covered</p>
          <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">
            {isLoading
              ? "..."
              : `${flights.reduce((a, f) => a + f.area_covered_acres, 0).toFixed(1)} ac`}
          </p>
        </div>
      </div>

      {isLoading && (
        <div className="p-8 text-center text-gray-500 dark:text-gray-400">
          Loading drone data...
        </div>
      )}
      {error && (
        <div className="p-4 text-red-800 bg-red-50 rounded-lg">Failed to load drone data</div>
      )}

      {/* Flight Log Table */}
      {!isLoading && !error && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Flight Log</h2>
          </div>
          {flights.length === 0 ? (
            <div className="text-center py-12">
              <Plane className="mx-auto h-12 w-12 text-gray-400" />
              <h3 className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">
                No flights recorded
              </h3>
              <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                Flight data will appear here after drone missions.
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                <thead className="bg-gray-50 dark:bg-gray-900">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Flight
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Drone
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Pilot
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Date
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Duration
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Captures
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Status
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                  {flights.map((f) => (
                    <tr
                      key={f.id}
                      className={`hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer ${selectedFlightId === f.id ? "bg-blue-50 dark:bg-blue-900/20" : ""}`}
                      onClick={() => setSelectedFlightId(f.id === selectedFlightId ? null : f.id)}
                    >
                      <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white">
                        {f.flight_name}
                      </td>
                      <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                        {f.drone_model}
                      </td>
                      <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                        {f.pilot}
                      </td>
                      <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                        {new Date(f.date).toLocaleDateString()}
                      </td>
                      <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                        {f.duration_minutes} min
                      </td>
                      <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                        {f.capture_count}
                      </td>
                      <td className="px-6 py-4">
                        <span
                          className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${flightStatusColors[f.status]}`}
                        >
                          {f.status}
                        </span>
                      </td>
                      <td className="px-6 py-4" onClick={(e) => e.stopPropagation()}>
                        {f.status === "completed" && (
                          <button
                            onClick={() => volumeMutation.mutate(f.id)}
                            disabled={volumeMutation.isPending}
                            className="flex items-center gap-1 px-3 py-1 text-xs font-medium text-blue-600 border border-blue-300 rounded hover:bg-blue-50 disabled:opacity-50"
                          >
                            <Calculator className="h-3 w-3" /> Volume
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Capture Gallery */}
      {!isLoading && !error && selectedCaptures.length > 0 && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
            Captures{selectedFlightId ? " (filtered)" : ""}
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
            {selectedCaptures.map((c) => (
              <div
                key={c.id}
                className="relative rounded-lg overflow-hidden border border-gray-200 dark:border-gray-700"
              >
                <div className="aspect-square bg-gray-100 dark:bg-gray-700 flex items-center justify-center">
                  {c.thumbnail_url && isSafeImageUrl(c.thumbnail_url) ? (
                    <img
                      src={c.thumbnail_url}
                      alt={c.capture_type}
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    <ImageIcon className="h-8 w-8 text-gray-400" />
                  )}
                </div>
                <div className="p-2">
                  <span className="text-xs font-medium text-gray-600 dark:text-gray-400 capitalize">
                    {c.capture_type.replace("_", " ")}
                  </span>
                  <p className="text-xs text-gray-400">
                    {new Date(c.timestamp).toLocaleDateString()}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Earthwork Volume Results */}
      {!isLoading && !error && earthwork.length > 0 && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
              Earthwork Volume Results
            </h2>
          </div>
          <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
            <thead className="bg-gray-50 dark:bg-gray-900">
              <tr>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Cut (CY)
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Fill (CY)
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Net (CY)
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Area (SF)
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Calculated
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {earthwork.map((ew) => (
                <tr key={ew.id} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                  <td className="px-6 py-4 text-sm text-right text-red-600 font-medium">
                    {ew.cut_volume_cy.toLocaleString()}
                  </td>
                  <td className="px-6 py-4 text-sm text-right text-green-600 font-medium">
                    {ew.fill_volume_cy.toLocaleString()}
                  </td>
                  <td className="px-6 py-4 text-sm text-right font-bold text-gray-900 dark:text-white">
                    {ew.net_volume_cy.toLocaleString()}
                  </td>
                  <td className="px-6 py-4 text-sm text-right text-gray-500 dark:text-gray-400">
                    {ew.area_sf.toLocaleString()}
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                    {new Date(ew.calculated_at).toLocaleString()}
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
