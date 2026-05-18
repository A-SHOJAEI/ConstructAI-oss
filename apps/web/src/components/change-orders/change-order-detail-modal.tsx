"use client";

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import {
  X,
  Sparkles,
  CheckCircle,
  AlertTriangle,
  Loader2,
  FileText,
  HelpCircle,
} from "lucide-react";
import { controlsApi, type ScopeAnalysisResult } from "@/lib/controls-api";

interface Props {
  coId: string;
  onClose: () => void;
}

const verdictMeta: Record<
  ScopeAnalysisResult["verdict"],
  { label: string; color: string; icon: typeof CheckCircle }
> = {
  additional_work: {
    label: "Genuine Additional Work",
    color: "bg-emerald-100 text-emerald-800",
    icon: CheckCircle,
  },
  covered_by_contract: {
    label: "Already Covered by Contract",
    color: "bg-red-100 text-red-800",
    icon: AlertTriangle,
  },
  covered_by_rfi: {
    label: "Resolved by Existing RFI",
    color: "bg-orange-100 text-orange-800",
    icon: AlertTriangle,
  },
  needs_clarification: {
    label: "Needs Clarification",
    color: "bg-amber-100 text-amber-800",
    icon: HelpCircle,
  },
};

function fmt(value: number | null | undefined): string {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(Number(value));
}

export function ChangeOrderDetailModal({ coId, onClose }: Props) {
  const [scope, setScope] = useState<ScopeAnalysisResult | null>(null);

  const { data: co, isLoading } = useQuery({
    queryKey: ["change-order", coId],
    queryFn: () => controlsApi.changeOrder(coId),
  });

  const scopeMutation = useMutation({
    mutationFn: () => controlsApi.scopeAnalysis(coId),
    onSuccess: (r) => setScope(r),
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      role="dialog"
      aria-modal="true"
      aria-labelledby="co-detail-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <div className="bg-white dark:bg-gray-900 rounded-xl shadow-xl w-full max-w-3xl m-4 max-h-[92vh] overflow-y-auto">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
          <h2 id="co-detail-title" className="text-lg font-semibold text-gray-900 dark:text-white">
            {isLoading || !co
              ? "Change Order"
              : `${co.co_number ? "CO-" + String(co.co_number).padStart(3, "0") : "Change Order"} — ${co.title}`}
          </h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600"
            aria-label="Close dialog"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="px-6 py-5 space-y-6">
          {isLoading && <p className="text-sm text-gray-500">Loading change order...</p>}

          {!isLoading && co && (
            <>
              <div className="flex items-center gap-2 flex-wrap">
                <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800 capitalize">
                  {co.status}
                </span>
                {co.change_type && (
                  <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
                    {co.change_type.replace(/_/g, " ")}
                  </span>
                )}
                {co.risk_score != null && (
                  <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-purple-100 text-purple-800">
                    Risk {co.risk_score}
                  </span>
                )}
              </div>

              <div>
                <h3 className="text-xs font-medium text-gray-500 uppercase mb-2">Description</h3>
                <p className="text-sm text-gray-800 dark:text-gray-200 whitespace-pre-wrap">
                  {co.description}
                </p>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="bg-gray-50 dark:bg-gray-800 rounded p-4">
                  <h3 className="text-xs font-medium text-gray-500 uppercase mb-2">Cost Impact</h3>
                  <p className="text-2xl font-bold text-gray-900 dark:text-gray-100">
                    {fmt(co.cost_impact ?? co.original_amount)}
                  </p>
                  <dl className="mt-3 space-y-1 text-xs">
                    {co.labor_cost != null && (
                      <div className="flex justify-between">
                        <dt className="text-gray-500">Labor</dt>
                        <dd className="text-gray-700 dark:text-gray-300">{fmt(co.labor_cost)}</dd>
                      </div>
                    )}
                    {co.material_cost != null && (
                      <div className="flex justify-between">
                        <dt className="text-gray-500">Material</dt>
                        <dd className="text-gray-700 dark:text-gray-300">{fmt(co.material_cost)}</dd>
                      </div>
                    )}
                    {co.equipment_cost != null && (
                      <div className="flex justify-between">
                        <dt className="text-gray-500">Equipment</dt>
                        <dd className="text-gray-700 dark:text-gray-300">{fmt(co.equipment_cost)}</dd>
                      </div>
                    )}
                    {co.subcontractor_cost != null && Number(co.subcontractor_cost) > 0 && (
                      <div className="flex justify-between">
                        <dt className="text-gray-500">Subcontractor</dt>
                        <dd className="text-gray-700 dark:text-gray-300">
                          {fmt(co.subcontractor_cost)}
                        </dd>
                      </div>
                    )}
                    {co.overhead_cost != null && (
                      <div className="flex justify-between">
                        <dt className="text-gray-500">Overhead</dt>
                        <dd className="text-gray-700 dark:text-gray-300">{fmt(co.overhead_cost)}</dd>
                      </div>
                    )}
                    {co.markup_pct != null && Number(co.markup_pct) > 0 && (
                      <div className="flex justify-between">
                        <dt className="text-gray-500">Markup</dt>
                        <dd className="text-gray-700 dark:text-gray-300">{co.markup_pct}%</dd>
                      </div>
                    )}
                  </dl>
                </div>

                <div className="bg-gray-50 dark:bg-gray-800 rounded p-4">
                  <h3 className="text-xs font-medium text-gray-500 uppercase mb-2">Schedule</h3>
                  <p className="text-2xl font-bold text-gray-900 dark:text-gray-100">
                    {co.schedule_impact_days > 0 ? "+" : ""}
                    {co.schedule_impact_days} days
                  </p>
                  <p className="text-xs text-gray-500 mt-2">
                    Submitted: {new Date(co.created_at).toLocaleDateString()}
                  </p>
                </div>
              </div>

              {/* AI Scope Analysis */}
              <div className="bg-gradient-to-br from-blue-50 to-indigo-50 dark:from-blue-900/20 dark:to-indigo-900/20 rounded-lg border border-blue-200 dark:border-blue-800 p-5">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                      <Sparkles className="h-4 w-4 text-blue-600" />
                      AI Scope Analysis
                    </h3>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                      Flags PCOs that are likely <em>not</em> additional work — for instance, if a
                      clarification is already in the contract or in an answered RFI.
                    </p>
                  </div>
                  <button
                    onClick={() => scopeMutation.mutate()}
                    disabled={scopeMutation.isPending}
                    className="shrink-0 flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
                  >
                    {scopeMutation.isPending ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Sparkles className="h-4 w-4" />
                    )}
                    {scopeMutation.isPending
                      ? "Analyzing..."
                      : scope
                        ? "Re-run Analysis"
                        : "Run Analysis"}
                  </button>
                </div>

                {scope &&
                  (() => {
                    const meta = verdictMeta[scope.verdict];
                    const Icon = meta.icon;
                    return (
                      <div className="mt-4 space-y-3">
                        <div className="flex items-center gap-3 flex-wrap">
                          <span
                            className={`inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-semibold ${meta.color}`}
                          >
                            <Icon className="h-3.5 w-3.5" />
                            {meta.label}
                          </span>
                          <span className="text-xs text-gray-500">
                            Confidence: {(scope.confidence * 100).toFixed(0)}%
                          </span>
                          {scope.model && (
                            <span className="text-xs text-gray-400">{scope.model}</span>
                          )}
                        </div>

                        {scope.summary && (
                          <p className="text-sm text-gray-800 bg-white/70 dark:bg-gray-800/40 rounded p-3 border border-blue-100 dark:border-blue-900">
                            {scope.summary}
                          </p>
                        )}

                        {scope.recommendation && (
                          <div className="bg-amber-50 border border-amber-200 rounded p-3">
                            <p className="text-xs font-medium text-amber-700 uppercase mb-1">
                              Recommendation
                            </p>
                            <p className="text-sm text-amber-900">{scope.recommendation}</p>
                          </div>
                        )}

                        {scope.evidence.length > 0 && (
                          <div>
                            <p className="text-xs font-medium text-gray-500 uppercase mb-2">
                              Cited Evidence ({scope.evidence.length})
                            </p>
                            <ul className="space-y-2">
                              {scope.evidence.map((e, i) => (
                                <li
                                  key={i}
                                  className="bg-white/70 dark:bg-gray-800/40 rounded p-2 border border-blue-100 dark:border-blue-900"
                                >
                                  <div className="flex items-center gap-2 mb-1">
                                    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase bg-blue-100 text-blue-800">
                                      {e.type}
                                    </span>
                                    <span className="text-xs font-mono text-gray-700">
                                      {e.ref}
                                    </span>
                                  </div>
                                  {e.quote && (
                                    <p className="text-xs text-gray-700 italic">“{e.quote}”</p>
                                  )}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {(scope.spec_sources.length > 0 || scope.rfi_sources.length > 0) && (
                          <div>
                            <p className="text-xs font-medium text-gray-500 uppercase mb-2">
                              Sources Considered
                            </p>
                            <ul className="space-y-1">
                              {scope.rfi_sources.map((r, i) => (
                                <li
                                  key={`rfi-${i}`}
                                  className="flex items-center gap-2 text-xs text-gray-700 bg-white/70 dark:bg-gray-800/40 rounded px-2 py-1 border border-blue-100 dark:border-blue-900"
                                >
                                  <FileText className="h-3 w-3 text-purple-500 shrink-0" />
                                  <span className="font-mono">{r.rfi_number}</span>
                                  <span className="truncate">{r.subject}</span>
                                  {r.similarity_score != null && (
                                    <span className="text-gray-400 shrink-0">
                                      sim {(r.similarity_score * 100).toFixed(0)}%
                                    </span>
                                  )}
                                </li>
                              ))}
                              {scope.spec_sources.slice(0, 5).map((s, i) => (
                                <li
                                  key={`spec-${i}`}
                                  className="flex items-center gap-2 text-xs text-gray-700 bg-white/70 dark:bg-gray-800/40 rounded px-2 py-1 border border-blue-100 dark:border-blue-900"
                                >
                                  <FileText className="h-3 w-3 text-blue-500 shrink-0" />
                                  <span className="truncate">
                                    {s.document_title}
                                    {s.section ? ` — ${s.section}` : ""}
                                    {s.page_number != null ? ` (p. ${s.page_number})` : ""}
                                  </span>
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {scope.error && (
                          <p className="text-xs text-red-700 bg-red-50 rounded p-2">
                            {scope.error}
                          </p>
                        )}
                      </div>
                    );
                  })()}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
