"use client";

import { useQuery } from "@tanstack/react-query";
import { payAppApi } from "@/lib/pay-app-api";
import type { PayApplication } from "@/lib/pay-app-api";
import { Receipt, Download, CheckCircle, Clock, DollarSign, FileText } from "lucide-react";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";

const statusColors: Record<string, string> = {
  draft: "bg-gray-100 text-gray-700",
  submitted: "bg-blue-100 text-blue-800",
  certified: "bg-green-100 text-green-800",
  paid: "bg-emerald-100 text-emerald-800",
  rejected: "bg-red-100 text-red-800",
};

function formatCurrency(val: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(val);
}

export default function PayApplicationsPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const { data, isLoading } = useQuery({
    queryKey: ["pay-applications", projectId],
    queryFn: () => payAppApi.list(projectId!),
    enabled: !!projectId,
  });

  const apps = data?.data ?? [];

  if (!projectId) return <NoProjectSelected />;

  const totalBilled = apps.reduce((s, a) => s + a.total_completed_and_stored, 0);
  const totalPaid = apps
    .filter((a) => a.status === "paid")
    .reduce((s, a) => s + a.current_payment_due, 0);
  const pendingCount = apps.filter(
    (a) => a.status === "submitted" || a.status === "certified",
  ).length;

  return (
    <div className="p-4 md:p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Receipt className="h-6 w-6 text-emerald-600" />
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Pay Applications</h1>
          <p className="text-sm text-gray-500">AIA G702/G703 payment applications</p>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="bg-white rounded-lg border border-gray-200 p-4 shadow-sm">
          <div className="flex items-center gap-2 mb-1">
            <FileText className="h-4 w-4 text-gray-400" />
            <p className="text-sm text-gray-500">Applications</p>
          </div>
          <p className="text-2xl font-bold text-gray-900">{apps.length}</p>
        </div>
        <div className="bg-white rounded-lg border border-gray-200 p-4 shadow-sm">
          <div className="flex items-center gap-2 mb-1">
            <DollarSign className="h-4 w-4 text-gray-400" />
            <p className="text-sm text-gray-500">Total Billed</p>
          </div>
          <p className="text-2xl font-bold text-gray-900">{formatCurrency(totalBilled)}</p>
        </div>
        <div className="bg-white rounded-lg border border-gray-200 p-4 shadow-sm">
          <div className="flex items-center gap-2 mb-1">
            <CheckCircle className="h-4 w-4 text-gray-400" />
            <p className="text-sm text-gray-500">Total Paid</p>
          </div>
          <p className="text-2xl font-bold text-emerald-600">{formatCurrency(totalPaid)}</p>
        </div>
        <div className="bg-white rounded-lg border border-gray-200 p-4 shadow-sm">
          <div className="flex items-center gap-2 mb-1">
            <Clock className="h-4 w-4 text-gray-400" />
            <p className="text-sm text-gray-500">Pending Review</p>
          </div>
          <p className="text-2xl font-bold text-blue-600">{pendingCount}</p>
        </div>
      </div>

      {/* Table */}
      <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
        {isLoading ? (
          <div className="p-8 text-center text-gray-400 text-sm">Loading pay applications...</div>
        ) : apps.length === 0 ? (
          <div className="p-8 text-center">
            <Receipt className="h-10 w-10 text-gray-300 mx-auto mb-2" />
            <p className="text-gray-500 text-sm">No pay applications yet</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200 bg-gray-50">
                  <th className="text-left px-4 py-3 font-medium text-gray-600">#</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Period To</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Status</th>
                  <th className="text-right px-4 py-3 font-medium text-gray-600">Contract Sum</th>
                  <th className="text-right px-4 py-3 font-medium text-gray-600">Completed</th>
                  <th className="text-right px-4 py-3 font-medium text-gray-600">Retainage</th>
                  <th className="text-right px-4 py-3 font-medium text-gray-600">Payment Due</th>
                  <th className="text-right px-4 py-3 font-medium text-gray-600">Balance</th>
                  <th className="text-center px-4 py-3 font-medium text-gray-600">PDFs</th>
                </tr>
              </thead>
              <tbody>
                {apps.map((app: PayApplication) => (
                  <tr key={app.id} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="px-4 py-3 font-medium text-gray-900">
                      {app.application_number}
                    </td>
                    <td className="px-4 py-3 text-gray-600">
                      {new Date(app.period_to).toLocaleDateString()}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium capitalize ${statusColors[app.status] ?? "bg-gray-100 text-gray-700"}`}
                      >
                        {app.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right text-gray-900">
                      {formatCurrency(app.contract_sum_to_date)}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-900">
                      {formatCurrency(app.total_completed_and_stored)}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-600">
                      {formatCurrency(app.total_retainage)}
                    </td>
                    <td className="px-4 py-3 text-right font-medium text-gray-900">
                      {formatCurrency(app.current_payment_due)}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-600">
                      {formatCurrency(app.balance_to_finish_including_retainage)}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-center gap-1">
                        <button
                          onClick={async () => {
                            const res = await fetch(payAppApi.downloadG702(app.id), {
                              credentials: "include",
                            });
                            const blob = await res.blob();
                            const url = URL.createObjectURL(blob);
                            const a = document.createElement("a");
                            a.href = url;
                            a.download = `G702_App${app.application_number}.pdf`;
                            a.click();
                            URL.revokeObjectURL(url);
                          }}
                          className="p-1 text-gray-400 hover:text-blue-600"
                          title="Download G702"
                          aria-label={`Download G702 for application ${app.application_number}`}
                        >
                          <Download className="h-4 w-4" />
                        </button>
                        <button
                          onClick={async () => {
                            const res = await fetch(payAppApi.downloadG703(app.id), {
                              credentials: "include",
                            });
                            const blob = await res.blob();
                            const url = URL.createObjectURL(blob);
                            const a = document.createElement("a");
                            a.href = url;
                            a.download = `G703_App${app.application_number}.pdf`;
                            a.click();
                            URL.revokeObjectURL(url);
                          }}
                          className="p-1 text-gray-400 hover:text-blue-600"
                          title="Download G703"
                          aria-label={`Download G703 for application ${app.application_number}`}
                        >
                          <FileText className="h-4 w-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
