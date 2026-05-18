"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { apiClient } from "@/lib/api-client";
import { RfiResponseForm } from "@/components/rfis/rfi-response-form";
import { DraftResponseViewer } from "@/components/rfis/draft-response-viewer";
import { toast } from "sonner";
import {
  ArrowLeft,
  Calendar,
  DollarSign,
  Clock,
  Paperclip,
  AlertTriangle,
  Sparkles,
  Loader2,
  CheckCircle2,
} from "lucide-react";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { isUuid } from "@/lib/validators";

interface AutoResolveResult {
  rfi_id: string;
  status: string;
  stage_reached: number;
  is_unnecessary: boolean;
  unnecessary_reason: string | null;
  unnecessary_source: string | null;
  draft_response: string | null;
  draft_confidence: number | null;
  verification_passed: boolean;
  hallucination_flags: string[];
  contradiction_flags: string[];
  completeness_flags: string[];
  resolution_log_id: string | null;
}

interface RFIResponse {
  id: string;
  rfi_id: string;
  responder_id: string | null;
  response_text: string;
  status: string;
  responded_at: string;
  created_at: string;
}

interface RFIAttachment {
  id: string;
  file_name: string;
  file_type: string | null;
  file_size_bytes: number | null;
  download_url: string | null;
  uploaded_at: string;
}

interface RFIDetail {
  id: string;
  project_id: string;
  rfi_number: string;
  subject: string;
  question: string;
  answer: string | null;
  status: string;
  priority: string;
  submitted_by: string | null;
  assigned_to: string | null;
  ball_in_court: string | null;
  ai_suggested_response: string | null;
  due_date: string | null;
  spec_section: string | null;
  drawing_reference: string | null;
  cost_impact: boolean | null;
  schedule_impact: boolean | null;
  cost_impact_amount: number | null;
  schedule_impact_days: number | null;
  is_overdue: boolean;
  days_open: number | null;
  responses: RFIResponse[];
  attachments: RFIAttachment[];
  created_at: string;
  updated_at: string;
}

const statusColors: Record<string, string> = {
  draft: "bg-gray-100 text-gray-800",
  open: "bg-blue-100 text-blue-800",
  pending_review: "bg-yellow-100 text-yellow-800",
  answered: "bg-green-100 text-green-800",
  closed: "bg-gray-100 text-gray-600",
  void: "bg-red-100 text-red-800",
};

const priorityColors: Record<string, string> = {
  urgent: "bg-red-100 text-red-800",
  high: "bg-orange-100 text-orange-800",
  normal: "bg-blue-100 text-blue-800",
  low: "bg-gray-100 text-gray-600",
};

export default function RFIDetailPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const params = useParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const rfiId = params.rfiId as string;
  const [closing, setClosing] = useState(false);
  const [autoResolving, setAutoResolving] = useState(false);
  const [autoResolveResult, setAutoResolveResult] = useState<AutoResolveResult | null>(null);
  const [showDraftViewer, setShowDraftViewer] = useState(false);

  // L-12: validate route param so invalid URLs surface immediately
  // instead of hitting the API just to 404.
  const hasValidRfiId = isUuid(rfiId);

  const {
    data: rfi,
    isLoading,
    error,
  } = useQuery<RFIDetail>({
    queryKey: ["rfi-detail", projectId, rfiId],
    queryFn: () => apiClient.get<RFIDetail>(`/api/v1/projects/${projectId}/rfis/${rfiId}`),
    enabled: hasValidRfiId && !!projectId,
  });

  if (!projectId) return <NoProjectSelected />;
  if (!hasValidRfiId) {
    return (
      <div className="p-6 text-red-700" role="alert">
        Invalid RFI id in URL.
      </div>
    );
  }

  const handleClose = async () => {
    if (!rfi) return;
    setClosing(true);
    try {
      await apiClient.post(`/api/v1/projects/${projectId}/rfis/${rfiId}/close`, {
        answer: rfi.answer,
      });
      queryClient.invalidateQueries({ queryKey: ["rfi-detail", rfiId] });
      queryClient.invalidateQueries({ queryKey: ["rfis"] });
      toast.success("RFI closed");
    } finally {
      setClosing(false);
    }
  };

  const handleAutoResolve = async () => {
    if (!rfi) return;
    setAutoResolving(true);
    setAutoResolveResult(null);
    try {
      const result = await apiClient.post<AutoResolveResult>(
        `/api/v1/projects/${projectId}/rfis/${rfiId}/auto-resolve`,
        {},
        { timeoutMs: 180_000 },
      );
      setAutoResolveResult(result);
      const conf =
        result.draft_confidence != null
          ? `${(result.draft_confidence * 100).toFixed(0)}% confidence`
          : "draft generated";
      toast.success(`AI resolved (stage ${result.stage_reached}, ${conf})`);
      queryClient.invalidateQueries({ queryKey: ["rfi-detail", rfiId] });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Auto-resolve failed");
    } finally {
      setAutoResolving(false);
    }
  };

  const handleResponseAdded = () => {
    queryClient.invalidateQueries({ queryKey: ["rfi-detail", rfiId] });
    toast.success("Response submitted");
  };

  if (isLoading) {
    return (
      <div className="p-6">
        <div className="text-center text-gray-500 py-12">Loading RFI details...</div>
      </div>
    );
  }

  if (error || !rfi) {
    return (
      <div className="p-6">
        <div className="p-4 text-red-800 bg-red-50 rounded">Failed to load RFI details</div>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-4xl">
      {/* Back + Header */}
      <button
        onClick={() => router.back()}
        className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-4"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to RFIs
      </button>

      <div className="flex items-start justify-between mb-6">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-gray-900">{rfi.rfi_number}</h1>
            <span
              className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                statusColors[rfi.status] ?? "bg-gray-100 text-gray-800"
              }`}
            >
              {rfi.status.replace("_", " ")}
            </span>
            <span
              className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                priorityColors[rfi.priority] ?? "bg-gray-100 text-gray-800"
              }`}
            >
              {rfi.priority}
            </span>
            {rfi.is_overdue && (
              <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800">
                <AlertTriangle className="h-3 w-3" />
                Overdue
              </span>
            )}
          </div>
          <h2 className="text-lg text-gray-700 mt-1">{rfi.subject}</h2>
        </div>
        <div className="flex gap-2">
          {rfi.status !== "closed" && rfi.status !== "void" && (
            <>
              <button
                onClick={() => setShowDraftViewer(true)}
                className="inline-flex items-center gap-1.5 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium shadow-sm"
                title="Generate an AI draft response with citations from the project corpus"
              >
                <Sparkles className="h-4 w-4" />
                AI Draft Response
              </button>
              <button
                onClick={handleAutoResolve}
                disabled={autoResolving}
                className="inline-flex items-center gap-1.5 px-4 py-2 bg-primary hover:bg-primary-dark text-white rounded-lg text-sm font-medium shadow-sm disabled:opacity-50"
                title="Run the full 3-stage RFI agent: necessity check, draft, verification"
              >
                {autoResolving ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Resolving…
                  </>
                ) : (
                  <>
                    <Sparkles className="h-4 w-4" />
                    Auto-Resolve (AI)
                  </>
                )}
              </button>
              <button
                onClick={handleClose}
                disabled={closing}
                className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              >
                {closing ? "Closing..." : "Close RFI"}
              </button>
            </>
          )}
        </div>
      </div>

      {/* Question & Answer */}
      <div className="bg-white rounded-lg border border-gray-200 p-6 mb-6">
        <h3 className="text-sm font-medium text-gray-500 uppercase mb-2">Question</h3>
        <p className="text-gray-900 whitespace-pre-wrap">{rfi.question}</p>

        {rfi.answer && (
          <div className="mt-4 pt-4 border-t border-gray-200">
            <h3 className="text-sm font-medium text-gray-500 uppercase mb-2">Official Answer</h3>
            <p className="text-gray-900 whitespace-pre-wrap">{rfi.answer}</p>
          </div>
        )}

        {rfi.ai_suggested_response && !rfi.answer && (
          <div className="mt-4 pt-4 border-t border-gray-200">
            <h3 className="text-sm font-medium text-blue-500 uppercase mb-2">
              AI Suggested Response
            </h3>
            <p className="text-gray-700 whitespace-pre-wrap italic">{rfi.ai_suggested_response}</p>
          </div>
        )}
      </div>

      {/* Details Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-sm font-medium text-gray-500 uppercase mb-3">Details</h3>
          <dl className="space-y-2 text-sm">
            {rfi.spec_section && (
              <div className="flex justify-between">
                <dt className="text-gray-500">Spec Section</dt>
                <dd className="text-gray-900">{rfi.spec_section}</dd>
              </div>
            )}
            {rfi.drawing_reference && (
              <div className="flex justify-between">
                <dt className="text-gray-500">Drawing Reference</dt>
                <dd className="text-gray-900">{rfi.drawing_reference}</dd>
              </div>
            )}
            <div className="flex justify-between">
              <dt className="text-gray-500">Days Open</dt>
              <dd className="text-gray-900">{rfi.days_open ?? "-"}</dd>
            </div>
            {rfi.due_date && (
              <div className="flex justify-between">
                <dt className="text-gray-500 flex items-center gap-1">
                  <Calendar className="h-3 w-3" />
                  Due Date
                </dt>
                <dd className="text-gray-900">{new Date(rfi.due_date).toLocaleDateString()}</dd>
              </div>
            )}
          </dl>
        </div>

        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-sm font-medium text-gray-500 uppercase mb-3">Impact</h3>
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-gray-500 flex items-center gap-1">
                <DollarSign className="h-3 w-3" />
                Cost Impact
              </dt>
              <dd className="text-gray-900">
                {rfi.cost_impact ? "Yes" : "No"}
                {rfi.cost_impact_amount != null && ` ($${rfi.cost_impact_amount.toLocaleString()})`}
              </dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-gray-500 flex items-center gap-1">
                <Clock className="h-3 w-3" />
                Schedule Impact
              </dt>
              <dd className="text-gray-900">
                {rfi.schedule_impact ? "Yes" : "No"}
                {rfi.schedule_impact_days != null && ` (${rfi.schedule_impact_days} days)`}
              </dd>
            </div>
          </dl>
        </div>
      </div>

      {/* Responses Timeline */}
      <div className="bg-white rounded-lg border border-gray-200 p-6 mb-6">
        <h3 className="text-sm font-medium text-gray-500 uppercase mb-4">
          Responses ({rfi.responses.length})
        </h3>
        {rfi.responses.length === 0 ? (
          <p className="text-sm text-gray-400">No responses yet.</p>
        ) : (
          <div className="space-y-4">
            {rfi.responses.map((resp) => (
              <div key={resp.id} className="border-l-2 border-blue-200 pl-4 py-2">
                <div className="flex items-center gap-2 mb-1">
                  <span
                    className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                      resp.status === "approved"
                        ? "bg-green-100 text-green-800"
                        : "bg-yellow-100 text-yellow-800"
                    }`}
                  >
                    {resp.status}
                  </span>
                  <span className="text-xs text-gray-400">
                    {new Date(resp.responded_at).toLocaleString()}
                  </span>
                </div>
                <p className="text-sm text-gray-900 whitespace-pre-wrap">{resp.response_text}</p>
              </div>
            ))}
          </div>
        )}

        {/* Response Form */}
        {rfi.status !== "closed" && rfi.status !== "void" && (
          <div className="mt-4 pt-4 border-t border-gray-200">
            <RfiResponseForm projectId={projectId} rfiId={rfiId} onSuccess={handleResponseAdded} />
          </div>
        )}
      </div>

      {/* Attachments */}
      {rfi.attachments.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-sm font-medium text-gray-500 uppercase mb-3 flex items-center gap-2">
            <Paperclip className="h-4 w-4" />
            Attachments ({rfi.attachments.length})
          </h3>
          <ul className="divide-y divide-gray-100">
            {rfi.attachments.map((att) => (
              <li key={att.id} className="flex items-center justify-between py-2">
                <div>
                  <p className="text-sm text-gray-900">{att.file_name}</p>
                  <p className="text-xs text-gray-400">
                    {att.file_size_bytes ? `${(att.file_size_bytes / 1024).toFixed(1)} KB` : ""}
                    {att.file_type ? ` - ${att.file_type}` : ""}
                  </p>
                </div>
                {att.download_url && (
                  <a
                    href={
                      att.download_url?.startsWith("https://") || att.download_url?.startsWith("/")
                        ? att.download_url
                        : "#"
                    }
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-sm text-blue-600 hover:text-blue-800"
                  >
                    Download
                  </a>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Auto-resolve result panel — shown after AI runs the 3-stage agent */}
      {autoResolveResult && (
        <div className="bg-white rounded-lg border border-blue-200 p-6 mb-6 shadow-sm">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <Sparkles className="h-5 w-5 text-blue-600" />
              <h3 className="text-sm font-medium text-gray-900 uppercase">
                AI Resolution — Stage {autoResolveResult.stage_reached}
              </h3>
            </div>
            <div className="flex items-center gap-2">
              {autoResolveResult.draft_confidence != null && (
                <span className="text-xs text-gray-500">
                  {(autoResolveResult.draft_confidence * 100).toFixed(0)}% confidence
                </span>
              )}
              {autoResolveResult.verification_passed && (
                <span className="inline-flex items-center gap-1 text-xs text-green-700">
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  Verified
                </span>
              )}
              <button
                onClick={() => setAutoResolveResult(null)}
                className="text-xs text-gray-400 hover:text-gray-600"
              >
                Dismiss
              </button>
            </div>
          </div>

          {autoResolveResult.is_unnecessary ? (
            <div className="rounded bg-yellow-50 border border-yellow-200 p-3 text-sm text-yellow-900">
              <strong>Stage 1 flagged this RFI as unnecessary.</strong>{" "}
              {autoResolveResult.unnecessary_source && (
                <>Already answered in {autoResolveResult.unnecessary_source}: </>
              )}
              {autoResolveResult.unnecessary_reason ?? ""}
            </div>
          ) : autoResolveResult.draft_response ? (
            <div>
              <div className="prose prose-sm max-w-none whitespace-pre-wrap text-gray-900">
                {autoResolveResult.draft_response}
              </div>
              {(autoResolveResult.hallucination_flags.length > 0 ||
                autoResolveResult.contradiction_flags.length > 0 ||
                autoResolveResult.completeness_flags.length > 0) && (
                <div className="mt-3 pt-3 border-t border-gray-200 space-y-1 text-xs">
                  {autoResolveResult.hallucination_flags.length > 0 && (
                    <div className="text-red-700">
                      Hallucination flags: {autoResolveResult.hallucination_flags.length}
                    </div>
                  )}
                  {autoResolveResult.contradiction_flags.length > 0 && (
                    <div className="text-orange-700">
                      Contradiction flags: {autoResolveResult.contradiction_flags.length}
                    </div>
                  )}
                  {autoResolveResult.completeness_flags.length > 0 && (
                    <div className="text-yellow-700">
                      Completeness flags: {autoResolveResult.completeness_flags.length}
                    </div>
                  )}
                </div>
              )}
            </div>
          ) : (
            <div className="text-sm text-gray-500">No draft was generated.</div>
          )}
        </div>
      )}

      {/* AI Draft Response modal */}
      {showDraftViewer && rfi && (
        <DraftResponseViewer
          projectId={projectId}
          rfiId={rfi.id}
          rfiSubject={rfi.subject}
          onClose={() => setShowDraftViewer(false)}
        />
      )}
    </div>
  );
}
