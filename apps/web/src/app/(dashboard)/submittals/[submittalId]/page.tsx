"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { apiClient } from "@/lib/api-client";
import {
  ArrowLeft,
  Calendar,
  Clock,
  Paperclip,
  AlertTriangle,
  RefreshCw,
  Sparkles,
  CheckCircle,
  Info,
  Loader2,
} from "lucide-react";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";

interface SubmittalReview {
  id: string;
  submittal_id: string;
  reviewer_id: string | null;
  review_action: string;
  comments: string | null;
  revision_number: number;
  reviewed_at: string;
  created_at: string;
}

interface SubmittalAttachment {
  id: string;
  file_name: string;
  file_type: string | null;
  file_size_bytes: number | null;
  download_url: string | null;
  uploaded_at: string;
}

interface AiReviewFinding {
  severity: "info" | "minor" | "major";
  text: string;
  spec_ref: string | null;
}

interface AiReviewSource {
  document_title: string;
  page_number: number | null;
  section: string | null;
}

interface AiReviewResult {
  recommendation: "no_exception_taken" | "approved_as_noted" | "revise_and_resubmit";
  summary: string;
  findings: AiReviewFinding[];
  confidence: number;
  sources: AiReviewSource[];
  model: string | null;
  error?: string;
}

interface SubmittalDetail {
  id: string;
  project_id: string;
  submittal_number: string;
  title: string;
  description: string | null;
  spec_section: string | null;
  spec_section_name: string | null;
  submittal_type: string;
  status: string;
  priority: string;
  revision_number: number;
  submitted_by: string | null;
  current_reviewer: string | null;
  ball_in_court: string | null;
  due_date: string | null;
  date_required: string | null;
  date_submitted: string | null;
  date_returned: string | null;
  lead_time_days: number | null;
  linked_rfi_ids: string[];
  review_chain: { user_id: string; role: string }[];
  is_overdue: boolean;
  days_open: number | null;
  reviews: SubmittalReview[];
  attachments: SubmittalAttachment[];
  created_at: string;
  updated_at: string;
}

const statusColors: Record<string, string> = {
  not_submitted: "bg-gray-100 text-gray-800",
  pending_review: "bg-yellow-100 text-yellow-800",
  approved: "bg-green-100 text-green-800",
  approved_as_noted: "bg-emerald-100 text-emerald-800",
  revise_and_resubmit: "bg-orange-100 text-orange-800",
  rejected: "bg-red-100 text-red-800",
  closed: "bg-gray-100 text-gray-600",
};

const priorityColors: Record<string, string> = {
  urgent: "bg-red-100 text-red-800",
  high: "bg-orange-100 text-orange-800",
  normal: "bg-blue-100 text-blue-800",
  low: "bg-gray-100 text-gray-600",
};

const actionColors: Record<string, string> = {
  approved: "bg-green-100 text-green-800",
  approved_as_noted: "bg-emerald-100 text-emerald-800",
  no_exception_taken: "bg-green-100 text-green-800",
  revise_and_resubmit: "bg-orange-100 text-orange-800",
  rejected: "bg-red-100 text-red-800",
};

export default function SubmittalDetailPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const params = useParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const submittalId = params.submittalId as string;

  const [reviewAction, setReviewAction] = useState("");
  const [reviewComments, setReviewComments] = useState("");
  const [submittingReview, setSubmittingReview] = useState(false);
  const [resubmitting, setResubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [aiReviewing, setAiReviewing] = useState(false);
  const [aiReview, setAiReview] = useState<AiReviewResult | null>(null);

  const {
    data: submittal,
    isLoading,
    error: loadError,
  } = useQuery<SubmittalDetail>({
    queryKey: ["submittal-detail", projectId, submittalId],
    queryFn: () =>
      apiClient.get<SubmittalDetail>(`/api/v1/projects/${projectId}/submittals/${submittalId}`),
    enabled: !!submittalId && !!projectId,
  });

  if (!projectId) return <NoProjectSelected />;

  const handleReview = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!reviewAction) return;
    setSubmittingReview(true);
    setError(null);
    try {
      await apiClient.post(`/api/v1/projects/${projectId}/submittals/${submittalId}/review`, {
        review_action: reviewAction,
        comments: reviewComments.trim() || null,
      });
      setReviewAction("");
      setReviewComments("");
      queryClient.invalidateQueries({ queryKey: ["submittal-detail", submittalId] });
      queryClient.invalidateQueries({ queryKey: ["submittals"] });
    } catch {
      setError("Failed to submit review.");
    } finally {
      setSubmittingReview(false);
    }
  };

  const handleAiReview = async () => {
    setAiReviewing(true);
    setAiReview(null);
    setError(null);
    try {
      const result = await apiClient.post<AiReviewResult>(
        `/api/v1/projects/${projectId}/submittals/${submittalId}/ai-review`,
        {},
        { timeoutMs: 180_000 },
      );
      setAiReview(result);
      if (result.recommendation && !reviewAction) {
        setReviewAction(result.recommendation);
      }
      if (result.summary && !reviewComments) {
        const findingLines = result.findings
          .map(
            (f) =>
              `- [${f.severity}] ${f.text}${f.spec_ref ? ` (${f.spec_ref})` : ""}`,
          )
          .join("\n");
        setReviewComments(
          [
            "AI-ASSISTED REVIEW (verify before submitting):",
            result.summary,
            findingLines,
          ]
            .filter(Boolean)
            .join("\n\n"),
        );
      }
    } catch (e) {
      setError(`AI review failed: ${(e as Error).message}`);
    } finally {
      setAiReviewing(false);
    }
  };

  const handleResubmit = async () => {
    setResubmitting(true);
    setError(null);
    try {
      await apiClient.post(`/api/v1/projects/${projectId}/submittals/${submittalId}/resubmit`, {
        notes: null,
      });
      queryClient.invalidateQueries({ queryKey: ["submittal-detail", submittalId] });
      queryClient.invalidateQueries({ queryKey: ["submittals"] });
    } catch {
      setError("Failed to resubmit.");
    } finally {
      setResubmitting(false);
    }
  };

  if (isLoading) {
    return (
      <div className="p-6">
        <div className="text-center text-gray-500 py-12">Loading submittal details...</div>
      </div>
    );
  }

  if (loadError || !submittal) {
    return (
      <div className="p-6">
        <div className="p-4 text-red-800 bg-red-50 rounded">Failed to load submittal details</div>
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
        Back to Submittals
      </button>

      <div className="flex items-start justify-between mb-6">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-gray-900">{submittal.submittal_number}</h1>
            <span
              className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                statusColors[submittal.status] ?? "bg-gray-100 text-gray-800"
              }`}
            >
              {submittal.status.replace(/_/g, " ")}
            </span>
            <span
              className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                priorityColors[submittal.priority] ?? "bg-gray-100 text-gray-800"
              }`}
            >
              {submittal.priority}
            </span>
            {submittal.is_overdue && (
              <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800">
                <AlertTriangle className="h-3 w-3" />
                Overdue
              </span>
            )}
          </div>
          <h2 className="text-lg text-gray-700 mt-1">{submittal.title}</h2>
          <p className="text-sm text-gray-500 mt-1">
            {submittal.submittal_type.replace(/_/g, " ")} | Rev {submittal.revision_number}
          </p>
        </div>
        <div className="flex gap-2">
          {(submittal.status === "revise_and_resubmit" || submittal.status === "rejected") && (
            <button
              onClick={handleResubmit}
              disabled={resubmitting}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
            >
              <RefreshCw className="h-4 w-4" />
              {resubmitting ? "Resubmitting..." : "Resubmit"}
            </button>
          )}
        </div>
      </div>

      {error && <div className="mb-4 p-3 text-sm text-red-800 bg-red-50 rounded">{error}</div>}

      {/* Description */}
      {submittal.description && (
        <div className="bg-white rounded-lg border border-gray-200 p-6 mb-6">
          <h3 className="text-sm font-medium text-gray-500 uppercase mb-2">Description</h3>
          <p className="text-gray-900 whitespace-pre-wrap">{submittal.description}</p>
        </div>
      )}

      {/* Details Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-sm font-medium text-gray-500 uppercase mb-3">Details</h3>
          <dl className="space-y-2 text-sm">
            {submittal.spec_section && (
              <div className="flex justify-between">
                <dt className="text-gray-500">Spec Section</dt>
                <dd className="text-gray-900">
                  {submittal.spec_section}
                  {submittal.spec_section_name && ` - ${submittal.spec_section_name}`}
                </dd>
              </div>
            )}
            <div className="flex justify-between">
              <dt className="text-gray-500">Type</dt>
              <dd className="text-gray-900">{submittal.submittal_type.replace(/_/g, " ")}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-gray-500">Revision</dt>
              <dd className="text-gray-900">{submittal.revision_number}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-gray-500">Days Open</dt>
              <dd className="text-gray-900">{submittal.days_open ?? "-"}</dd>
            </div>
            {submittal.date_required && (
              <div className="flex justify-between">
                <dt className="text-gray-500 flex items-center gap-1">
                  <Calendar className="h-3 w-3" />
                  Date Required
                </dt>
                <dd className="text-gray-900">
                  {new Date(submittal.date_required).toLocaleDateString()}
                </dd>
              </div>
            )}
            {submittal.due_date && (
              <div className="flex justify-between">
                <dt className="text-gray-500 flex items-center gap-1">
                  <Calendar className="h-3 w-3" />
                  Due Date
                </dt>
                <dd className="text-gray-900">
                  {new Date(submittal.due_date).toLocaleDateString()}
                </dd>
              </div>
            )}
            {submittal.lead_time_days != null && (
              <div className="flex justify-between">
                <dt className="text-gray-500 flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  Lead Time
                </dt>
                <dd className="text-gray-900">{submittal.lead_time_days} days</dd>
              </div>
            )}
          </dl>
        </div>

        {/* Linked RFIs */}
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-sm font-medium text-gray-500 uppercase mb-3">Linked RFIs</h3>
          {submittal.linked_rfi_ids.length === 0 ? (
            <p className="text-sm text-gray-400">No linked RFIs.</p>
          ) : (
            <ul className="space-y-1">
              {submittal.linked_rfi_ids.map((rfiId) => (
                <li key={rfiId}>
                  <button
                    onClick={() => router.push(`/rfis/${rfiId}`)}
                    className="text-sm text-blue-600 hover:text-blue-800"
                  >
                    {rfiId}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* AI-Assisted Review */}
      <div className="bg-gradient-to-br from-blue-50 to-indigo-50 dark:from-blue-900/20 dark:to-indigo-900/20 rounded-lg border border-blue-200 dark:border-blue-800 p-6 mb-6">
        <div className="flex items-start justify-between">
          <div>
            <h3 className="text-sm font-semibold text-gray-900 uppercase flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-blue-600" />
              AI Compliance Review
            </h3>
            <p className="text-xs text-gray-500 mt-1">
              Compares this submittal against project specs and produces an AI-assisted
              recommendation. Human reviewer signs off below.
            </p>
          </div>
          <button
            onClick={handleAiReview}
            disabled={aiReviewing}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            {aiReviewing ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Sparkles className="h-4 w-4" />
            )}
            {aiReviewing ? "Analyzing..." : aiReview ? "Re-run Review" : "Run AI Review"}
          </button>
        </div>

        {aiReview && (
          <div className="mt-4 space-y-3">
            <div className="flex items-center gap-3">
              <span
                className={`inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-semibold ${
                  aiReview.recommendation === "no_exception_taken"
                    ? "bg-green-100 text-green-800"
                    : aiReview.recommendation === "approved_as_noted"
                      ? "bg-emerald-100 text-emerald-800"
                      : "bg-orange-100 text-orange-800"
                }`}
              >
                <CheckCircle className="h-3.5 w-3.5" />
                {aiReview.recommendation.replace(/_/g, " ")}
              </span>
              <span className="text-xs text-gray-500">
                Confidence: {(aiReview.confidence * 100).toFixed(0)}%
              </span>
              {aiReview.model && (
                <span className="text-xs text-gray-400">{aiReview.model}</span>
              )}
            </div>

            {aiReview.summary && (
              <p className="text-sm text-gray-800 bg-white/70 dark:bg-gray-800/40 rounded p-3 border border-blue-100 dark:border-blue-900">
                {aiReview.summary}
              </p>
            )}

            {aiReview.findings.length > 0 && (
              <div>
                <p className="text-xs font-medium text-gray-500 uppercase mb-2">
                  Findings ({aiReview.findings.length})
                </p>
                <ul className="space-y-1">
                  {aiReview.findings.map((f, i) => (
                    <li
                      key={i}
                      className="flex items-start gap-2 text-sm bg-white/70 dark:bg-gray-800/40 rounded p-2 border border-blue-100 dark:border-blue-900"
                    >
                      <span
                        className={`shrink-0 inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase ${
                          f.severity === "major"
                            ? "bg-red-100 text-red-800"
                            : f.severity === "minor"
                              ? "bg-amber-100 text-amber-800"
                              : "bg-blue-100 text-blue-800"
                        }`}
                      >
                        {f.severity}
                      </span>
                      <div className="min-w-0">
                        <p className="text-gray-800">{f.text}</p>
                        {f.spec_ref && (
                          <p className="text-xs text-gray-500 mt-0.5">
                            ref: {f.spec_ref}
                          </p>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {aiReview.sources.length > 0 && (
              <div>
                <p className="text-xs font-medium text-gray-500 uppercase mb-2">
                  Spec Sources Considered
                </p>
                <ul className="space-y-1">
                  {aiReview.sources.slice(0, 5).map((src, i) => (
                    <li
                      key={i}
                      className="flex items-center gap-2 text-xs text-gray-700 bg-white/70 dark:bg-gray-800/40 rounded px-2 py-1 border border-blue-100 dark:border-blue-900"
                    >
                      <Info className="h-3 w-3 text-gray-400 shrink-0" />
                      <span className="truncate">
                        {src.document_title}
                        {src.section ? ` - ${src.section}` : ""}
                        {src.page_number != null ? ` (p. ${src.page_number})` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {aiReview.error && (
              <p className="text-xs text-red-700 bg-red-50 rounded p-2">{aiReview.error}</p>
            )}
          </div>
        )}
      </div>

      {/* Reviews Timeline */}
      <div className="bg-white rounded-lg border border-gray-200 p-6 mb-6">
        <h3 className="text-sm font-medium text-gray-500 uppercase mb-4">
          Reviews ({submittal.reviews.length})
        </h3>
        {submittal.reviews.length === 0 ? (
          <p className="text-sm text-gray-400">No reviews yet.</p>
        ) : (
          <div className="space-y-4">
            {submittal.reviews.map((review) => (
              <div key={review.id} className="border-l-2 border-blue-200 pl-4 py-2">
                <div className="flex items-center gap-2 mb-1">
                  <span
                    className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                      actionColors[review.review_action] ?? "bg-gray-100 text-gray-800"
                    }`}
                  >
                    {review.review_action.replace(/_/g, " ")}
                  </span>
                  <span className="text-xs text-gray-400">Rev {review.revision_number}</span>
                  <span className="text-xs text-gray-400">
                    {new Date(review.reviewed_at).toLocaleString()}
                  </span>
                </div>
                {review.comments && (
                  <p className="text-sm text-gray-900 whitespace-pre-wrap">{review.comments}</p>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Review Form */}
        {submittal.status === "pending_review" && (
          <div className="mt-4 pt-4 border-t border-gray-200">
            <form onSubmit={handleReview} className="space-y-3">
              <label className="block text-sm font-medium text-gray-700">Submit Review</label>
              <select
                value={reviewAction}
                onChange={(e) => setReviewAction(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                required
              >
                <option value="">Select action...</option>
                <option value="approved">Approved</option>
                <option value="approved_as_noted">Approved as Noted</option>
                <option value="no_exception_taken">No Exception Taken</option>
                <option value="revise_and_resubmit">Revise & Resubmit</option>
                <option value="rejected">Rejected</option>
              </select>
              <textarea
                value={reviewComments}
                onChange={(e) => setReviewComments(e.target.value)}
                rows={3}
                className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm"
                placeholder="Comments (optional)..."
              />
              <button
                type="submit"
                disabled={submittingReview || !reviewAction}
                className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
              >
                {submittingReview ? "Submitting..." : "Submit Review"}
              </button>
            </form>
          </div>
        )}
      </div>

      {/* Attachments */}
      {submittal.attachments.length > 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <h3 className="text-sm font-medium text-gray-500 uppercase mb-3 flex items-center gap-2">
            <Paperclip className="h-4 w-4" />
            Attachments ({submittal.attachments.length})
          </h3>
          <ul className="divide-y divide-gray-100">
            {submittal.attachments.map((att) => (
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
    </div>
  );
}
