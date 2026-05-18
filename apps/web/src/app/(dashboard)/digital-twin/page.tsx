"use client";

import { useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { Box, Upload, Activity, AlertTriangle } from "lucide-react";
import { toast } from "sonner";

interface DigitalTwin {
  id: string;
  name: string;
  ifc_file: string | null;
  status: "active" | "syncing" | "error";
  last_sync: string | null;
  sensor_count: number;
  anomaly_count: number;
  created_at: string;
}

interface SensorReading {
  id: string;
  sensor_name: string;
  sensor_type: string;
  value: number;
  unit: string;
  is_anomaly: boolean;
  anomaly_description: string | null;
  timestamp: string;
}

interface DigitalTwinData {
  twins: DigitalTwin[];
  recent_readings: SensorReading[];
}

const isValidUUID = (id: string) =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);

const twinStatusColors: Record<string, string> = {
  active: "bg-green-100 text-green-800",
  syncing: "bg-yellow-100 text-yellow-800",
  error: "bg-red-100 text-red-800",
};

export default function DigitalTwinPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const queryClient = useQueryClient();

  const { data, isLoading, error } = useQuery<DigitalTwinData>({
    queryKey: ["digital-twin", projectId],
    queryFn: () => apiClient.get<DigitalTwinData>(`/api/v1/projects/${projectId}/digital-twin`),
    enabled: !!projectId && isValidUUID(projectId),
  });

  const uploadMutation = useMutation({
    mutationFn: (formData: FormData) => {
      if (!projectId || !isValidUUID(projectId))
        return Promise.reject(new Error("Invalid project ID"));
      return apiClient.upload<DigitalTwin>(
        `/api/v1/projects/${projectId}/digital-twin/upload-ifc`,
        formData,
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["digital-twin", projectId] });
      toast.success("IFC file uploaded");
    },
    onError: () => toast.error("Failed to upload IFC file"),
  });

  const handleUploadIFC = useCallback(() => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".ifc";
    input.onchange = () => {
      if (input.files?.[0]) {
        const formData = new FormData();
        formData.append("file", input.files[0]);
        uploadMutation.mutate(formData);
      }
    };
    input.click();
  }, [uploadMutation]);

  if (!projectId) return <NoProjectSelected />;

  const twins = data?.twins ?? [];
  const readings = data?.recent_readings ?? [];
  const anomalyCount = readings.filter((r) => r.is_anomaly).length;

  return (
    <div className="p-4 md:p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Digital Twin</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            BIM models, sensor integration, and anomaly detection
          </p>
        </div>
        <button
          onClick={handleUploadIFC}
          disabled={uploadMutation.isPending}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
        >
          <Upload className="h-4 w-4" />
          {uploadMutation.isPending ? "Uploading..." : "Upload IFC"}
        </button>
      </div>

      {/* Summary */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <Box className="h-5 w-5 text-blue-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Twin Models</p>
          </div>
          <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">
            {isLoading ? "..." : twins.length}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <Activity className="h-5 w-5 text-green-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Sensor Readings</p>
          </div>
          <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">
            {isLoading ? "..." : readings.length}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-red-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Anomalies</p>
          </div>
          <p className="text-3xl font-bold text-red-600 mt-1">{isLoading ? "..." : anomalyCount}</p>
        </div>
      </div>

      {isLoading && (
        <div className="p-8 text-center text-gray-500 dark:text-gray-400">
          Loading digital twin data...
        </div>
      )}
      {error && (
        <div className="p-4 text-red-800 bg-red-50 rounded-lg">
          Failed to load digital twin data
        </div>
      )}

      {/* Twins List */}
      {!isLoading && !error && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Twin Models</h2>
          </div>
          {twins.length === 0 ? (
            <div className="text-center py-12">
              <Box className="mx-auto h-12 w-12 text-gray-400" />
              <h3 className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">
                No digital twins
              </h3>
              <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                Upload an IFC file to create a digital twin.
              </p>
            </div>
          ) : (
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
              <thead className="bg-gray-50 dark:bg-gray-900">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Name
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Status
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Sensors
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Anomalies
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Last Sync
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                {twins.map((twin) => (
                  <tr key={twin.id} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                    <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white">
                      {twin.name}
                    </td>
                    <td className="px-6 py-4">
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${twinStatusColors[twin.status]}`}
                      >
                        {twin.status}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                      {twin.sensor_count}
                    </td>
                    <td className="px-6 py-4">
                      <span
                        className={`text-sm font-medium ${twin.anomaly_count > 0 ? "text-red-600" : "text-green-600"}`}
                      >
                        {twin.anomaly_count}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                      {twin.last_sync ? new Date(twin.last_sync).toLocaleString() : "Never"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Sensor Readings */}
      {!isLoading && !error && readings.length > 0 && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
              Recent Sensor Readings
            </h2>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
              <thead className="bg-gray-50 dark:bg-gray-900">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Sensor
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Type
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Value
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Status
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Time
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                {readings.map((r) => (
                  <tr
                    key={r.id}
                    className={`hover:bg-gray-50 dark:hover:bg-gray-700 ${r.is_anomaly ? "bg-red-50 dark:bg-red-900/10" : ""}`}
                  >
                    <td className="px-6 py-3 text-sm font-medium text-gray-900 dark:text-white">
                      {r.sensor_name}
                    </td>
                    <td className="px-6 py-3 text-sm text-gray-500 dark:text-gray-400">
                      {r.sensor_type}
                    </td>
                    <td className="px-6 py-3 text-sm text-right font-mono text-gray-900 dark:text-white">
                      {r.value.toFixed(2)} {r.unit}
                    </td>
                    <td className="px-6 py-3">
                      {r.is_anomaly ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800">
                          <AlertTriangle className="h-3 w-3" /> Anomaly
                        </span>
                      ) : (
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
                          Normal
                        </span>
                      )}
                    </td>
                    <td className="px-6 py-3 text-sm text-gray-500 dark:text-gray-400">
                      {new Date(r.timestamp).toLocaleString()}
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
