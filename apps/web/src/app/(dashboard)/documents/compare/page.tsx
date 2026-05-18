"use client";

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";

interface DiffSection {
  section: string;
  change_type: "added" | "removed" | "modified" | "unchanged";
  old_text: string;
  new_text: string;
  line_start: number;
  line_end: number;
}

interface ComparisonResult {
  document_a_id: string;
  document_b_id: string;
  total_sections: number;
  added: number;
  removed: number;
  modified: number;
  unchanged: number;
  similarity_ratio: number;
  diffs: DiffSection[];
}

interface Document {
  id: string;
  filename: string;
  status: string;
  created_at: string;
}

const CHANGE_COLORS = {
  added: "bg-green-50 border-green-300 dark:bg-green-900/20 dark:border-green-700",
  removed: "bg-red-50 border-red-300 dark:bg-red-900/20 dark:border-red-700",
  modified: "bg-yellow-50 border-yellow-300 dark:bg-yellow-900/20 dark:border-yellow-700",
  unchanged: "bg-gray-50 border-gray-200 dark:bg-gray-800 dark:border-gray-700",
};

const CHANGE_LABELS = {
  added: "Added",
  removed: "Removed",
  modified: "Modified",
  unchanged: "Unchanged",
};

export default function DocumentComparePage() {
  const { selectedProjectId: projectId } = useProjectStore();
  const [docA, setDocA] = useState("");
  const [docB, setDocB] = useState("");

  const { data: documents } = useQuery<{ items: Document[] }>({
    queryKey: ["documents", projectId],
    queryFn: () => apiClient.get(`/api/v1/documents/?project_id=${projectId}`),
    enabled: !!projectId,
  });

  const compareMutation = useMutation<ComparisonResult>({
    mutationFn: () =>
      apiClient.post(`/api/v1/projects/${projectId}/documents/compare`, {
        document_a_id: docA,
        document_b_id: docB,
        context_lines: 3,
      }),
  });

  const result = compareMutation.data;

  if (!projectId) {
    return (
      <div className="p-6 text-center text-gray-500">Select a project to compare documents.</div>
    );
  }

  return (
    <div className="space-y-6 p-6">
      <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Document Comparison</h1>

      {/* Document Selection */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end">
        <div className="flex-1">
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
            Document A (Base)
          </label>
          <select
            value={docA}
            onChange={(e) => setDocA(e.target.value)}
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-white"
          >
            <option value="">Select document...</option>
            {documents?.items?.map((d) => (
              <option key={d.id} value={d.id}>
                {d.filename}
              </option>
            ))}
          </select>
        </div>

        <div className="flex-1">
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
            Document B (Compare)
          </label>
          <select
            value={docB}
            onChange={(e) => setDocB(e.target.value)}
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-white"
          >
            <option value="">Select document...</option>
            {documents?.items?.map((d) => (
              <option key={d.id} value={d.id}>
                {d.filename}
              </option>
            ))}
          </select>
        </div>

        <button
          onClick={() => compareMutation.mutate()}
          disabled={!docA || !docB || docA === docB || compareMutation.isPending}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {compareMutation.isPending ? "Comparing..." : "Compare"}
        </button>
      </div>

      {compareMutation.isError && (
        <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/20 dark:text-red-400">
          Failed to compare documents. Please try again.
        </div>
      )}

      {/* Results */}
      {result && (
        <>
          {/* Summary */}
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
            <div className="rounded-lg border p-3 text-center dark:border-gray-700">
              <p className="text-2xl font-bold text-gray-900 dark:text-white">
                {(result.similarity_ratio * 100).toFixed(1)}%
              </p>
              <p className="text-xs text-gray-500">Similarity</p>
            </div>
            <div className="rounded-lg border border-green-200 bg-green-50 p-3 text-center dark:border-green-800 dark:bg-green-900/20">
              <p className="text-2xl font-bold text-green-700 dark:text-green-400">
                {result.added}
              </p>
              <p className="text-xs text-green-600">Added</p>
            </div>
            <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-center dark:border-red-800 dark:bg-red-900/20">
              <p className="text-2xl font-bold text-red-700 dark:text-red-400">{result.removed}</p>
              <p className="text-xs text-red-600">Removed</p>
            </div>
            <div className="rounded-lg border border-yellow-200 bg-yellow-50 p-3 text-center dark:border-yellow-800 dark:bg-yellow-900/20">
              <p className="text-2xl font-bold text-yellow-700 dark:text-yellow-400">
                {result.modified}
              </p>
              <p className="text-xs text-yellow-600">Modified</p>
            </div>
            <div className="rounded-lg border p-3 text-center dark:border-gray-700">
              <p className="text-2xl font-bold text-gray-500">{result.unchanged}</p>
              <p className="text-xs text-gray-500">Unchanged</p>
            </div>
          </div>

          {/* Diff View */}
          <div className="space-y-3">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
              Changes ({result.diffs.length})
            </h2>
            {result.diffs.length === 0 ? (
              <p className="text-sm text-gray-500">Documents are identical.</p>
            ) : (
              result.diffs.map((diff, i) => (
                <div key={i} className={`rounded-lg border p-4 ${CHANGE_COLORS[diff.change_type]}`}>
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium uppercase">
                      {CHANGE_LABELS[diff.change_type]}
                    </span>
                    <span className="text-xs text-gray-500">
                      Line {diff.line_start}
                      {diff.line_end > diff.line_start && `–${diff.line_end}`}
                    </span>
                  </div>
                  {diff.old_text && (
                    <pre className="mt-2 whitespace-pre-wrap text-sm text-red-800 line-through dark:text-red-300">
                      {diff.old_text}
                    </pre>
                  )}
                  {diff.new_text && (
                    <pre className="mt-1 whitespace-pre-wrap text-sm text-green-800 dark:text-green-300">
                      {diff.new_text}
                    </pre>
                  )}
                </div>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}
