"use client";

import { useState, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { Upload, Layers, Calculator, ArrowRight } from "lucide-react";
import { toast } from "sonner";

interface TakeoffItem {
  id: string;
  description: string;
  csi_code: string;
  quantity: number;
  unit: string;
  unit_cost: number;
  total_cost: number;
  confidence: number;
}

interface Takeoff {
  id: string;
  drawing_name: string;
  status: "processing" | "complete" | "failed";
  confidence_avg: number;
  total_cost: number;
  item_count: number;
  created_at: string;
  items: TakeoffItem[];
}

interface TakeoffsData {
  takeoffs: Takeoff[];
  total: number;
}

const isValidUUID = (id: string) =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);

const statusColors: Record<string, string> = {
  processing: "bg-yellow-100 text-yellow-800",
  complete: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
};

function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
  }).format(value);
}

function getConfidenceColor(c: number): string {
  if (c >= 0.9) return "text-green-600";
  if (c >= 0.7) return "text-yellow-600";
  return "text-red-600";
}

export default function TakeoffsPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const queryClient = useQueryClient();
  const [selectedTakeoffId, setSelectedTakeoffId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery<TakeoffsData>({
    queryKey: ["takeoffs", projectId],
    queryFn: () => apiClient.get<TakeoffsData>(`/api/v1/projects/${projectId}/takeoffs`),
    enabled: !!projectId && isValidUUID(projectId),
  });

  const uploadMutation = useMutation({
    mutationFn: (formData: FormData) => {
      if (!projectId || !isValidUUID(projectId))
        return Promise.reject(new Error("Invalid project ID"));
      return apiClient.upload<Takeoff>(`/api/v1/projects/${projectId}/takeoffs/upload`, formData);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["takeoffs", projectId] });
      toast.success("Drawing uploaded for takeoff analysis");
    },
    onError: () => toast.error("Failed to upload drawing"),
  });

  const convertMutation = useMutation({
    mutationFn: (takeoffId: string) =>
      apiClient.post(`/api/v1/projects/${projectId}/takeoffs/${takeoffId}/convert-to-estimate`),
    onSuccess: () => toast.success("Takeoff converted to estimate"),
    onError: () => toast.error("Failed to convert takeoff"),
  });

  const handleUpload = useCallback(() => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".pdf,.dwg,.png,.jpg,.jpeg,.tiff";
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

  const takeoffs = data?.takeoffs ?? [];
  const selectedTakeoff = takeoffs.find((t) => t.id === selectedTakeoffId);

  return (
    <div className="p-4 md:p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Plan Takeoffs</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            AI-powered quantity takeoff from drawings with CSI code mapping
          </p>
        </div>
        <button
          onClick={handleUpload}
          disabled={uploadMutation.isPending}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
        >
          <Upload className="h-4 w-4" />
          {uploadMutation.isPending ? "Uploading..." : "Upload Drawing"}
        </button>
      </div>

      {isLoading && (
        <div className="p-8 text-center text-gray-500 dark:text-gray-400">Loading takeoffs...</div>
      )}
      {error && (
        <div className="p-4 text-red-800 bg-red-50 rounded-lg">Failed to load takeoffs</div>
      )}

      {/* Takeoffs List */}
      {!isLoading && !error && takeoffs.length === 0 && (
        <div className="text-center py-12 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
          <Layers className="mx-auto h-12 w-12 text-gray-400" />
          <h3 className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">No takeoffs</h3>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Upload a drawing to start automated takeoff.
          </p>
        </div>
      )}

      {!isLoading && !error && takeoffs.length > 0 && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
            <thead className="bg-gray-50 dark:bg-gray-900">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Drawing
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Status
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Confidence
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Items
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Total Cost
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Created
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {takeoffs.map((t) => (
                <tr
                  key={t.id}
                  className={`hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer ${selectedTakeoffId === t.id ? "bg-blue-50 dark:bg-blue-900/20" : ""}`}
                  onClick={() => setSelectedTakeoffId(t.id === selectedTakeoffId ? null : t.id)}
                >
                  <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white">
                    {t.drawing_name}
                  </td>
                  <td className="px-6 py-4">
                    <span
                      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${statusColors[t.status]}`}
                    >
                      {t.status}
                    </span>
                  </td>
                  <td className="px-6 py-4">
                    <span className={`text-sm font-medium ${getConfidenceColor(t.confidence_avg)}`}>
                      {(t.confidence_avg * 100).toFixed(0)}%
                    </span>
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                    {t.item_count}
                  </td>
                  <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white">
                    {formatCurrency(t.total_cost)}
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                    {new Date(t.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-6 py-4" onClick={(e) => e.stopPropagation()}>
                    {t.status === "complete" && (
                      <button
                        onClick={() => convertMutation.mutate(t.id)}
                        disabled={convertMutation.isPending}
                        className="flex items-center gap-1 px-3 py-1 text-xs font-medium text-blue-600 border border-blue-300 rounded hover:bg-blue-50 disabled:opacity-50"
                      >
                        <Calculator className="h-3 w-3" />
                        <ArrowRight className="h-3 w-3" />
                        Estimate
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Line Items Detail */}
      {selectedTakeoff && selectedTakeoff.items && selectedTakeoff.items.length > 0 && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
              Line Items - {selectedTakeoff.drawing_name}
            </h2>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
              <thead className="bg-gray-50 dark:bg-gray-900">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    CSI Code
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Description
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Qty
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Unit
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Unit Cost
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Total
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Confidence
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                {selectedTakeoff.items.map((item) => (
                  <tr key={item.id} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                    <td className="px-6 py-3 text-sm font-mono text-gray-900 dark:text-white">
                      {item.csi_code}
                    </td>
                    <td className="px-6 py-3 text-sm text-gray-900 dark:text-white">
                      {item.description}
                    </td>
                    <td className="px-6 py-3 text-sm text-right text-gray-500 dark:text-gray-400">
                      {item.quantity.toLocaleString()}
                    </td>
                    <td className="px-6 py-3 text-sm text-gray-500 dark:text-gray-400">
                      {item.unit}
                    </td>
                    <td className="px-6 py-3 text-sm text-right text-gray-500 dark:text-gray-400">
                      {formatCurrency(item.unit_cost)}
                    </td>
                    <td className="px-6 py-3 text-sm text-right font-medium text-gray-900 dark:text-white">
                      {formatCurrency(item.total_cost)}
                    </td>
                    <td className="px-6 py-3 text-sm text-right">
                      <span className={`font-medium ${getConfidenceColor(item.confidence)}`}>
                        {(item.confidence * 100).toFixed(0)}%
                      </span>
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
