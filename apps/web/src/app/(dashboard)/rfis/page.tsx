"use client";

import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { apiClient } from "@/lib/api-client";
import { CreateRfiDialog } from "@/components/rfis/create-rfi-dialog";
import { AIResolutionBadge } from "@/components/rfis/ai-resolution-badge";
import { DraftResponseViewer } from "@/components/rfis/draft-response-viewer";
import {
  Download,
  Plus,
  AlertTriangle,
  Clock,
  CheckCircle2,
  FileQuestion,
  Bot,
} from "lucide-react";
import { toast } from "sonner";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";

interface RFI {
  id: string;
  project_id: string;
  rfi_number: string;
  subject: string;
  question: string;
  status: string;
  priority: string;
  assigned_to: string | null;
  ball_in_court: string | null;
  due_date: string | null;
  is_overdue: boolean;
  days_open: number | null;
  ai_status?: "unnecessary" | "draft_available" | "auto_resolved" | null;
  created_at: string;
}

interface RFIStats {
  total: number;
  open: number;
  pending_review: number;
  answered: number;
  closed: number;
  overdue: number;
  avg_response_days: number | null;
  unnecessary_count?: number;
}

interface RFIListResponse {
  data: RFI[];
  meta: { cursor: string | null; has_more: boolean };
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

const isValidUUID = (id: string) =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);

export default function RFIsPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const router = useRouter();
  const [statusFilter, setStatusFilter] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("");
  const [search, setSearch] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [draftViewerRfi, setDraftViewerRfi] = useState<{ id: string; subject: string } | null>(
    null,
  );
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [sortColumn, setSortColumn] = useState<string>("");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("asc");

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(timer);
  }, [search]);

  const { data: stats } = useQuery<RFIStats>({
    queryKey: ["rfi-stats", projectId],
    queryFn: () => apiClient.get<RFIStats>(`/api/v1/projects/${projectId}/rfis/stats`),
    enabled: !!projectId && isValidUUID(projectId),
  });

  const queryParams = new URLSearchParams();
  if (statusFilter) queryParams.set("status", statusFilter);
  if (priorityFilter) queryParams.set("priority", priorityFilter);
  if (debouncedSearch.trim()) queryParams.set("search", debouncedSearch.trim());

  const { data, isLoading, error } = useQuery<RFIListResponse>({
    queryKey: ["rfis", projectId, statusFilter, priorityFilter, debouncedSearch],
    queryFn: () =>
      apiClient.get<RFIListResponse>(
        `/api/v1/projects/${projectId}/rfis?${queryParams.toString()}`,
      ),
    enabled: !!projectId && isValidUUID(projectId),
  });

  const rfisRaw = data?.data ?? [];

  const rfis = sortColumn
    ? [...rfisRaw].sort((a, b) => {
        let aVal: string | number = "";
        let bVal: string | number = "";
        if (sortColumn === "rfi_number") {
          aVal = a.rfi_number;
          bVal = b.rfi_number;
        } else if (sortColumn === "subject") {
          aVal = a.subject.toLowerCase();
          bVal = b.subject.toLowerCase();
        } else if (sortColumn === "status") {
          aVal = a.status;
          bVal = b.status;
        } else if (sortColumn === "priority") {
          const order: Record<string, number> = { urgent: 0, high: 1, normal: 2, low: 3 };
          aVal = order[a.priority] ?? 4;
          bVal = order[b.priority] ?? 4;
        } else if (sortColumn === "due_date") {
          aVal = a.due_date ? new Date(a.due_date).getTime() : 0;
          bVal = b.due_date ? new Date(b.due_date).getTime() : 0;
        } else if (sortColumn === "days_open") {
          aVal = a.days_open ?? 0;
          bVal = b.days_open ?? 0;
        }
        if (aVal < bVal) return sortDirection === "asc" ? -1 : 1;
        if (aVal > bVal) return sortDirection === "asc" ? 1 : -1;
        return 0;
      })
    : rfisRaw;

  if (!projectId) return <NoProjectSelected />;

  const handleExport = async () => {
    if (!projectId || !isValidUUID(projectId)) {
      toast.error("Invalid project ID");
      return;
    }
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const response = await fetch(`${baseUrl}/api/v1/projects/${projectId}/rfis/export`, {
        credentials: "include",
      });
      if (!response.ok) {
        throw new Error(`Export failed: ${response.status}`);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "rfis_export.csv";
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error("Failed to export RFIs. Please try again.");
    }
  };

  return (
    <div className="p-4 md:p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">RFIs</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Requests for Information — AI-powered resolution
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleExport}
            className="flex items-center gap-2 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700"
          >
            <Download className="h-4 w-4" />
            Export
          </button>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
          >
            <Plus className="h-4 w-4" />
            New RFI
          </button>
        </div>
      </div>

      {/* Stats Cards */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-2">
              <FileQuestion className="h-5 w-5 text-blue-500" />
              <p className="text-sm text-gray-500 dark:text-gray-400">Total</p>
            </div>
            <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">{stats.total}</p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-2">
              <Clock className="h-5 w-5 text-blue-500" />
              <p className="text-sm text-gray-500 dark:text-gray-400">Open</p>
            </div>
            <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">{stats.open}</p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-red-500" />
              <p className="text-sm text-gray-500 dark:text-gray-400">Overdue</p>
            </div>
            <p className="text-3xl font-bold text-red-600 mt-1">{stats.overdue}</p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-2">
              <Bot className="h-5 w-5 text-purple-500" />
              <p className="text-sm text-gray-500 dark:text-gray-400">AI Flagged</p>
            </div>
            <p className="text-3xl font-bold text-purple-600 mt-1">
              {stats.unnecessary_count ?? 0}
            </p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="h-5 w-5 text-green-500" />
              <p className="text-sm text-gray-500 dark:text-gray-400">Avg Response</p>
            </div>
            <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">
              {stats.avg_response_days != null ? `${stats.avg_response_days}d` : "N/A"}
            </p>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-3">
        <select
          aria-label="Filter by status"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm dark:bg-gray-700 dark:text-gray-200"
        >
          <option value="">All Statuses</option>
          <option value="draft">Draft</option>
          <option value="open">Open</option>
          <option value="pending_review">Pending Review</option>
          <option value="answered">Answered</option>
          <option value="closed">Closed</option>
        </select>
        <select
          aria-label="Filter by priority"
          value={priorityFilter}
          onChange={(e) => setPriorityFilter(e.target.value)}
          className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm dark:bg-gray-700 dark:text-gray-200"
        >
          <option value="">All Priorities</option>
          <option value="urgent">Urgent</option>
          <option value="high">High</option>
          <option value="normal">Normal</option>
          <option value="low">Low</option>
        </select>
        <input
          type="text"
          placeholder="Search RFIs..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm flex-1 max-w-xs dark:bg-gray-700 dark:text-gray-200"
        />
      </div>

      {/* Table */}
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
        {isLoading && (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">Loading RFIs...</div>
        )}
        {error && <div className="p-4 text-red-800 bg-red-50 rounded">Failed to load RFIs</div>}
        {!isLoading && rfis.length === 0 && (
          <div className="text-center py-12">
            <svg
              className="mx-auto h-12 w-12 text-gray-400"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={1.5}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M9.879 7.519c1.171-1.025 3.071-1.025 4.242 0 1.172 1.025 1.172 2.687 0 3.712-.203.179-.43.326-.67.442-.745.361-1.45.999-1.45 1.827v.75M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9 5.25h.008v.008H12v-.008z"
              />
            </svg>
            <h3 className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">No RFIs</h3>
            <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
              Get started by creating a new RFI.
            </p>
          </div>
        )}
        {!isLoading && rfis.length > 0 && (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
              <thead className="bg-gray-50 dark:bg-gray-900">
                <tr>
                  {[
                    { key: "rfi_number", label: "RFI #" },
                    { key: "subject", label: "Subject" },
                    { key: "status", label: "Status" },
                    { key: "priority", label: "Priority" },
                    { key: "", label: "AI Status" },
                    { key: "due_date", label: "Due Date" },
                    { key: "days_open", label: "Days Open" },
                  ].map((col, idx) => (
                    <th
                      key={idx}
                      className={`px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase ${col.key ? "cursor-pointer select-none hover:text-gray-700 dark:hover:text-gray-300" : ""}`}
                      onClick={() => {
                        if (!col.key) return;
                        if (sortColumn === col.key) {
                          setSortDirection((d) => (d === "asc" ? "desc" : "asc"));
                        } else {
                          setSortColumn(col.key);
                          setSortDirection("asc");
                        }
                      }}
                    >
                      <span className="inline-flex items-center gap-1">
                        {col.label}
                        {col.key && sortColumn === col.key && (
                          <svg className="h-3 w-3" viewBox="0 0 12 12" fill="currentColor">
                            {sortDirection === "asc" ? (
                              <path d="M6 2L10 8H2L6 2Z" />
                            ) : (
                              <path d="M6 10L2 4H10L6 10Z" />
                            )}
                          </svg>
                        )}
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
                {rfis.map((rfi) => (
                  <tr
                    key={rfi.id}
                    className="hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer"
                    onClick={() => router.push(`/rfis/${rfi.id}`)}
                  >
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900 dark:text-gray-200">
                      {rfi.rfi_number}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-900 dark:text-gray-200 max-w-xs truncate">
                      {rfi.subject}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${statusColors[rfi.status] ?? "bg-gray-100 text-gray-800"}`}
                      >
                        {rfi.status.replace("_", " ")}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${priorityColors[rfi.priority] ?? "bg-gray-100 text-gray-800"}`}
                      >
                        {rfi.priority}
                      </span>
                    </td>
                    <td
                      className="px-6 py-4 whitespace-nowrap"
                      onClick={(e) => {
                        if (rfi.ai_status === "draft_available") {
                          e.stopPropagation();
                          setDraftViewerRfi({ id: rfi.id, subject: rfi.subject });
                        }
                      }}
                    >
                      <AIResolutionBadge aiStatus={rfi.ai_status ?? null} />
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                      {rfi.due_date ? new Date(rfi.due_date).toLocaleDateString() : "-"}
                      {rfi.is_overdue && (
                        <AlertTriangle className="inline h-4 w-4 text-red-500 ml-1" />
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                      {rfi.days_open ?? "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showCreate && (
        <CreateRfiDialog projectId={projectId!} onClose={() => setShowCreate(false)} />
      )}

      {draftViewerRfi && (
        <DraftResponseViewer
          projectId={projectId!}
          rfiId={draftViewerRfi.id}
          rfiSubject={draftViewerRfi.subject}
          onClose={() => setDraftViewerRfi(null)}
        />
      )}
    </div>
  );
}
