"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { Sparkles, ShieldAlert, X } from "lucide-react";

interface Inspection {
  id: string;
  project_id: string;
  inspection_type: string;
  status: string;
  inspector_id: string | null;
  location: string | null;
  checklist_data: Record<string, unknown>;
  score: string | number | null;
  scheduled_at: string | null;
  completed_at: string | null;
  created_at: string;
}

interface DefectAIClassification {
  model?: string;
  top_class?: string;
  confidence?: number;
  probabilities?: Record<string, number>;
  severity_estimate?: string;
  recommendations?: string[];
}

interface Defect {
  id: string;
  project_id: string;
  inspection_id: string | null;
  defect_type: string;
  severity: string;
  status: string;
  description: string;
  location: string | null;
  image_urls: string[];
  ai_classification: DefectAIClassification;
  created_at: string;
}

interface ListResponse<T> {
  data: T[];
  meta: { cursor: string | null; has_more: boolean };
}

const inspectionStatusColors: Record<string, string> = {
  scheduled: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300",
  in_progress: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300",
  completed: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300",
  failed: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300",
  passed: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300",
};

const severityColors: Record<string, string> = {
  minor: "bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300",
  major: "bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-300",
  critical: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300",
};

const defectStatusColors: Record<string, string> = {
  open: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300",
  in_progress: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300",
  resolved: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300",
  closed: "bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300",
};

function fmtPct(value: number | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function DefectDetailModal({ defect, onClose }: { defect: Defect; onClose: () => void }) {
  const ai = defect.ai_classification ?? {};
  const probs = ai.probabilities ?? {};
  const sortedProbs = Object.entries(probs).sort(([, a], [, b]) => b - a);
  const img = defect.image_urls?.[0];
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      role="dialog"
      aria-modal="true"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <div className="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-2xl m-4 max-h-[92vh] overflow-y-auto">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white capitalize">
            {defect.defect_type.replace(/_/g, " ")} — defect detail
          </h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600"
            aria-label="Close dialog"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="px-6 py-5 space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                severityColors[defect.severity] ?? "bg-gray-100 text-gray-700"
              }`}
            >
              {defect.severity}
            </span>
            <span
              className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                defectStatusColors[defect.status] ?? "bg-gray-100 text-gray-700"
              }`}
            >
              {defect.status.replace(/_/g, " ")}
            </span>
            {defect.location && (
              <span className="text-xs text-gray-500 dark:text-gray-400">
                {defect.location}
              </span>
            )}
          </div>

          {img && (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={img}
              alt={`${defect.defect_type} defect`}
              className="w-full rounded-lg border border-gray-200 dark:border-gray-700"
            />
          )}

          <div>
            <h3 className="text-xs font-medium text-gray-500 uppercase mb-1">Description</h3>
            <p className="text-sm text-gray-800 dark:text-gray-200 whitespace-pre-wrap">
              {defect.description}
            </p>
          </div>

          {/* AI Classification panel */}
          {ai.top_class && (
            <div className="bg-gradient-to-br from-blue-50 to-indigo-50 dark:from-blue-900/20 dark:to-indigo-900/20 rounded-lg border border-blue-200 dark:border-blue-800 p-4 space-y-3">
              <div className="flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-blue-600" />
                <h3 className="text-sm font-semibold text-gray-900 dark:text-white">
                  AI Classification (Defect ViT v1.1)
                </h3>
              </div>

              <div className="flex items-center gap-3 flex-wrap">
                <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-blue-100 text-blue-800">
                  {ai.top_class.replace(/_/g, " ")}
                </span>
                <span className="text-xs text-gray-600">
                  Confidence: <strong>{fmtPct(ai.confidence)}</strong>
                </span>
                {ai.severity_estimate && (
                  <span className="text-xs text-gray-600">
                    Severity: <strong>{ai.severity_estimate}</strong>
                  </span>
                )}
                {ai.model && <span className="text-xs text-gray-400">{ai.model}</span>}
              </div>

              {sortedProbs.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-gray-500 uppercase mb-2">
                    Class probabilities
                  </p>
                  <div className="space-y-1.5">
                    {sortedProbs.map(([cls, p]) => {
                      const pct = Math.max(0, Math.min(1, p));
                      const isTop = cls === ai.top_class;
                      return (
                        <div key={cls} className="flex items-center gap-2 text-xs">
                          <span
                            className={`w-32 ${isTop ? "font-semibold text-gray-900 dark:text-white" : "text-gray-600 dark:text-gray-400"}`}
                          >
                            {cls.replace(/_/g, " ")}
                          </span>
                          <div className="flex-1 h-2 bg-white/60 dark:bg-gray-800/60 rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full ${isTop ? "bg-blue-600" : "bg-gray-400"}`}
                              style={{ width: `${pct * 100}%` }}
                            />
                          </div>
                          <span className="w-12 text-right text-gray-500">{fmtPct(p)}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {ai.recommendations && ai.recommendations.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-gray-500 uppercase mb-2">
                    Recommended actions
                  </p>
                  <ul className="space-y-1 text-sm text-gray-800 dark:text-gray-200 list-disc list-inside">
                    {ai.recommendations.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function QualityPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const [selected, setSelected] = useState<Defect | null>(null);

  const inspectionsQ = useQuery<ListResponse<Inspection>>({
    queryKey: ["inspections", projectId],
    queryFn: () =>
      apiClient.get<ListResponse<Inspection>>(
        `/api/v1/quality/inspections?project_id=${projectId}`,
      ),
    enabled: !!projectId,
  });

  const defectsQ = useQuery<ListResponse<Defect>>({
    queryKey: ["defects", projectId],
    queryFn: () =>
      apiClient.get<ListResponse<Defect>>(`/api/v1/quality/defects?project_id=${projectId}`),
    enabled: !!projectId,
  });

  if (!projectId) return <NoProjectSelected />;

  const inspections = inspectionsQ.data?.data ?? [];
  const defects = defectsQ.data?.data ?? [];

  const totalInspections = inspections.length;
  const completedInspections = inspections.filter(
    (i) => i.status === "completed" || i.status === "passed",
  ).length;
  const inProgressInspections = inspections.filter((i) => i.status === "in_progress").length;
  const openDefects = defects.filter((d) => d.status === "open").length;
  const criticalDefects = defects.filter((d) => d.severity === "critical").length;

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Quality Management</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Inspections, AI-classified defects (Defect ViT v1.1), and quality metrics
        </p>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">Inspections</p>
          <p className="text-3xl font-bold text-gray-900 dark:text-white">
            {inspectionsQ.isLoading ? "—" : totalInspections}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">Completed</p>
          <p className="text-3xl font-bold text-green-600">
            {inspectionsQ.isLoading ? "—" : completedInspections}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">In Progress</p>
          <p className="text-3xl font-bold text-yellow-600">
            {inspectionsQ.isLoading ? "—" : inProgressInspections}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">Open Defects</p>
          <p className="text-3xl font-bold text-orange-600">
            {defectsQ.isLoading ? "—" : openDefects}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">Critical</p>
          <p className="text-3xl font-bold text-red-600">
            {defectsQ.isLoading ? "—" : criticalDefects}
          </p>
        </div>
      </div>

      {/* AI-classified defect gallery */}
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-blue-600" />
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
            AI-Classified Defects
          </h2>
          <span className="text-xs text-gray-500 ml-2">
            Inferred by Defect ViT v1.1 — click any card for full classification
          </span>
        </div>
        {defectsQ.isLoading && (
          <div className="p-8 text-center text-gray-500">Loading defects...</div>
        )}
        {!defectsQ.isLoading && defects.length === 0 && (
          <div className="p-8 text-center text-gray-500">No defects reported yet.</div>
        )}
        {!defectsQ.isLoading && defects.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 p-4">
            {defects.map((d) => {
              const img = d.image_urls?.[0];
              const ai = d.ai_classification ?? {};
              return (
                <button
                  key={d.id}
                  onClick={() => setSelected(d)}
                  className="text-left bg-gray-50 dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 hover:border-blue-400 transition-colors overflow-hidden"
                >
                  <div className="aspect-[4/3] bg-gray-200 dark:bg-gray-800 overflow-hidden">
                    {img ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={img} alt={d.defect_type} className="w-full h-full object-cover" />
                    ) : (
                      <div className="flex items-center justify-center h-full">
                        <ShieldAlert className="h-8 w-8 text-gray-400" />
                      </div>
                    )}
                  </div>
                  <div className="p-3 space-y-1.5">
                    <div className="flex items-center justify-between gap-1">
                      <span className="text-sm font-medium text-gray-900 dark:text-white capitalize truncate">
                        {d.defect_type.replace(/_/g, " ")}
                      </span>
                      <span
                        className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${
                          severityColors[d.severity] ?? "bg-gray-100"
                        }`}
                      >
                        {d.severity}
                      </span>
                    </div>
                    {ai.confidence != null && (
                      <p className="text-xs text-gray-500">
                        AI confidence: <strong>{fmtPct(ai.confidence)}</strong>
                      </p>
                    )}
                    {d.location && (
                      <p className="text-xs text-gray-400 truncate">{d.location}</p>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Inspections list */}
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Inspections</h2>
        </div>
        {inspectionsQ.isLoading && (
          <div className="p-8 text-center text-gray-500">Loading inspections...</div>
        )}
        {!inspectionsQ.isLoading && inspections.length === 0 && (
          <div className="p-8 text-center text-gray-500">No inspections found.</div>
        )}
        {!inspectionsQ.isLoading && inspections.length > 0 && (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
              <thead className="bg-gray-50 dark:bg-gray-900">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Type
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Status
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Location
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Score
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Scheduled
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Completed
                  </th>
                </tr>
              </thead>
              <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
                {inspections.map((insp) => (
                  <tr key={insp.id} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900 dark:text-white capitalize">
                      {insp.inspection_type.replace(/_/g, " ")}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                          inspectionStatusColors[insp.status] ?? "bg-gray-100"
                        }`}
                      >
                        {insp.status?.replace(/_/g, " ")}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                      {insp.location ?? "—"}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900 dark:text-gray-100 font-mono">
                      {insp.score != null ? `${insp.score}` : "—"}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                      {insp.scheduled_at
                        ? new Date(insp.scheduled_at).toLocaleDateString()
                        : "—"}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                      {insp.completed_at
                        ? new Date(insp.completed_at).toLocaleDateString()
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {selected && <DefectDetailModal defect={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
