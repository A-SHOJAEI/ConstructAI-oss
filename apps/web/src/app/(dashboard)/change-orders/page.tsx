"use client";

import { useQuery } from "@tanstack/react-query";
import { controlsApi } from "@/lib/controls-api";
import type { ChangeOrderFull } from "@/lib/controls-api";
import { GitPullRequest, DollarSign, Calendar, AlertTriangle, Sparkles } from "lucide-react";
import { useState } from "react";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { ChangeOrderDetailModal } from "@/components/change-orders/change-order-detail-modal";

const statusColors: Record<string, string> = {
  draft: "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300",
  pending: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300",
  submitted: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300",
  approved: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300",
  rejected: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300",
  void: "bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400",
};

const tabs = ["All", "PCO", "COR", "Approved CO"] as const;
type Tab = (typeof tabs)[number];

function formatCurrency(val: number | null | undefined): string {
  if (val == null || Number.isNaN(Number(val))) return "\u2014";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(Number(val));
}

export default function ChangeOrdersPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const [activeTab, setActiveTab] = useState<Tab>("All");
  const [selectedCoId, setSelectedCoId] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["change-orders", projectId],
    queryFn: () => controlsApi.changeOrders(projectId!),
    enabled: !!projectId,
  });

  const { data: impact } = useQuery({
    queryKey: ["cumulative-impact", projectId],
    queryFn: () => controlsApi.cumulativeImpact(projectId!),
    enabled: !!projectId,
  });

  const all = data?.data ?? [];

  if (!projectId) return <NoProjectSelected />;

  const filtered =
    activeTab === "All"
      ? all
      : all.filter((co) => {
          const s = co.status.toLowerCase();
          if (activeTab === "PCO") return s === "draft" || s === "pending";
          if (activeTab === "COR") return s === "submitted";
          return s === "approved";
        });

  return (
    <div className="p-4 md:p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <GitPullRequest className="h-6 w-6 text-orange-600" />
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Change Orders</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            PCO / COR / CO lifecycle management
          </p>
        </div>
      </div>

      {/* Cumulative Impact Card */}
      {impact && (
        <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
          <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-4 shadow-sm">
            <p className="text-sm text-gray-500 dark:text-gray-400 mb-1">Original Contract</p>
            <p className="text-xl font-bold text-gray-900 dark:text-gray-100">
              {formatCurrency(impact.original_contract_sum)}
            </p>
          </div>
          <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-4 shadow-sm">
            <p className="text-sm text-gray-500 dark:text-gray-400 mb-1">Approved COs</p>
            <p className="text-xl font-bold text-green-600">
              {formatCurrency(impact.total_approved_amount)}
            </p>
          </div>
          <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-4 shadow-sm">
            <p className="text-sm text-gray-500 dark:text-gray-400 mb-1">Pending</p>
            <p className="text-xl font-bold text-amber-600">
              {formatCurrency(impact.total_pending_amount)}
            </p>
          </div>
          <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-4 shadow-sm">
            <p className="text-sm text-gray-500 dark:text-gray-400 mb-1">Revised Contract</p>
            <p className="text-xl font-bold text-gray-900 dark:text-gray-100">
              {formatCurrency(impact.revised_contract_sum)}
            </p>
          </div>
          <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-4 shadow-sm">
            <p className="text-sm text-gray-500 dark:text-gray-400 mb-1">Schedule Impact</p>
            <p className="text-xl font-bold text-red-600">
              {impact.total_schedule_impact_days} days
            </p>
          </div>
        </div>
      )}

      {/* Tabs + Table */}
      <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm overflow-hidden">
        <div className="border-b border-gray-200 dark:border-gray-700 px-4">
          <div className="flex gap-0">
            {tabs.map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === tab
                    ? "border-gray-900 text-gray-900 dark:border-gray-100 dark:text-gray-100"
                    : "border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300"
                }`}
              >
                {tab}
              </button>
            ))}
          </div>
        </div>

        {isLoading ? (
          <div className="p-8 text-center text-gray-400 dark:text-gray-500 text-sm">
            Loading change orders...
          </div>
        ) : filtered.length === 0 ? (
          <div className="p-8 text-center">
            <GitPullRequest className="h-10 w-10 text-gray-300 dark:text-gray-600 mx-auto mb-2" />
            <p className="text-gray-500 dark:text-gray-400 text-sm">No change orders found</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800">
                  <th className="text-left px-4 py-3 font-medium text-gray-600 dark:text-gray-400">
                    CO #
                  </th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600 dark:text-gray-400">
                    Title
                  </th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600 dark:text-gray-400">
                    Status
                  </th>
                  <th className="text-right px-4 py-3 font-medium text-gray-600 dark:text-gray-400">
                    Amount
                  </th>
                  <th className="text-right px-4 py-3 font-medium text-gray-600 dark:text-gray-400">
                    Approved
                  </th>
                  <th className="text-center px-4 py-3 font-medium text-gray-600 dark:text-gray-400">
                    Days Impact
                  </th>
                  <th className="text-center px-4 py-3 font-medium text-gray-600 dark:text-gray-400">
                    Risk
                  </th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600 dark:text-gray-400">
                    Date
                  </th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((co: ChangeOrderFull) => (
                  <tr
                    key={co.id}
                    onClick={() => setSelectedCoId(co.id)}
                    className="border-b border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer"
                  >
                    <td className="px-4 py-3 font-medium text-gray-900 dark:text-gray-100">
                      CO-{String(co.co_number).padStart(3, "0")}
                    </td>
                    <td className="px-4 py-3 text-gray-700 dark:text-gray-300 max-w-xs truncate">
                      {co.title}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium capitalize ${statusColors[co.status] ?? "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300"}`}
                      >
                        {co.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right text-gray-900 dark:text-gray-100">
                      {formatCurrency(co.original_amount)}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-900 dark:text-gray-100">
                      {formatCurrency(co.approved_amount)}
                    </td>
                    <td className="px-4 py-3 text-center">
                      {(co.schedule_impact_days ?? 0) > 0 ? (
                        <span className="flex items-center justify-center gap-1 text-red-600 font-medium">
                          <Calendar className="h-3 w-3" /> +{co.schedule_impact_days}
                        </span>
                      ) : (
                        <span className="text-gray-400 dark:text-gray-500">0</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-center">
                      {co.risk_score != null ? (
                        <span
                          className={`inline-flex items-center gap-1 text-xs font-medium ${
                            co.risk_score >= 7
                              ? "text-red-600"
                              : co.risk_score >= 4
                                ? "text-amber-600"
                                : "text-green-600"
                          }`}
                        >
                          <AlertTriangle className="h-3 w-3" /> {co.risk_score}
                        </span>
                      ) : (
                        <span className="text-gray-400 dark:text-gray-500">{"\u2014"}</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-gray-500 dark:text-gray-400">
                      {new Date(co.created_at).toLocaleDateString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* AI Scope Analysis affordance — visible call-out so users know
          the feature exists. Clicking any CO row opens the detail modal
          which has the "Run Analysis" button. */}
      <div className="bg-gradient-to-br from-blue-50 to-indigo-50 dark:from-blue-900/10 dark:to-indigo-900/10 rounded-lg border border-blue-200 dark:border-blue-800 p-4 flex items-center gap-3">
        <Sparkles className="h-5 w-5 text-blue-600 shrink-0" />
        <div className="text-sm text-gray-700 dark:text-gray-300">
          <strong className="text-gray-900 dark:text-white">AI-assisted scope analysis</strong>{" "}
          flags PCOs that are likely <em>not</em> additional work — for instance, if a
          clarification is already in the contract or in an answered RFI. Click any change-order
          row above to open its detail and run the analysis.
        </div>
      </div>

      {/* Pipeline Summary */}
      {impact && (
        <div className="bg-gray-50 dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-6 text-sm text-gray-600 dark:text-gray-400">
            <span>
              PCOs: <strong className="text-gray-900 dark:text-gray-100">{impact.pco_count}</strong>
            </span>
            <span>
              CORs: <strong className="text-gray-900 dark:text-gray-100">{impact.cor_count}</strong>
            </span>
            <span>
              Approved COs:{" "}
              <strong className="text-gray-900 dark:text-gray-100">{impact.co_count}</strong>
            </span>
            <span className="flex items-center gap-1">
              <DollarSign className="h-3.5 w-3.5" />
              Net Change:{" "}
              <strong className="text-gray-900 dark:text-gray-100">
                {formatCurrency(impact.total_approved_amount)}
              </strong>
            </span>
          </div>
        </div>
      )}
      {selectedCoId && (
        <ChangeOrderDetailModal
          coId={selectedCoId}
          onClose={() => setSelectedCoId(null)}
        />
      )}
    </div>
  );
}
