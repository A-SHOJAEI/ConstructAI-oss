"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiClient } from "@/lib/api-client";
import { useProjectStore, type Project } from "@/stores/project-store";
import { Check, X } from "lucide-react";

interface ProjectsResponse {
  data: Project[];
  meta?: { cursor: string | null; has_more: boolean };
}

interface ProjectCreatePayload {
  name: string;
  project_number?: string;
  type?: string;
  address?: string;
  contract_value?: number;
  start_date?: string;
  end_date?: string;
}

const statusColors: Record<string, string> = {
  active: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400",
  planning: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
  on_hold: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400",
  completed: "bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300",
  cancelled: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
};

const projectTypes = [
  "commercial",
  "residential",
  "industrial",
  "infrastructure",
  "renovation",
  "mixed_use",
];

function formatCurrency(value: number | null | undefined): string {
  if (value === null || value === undefined) return "N/A";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value);
}

function LoadingSkeleton() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
      {Array.from({ length: 6 }).map((_, i) => (
        <div
          key={i}
          className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-6 animate-pulse"
        >
          <div className="h-5 bg-gray-200 dark:bg-gray-700 rounded w-3/4 mb-3" />
          <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded w-1/4 mb-4" />
          <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded w-1/2" />
        </div>
      ))}
    </div>
  );
}

function CreateProjectDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();

  const [form, setForm] = useState<ProjectCreatePayload>({ name: "" });

  const mutation = useMutation({
    mutationFn: (payload: ProjectCreatePayload) =>
      apiClient.post<Project>("/api/v1/projects/", payload),
    onSuccess: (project) => {
      toast.success(`Project "${project.name}" created successfully.`);
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      setForm({ name: "" });
      onClose();
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to create project");
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.name.trim()) {
      toast.error("Project name is required.");
      return;
    }
    if (form.name.trim().length < 3) {
      toast.error("Project name must be at least 3 characters.");
      return;
    }
    if (
      form.contract_value !== undefined &&
      form.contract_value !== null &&
      form.contract_value < 0
    ) {
      toast.error("Contract value must be a positive number.");
      return;
    }
    if (form.start_date && form.end_date && new Date(form.end_date) <= new Date(form.start_date)) {
      toast.error("End date must be after start date.");
      return;
    }
    // Build the payload, omitting empty optional fields
    const payload: ProjectCreatePayload = { name: form.name.trim() };
    if (form.project_number?.trim()) payload.project_number = form.project_number.trim();
    if (form.type) payload.type = form.type;
    if (form.address?.trim()) payload.address = form.address.trim();
    if (form.contract_value && form.contract_value > 0)
      payload.contract_value = form.contract_value;
    if (form.start_date) payload.start_date = form.start_date;
    if (form.end_date) payload.end_date = form.end_date;
    mutation.mutate(payload);
  }

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between p-6 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
            Create New Project
          </h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          {/* Project Name */}
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Project Name <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm"
              placeholder="e.g. Downtown Office Tower"
              required
            />
          </div>

          {/* Project Number */}
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Project Number
            </label>
            <input
              type="text"
              value={form.project_number ?? ""}
              onChange={(e) => setForm({ ...form, project_number: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm"
              placeholder="e.g. PRJ-2026-001"
            />
          </div>

          {/* Type */}
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Project Type
            </label>
            <select
              value={form.type ?? ""}
              onChange={(e) => setForm({ ...form, type: e.target.value || undefined })}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm"
            >
              <option value="">Select type...</option>
              {projectTypes.map((t) => (
                <option key={t} value={t}>
                  {t.replace("_", " ").replace(/\b\w/g, (c) => c.toUpperCase())}
                </option>
              ))}
            </select>
          </div>

          {/* Address */}
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Address
            </label>
            <input
              type="text"
              value={form.address ?? ""}
              onChange={(e) => setForm({ ...form, address: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm"
              placeholder="123 Main St, City, State"
            />
          </div>

          {/* Contract Value */}
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Contract Value ($)
            </label>
            <input
              type="number"
              min="0"
              step="1000"
              value={form.contract_value ?? ""}
              onChange={(e) =>
                setForm({
                  ...form,
                  contract_value: e.target.value ? Number(e.target.value) : undefined,
                })
              }
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm"
              placeholder="5000000"
            />
          </div>

          {/* Dates */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Start Date
              </label>
              <input
                type="date"
                value={form.start_date ?? ""}
                onChange={(e) => setForm({ ...form, start_date: e.target.value || undefined })}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                End Date
              </label>
              <input
                type="date"
                value={form.end_date ?? ""}
                onChange={(e) => setForm({ ...form, end_date: e.target.value || undefined })}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm"
              />
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center justify-end gap-3 pt-4 border-t border-gray-200 dark:border-gray-700">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-600 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={mutation.isPending}
              className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {mutation.isPending ? "Creating..." : "Create Project"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function ProjectsPage() {
  const { selectedProjectId, setProject, validateProject } = useProjectStore();
  const [showCreateDialog, setShowCreateDialog] = useState(false);

  const { data, isLoading, error } = useQuery<ProjectsResponse>({
    queryKey: ["projects"],
    queryFn: () => apiClient.get<ProjectsResponse>("/api/v1/projects/"),
  });

  const projects = data?.data ?? [];

  // Invalidate the selected project if it was deleted or is no longer in the list
  useEffect(() => {
    if (data?.data) {
      validateProject(data.data.map((p) => p.id));
    }
  }, [data, validateProject]);

  function handleSelectProject(project: Project) {
    setProject(project);
    toast.success(`Selected: ${project.name}`);
    // Clicking a project just selects it as the current scope; the user
    // navigates to project-specific data (RFIs, safety, etc.) via the
    // sidebar at their own pace. (We previously force-redirected to
    // /rfis on click, which felt like a hijack.)
  }

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Projects</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Manage and monitor your construction projects
          </p>
        </div>
        <button
          onClick={() => setShowCreateDialog(true)}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors text-sm font-medium"
        >
          + New Project
        </button>
      </div>

      <CreateProjectDialog open={showCreateDialog} onClose={() => setShowCreateDialog(false)} />

      {isLoading && <LoadingSkeleton />}

      {error && (
        <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-4 text-red-800 dark:text-red-300">
          <p className="font-medium">Failed to load projects</p>
          <p className="text-sm mt-1">{(error as Error).message}</p>
        </div>
      )}

      {!isLoading && !error && projects.length === 0 && (
        <div className="text-center py-16 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
          <div className="text-gray-400 text-5xl mb-4">📁</div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-2">
            No projects yet
          </h3>
          <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
            Get started by creating your first project.
          </p>
          <button
            onClick={() => setShowCreateDialog(true)}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors text-sm font-medium"
          >
            Create Project
          </button>
        </div>
      )}

      {!isLoading && !error && projects.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {projects.map((project) => (
            <button
              key={project.id}
              onClick={() => handleSelectProject(project)}
              className={`bg-white dark:bg-gray-800 rounded-lg shadow-sm border p-6 hover:shadow-md transition-shadow cursor-pointer text-left w-full ${
                project.id === selectedProjectId
                  ? "border-blue-500 ring-2 ring-blue-100 dark:ring-blue-900"
                  : "border-gray-200 dark:border-gray-700"
              }`}
            >
              <div className="flex items-start justify-between mb-3">
                <h3 className="text-lg font-semibold text-gray-900 dark:text-white truncate pr-2">
                  {project.name}
                </h3>
                <div className="flex items-center gap-2">
                  {project.id === selectedProjectId && <Check className="h-4 w-4 text-blue-600" />}
                  <span
                    className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium whitespace-nowrap ${
                      statusColors[project.status] ?? "bg-gray-100 text-gray-800"
                    }`}
                  >
                    {project.status?.replace("_", " ")}
                  </span>
                </div>
              </div>
              <div className="border-t border-gray-100 dark:border-gray-700 pt-3 mt-auto">
                <div className="flex items-center justify-between text-sm">
                  <span className="text-gray-500 dark:text-gray-400">Contract Value</span>
                  <span className="font-semibold text-gray-900 dark:text-white">
                    {formatCurrency(project.contract_value)}
                  </span>
                </div>
                {project.start_date && (
                  <div className="flex items-center justify-between text-sm mt-1">
                    <span className="text-gray-500 dark:text-gray-400">Start</span>
                    <span className="text-gray-700 dark:text-gray-300">{project.start_date}</span>
                  </div>
                )}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
