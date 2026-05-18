"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { Smartphone, RefreshCw, Camera, AlertTriangle, CheckCircle2 } from "lucide-react";
import { toast } from "sonner";

interface SyncDevice {
  id: string;
  device_name: string;
  device_type: string;
  os_version: string;
  app_version: string;
  last_sync: string | null;
  sync_status: "synced" | "pending" | "error";
  pending_uploads: number;
  pending_photos: number;
}

interface ConflictEntry {
  id: string;
  device_id: string;
  device_name: string;
  entity_type: string;
  entity_id: string;
  field_name: string;
  local_value: string;
  server_value: string;
  detected_at: string;
  resolved: boolean;
}

interface SyncData {
  devices: SyncDevice[];
  conflicts: ConflictEntry[];
  total_pending_photos: number;
  last_full_sync: string | null;
}

const isValidUUID = (id: string) =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);

const syncStatusColors: Record<string, string> = {
  synced: "bg-green-100 text-green-800",
  pending: "bg-yellow-100 text-yellow-800",
  error: "bg-red-100 text-red-800",
};

export default function SyncPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const queryClient = useQueryClient();

  const { data, isLoading, error } = useQuery<SyncData>({
    queryKey: ["sync", projectId],
    queryFn: () => apiClient.get<SyncData>(`/api/v1/projects/${projectId}/sync`),
    enabled: !!projectId && isValidUUID(projectId),
  });

  const resolveConflictMutation = useMutation({
    mutationFn: ({
      conflictId,
      resolution,
    }: {
      conflictId: string;
      resolution: "local" | "server";
    }) =>
      apiClient.post(`/api/v1/projects/${projectId}/sync/conflicts/${conflictId}/resolve`, {
        resolution,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sync", projectId] });
      toast.success("Conflict resolved");
    },
    onError: () => toast.error("Failed to resolve conflict"),
  });

  const forceSyncMutation = useMutation({
    mutationFn: (deviceId: string) =>
      apiClient.post(`/api/v1/projects/${projectId}/sync/devices/${deviceId}/force-sync`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sync", projectId] });
      toast.success("Sync initiated");
    },
    onError: () => toast.error("Failed to initiate sync"),
  });

  if (!projectId) return <NoProjectSelected />;

  const devices = data?.devices ?? [];
  const conflicts = data?.conflicts?.filter((c) => !c.resolved) ?? [];

  return (
    <div className="p-4 md:p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Offline Sync</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Device management, pending uploads, and conflict resolution
        </p>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <Smartphone className="h-5 w-5 text-blue-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Devices</p>
          </div>
          <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">
            {isLoading ? "..." : devices.length}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <Camera className="h-5 w-5 text-green-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Pending Photos</p>
          </div>
          <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">
            {isLoading ? "..." : (data?.total_pending_photos ?? 0)}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-orange-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Conflicts</p>
          </div>
          <p
            className={`text-3xl font-bold mt-1 ${conflicts.length > 0 ? "text-orange-600" : "text-green-600"}`}
          >
            {isLoading ? "..." : conflicts.length}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">Last Full Sync</p>
          <p className="text-sm font-medium text-gray-900 dark:text-white mt-1">
            {isLoading
              ? "..."
              : data?.last_full_sync
                ? new Date(data.last_full_sync).toLocaleString()
                : "Never"}
          </p>
        </div>
      </div>

      {isLoading && (
        <div className="p-8 text-center text-gray-500 dark:text-gray-400">Loading sync data...</div>
      )}
      {error && (
        <div className="p-4 text-red-800 bg-red-50 rounded-lg">Failed to load sync data</div>
      )}

      {/* Device List */}
      {!isLoading && !error && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Devices</h2>
          </div>
          {devices.length === 0 ? (
            <div className="text-center py-12">
              <Smartphone className="mx-auto h-12 w-12 text-gray-400" />
              <h3 className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">
                No devices registered
              </h3>
              <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                Devices will appear when the mobile app connects.
              </p>
            </div>
          ) : (
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
              <thead className="bg-gray-50 dark:bg-gray-900">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Device
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Type
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    App Version
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Last Sync
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Status
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Pending
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                {devices.map((d) => (
                  <tr key={d.id} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                    <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white">
                      {d.device_name}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                      {d.device_type}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                      {d.app_version}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                      {d.last_sync ? new Date(d.last_sync).toLocaleString() : "Never"}
                    </td>
                    <td className="px-6 py-4">
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${syncStatusColors[d.sync_status]}`}
                      >
                        {d.sync_status}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-sm text-right text-gray-500 dark:text-gray-400">
                      {d.pending_uploads} items, {d.pending_photos} photos
                    </td>
                    <td className="px-6 py-4">
                      <button
                        onClick={() => forceSyncMutation.mutate(d.id)}
                        disabled={forceSyncMutation.isPending}
                        className="flex items-center gap-1 px-3 py-1 text-xs font-medium text-blue-600 border border-blue-300 rounded hover:bg-blue-50 disabled:opacity-50"
                      >
                        <RefreshCw className="h-3 w-3" /> Sync
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Conflict Log */}
      {!isLoading && !error && conflicts.length > 0 && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
              Unresolved Conflicts
            </h2>
          </div>
          <div className="divide-y divide-gray-200 dark:divide-gray-700">
            {conflicts.map((c) => (
              <div key={c.id} className="px-6 py-4">
                <div className="flex items-start justify-between">
                  <div>
                    <p className="text-sm font-medium text-gray-900 dark:text-white">
                      {c.entity_type} - {c.field_name}
                    </p>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                      Device: {c.device_name} &middot; Detected:{" "}
                      {new Date(c.detected_at).toLocaleString()}
                    </p>
                    <div className="mt-2 grid grid-cols-2 gap-4 text-sm">
                      <div className="p-2 bg-blue-50 dark:bg-blue-900/20 rounded">
                        <p className="text-xs text-blue-600 font-medium mb-1">Local (Device)</p>
                        <p className="text-gray-900 dark:text-white">{c.local_value}</p>
                      </div>
                      <div className="p-2 bg-green-50 dark:bg-green-900/20 rounded">
                        <p className="text-xs text-green-600 font-medium mb-1">Server</p>
                        <p className="text-gray-900 dark:text-white">{c.server_value}</p>
                      </div>
                    </div>
                  </div>
                  <div className="flex gap-2 ml-4 flex-shrink-0">
                    <button
                      onClick={() =>
                        resolveConflictMutation.mutate({ conflictId: c.id, resolution: "local" })
                      }
                      disabled={resolveConflictMutation.isPending}
                      className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-blue-600 border border-blue-300 rounded hover:bg-blue-50 disabled:opacity-50"
                      title="Keep device value"
                    >
                      <Smartphone className="h-3 w-3" /> Keep Local
                    </button>
                    <button
                      onClick={() =>
                        resolveConflictMutation.mutate({ conflictId: c.id, resolution: "server" })
                      }
                      disabled={resolveConflictMutation.isPending}
                      className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-green-600 border border-green-300 rounded hover:bg-green-50 disabled:opacity-50"
                      title="Keep server value"
                    >
                      <CheckCircle2 className="h-3 w-3" /> Keep Server
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {!isLoading && !error && conflicts.length === 0 && devices.length > 0 && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6 text-center">
          <CheckCircle2 className="mx-auto h-12 w-12 text-green-400" />
          <p className="mt-2 text-sm text-green-600 font-medium">No unresolved conflicts</p>
        </div>
      )}
    </div>
  );
}
