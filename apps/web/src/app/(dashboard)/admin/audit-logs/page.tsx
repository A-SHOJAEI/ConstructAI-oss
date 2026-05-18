"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { Pagination } from "@/components/pagination";

interface AuditLog {
  id: string;
  action: string;
  user_email: string;
  user_id: string;
  resource_type: string;
  resource_id: string;
  details: Record<string, unknown>;
  ip_address: string;
  user_agent: string;
  created_at: string;
}

interface AuditLogsResponse {
  items: AuditLog[];
  total: number;
}

const ACTION_COLORS: Record<string, string> = {
  login: "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400",
  logout: "bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300",
  register: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
  role_changed: "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400",
  resource_created: "bg-teal-100 text-teal-800 dark:bg-teal-900/30 dark:text-teal-400",
  resource_updated: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400",
  resource_deleted: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
};

const PAGE_SIZE = 25;

export default function AuditLogsPage() {
  const [page, setPage] = useState(1);
  const [actionFilter, setActionFilter] = useState("");
  const [search, setSearch] = useState("");

  const { data, isLoading } = useQuery<AuditLogsResponse>({
    queryKey: ["audit-logs", page, actionFilter, search],
    queryFn: () => {
      const params = new URLSearchParams({
        skip: String((page - 1) * PAGE_SIZE),
        limit: String(PAGE_SIZE),
      });
      if (actionFilter) params.set("action", actionFilter);
      if (search.trim()) params.set("search", search.trim());
      return apiClient.get(`/api/v1/admin/audit-logs?${params}`);
    },
  });

  const logs = data?.items ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="space-y-4 p-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Audit Logs</h1>
        <p className="text-sm text-gray-500">Track all user actions and system events</p>
      </div>

      {/* Filters */}
      <div className="flex flex-col gap-3 sm:flex-row">
        <input
          type="text"
          placeholder="Search by user or resource..."
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
          className="max-w-xs rounded-md border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-white"
        />
        <select
          value={actionFilter}
          onChange={(e) => {
            setActionFilter(e.target.value);
            setPage(1);
          }}
          className="rounded-md border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-white"
        >
          <option value="">All Actions</option>
          <option value="login">Login</option>
          <option value="logout">Logout</option>
          <option value="register">Register</option>
          <option value="role_changed">Role Changed</option>
          <option value="resource_created">Created</option>
          <option value="resource_updated">Updated</option>
          <option value="resource_deleted">Deleted</option>
        </select>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="rounded-lg border bg-white p-8 text-center dark:border-gray-700 dark:bg-gray-800">
          <p className="text-gray-500">Loading audit logs...</p>
        </div>
      ) : logs.length === 0 ? (
        <div className="rounded-lg border bg-white p-8 text-center dark:border-gray-700 dark:bg-gray-800">
          <p className="text-gray-500">No audit logs found.</p>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border dark:border-gray-700">
          <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
            <thead className="bg-gray-50 dark:bg-gray-800">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">
                  Timestamp
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">
                  Action
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">
                  User
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">
                  Resource
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">
                  IP Address
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">
                  Details
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 bg-white dark:divide-gray-700 dark:bg-gray-900">
              {logs.map((log) => (
                <tr key={log.id} className="hover:bg-gray-50 dark:hover:bg-gray-800">
                  <td className="whitespace-nowrap px-4 py-3 text-xs text-gray-500">
                    {new Date(log.created_at).toLocaleString()}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${
                        ACTION_COLORS[log.action] ??
                        "bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300"
                      }`}
                    >
                      {log.action.replace(/_/g, " ")}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700 dark:text-gray-300">
                    {log.user_email || log.user_id?.slice(0, 8)}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-500">
                    {log.resource_type}
                    {log.resource_id && (
                      <span className="ml-1 font-mono text-xs text-gray-400">
                        {log.resource_id.slice(0, 8)}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-gray-400">{log.ip_address}</td>
                  <td className="max-w-xs truncate px-4 py-3 text-xs text-gray-400">
                    {log.details ? JSON.stringify(log.details).slice(0, 80) : "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {total > PAGE_SIZE && (
        <Pagination page={page} total={total} pageSize={PAGE_SIZE} onChange={setPage} />
      )}
    </div>
  );
}
