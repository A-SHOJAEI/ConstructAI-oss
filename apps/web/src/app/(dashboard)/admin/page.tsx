"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";

interface AdminUser {
  id: string;
  full_name: string;
  email: string;
  role: string;
  is_active: boolean;
  email_verified: boolean;
  mfa_enabled: boolean;
  created_at: string;
}

interface UsersResponse {
  items: AdminUser[];
  total: number;
}

const ROLES = [
  "org_admin",
  "project_admin",
  "project_manager",
  "superintendent",
  "engineer",
  "readonly",
] as const;

const roleColors: Record<string, string> = {
  org_admin: "bg-purple-100 text-purple-800",
  project_admin: "bg-indigo-100 text-indigo-800",
  project_manager: "bg-blue-100 text-blue-800",
  superintendent: "bg-teal-100 text-teal-800",
  engineer: "bg-green-100 text-green-800",
  readonly: "bg-gray-100 text-gray-800",
};

function roleLabel(role: string) {
  return role.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

type AdminTab = "users" | "annotations";

export default function AdminPage() {
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [editUser, setEditUser] = useState<AdminUser | null>(null);
  const [search, setSearch] = useState("");
  const [activeTab, setActiveTab] = useState<AdminTab>("users");

  const { data, isLoading, error } = useQuery<UsersResponse>({
    queryKey: ["admin-users", search],
    queryFn: () =>
      apiClient.get<UsersResponse>(
        `/api/v1/admin/users${search.trim() ? `?search=${encodeURIComponent(search.trim())}` : ""}`,
      ),
  });

  const createMutation = useMutation({
    mutationFn: (body: { email: string; full_name: string; role: string }) =>
      apiClient.post("/api/v1/admin/users", body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      setShowCreate(false);
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({
      id,
      ...body
    }: {
      id: string;
      full_name?: string;
      role?: string;
      is_active?: boolean;
    }) => apiClient.patch(`/api/v1/admin/users/${id}`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      setEditUser(null);
    },
  });

  const deactivateMutation = useMutation({
    mutationFn: (id: string) => apiClient.delete(`/api/v1/admin/users/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-users"] }),
  });

  const users = data?.items ?? [];

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Administration</h1>
          <p className="text-sm text-gray-500 mt-1">Manage users, roles, and system settings</p>
        </div>
        {activeTab === "users" && (
          <button
            onClick={() => setShowCreate(true)}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors text-sm font-medium"
          >
            + Add User
          </button>
        )}
      </div>

      {/* Tabs */}
      <div className="mb-6 border-b border-gray-200 dark:border-gray-700">
        <nav className="flex gap-4">
          {(["users", "annotations"] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`pb-2 text-sm font-medium border-b-2 transition-colors ${
                activeTab === tab
                  ? "border-blue-600 text-blue-600"
                  : "border-transparent text-gray-500 hover:text-gray-700"
              }`}
            >
              {tab === "users" ? "Users" : "Annotation Queue"}
            </button>
          ))}
        </nav>
      </div>

      {activeTab === "annotations" && <AnnotationQueueTab />}

      {activeTab !== "users" ? null : (
        <>
          {/* Search */}
          <div className="mb-4">
            <input
              type="text"
              placeholder="Search by name or email..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full max-w-md px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {/* Loading */}
          {isLoading && (
            <div className="bg-white rounded-lg border border-gray-200 p-8 text-center">
              <p className="text-gray-500">Loading users...</p>
            </div>
          )}

          {/* Error */}
          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-800">
              <p className="font-medium">Failed to load users</p>
              <p className="text-sm mt-1">{(error as Error).message}</p>
            </div>
          )}

          {/* Empty State */}
          {!isLoading && !error && users.length === 0 && (
            <div className="text-center py-12 bg-white rounded-lg border border-gray-200">
              <h3 className="text-lg font-medium text-gray-900 mb-2">No users found</h3>
              <p className="text-sm text-gray-500">Add users to manage access to the platform.</p>
            </div>
          )}

          {/* User Table */}
          {!isLoading && !error && users.length > 0 && (
            <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Name
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Email
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Role
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Status
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {users.map((u) => (
                    <tr key={u.id} className="hover:bg-gray-50">
                      <td className="px-6 py-4 whitespace-nowrap">
                        <div className="flex items-center gap-3">
                          <div className="w-8 h-8 bg-blue-600 rounded-full flex items-center justify-center text-white text-xs font-bold">
                            {(u.full_name || "U").charAt(0).toUpperCase()}
                          </div>
                          <span className="text-sm font-medium text-gray-900">{u.full_name}</span>
                        </div>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                        {u.email}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <span
                          className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                            roleColors[u.role] ?? "bg-gray-100 text-gray-800"
                          }`}
                        >
                          {roleLabel(u.role)}
                        </span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap">
                        <span
                          className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                            u.is_active ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"
                          }`}
                        >
                          {u.is_active ? "Active" : "Inactive"}
                        </span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-right text-sm">
                        <button
                          onClick={() => setEditUser(u)}
                          className="text-blue-600 hover:text-blue-800 mr-3"
                        >
                          Edit
                        </button>
                        {u.is_active && (
                          <button
                            onClick={() => {
                              if (confirm(`Deactivate ${u.full_name}? They will lose access.`)) {
                                deactivateMutation.mutate(u.id);
                              }
                            }}
                            className="text-red-600 hover:text-red-800"
                          >
                            Deactivate
                          </button>
                        )}
                        {!u.is_active && (
                          <button
                            onClick={() => updateMutation.mutate({ id: u.id, is_active: true })}
                            className="text-green-600 hover:text-green-800"
                          >
                            Reactivate
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data && (
                <div className="px-6 py-3 bg-gray-50 border-t border-gray-200 text-sm text-gray-500">
                  Showing {users.length} of {data.total} users
                </div>
              )}
            </div>
          )}

          {/* Create User Dialog */}
          {showCreate && (
            <CreateUserDialog
              onClose={() => setShowCreate(false)}
              onSubmit={(body) => createMutation.mutate(body)}
              isLoading={createMutation.isPending}
              error={createMutation.error?.message}
            />
          )}

          {/* Edit User Dialog */}
          {editUser && (
            <EditUserDialog
              user={editUser}
              onClose={() => setEditUser(null)}
              onSubmit={(body) => updateMutation.mutate({ id: editUser.id, ...body })}
              isLoading={updateMutation.isPending}
              error={updateMutation.error?.message}
            />
          )}
        </>
      )}
    </div>
  );
}

function CreateUserDialog({
  onClose,
  onSubmit,
  isLoading,
  error,
}: {
  onClose: () => void;
  onSubmit: (body: { email: string; full_name: string; role: string }) => void;
  isLoading: boolean;
  error?: string;
}) {
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [role, setRole] = useState<string>("readonly");

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
      role="dialog"
      aria-modal="true"
      aria-labelledby="create-user-dialog-title"
      onKeyDown={(e) => {
        if (e.key === "Tab") {
          const focusable = e.currentTarget.querySelectorAll<HTMLElement>(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
          );
          if (focusable.length === 0) return;
          const first = focusable[0];
          const last = focusable[focusable.length - 1];
          if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
        }
        if (e.key === "Escape") {
          onClose();
        }
      }}
    >
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6">
        <h2 id="create-user-dialog-title" className="text-lg font-semibold mb-4">
          Add User
        </h2>
        {error && <div className="mb-3 p-2 bg-red-50 text-red-700 text-sm rounded">{error}</div>}
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Full Name</label>
            <input
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
              placeholder="Jane Doe"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
              placeholder="jane@example.com"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Role</label>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {roleLabel(r)}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="flex justify-end gap-3 mt-6">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">
            Cancel
          </button>
          <button
            onClick={() => onSubmit({ email, full_name: fullName, role })}
            disabled={isLoading || !email || !fullName}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            {isLoading ? "Creating..." : "Create User"}
          </button>
        </div>
      </div>
    </div>
  );
}

function EditUserDialog({
  user,
  onClose,
  onSubmit,
  isLoading,
  error,
}: {
  user: AdminUser;
  onClose: () => void;
  onSubmit: (body: { full_name?: string; role?: string }) => void;
  isLoading: boolean;
  error?: string;
}) {
  const [fullName, setFullName] = useState(user.full_name);
  const [role, setRole] = useState(user.role);

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
      role="dialog"
      aria-modal="true"
      aria-labelledby="edit-user-dialog-title"
      onKeyDown={(e) => {
        if (e.key === "Tab") {
          const focusable = e.currentTarget.querySelectorAll<HTMLElement>(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
          );
          if (focusable.length === 0) return;
          const first = focusable[0];
          const last = focusable[focusable.length - 1];
          if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
        }
        if (e.key === "Escape") {
          onClose();
        }
      }}
    >
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6">
        <h2 id="edit-user-dialog-title" className="text-lg font-semibold mb-4">
          Edit User
        </h2>
        {error && <div className="mb-3 p-2 bg-red-50 text-red-700 text-sm rounded">{error}</div>}
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Full Name</label>
            <input
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Role</label>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {roleLabel(r)}
                </option>
              ))}
            </select>
          </div>
          <div className="text-sm text-gray-500">Email: {user.email} (cannot be changed)</div>
        </div>
        <div className="flex justify-end gap-3 mt-6">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">
            Cancel
          </button>
          <button
            onClick={() => onSubmit({ full_name: fullName, role })}
            disabled={isLoading}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            {isLoading ? "Saving..." : "Save Changes"}
          </button>
        </div>
      </div>
    </div>
  );
}

interface AnnotationSample {
  image_path: string;
  uncertainty: number;
  pre_annotations: Array<{
    class_name: string;
    confidence: number;
    bbox: number[];
  }>;
}

function AnnotationQueueTab() {
  const [budget, setBudget] = useState(50);
  const [strategy, setStrategy] = useState<"uncertainty" | "random" | "diverse">("uncertainty");

  const generateMutation = useMutation<{
    batch_id: string;
    total_images: number;
    images: AnnotationSample[];
  }>({
    mutationFn: () =>
      apiClient.post("/api/v1/evaluation/annotation-batch", {
        budget,
        strategy,
      }),
  });

  const batch = generateMutation.data;

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-800">
        <h3 className="text-sm font-semibold text-gray-900 dark:text-white mb-3">
          Generate Annotation Batch
        </h3>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Budget (images)</label>
            <input
              type="number"
              value={budget}
              onChange={(e) => setBudget(Number(e.target.value))}
              min={10}
              max={500}
              className="w-24 rounded border border-gray-300 px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-700 dark:text-white"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Strategy</label>
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value as typeof strategy)}
              className="rounded border border-gray-300 px-2 py-1.5 text-sm dark:border-gray-600 dark:bg-gray-700 dark:text-white"
            >
              <option value="uncertainty">Uncertainty Sampling</option>
              <option value="random">Random</option>
              <option value="diverse">Diverse Coverage</option>
            </select>
          </div>
          <button
            onClick={() => generateMutation.mutate()}
            disabled={generateMutation.isPending}
            className="rounded bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {generateMutation.isPending ? "Generating..." : "Generate Batch"}
          </button>
        </div>
      </div>

      {generateMutation.isError && (
        <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-700 dark:bg-red-900/20 dark:text-red-400">
          Failed to generate batch. Ensure model predictions are available.
        </div>
      )}

      {batch && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-sm text-gray-700 dark:text-gray-300">
              Batch <span className="font-mono">{batch.batch_id}</span> — {batch.total_images}{" "}
              images
            </p>
          </div>

          <div className="overflow-hidden rounded-lg border border-gray-200 dark:border-gray-700">
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
              <thead className="bg-gray-50 dark:bg-gray-800">
                <tr>
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                    Image
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                    Uncertainty
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                    Pre-annotations
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 bg-white dark:divide-gray-700 dark:bg-gray-900">
                {batch.images.slice(0, 20).map((img, idx) => (
                  <tr key={idx} className="hover:bg-gray-50 dark:hover:bg-gray-800">
                    <td className="px-4 py-2 text-sm font-mono text-gray-700 dark:text-gray-300">
                      {img.image_path.split("/").pop()}
                    </td>
                    <td className="px-4 py-2">
                      <div className="flex items-center gap-2">
                        <div className="h-2 w-16 rounded-full bg-gray-200 dark:bg-gray-700">
                          <div
                            className="h-2 rounded-full bg-orange-500"
                            style={{ width: `${img.uncertainty * 100}%` }}
                          />
                        </div>
                        <span className="text-xs text-gray-500">
                          {(img.uncertainty * 100).toFixed(1)}%
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-2 text-xs text-gray-500">
                      {img.pre_annotations.length} detections
                      {img.pre_annotations.length > 0 && (
                        <span className="ml-1 text-gray-400">
                          ({[...new Set(img.pre_annotations.map((a) => a.class_name))].join(", ")})
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {batch.total_images > 20 && (
              <div className="bg-gray-50 px-4 py-2 text-xs text-gray-500 dark:bg-gray-800">
                Showing 20 of {batch.total_images} images
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
