"use client";

import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { apiClient } from "@/lib/api-client";
import { CreateSubmittalDialog } from "@/components/submittals/create-submittal-dialog";
import { Download, Plus, AlertTriangle, Clock, CheckCircle2, FileCheck } from "lucide-react";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { toast } from "sonner";

interface SubmittalItem {
  id: string;
  project_id: string;
  submittal_number: string;
  title: string;
  submittal_type: string;
  spec_section: string | null;
  status: string;
  priority: string;
  revision_number: number;
  date_required: string | null;
  is_overdue: boolean;
  days_open: number | null;
  created_at: string;
}

interface SubmittalStats {
  total: number;
  not_submitted: number;
  pending_review: number;
  approved: number;
  approved_as_noted: number;
  revise_and_resubmit: number;
  rejected: number;
  closed: number;
  overdue: number;
  avg_review_days: number | null;
}

interface SubmittalListResponse {
  data: SubmittalItem[];
  meta: { cursor: string | null; has_more: boolean };
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

export default function SubmittalsPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const router = useRouter();
  const [statusFilter, setStatusFilter] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [showCreate, setShowCreate] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(timer);
  }, [search]);

  const { data: stats } = useQuery<SubmittalStats>({
    queryKey: ["submittal-stats", projectId],
    queryFn: () => apiClient.get<SubmittalStats>(`/api/v1/projects/${projectId}/submittals/stats`),
    enabled: !!projectId,
  });

  const queryParams = new URLSearchParams();
  if (statusFilter) queryParams.set("status", statusFilter);
  if (priorityFilter) queryParams.set("priority", priorityFilter);
  if (typeFilter) queryParams.set("type", typeFilter);
  if (debouncedSearch.trim()) queryParams.set("search", debouncedSearch.trim());

  const { data, isLoading, error } = useQuery<SubmittalListResponse>({
    queryKey: ["submittals", projectId, statusFilter, priorityFilter, typeFilter, debouncedSearch],
    queryFn: () =>
      apiClient.get<SubmittalListResponse>(
        `/api/v1/projects/${projectId}/submittals?${queryParams.toString()}`,
      ),
    enabled: !!projectId,
  });

  const submittals = data?.data ?? [];

  if (!projectId) return <NoProjectSelected />;

  const handleExport = async () => {
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const response = await fetch(`${baseUrl}/api/v1/projects/${projectId}/submittals/export`, {
        credentials: "include",
      });
      if (!response.ok) throw new Error(`Export failed: ${response.status}`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "submittals_export.csv";
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error("Failed to export submittals. Please try again.");
    }
  };

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Submittals</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Submittal tracking and approval workflow
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
            New Submittal
          </button>
        </div>
      </div>

      {/* Stats Cards */}
      {stats && (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-2">
              <FileCheck className="h-5 w-5 text-blue-500" />
              <p className="text-sm text-gray-500 dark:text-gray-400">Total</p>
            </div>
            <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">{stats.total}</p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-2">
              <Clock className="h-5 w-5 text-yellow-500" />
              <p className="text-sm text-gray-500 dark:text-gray-400">Pending Review</p>
            </div>
            <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">
              {stats.pending_review}
            </p>
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
              <CheckCircle2 className="h-5 w-5 text-green-500" />
              <p className="text-sm text-gray-500 dark:text-gray-400">Avg Review</p>
            </div>
            <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">
              {stats.avg_review_days != null ? `${stats.avg_review_days}d` : "N/A"}
            </p>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex gap-3 mb-4 flex-wrap">
        <select
          aria-label="Filter by status"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm dark:bg-gray-700 dark:text-gray-200"
        >
          <option value="">All Statuses</option>
          <option value="not_submitted">Not Submitted</option>
          <option value="pending_review">Pending Review</option>
          <option value="approved">Approved</option>
          <option value="approved_as_noted">Approved as Noted</option>
          <option value="revise_and_resubmit">Revise & Resubmit</option>
          <option value="rejected">Rejected</option>
          <option value="closed">Closed</option>
        </select>
        <select
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
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm dark:bg-gray-700 dark:text-gray-200"
        >
          <option value="">All Types</option>
          <option value="shop_drawing">Shop Drawing</option>
          <option value="product_data">Product Data</option>
          <option value="sample">Sample</option>
          <option value="mock_up">Mock-Up</option>
          <option value="test_report">Test Report</option>
          <option value="certificate">Certificate</option>
          <option value="other">Other</option>
        </select>
        <input
          type="text"
          placeholder="Search submittals..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm flex-1 max-w-xs dark:bg-gray-700 dark:text-gray-200"
        />
      </div>

      {/* Table */}
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
        {isLoading && (
          <div className="p-8 text-center text-gray-500 dark:text-gray-400">
            Loading submittals...
          </div>
        )}
        {error && (
          <div className="p-4 text-red-800 bg-red-50 rounded">Failed to load submittals</div>
        )}
        {!isLoading && submittals.length === 0 && (
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
                d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z"
              />
            </svg>
            <h3 className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">
              No submittals
            </h3>
            <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
              Get started by creating a new submittal.
            </p>
          </div>
        )}
        {!isLoading && submittals.length > 0 && (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
              <thead className="bg-gray-50 dark:bg-gray-900">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    SUB #
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Title
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Type
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Spec Section
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Status
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Priority
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Rev
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Date Required
                  </th>
                </tr>
              </thead>
              <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
                {submittals.map((sub) => (
                  <tr
                    key={sub.id}
                    className="hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer"
                    onClick={() => router.push(`/submittals/${sub.id}`)}
                  >
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900 dark:text-gray-200">
                      {sub.submittal_number}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-900 dark:text-gray-200 max-w-xs truncate">
                      {sub.title}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                      {sub.submittal_type.replace("_", " ")}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                      {sub.spec_section ?? "-"}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                          statusColors[sub.status] ?? "bg-gray-100 text-gray-800"
                        }`}
                      >
                        {sub.status.replace(/_/g, " ")}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                          priorityColors[sub.priority] ?? "bg-gray-100 text-gray-800"
                        }`}
                      >
                        {sub.priority}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                      {sub.revision_number}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                      {sub.date_required ? new Date(sub.date_required).toLocaleDateString() : "-"}
                      {sub.is_overdue && (
                        <AlertTriangle className="inline h-4 w-4 text-red-500 ml-1" />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {showCreate && (
        <CreateSubmittalDialog projectId={projectId!} onClose={() => setShowCreate(false)} />
      )}
    </div>
  );
}
