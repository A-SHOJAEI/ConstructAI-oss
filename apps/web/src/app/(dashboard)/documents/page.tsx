"use client";

import { useState, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";

const MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024; // 100MB
const ALLOWED_EXTENSIONS = [
  ".pdf",
  ".dwg",
  ".ifc",
  ".xlsx",
  ".docx",
  ".csv",
  ".xer",
  ".mpp",
  ".xml",
];

interface Document {
  id: string;
  name: string;
  type: string;
  status: string;
  discipline: string | null;
  created_at: string;
  file_size?: number;
}

interface DocumentsResponse {
  items: Document[];
  total: number;
}

const isValidUUID = (id: string) =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);

const statusColors: Record<string, string> = {
  processing: "bg-yellow-100 text-yellow-800",
  complete: "bg-green-100 text-green-800",
  completed: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
  pending: "bg-gray-100 text-gray-800",
  uploaded: "bg-blue-100 text-blue-800",
};

type StatusFilter = "all" | "processing" | "complete" | "failed";

export default function DocumentsPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [isDragOver, setIsDragOver] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data, isLoading, error } = useQuery<DocumentsResponse>({
    queryKey: ["documents", projectId],
    queryFn: () => apiClient.get<DocumentsResponse>(`/api/v1/documents/?project_id=${projectId}`),
    enabled: !!projectId && isValidUUID(projectId),
  });

  const uploadMutation = useMutation({
    mutationFn: (formData: FormData) => {
      if (!projectId || !isValidUUID(projectId)) {
        return Promise.reject(new Error("Invalid project ID"));
      }
      return apiClient.upload<Document>(`/api/v1/documents/?project_id=${projectId}`, formData);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["documents", projectId] });
    },
  });

  const handleFiles = useCallback(
    (files: FileList | null) => {
      if (!files) return;
      setUploadError(null);
      const oversized: string[] = [];
      const invalidType: string[] = [];
      Array.from(files).forEach((file) => {
        const ext = file.name.substring(file.name.lastIndexOf(".")).toLowerCase();
        if (!ALLOWED_EXTENSIONS.includes(ext)) {
          invalidType.push(file.name);
          return;
        }
        if (file.size > MAX_FILE_SIZE_BYTES) {
          oversized.push(`${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)`);
          return;
        }
        const formData = new FormData();
        formData.append("file", file);
        uploadMutation.mutate(formData);
      });
      const errors: string[] = [];
      if (invalidType.length > 0) {
        errors.push(
          `Unsupported file type: ${invalidType.join(", ")}. Allowed: ${ALLOWED_EXTENSIONS.join(", ")}`,
        );
      }
      if (oversized.length > 0) {
        errors.push(`File(s) exceed 100 MB limit: ${oversized.join(", ")}`);
      }
      if (errors.length > 0) {
        setUploadError(errors.join(" | "));
      }
    },
    [uploadMutation],
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragOver(false);
      handleFiles(e.dataTransfer.files);
    },
    [handleFiles],
  );

  const documents = data?.items ?? [];
  const filtered =
    statusFilter === "all"
      ? documents
      : documents.filter(
          (d) =>
            d.status === statusFilter || (statusFilter === "complete" && d.status === "completed"),
        );

  const filterButtons: { label: string; value: StatusFilter }[] = [
    { label: "All", value: "all" },
    { label: "Processing", value: "processing" },
    { label: "Complete", value: "complete" },
    { label: "Failed", value: "failed" },
  ];

  if (!projectId) return <NoProjectSelected />;

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Documents</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Upload, manage, and track construction documents
        </p>
      </div>

      {/* Upload Area */}
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`border-2 border-dashed rounded-lg p-8 text-center mb-6 transition-colors cursor-pointer ${
          isDragOver
            ? "border-blue-500 bg-blue-50 dark:bg-blue-900/20"
            : "border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-800 hover:border-gray-400"
        }`}
        onClick={() => {
          const input = document.createElement("input");
          input.type = "file";
          input.multiple = true;
          input.accept = ".pdf,.dwg,.ifc,.xlsx,.docx,.csv,.xer,.mpp,.xml";
          input.onchange = () => handleFiles(input.files);
          input.click();
        }}
      >
        <div className="text-gray-400 text-4xl mb-2">&#128196;</div>
        <p className="text-sm font-medium text-gray-700 dark:text-gray-200">
          Drag and drop files here, or click to browse
        </p>
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
          Supports PDF, DWG, IFC, XLSX, and more
        </p>
        {uploadMutation.isPending && (
          <div className="mt-3">
            <div className="w-48 mx-auto bg-gray-200 rounded-full h-2 overflow-hidden">
              <div className="bg-blue-600 h-2 rounded-full animate-pulse w-3/4" />
            </div>
            <p className="text-sm text-blue-600 mt-1">Uploading...</p>
          </div>
        )}
        {uploadMutation.isError && (
          <p className="text-sm text-red-600 mt-2">
            Upload failed: {(uploadMutation.error as Error).message}
          </p>
        )}
        {uploadError && <p className="text-sm text-red-600 mt-2">{uploadError}</p>}
      </div>

      {/* Status Filters */}
      <div className="flex gap-2 mb-4">
        {filterButtons.map((btn) => (
          <button
            key={btn.value}
            onClick={() => setStatusFilter(btn.value)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              statusFilter === btn.value
                ? "bg-blue-600 text-white"
                : "bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-200 hover:bg-gray-200 dark:hover:bg-gray-700"
            }`}
          >
            {btn.label}
          </button>
        ))}
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-8 text-center">
          <p className="text-gray-500 dark:text-gray-400">Loading documents...</p>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-800">
          <p className="font-medium">Failed to load documents</p>
          <p className="text-sm mt-1">{(error as Error).message}</p>
        </div>
      )}

      {/* Empty State */}
      {!isLoading && !error && filtered.length === 0 && (
        <div className="text-center py-12 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
          <div className="text-gray-400 text-5xl mb-4">&#128196;</div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-2">
            {statusFilter === "all" ? "No documents uploaded" : `No ${statusFilter} documents`}
          </h3>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Upload documents using the drag-and-drop area above.
          </p>
        </div>
      )}

      {/* Document Table */}
      {!isLoading && !error && filtered.length > 0 && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
            <thead className="bg-gray-50 dark:bg-gray-900">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Name
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Type
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Status
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Discipline
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                  Created
                </th>
              </tr>
            </thead>
            <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
              {filtered.map((doc) => (
                <tr key={doc.id} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                  <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900 dark:text-white">
                    {doc.name}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                    {doc.type}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <span
                      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                        statusColors[doc.status] ?? "bg-gray-100 text-gray-800"
                      }`}
                    >
                      {doc.status}
                    </span>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                    {doc.discipline ?? "—"}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                    {new Date(doc.created_at).toLocaleDateString()}
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
