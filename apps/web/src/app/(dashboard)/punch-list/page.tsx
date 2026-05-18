"use client";

import { useState, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { apiClient } from "@/lib/api-client";
import { CreatePunchListDialog } from "@/components/punch-list/create-punch-list-dialog";
import { Plus, Download, AlertTriangle, Clock, MapPin, Camera, ListChecks } from "lucide-react";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { toast } from "sonner";

interface PunchListItem {
  id: string;
  project_id: string;
  item_number: string;
  description: string;
  location: string | null;
  category: string | null;
  priority: string;
  status: string;
  assigned_to: string | null;
  due_date: string | null;
  completed_date: string | null;
  photos: unknown[];
  company: string | null;
  drawing_reference: string | null;
  gps_lat: number | null;
  gps_lon: number | null;
  created_at: string;
}

interface PunchListStats {
  total: number;
  open: number;
  in_progress: number;
  resolved: number;
  verified: number;
  by_priority: Record<string, number>;
  by_company: Record<string, number>;
  overdue: number;
}

interface PunchListResponse {
  data: PunchListItem[];
  meta: { cursor: string | null; has_more: boolean };
}

const statusColors: Record<string, string> = {
  open: "bg-red-100 text-red-800",
  in_progress: "bg-yellow-100 text-yellow-800",
  resolved: "bg-blue-100 text-blue-800",
  verified: "bg-green-100 text-green-800",
};

const priorityColors: Record<string, string> = {
  critical: "bg-red-100 text-red-800",
  high: "bg-orange-100 text-orange-800",
  medium: "bg-blue-100 text-blue-800",
  low: "bg-gray-100 text-gray-600",
};

export default function PunchListPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const router = useRouter();
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("");
  const [companyFilter] = useState("");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [showCreate, setShowCreate] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(timer);
  }, [search]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkUpdating, setBulkUpdating] = useState(false);

  const { data: stats } = useQuery<PunchListStats>({
    queryKey: ["punch-list-stats", projectId],
    queryFn: () => apiClient.get<PunchListStats>(`/api/v1/projects/${projectId}/punch-list/stats`),
    enabled: !!projectId,
  });

  const queryParams = new URLSearchParams();
  if (statusFilter) queryParams.set("status", statusFilter);
  if (priorityFilter) queryParams.set("priority", priorityFilter);
  if (companyFilter) queryParams.set("company", companyFilter);
  if (debouncedSearch.trim()) queryParams.set("search", debouncedSearch.trim());

  const { data, isLoading, error } = useQuery<PunchListResponse>({
    queryKey: [
      "punch-list",
      projectId,
      statusFilter,
      priorityFilter,
      companyFilter,
      debouncedSearch,
    ],
    queryFn: () =>
      apiClient.get<PunchListResponse>(
        `/api/v1/projects/${projectId}/punch-list?${queryParams.toString()}`,
      ),
    enabled: !!projectId,
  });

  const items = data?.data ?? [];

  if (!projectId) return <NoProjectSelected />;

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleBulkStatus = async (newStatus: string) => {
    if (selectedIds.size === 0) return;
    const confirmed = window.confirm(`Update ${selectedIds.size} item(s) to "${newStatus}"?`);
    if (!confirmed) return;
    setBulkUpdating(true);
    try {
      await apiClient.post(`/api/v1/projects/${projectId}/punch-list/bulk-status-update`, {
        item_ids: Array.from(selectedIds),
        status: newStatus,
      });
      setSelectedIds(new Set());
      queryClient.invalidateQueries({ queryKey: ["punch-list"] });
      queryClient.invalidateQueries({ queryKey: ["punch-list-stats"] });
    } catch {
      toast.error("Failed to update punch list items. Please try again.");
    } finally {
      setBulkUpdating(false);
    }
  };

  const handleExport = async () => {
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const response = await fetch(`${baseUrl}/api/v1/projects/${projectId}/punch-list/export`, {
        credentials: "include",
      });
      if (!response.ok) throw new Error(`Export failed: ${response.status}`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "punch_list_export.csv";
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error("Failed to export punch list. Please try again.");
    }
  };

  return (
    <div className="p-4 md:p-6 max-w-4xl mx-auto">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-6 gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Punch List</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Track and resolve punch list items
          </p>
        </div>
        <div className="flex gap-2 flex-wrap">
          <button
            onClick={handleExport}
            className="flex items-center gap-2 px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700"
          >
            <Download className="h-4 w-4" />
            Export
          </button>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-2 px-3 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
          >
            <Plus className="h-4 w-4" />
            New Item
          </button>
        </div>
      </div>

      {/* Stats Cards */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-3 text-center">
            <p className="text-2xl font-bold text-gray-900 dark:text-white">{stats.total}</p>
            <p className="text-xs text-gray-500 dark:text-gray-400">Total</p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-3 text-center">
            <p className="text-2xl font-bold text-red-600">{stats.open}</p>
            <p className="text-xs text-gray-500 dark:text-gray-400">Open</p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-3 text-center">
            <p className="text-2xl font-bold text-yellow-600">{stats.in_progress}</p>
            <p className="text-xs text-gray-500 dark:text-gray-400">In Progress</p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-3 text-center">
            <p className="text-2xl font-bold text-green-600">{stats.verified}</p>
            <p className="text-xs text-gray-500 dark:text-gray-400">Verified</p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-3 text-center">
            <p className="text-2xl font-bold text-red-600">{stats.overdue}</p>
            <p className="text-xs text-gray-500 dark:text-gray-400">Overdue</p>
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
          <option value="open">Open</option>
          <option value="in_progress">In Progress</option>
          <option value="resolved">Resolved</option>
          <option value="verified">Verified</option>
        </select>
        <select
          value={priorityFilter}
          onChange={(e) => setPriorityFilter(e.target.value)}
          className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm dark:bg-gray-700 dark:text-gray-200"
        >
          <option value="">All Priorities</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
        <input
          type="text"
          placeholder="Search items..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm flex-1 max-w-xs dark:bg-gray-700 dark:text-gray-200"
        />
      </div>

      {/* Bulk Actions */}
      {selectedIds.size > 0 && (
        <div className="flex items-center gap-3 mb-4 p-3 bg-blue-50 rounded-lg">
          <span className="text-sm font-medium text-blue-800">{selectedIds.size} selected</span>
          <button
            onClick={() => handleBulkStatus("in_progress")}
            disabled={bulkUpdating}
            className="px-3 py-1 text-xs font-medium text-yellow-800 bg-yellow-100 rounded hover:bg-yellow-200 disabled:opacity-50"
          >
            Mark In Progress
          </button>
          <button
            onClick={() => handleBulkStatus("resolved")}
            disabled={bulkUpdating}
            className="px-3 py-1 text-xs font-medium text-blue-800 bg-blue-100 rounded hover:bg-blue-200 disabled:opacity-50"
          >
            Mark Resolved
          </button>
          <button
            onClick={() => handleBulkStatus("verified")}
            disabled={bulkUpdating}
            className="px-3 py-1 text-xs font-medium text-green-800 bg-green-100 rounded hover:bg-green-200 disabled:opacity-50"
          >
            Mark Verified
          </button>
          <button
            onClick={() => setSelectedIds(new Set())}
            className="px-3 py-1 text-xs font-medium text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white"
          >
            Clear
          </button>
        </div>
      )}

      {/* Loading / Error */}
      {isLoading && (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-16 bg-gray-200 dark:bg-gray-700 rounded animate-pulse" />
          ))}
        </div>
      )}
      {error && (
        <div className="p-4 text-red-800 bg-red-50 rounded">Failed to load punch list items</div>
      )}
      {!isLoading && items.length === 0 && (
        <div className="text-center text-gray-500 dark:text-gray-400 py-12">
          No punch list items found. Create one to get started.
        </div>
      )}

      {/* Card List — mobile-friendly with photo thumbnails */}
      <div className="space-y-3">
        {items.map((item) => {
          const isOverdue =
            item.due_date &&
            new Date(item.due_date) < new Date() &&
            (item.status === "open" || item.status === "in_progress");

          return (
            <div
              key={item.id}
              className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 hover:shadow-md transition-shadow"
            >
              <div className="flex items-start gap-3">
                {/* Checkbox for bulk select */}
                <input
                  type="checkbox"
                  checked={selectedIds.has(item.id)}
                  onChange={() => toggleSelect(item.id)}
                  className="mt-1 h-4 w-4 rounded border-gray-300 text-blue-600"
                  onClick={(e) => e.stopPropagation()}
                />

                {/* Photo thumbnail */}
                <div
                  className="flex-shrink-0 w-16 h-16 bg-gray-100 dark:bg-gray-700 rounded flex items-center justify-center cursor-pointer"
                  onClick={() => router.push(`/punch-list/${item.id}`)}
                >
                  {item.photos.length > 0 ? (
                    <div className="relative w-full h-full">
                      <Camera className="h-6 w-6 text-gray-400 absolute inset-0 m-auto" />
                      <span className="absolute bottom-0 right-0 bg-blue-600 text-white text-xs px-1 rounded-tl">
                        {item.photos.length}
                      </span>
                    </div>
                  ) : (
                    <ListChecks className="h-6 w-6 text-gray-300" />
                  )}
                </div>

                {/* Content */}
                <div
                  className="flex-1 min-w-0 cursor-pointer"
                  onClick={() => router.push(`/punch-list/${item.id}`)}
                >
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-bold text-gray-900 dark:text-white">
                      {item.item_number}
                    </span>
                    <span
                      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                        statusColors[item.status] ?? "bg-gray-100 text-gray-800"
                      }`}
                    >
                      {item.status.replace(/_/g, " ")}
                    </span>
                    <span
                      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                        priorityColors[item.priority] ?? "bg-gray-100 text-gray-800"
                      }`}
                    >
                      {item.priority}
                    </span>
                    {isOverdue && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800">
                        <AlertTriangle className="h-3 w-3" />
                        Overdue
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-gray-900 dark:text-gray-200 mt-1 line-clamp-2">
                    {item.description}
                  </p>
                  <div className="flex items-center gap-3 mt-2 text-xs text-gray-500 dark:text-gray-400 flex-wrap">
                    {item.location && (
                      <span className="flex items-center gap-1">
                        <MapPin className="h-3 w-3" />
                        {item.location}
                      </span>
                    )}
                    {item.company && <span>{item.company}</span>}
                    {item.drawing_reference && <span>Dwg: {item.drawing_reference}</span>}
                    {item.due_date && (
                      <span className="flex items-center gap-1">
                        <Clock className="h-3 w-3" />
                        {new Date(item.due_date).toLocaleDateString()}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {showCreate && (
        <CreatePunchListDialog projectId={projectId!} onClose={() => setShowCreate(false)} />
      )}
    </div>
  );
}
