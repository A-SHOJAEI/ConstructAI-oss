"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { Sparkles, X, CheckCircle, AlertTriangle, Loader2 } from "lucide-react";

// Wire shape from POST /api/v1/projects/{id}/rfis/{id}/draft-response.
// The backend returns snake_case fields with `draft_response` (the body),
// `draft_confidence` (0-1), and a `sources` array where each entry has
// `document_title`, `page_number`, and `section`. Earlier this file had
// guessed names (`draft_text`, `confidence`, `title`, `relevance`) that
// don't exist on the response — every numeric came back undefined,
// which rendered as "NaN%".
interface DraftSource {
  document_title: string;
  page_number: number | null;
  section?: string | null;
}

interface DraftResponse {
  rfi_id: string;
  draft_response: string;
  draft_confidence: number | null;
  sources: DraftSource[];
  verification_passed: boolean;
  hallucination_flags?: string[];
  contradiction_flags?: string[];
  completeness_flags?: string[];
  error?: string | null;
}

interface DraftResponseViewerProps {
  projectId: string;
  rfiId: string;
  rfiSubject: string;
  onClose: () => void;
}

export function DraftResponseViewer({
  projectId,
  rfiId,
  rfiSubject,
  onClose,
}: DraftResponseViewerProps) {
  const [draft, setDraft] = useState<DraftResponse | null>(null);

  const generateMutation = useMutation({
    mutationFn: () =>
      apiClient.post<DraftResponse>(
        `/api/v1/projects/${projectId}/rfis/${rfiId}/draft-response`,
        {},
        // The Stage 2 retrieval + LLM draft + Stage 3 verification can
        // take 60-90 s depending on prompt length and which model is
        // serving on Spark 1. Default fetch timeout (30 s) was aborting
        // the call before the API finished. 3 min covers the worst case.
        { timeoutMs: 180_000 },
      ),
    onSuccess: (data) => setDraft(data),
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      role="dialog"
      aria-modal="true"
      aria-labelledby="draft-response-dialog-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[85vh] overflow-y-auto m-4">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
          <div className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-blue-600" />
            <h2 id="draft-response-dialog-title" className="text-lg font-semibold text-gray-900">
              AI Draft Response
            </h2>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600"
            aria-label="Close dialog"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="px-6 py-4 space-y-4">
          <p className="text-sm text-gray-600">
            <span className="font-medium">RFI:</span> {rfiSubject}
          </p>

          {!draft && !generateMutation.isPending && (
            <div className="text-center py-8">
              <Sparkles className="h-10 w-10 text-blue-300 mx-auto mb-3" />
              <p className="text-gray-600 mb-4">
                Generate an AI-assisted draft response using project documents, specs, and similar
                RFIs.
              </p>
              <button
                onClick={() => generateMutation.mutate()}
                className="bg-primary text-white px-6 py-2 rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors"
              >
                Generate Draft
              </button>
            </div>
          )}

          {generateMutation.isPending && (
            <div className="text-center py-8">
              <Loader2 className="h-8 w-8 text-blue-500 mx-auto mb-3 animate-spin" />
              <p className="text-gray-600">Analyzing project documents and generating draft...</p>
              <p className="text-xs text-gray-400 mt-2">This may take 10-30 seconds</p>
            </div>
          )}

          {generateMutation.isError && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-800">
              Failed to generate draft. {(generateMutation.error as Error).message}
            </div>
          )}

          {draft &&
            (() => {
              const conf = draft.draft_confidence;
              const confPct =
                typeof conf === "number" && Number.isFinite(conf) ? Math.round(conf * 100) : null;
              return (
                <>
                  {/* Confidence + Verification */}
                  <div className="flex items-center gap-4">
                    {confPct !== null && (
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-gray-500">Confidence:</span>
                        <div className="w-24 h-2 bg-gray-200 rounded-full overflow-hidden">
                          <div
                            className={`h-full rounded-full ${
                              confPct >= 80
                                ? "bg-green-500"
                                : confPct >= 50
                                  ? "bg-amber-500"
                                  : "bg-red-500"
                            }`}
                            style={{ width: `${confPct}%` }}
                          />
                        </div>
                        <span className="text-xs font-medium">{confPct}%</span>
                      </div>
                    )}
                    {draft.verification_passed ? (
                      <span className="flex items-center gap-1 text-xs text-green-700">
                        <CheckCircle className="h-3.5 w-3.5" /> Verified
                      </span>
                    ) : (
                      <span className="flex items-center gap-1 text-xs text-amber-700">
                        <AlertTriangle className="h-3.5 w-3.5" /> Needs Review
                      </span>
                    )}
                  </div>

                  {/* Draft Text */}
                  <div className="bg-gray-50 rounded-lg border border-gray-200 p-4">
                    <p className="text-xs font-medium text-gray-500 uppercase mb-2">
                      AI-Assisted Draft
                    </p>
                    <div className="prose prose-sm max-w-none text-gray-800 whitespace-pre-wrap">
                      {draft.draft_response}
                    </div>
                  </div>

                  {/* Sources */}
                  {draft.sources && draft.sources.length > 0 && (
                    <div>
                      <p className="text-xs font-medium text-gray-500 uppercase mb-2">
                        Sources Referenced
                      </p>
                      <div className="space-y-1">
                        {draft.sources.map((src, i) => (
                          <div
                            key={i}
                            className="flex items-start justify-between gap-3 text-xs bg-white border border-gray-100 rounded px-3 py-1.5"
                          >
                            <div className="text-gray-700 min-w-0">
                              <div className="font-medium truncate">{src.document_title}</div>
                              {src.section && (
                                <div className="text-gray-500 truncate">{src.section}</div>
                              )}
                            </div>
                            {src.page_number != null && (
                              <span className="text-gray-400 shrink-0">p. {src.page_number}</span>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              );
            })()}
        </div>
      </div>
    </div>
  );
}
