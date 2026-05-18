"use client";

import { useState, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { Upload, FileText, AlertTriangle, CheckCircle2, Clock } from "lucide-react";
import { toast } from "sonner";

interface Contract {
  id: string;
  file_name: string;
  contract_type: string;
  status: "processing" | "parsed" | "failed";
  parties: string[];
  effective_date: string | null;
  expiration_date: string | null;
  total_value: number | null;
  created_at: string;
}

interface ClauseSummary {
  clause_type: string;
  description: string;
  risk_level: "low" | "medium" | "high";
  standard_deviation: string | null;
}

interface DeviationAlert {
  id: string;
  contract_id: string;
  clause_type: string;
  description: string;
  severity: "info" | "warning" | "critical";
  recommendation: string;
}

interface ContractsData {
  contracts: Contract[];
  total: number;
}

interface ContractDetail {
  contract: Contract;
  clauses: ClauseSummary[];
  deviations: DeviationAlert[];
}

const isValidUUID = (id: string) =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);

const statusColors: Record<string, string> = {
  processing: "bg-yellow-100 text-yellow-800",
  parsed: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
};

const riskColors: Record<string, string> = {
  low: "bg-green-100 text-green-800",
  medium: "bg-yellow-100 text-yellow-800",
  high: "bg-red-100 text-red-800",
};

const severityColors: Record<string, string> = {
  info: "bg-blue-100 text-blue-800",
  warning: "bg-yellow-100 text-yellow-800",
  critical: "bg-red-100 text-red-800",
};

function formatCurrency(value: number | null): string {
  if (value === null) return "N/A";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
  }).format(value);
}

export default function ContractsPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const queryClient = useQueryClient();
  const [selectedContractId, setSelectedContractId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery<ContractsData>({
    queryKey: ["contracts", projectId],
    queryFn: () => apiClient.get<ContractsData>(`/api/v1/projects/${projectId}/contracts`),
    enabled: !!projectId && isValidUUID(projectId),
  });

  const { data: detail } = useQuery<ContractDetail>({
    queryKey: ["contract-detail", projectId, selectedContractId],
    queryFn: () =>
      apiClient.get<ContractDetail>(
        `/api/v1/projects/${projectId}/contracts/${selectedContractId}`,
      ),
    enabled: !!projectId && isValidUUID(projectId) && !!selectedContractId,
  });

  const uploadMutation = useMutation({
    mutationFn: (formData: FormData) => {
      if (!projectId || !isValidUUID(projectId))
        return Promise.reject(new Error("Invalid project ID"));
      return apiClient.upload<Contract>(`/api/v1/projects/${projectId}/contracts/upload`, formData);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["contracts", projectId] });
      toast.success("Contract uploaded successfully");
    },
    onError: () => toast.error("Failed to upload contract"),
  });

  const handleUpload = useCallback(() => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".pdf,.docx";
    input.onchange = () => {
      if (input.files?.[0]) {
        const formData = new FormData();
        formData.append("file", input.files[0]);
        uploadMutation.mutate(formData);
      }
    };
    input.click();
  }, [uploadMutation]);

  const contracts = data?.contracts ?? [];

  if (!projectId) return <NoProjectSelected />;

  return (
    <div className="p-4 md:p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">
            Contract Intelligence
          </h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            AI-powered contract parsing, clause analysis, and deviation alerts
          </p>
        </div>
        <button
          onClick={handleUpload}
          disabled={uploadMutation.isPending}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
        >
          <Upload className="h-4 w-4" />
          {uploadMutation.isPending ? "Uploading..." : "Upload Contract"}
        </button>
      </div>

      {isLoading && (
        <div className="p-8 text-center text-gray-500 dark:text-gray-400">Loading contracts...</div>
      )}
      {error && (
        <div className="p-4 text-red-800 bg-red-50 rounded-lg">Failed to load contracts</div>
      )}

      {/* Contracts Table */}
      {!isLoading && !error && contracts.length === 0 && (
        <div className="text-center py-12 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
          <FileText className="mx-auto h-12 w-12 text-gray-400" />
          <h3 className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">No contracts</h3>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Upload a contract to get started with AI analysis.
          </p>
        </div>
      )}

      {!isLoading && !error && contracts.length > 0 && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
            <thead className="bg-gray-50 dark:bg-gray-900">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  File
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Type
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Parties
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Value
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Status
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                  Uploaded
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {contracts.map((c) => (
                <tr
                  key={c.id}
                  className={`hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer ${selectedContractId === c.id ? "bg-blue-50 dark:bg-blue-900/20" : ""}`}
                  onClick={() => setSelectedContractId(c.id === selectedContractId ? null : c.id)}
                >
                  <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white">
                    {c.file_name}
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                    {c.contract_type}
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                    {c.parties?.join(", ") ?? "-"}
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-900 dark:text-white">
                    {formatCurrency(c.total_value)}
                  </td>
                  <td className="px-6 py-4">
                    <span
                      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${statusColors[c.status]}`}
                    >
                      {c.status === "processing" && <Clock className="h-3 w-3 mr-1" />}
                      {c.status === "parsed" && <CheckCircle2 className="h-3 w-3 mr-1" />}
                      {c.status === "failed" && <AlertTriangle className="h-3 w-3 mr-1" />}
                      {c.status}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                    {new Date(c.created_at).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Contract Detail: Clauses + Deviations */}
      {selectedContractId && detail && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Clause Summary */}
          <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
              Clause Summary
            </h2>
            {detail.clauses.length === 0 ? (
              <p className="text-sm text-gray-500 dark:text-gray-400 text-center py-4">
                No clauses extracted.
              </p>
            ) : (
              <div className="space-y-3">
                {detail.clauses.map((clause, idx) => (
                  <div key={idx} className="border-b border-gray-100 dark:border-gray-700 pb-3">
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-medium text-gray-900 dark:text-white">
                        {clause.clause_type}
                      </p>
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${riskColors[clause.risk_level]}`}
                      >
                        {clause.risk_level}
                      </span>
                    </div>
                    <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                      {clause.description}
                    </p>
                    {clause.standard_deviation && (
                      <p className="text-xs text-yellow-600 mt-1">
                        Deviation: {clause.standard_deviation}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Deviation Alerts */}
          <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <div className="flex items-center gap-2 mb-4">
              <AlertTriangle className="h-5 w-5 text-orange-500" />
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
                Deviation Alerts
              </h2>
            </div>
            {detail.deviations.length === 0 ? (
              <p className="text-sm text-gray-500 dark:text-gray-400 text-center py-4">
                No deviations detected.
              </p>
            ) : (
              <div className="space-y-3">
                {detail.deviations.map((dev) => (
                  <div key={dev.id} className="border-b border-gray-100 dark:border-gray-700 pb-3">
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-medium text-gray-900 dark:text-white">
                        {dev.clause_type}
                      </p>
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${severityColors[dev.severity]}`}
                      >
                        {dev.severity}
                      </span>
                    </div>
                    <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                      {dev.description}
                    </p>
                    <p className="text-xs text-blue-600 mt-1">{dev.recommendation}</p>
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
