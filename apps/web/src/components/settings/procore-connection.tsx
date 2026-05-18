"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { procoreApi } from "@/lib/procore-api";
import { Link2, Unlink, RefreshCw, CheckCircle, AlertCircle, Loader2 } from "lucide-react";

export function ProcoreConnection() {
  const queryClient = useQueryClient();

  const { data: status, isLoading } = useQuery({
    queryKey: ["procore-status"],
    queryFn: () => procoreApi.getStatus(),
    retry: 1,
  });

  const { data: syncStatus } = useQuery({
    queryKey: ["procore-sync-status"],
    queryFn: () => procoreApi.syncStatus(),
    enabled: !!status?.connected,
    refetchInterval: (query) => (query.state.data?.status === "in_progress" ? 3000 : false),
  });

  const syncMutation = useMutation({
    mutationFn: () => procoreApi.sync(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["procore-sync-status"] });
    },
  });

  const disconnectMutation = useMutation({
    mutationFn: () => procoreApi.disconnect(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["procore-status"] });
    },
  });

  if (isLoading) {
    return (
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <div className="animate-pulse space-y-3">
          <div className="h-5 bg-gray-200 rounded w-40" />
          <div className="h-4 bg-gray-100 rounded w-64" />
        </div>
      </div>
    );
  }

  const connected = status?.connected ?? false;
  const syncing = syncStatus?.status === "in_progress";

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-6">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="h-10 w-10 bg-orange-100 rounded-lg flex items-center justify-center">
            <Link2 className="h-5 w-5 text-orange-600" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-gray-900">Procore Integration</h3>
            <p className="text-xs text-gray-500">Sync projects, RFIs, and submittals</p>
          </div>
        </div>
        <span
          className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium ${
            connected ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-600"
          }`}
        >
          {connected ? (
            <>
              <CheckCircle className="h-3 w-3" /> Connected
            </>
          ) : (
            <>
              <AlertCircle className="h-3 w-3" /> Not Connected
            </>
          )}
        </span>
      </div>

      {!connected ? (
        <div className="bg-gray-50 rounded-lg p-4 text-center">
          <p className="text-sm text-gray-600 mb-3">
            Connect your Procore account to sync project data automatically.
          </p>
          <a
            href={(() => {
              const url = procoreApi.getConnectUrl();
              try {
                const expectedOrigin = new URL(
                  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
                ).origin;
                const parsedUrl = new URL(url);
                return parsedUrl.origin === expectedOrigin ? url : "#";
              } catch {
                return "#";
              }
            })()}
            className="inline-flex items-center gap-2 px-4 py-2 bg-orange-600 text-white rounded-lg text-sm font-medium hover:bg-orange-700 transition-colors"
          >
            <Link2 className="h-4 w-4" />
            Connect Procore
          </a>
        </div>
      ) : (
        <div className="space-y-4">
          {/* Connection Info */}
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <p className="text-gray-500">Company</p>
              <p className="font-medium text-gray-900">{status?.company_name ?? "N/A"}</p>
            </div>
            <div>
              <p className="text-gray-500">Connected</p>
              <p className="font-medium text-gray-900">
                {status?.connected_at ? new Date(status.connected_at).toLocaleDateString() : "N/A"}
              </p>
            </div>
          </div>

          {/* Sync Controls */}
          <div className="border-t border-gray-100 pt-4">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-700">Data Sync</p>
                {syncStatus?.completed_at && (
                  <p className="text-xs text-gray-500">
                    Last sync: {new Date(syncStatus.completed_at).toLocaleString()}
                    {syncStatus.records_synced > 0 && ` — ${syncStatus.records_synced} records`}
                  </p>
                )}
              </div>
              <button
                onClick={() => syncMutation.mutate()}
                disabled={syncing || syncMutation.isPending}
                className="flex items-center gap-2 px-3 py-1.5 bg-gray-900 text-white rounded-lg text-sm font-medium hover:bg-gray-800 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {syncing || syncMutation.isPending ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> Syncing...
                  </>
                ) : (
                  <>
                    <RefreshCw className="h-3.5 w-3.5" /> Sync Now
                  </>
                )}
              </button>
            </div>
          </div>

          {/* Disconnect */}
          <div className="border-t border-gray-100 pt-4">
            <button
              onClick={() => {
                if (confirm("Disconnect Procore? Synced data will be preserved.")) {
                  disconnectMutation.mutate();
                }
              }}
              className="flex items-center gap-2 text-sm text-red-600 hover:text-red-700"
            >
              <Unlink className="h-4 w-4" />
              Disconnect Procore
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
