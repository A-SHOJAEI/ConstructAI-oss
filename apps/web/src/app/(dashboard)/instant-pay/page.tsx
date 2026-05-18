"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { DollarSign, FileCheck, Zap, Settings } from "lucide-react";
import { toast } from "sonner";

interface PaymentTransaction {
  id: string;
  pay_app_number: number;
  amount: number;
  status: "draft" | "submitted" | "approved" | "paid" | "rejected";
  submitted_date: string | null;
  approved_date: string | null;
  paid_date: string | null;
  retainage_amount: number;
}

interface LienWaiverPackage {
  id: string;
  pay_app_number: number;
  subcontractor: string;
  waiver_type: "conditional" | "unconditional";
  amount: number;
  status: "pending" | "received" | "verified";
  due_date: string;
}

interface PaymentConfig {
  auto_generate_enabled: boolean;
  payment_terms_days: number;
  retainage_pct: number;
  integration_provider: string | null;
}

interface InstantPayData {
  transactions: PaymentTransaction[];
  lien_waivers: LienWaiverPackage[];
  config: PaymentConfig;
  total_billed: number;
  total_paid: number;
  total_outstanding: number;
}

const isValidUUID = (id: string) =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);

function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
  }).format(value);
}

const txStatusColors: Record<string, string> = {
  draft: "bg-gray-100 text-gray-800",
  submitted: "bg-blue-100 text-blue-800",
  approved: "bg-green-100 text-green-800",
  paid: "bg-emerald-100 text-emerald-800",
  rejected: "bg-red-100 text-red-800",
};

const waiverStatusColors: Record<string, string> = {
  pending: "bg-yellow-100 text-yellow-800",
  received: "bg-blue-100 text-blue-800",
  verified: "bg-green-100 text-green-800",
};

type Tab = "transactions" | "waivers" | "config";

export default function InstantPayPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<Tab>("transactions");

  const { data, isLoading, error } = useQuery<InstantPayData>({
    queryKey: ["instant-pay", projectId],
    queryFn: () => apiClient.get<InstantPayData>(`/api/v1/projects/${projectId}/instant-pay`),
    enabled: !!projectId && isValidUUID(projectId),
  });

  const generateMutation = useMutation({
    mutationFn: () => apiClient.post(`/api/v1/projects/${projectId}/instant-pay/auto-generate`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["instant-pay", projectId] });
      toast.success("Pay application generated");
    },
    onError: () => toast.error("Failed to generate pay application"),
  });

  if (!projectId) return <NoProjectSelected />;

  const transactions = data?.transactions ?? [];
  const lienWaivers = data?.lien_waivers ?? [];

  return (
    <div className="p-4 md:p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Instant Pay</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Auto-generate pay applications, track payments, and manage lien waivers
          </p>
        </div>
        <button
          onClick={() => generateMutation.mutate()}
          disabled={generateMutation.isPending}
          className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50"
        >
          <Zap className="h-4 w-4" />
          {generateMutation.isPending ? "Generating..." : "Auto-Generate Pay App"}
        </button>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <DollarSign className="h-5 w-5 text-green-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Total Billed</p>
          </div>
          <p className="text-2xl font-bold text-gray-900 dark:text-white mt-1">
            {isLoading ? "..." : formatCurrency(data?.total_billed ?? 0)}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <FileCheck className="h-5 w-5 text-emerald-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Total Paid</p>
          </div>
          <p className="text-2xl font-bold text-emerald-600 mt-1">
            {isLoading ? "..." : formatCurrency(data?.total_paid ?? 0)}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <DollarSign className="h-5 w-5 text-orange-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Outstanding</p>
          </div>
          <p className="text-2xl font-bold text-orange-600 mt-1">
            {isLoading ? "..." : formatCurrency(data?.total_outstanding ?? 0)}
          </p>
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200 dark:border-gray-700">
        <nav className="flex gap-4">
          {[
            { key: "transactions" as Tab, label: "Payment Transactions" },
            { key: "waivers" as Tab, label: "Lien Waivers" },
            { key: "config" as Tab, label: "Configuration" },
          ].map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`pb-3 px-1 text-sm font-medium border-b-2 transition-colors ${
                activeTab === tab.key
                  ? "border-green-600 text-green-600"
                  : "border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </div>

      {isLoading && (
        <div className="p-8 text-center text-gray-500 dark:text-gray-400">
          Loading payment data...
        </div>
      )}
      {error && (
        <div className="p-4 text-red-800 bg-red-50 rounded-lg">Failed to load payment data</div>
      )}

      {/* Transactions Table */}
      {!isLoading && !error && activeTab === "transactions" && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          {transactions.length === 0 ? (
            <div className="text-center py-12">
              <DollarSign className="mx-auto h-12 w-12 text-gray-400" />
              <h3 className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">
                No transactions
              </h3>
              <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                Generate a pay application to create a transaction.
              </p>
            </div>
          ) : (
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
              <thead className="bg-gray-50 dark:bg-gray-900">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Pay App #
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Amount
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Retainage
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Status
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Submitted
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Paid
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                {transactions.map((tx) => (
                  <tr key={tx.id} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                    <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white">
                      #{tx.pay_app_number}
                    </td>
                    <td className="px-6 py-4 text-sm text-right font-medium text-gray-900 dark:text-white">
                      {formatCurrency(tx.amount)}
                    </td>
                    <td className="px-6 py-4 text-sm text-right text-gray-500 dark:text-gray-400">
                      {formatCurrency(tx.retainage_amount)}
                    </td>
                    <td className="px-6 py-4">
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${txStatusColors[tx.status]}`}
                      >
                        {tx.status}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                      {tx.submitted_date ? new Date(tx.submitted_date).toLocaleDateString() : "-"}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                      {tx.paid_date ? new Date(tx.paid_date).toLocaleDateString() : "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Lien Waivers */}
      {!isLoading && !error && activeTab === "waivers" && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          {lienWaivers.length === 0 ? (
            <div className="text-center py-12">
              <FileCheck className="mx-auto h-12 w-12 text-gray-400" />
              <h3 className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">
                No lien waivers
              </h3>
              <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                Lien waivers will appear after payment processing.
              </p>
            </div>
          ) : (
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
              <thead className="bg-gray-50 dark:bg-gray-900">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Pay App #
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Subcontractor
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Type
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Amount
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Status
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Due
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                {lienWaivers.map((lw) => (
                  <tr key={lw.id} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                    <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white">
                      #{lw.pay_app_number}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-900 dark:text-white">
                      {lw.subcontractor}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400 capitalize">
                      {lw.waiver_type}
                    </td>
                    <td className="px-6 py-4 text-sm text-right text-gray-900 dark:text-white">
                      {formatCurrency(lw.amount)}
                    </td>
                    <td className="px-6 py-4">
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${waiverStatusColors[lw.status]}`}
                      >
                        {lw.status}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                      {new Date(lw.due_date).toLocaleDateString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Configuration */}
      {!isLoading && !error && activeTab === "config" && data?.config && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
          <div className="flex items-center gap-2 mb-4">
            <Settings className="h-5 w-5 text-gray-500" />
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
              Payment Configuration
            </h2>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div>
              <p className="text-sm text-gray-500 dark:text-gray-400">Auto-Generate Enabled</p>
              <p className="text-lg font-medium text-gray-900 dark:text-white">
                {data.config.auto_generate_enabled ? "Yes" : "No"}
              </p>
            </div>
            <div>
              <p className="text-sm text-gray-500 dark:text-gray-400">Payment Terms</p>
              <p className="text-lg font-medium text-gray-900 dark:text-white">
                {data.config.payment_terms_days} days
              </p>
            </div>
            <div>
              <p className="text-sm text-gray-500 dark:text-gray-400">Retainage</p>
              <p className="text-lg font-medium text-gray-900 dark:text-white">
                {data.config.retainage_pct}%
              </p>
            </div>
            <div>
              <p className="text-sm text-gray-500 dark:text-gray-400">Integration Provider</p>
              <p className="text-lg font-medium text-gray-900 dark:text-white">
                {data.config.integration_provider ?? "Not configured"}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
