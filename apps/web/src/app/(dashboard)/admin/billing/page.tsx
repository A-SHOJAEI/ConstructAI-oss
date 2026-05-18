"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";

interface UsageSummary {
  org_id: string;
  period_start: string;
  period_end: string;
  api_calls: number;
  ai_inferences: number;
  storage_bytes: number;
  active_users: number;
  projects: number;
  documents_processed: number;
  safety_alerts: number;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
}

function formatNumber(n: number): string {
  return n.toLocaleString();
}

export default function BillingPage() {
  const { data: usage, isLoading } = useQuery<UsageSummary>({
    queryKey: ["usage-summary"],
    queryFn: () => apiClient.get("/api/v1/admin/usage"),
  });

  const metrics = [
    {
      label: "API Calls",
      value: usage ? formatNumber(usage.api_calls) : "—",
      description: "Total API requests this billing period",
    },
    {
      label: "AI Inferences",
      value: usage ? formatNumber(usage.ai_inferences) : "—",
      description: "Safety detection, document classification, RAG queries",
    },
    {
      label: "Storage",
      value: usage ? formatBytes(usage.storage_bytes) : "—",
      description: "Documents, images, and model artifacts",
    },
    {
      label: "Documents Processed",
      value: usage ? formatNumber(usage.documents_processed) : "—",
      description: "PDFs, IFC files, and specs ingested",
    },
    {
      label: "Active Users",
      value: usage ? formatNumber(usage.active_users) : "—",
      description: "Users who logged in this period",
    },
    {
      label: "Projects",
      value: usage ? formatNumber(usage.projects) : "—",
      description: "Active projects in organization",
    },
  ];

  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Usage & Billing</h1>
        <p className="text-sm text-gray-500">
          {usage ? `${usage.period_start} to ${usage.period_end}` : "Current billing period"}
        </p>
      </div>

      {isLoading ? (
        <div className="rounded-lg border bg-white p-8 text-center dark:border-gray-700 dark:bg-gray-800">
          <p className="text-gray-500">Loading usage data...</p>
        </div>
      ) : (
        <>
          {/* Metrics Grid */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {metrics.map((metric) => (
              <div
                key={metric.label}
                className="rounded-lg border bg-white p-4 dark:border-gray-700 dark:bg-gray-800"
              >
                <p className="text-sm font-medium text-gray-500 dark:text-gray-400">
                  {metric.label}
                </p>
                <p className="mt-1 text-2xl font-bold text-gray-900 dark:text-white">
                  {metric.value}
                </p>
                <p className="mt-1 text-xs text-gray-400">{metric.description}</p>
              </div>
            ))}
          </div>

          {/* Plan Info */}
          <div className="rounded-lg border bg-white p-6 dark:border-gray-700 dark:bg-gray-800">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Current Plan</h2>
            <div className="mt-3 flex items-center justify-between">
              <div>
                <p className="text-sm text-gray-600 dark:text-gray-300">Enterprise Plan</p>
                <p className="text-xs text-gray-400">
                  Unlimited users, projects, and AI inferences
                </p>
              </div>
              <span className="rounded-full bg-green-100 px-3 py-1 text-xs font-medium text-green-800 dark:bg-green-900/30 dark:text-green-400">
                Active
              </span>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
