"use client";

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { DollarSign, FileText, CheckCircle2, AlertTriangle, Download } from "lucide-react";
import { toast } from "sonner";

interface PayrollRecord {
  id: string;
  employee_name: string;
  trade: string;
  classification: string;
  hours_straight: number;
  hours_overtime: number;
  rate_straight: number;
  rate_overtime: number;
  gross_pay: number;
  fringe_benefits: number;
  week_ending: string;
  is_compliant: boolean;
  compliance_notes: string | null;
}

interface PrevailingWage {
  trade: string;
  classification: string;
  base_rate: number;
  fringe_rate: number;
  total_rate: number;
  effective_date: string;
  source: string;
}

interface ComplianceSummary {
  total_records: number;
  compliant_count: number;
  non_compliant_count: number;
  compliance_pct: number;
  total_gross_pay: number;
  total_fringe: number;
}

interface PayrollData {
  records: PayrollRecord[];
  prevailing_wages: PrevailingWage[];
  compliance: ComplianceSummary;
}

const isValidUUID = (id: string) =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);

function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  }).format(value);
}

type Tab = "records" | "wages" | "compliance";

export default function PayrollPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const [activeTab, setActiveTab] = useState<Tab>("records");
  const [weekFilter, setWeekFilter] = useState("");

  const { data, isLoading, error } = useQuery<PayrollData>({
    queryKey: ["payroll", projectId, weekFilter],
    queryFn: () => {
      const params = weekFilter ? `?week_ending=${weekFilter}` : "";
      return apiClient.get<PayrollData>(`/api/v1/projects/${projectId}/payroll${params}`);
    },
    enabled: !!projectId && isValidUUID(projectId),
  });

  const wh347Mutation = useMutation({
    mutationFn: () => {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      return fetch(
        `${baseUrl}/api/v1/projects/${projectId}/payroll/wh347${weekFilter ? `?week_ending=${weekFilter}` : ""}`,
        {
          credentials: "include",
        },
      ).then(async (res) => {
        if (!res.ok) throw new Error("Export failed");
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `WH-347_${weekFilter || "all"}.pdf`;
        a.click();
        URL.revokeObjectURL(url);
      });
    },
    onSuccess: () => toast.success("WH-347 downloaded"),
    onError: () => toast.error("Failed to generate WH-347"),
  });

  if (!projectId) return <NoProjectSelected />;

  const records = data?.records ?? [];
  const wages = data?.prevailing_wages ?? [];
  const compliance = data?.compliance;

  return (
    <div className="p-4 md:p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Certified Payroll</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Payroll records, prevailing wage compliance, and WH-347 generation
          </p>
        </div>
        <div className="flex items-center gap-3">
          <input
            type="date"
            value={weekFilter}
            onChange={(e) => setWeekFilter(e.target.value)}
            className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm dark:bg-gray-700 dark:text-gray-200"
            title="Filter by week ending"
          />
          <button
            onClick={() => wh347Mutation.mutate()}
            disabled={wh347Mutation.isPending}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            <Download className="h-4 w-4" />
            {wh347Mutation.isPending ? "Generating..." : "Generate WH-347"}
          </button>
        </div>
      </div>

      {/* Compliance Summary */}
      {compliance && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-2">
              <FileText className="h-5 w-5 text-blue-500" />
              <p className="text-sm text-gray-500 dark:text-gray-400">Total Records</p>
            </div>
            <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">
              {compliance.total_records}
            </p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="h-5 w-5 text-green-500" />
              <p className="text-sm text-gray-500 dark:text-gray-400">Compliance Rate</p>
            </div>
            <p
              className={`text-3xl font-bold mt-1 ${compliance.compliance_pct >= 100 ? "text-green-600" : "text-red-600"}`}
            >
              {compliance.compliance_pct.toFixed(1)}%
            </p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-2">
              <DollarSign className="h-5 w-5 text-green-500" />
              <p className="text-sm text-gray-500 dark:text-gray-400">Total Gross Pay</p>
            </div>
            <p className="text-2xl font-bold text-gray-900 dark:text-white mt-1">
              {formatCurrency(compliance.total_gross_pay)}
            </p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-orange-500" />
              <p className="text-sm text-gray-500 dark:text-gray-400">Non-Compliant</p>
            </div>
            <p
              className={`text-3xl font-bold mt-1 ${compliance.non_compliant_count > 0 ? "text-red-600" : "text-green-600"}`}
            >
              {compliance.non_compliant_count}
            </p>
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="border-b border-gray-200 dark:border-gray-700">
        <nav className="flex gap-4">
          {[
            { key: "records" as Tab, label: "Payroll Records" },
            { key: "wages" as Tab, label: "Prevailing Wages" },
            { key: "compliance" as Tab, label: "Compliance Detail" },
          ].map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`pb-3 px-1 text-sm font-medium border-b-2 transition-colors ${
                activeTab === tab.key
                  ? "border-blue-600 text-blue-600"
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
          Loading payroll data...
        </div>
      )}
      {error && (
        <div className="p-4 text-red-800 bg-red-50 rounded-lg">Failed to load payroll data</div>
      )}

      {/* Payroll Records */}
      {!isLoading && !error && activeTab === "records" && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          {records.length === 0 ? (
            <div className="text-center py-12">
              <FileText className="mx-auto h-12 w-12 text-gray-400" />
              <h3 className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">
                No payroll records
              </h3>
              <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                Payroll data will appear once imported.
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                <thead className="bg-gray-50 dark:bg-gray-900">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Employee
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Trade
                    </th>
                    <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      ST Hrs
                    </th>
                    <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      OT Hrs
                    </th>
                    <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      ST Rate
                    </th>
                    <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Gross
                    </th>
                    <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Fringe
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Week
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Status
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                  {records.map((r) => (
                    <tr
                      key={r.id}
                      className={`hover:bg-gray-50 dark:hover:bg-gray-700 ${!r.is_compliant ? "bg-red-50 dark:bg-red-900/10" : ""}`}
                    >
                      <td className="px-4 py-3 text-sm font-medium text-gray-900 dark:text-white">
                        {r.employee_name}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-500 dark:text-gray-400">
                        {r.trade}
                      </td>
                      <td className="px-4 py-3 text-sm text-right text-gray-500 dark:text-gray-400">
                        {r.hours_straight}
                      </td>
                      <td className="px-4 py-3 text-sm text-right text-gray-500 dark:text-gray-400">
                        {r.hours_overtime}
                      </td>
                      <td className="px-4 py-3 text-sm text-right text-gray-500 dark:text-gray-400">
                        {formatCurrency(r.rate_straight)}
                      </td>
                      <td className="px-4 py-3 text-sm text-right font-medium text-gray-900 dark:text-white">
                        {formatCurrency(r.gross_pay)}
                      </td>
                      <td className="px-4 py-3 text-sm text-right text-gray-500 dark:text-gray-400">
                        {formatCurrency(r.fringe_benefits)}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-500 dark:text-gray-400">
                        {r.week_ending}
                      </td>
                      <td className="px-4 py-3">
                        {r.is_compliant ? (
                          <CheckCircle2 className="h-4 w-4 text-green-500" />
                        ) : (
                          <span
                            className="inline-flex items-center gap-1 text-xs text-red-600"
                            title={r.compliance_notes ?? ""}
                          >
                            <AlertTriangle className="h-4 w-4" /> Non-compliant
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Prevailing Wages */}
      {!isLoading && !error && activeTab === "wages" && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          {wages.length === 0 ? (
            <p className="text-sm text-gray-500 dark:text-gray-400 py-8 text-center">
              No prevailing wage data loaded.
            </p>
          ) : (
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
              <thead className="bg-gray-50 dark:bg-gray-900">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Trade
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Classification
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Base Rate
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Fringe
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Total
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Effective
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Source
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                {wages.map((w, i) => (
                  <tr key={i} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                    <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white">
                      {w.trade}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                      {w.classification}
                    </td>
                    <td className="px-6 py-4 text-sm text-right text-gray-900 dark:text-white">
                      {formatCurrency(w.base_rate)}
                    </td>
                    <td className="px-6 py-4 text-sm text-right text-gray-500 dark:text-gray-400">
                      {formatCurrency(w.fringe_rate)}
                    </td>
                    <td className="px-6 py-4 text-sm text-right font-medium text-gray-900 dark:text-white">
                      {formatCurrency(w.total_rate)}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                      {new Date(w.effective_date).toLocaleDateString()}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                      {w.source}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Compliance Detail */}
      {!isLoading && !error && activeTab === "compliance" && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
            Non-Compliant Records
          </h2>
          {records.filter((r) => !r.is_compliant).length === 0 ? (
            <div className="text-center py-8">
              <CheckCircle2 className="mx-auto h-12 w-12 text-green-400" />
              <p className="mt-2 text-sm text-green-600 font-medium">
                All payroll records are compliant.
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {records
                .filter((r) => !r.is_compliant)
                .map((r) => (
                  <div
                    key={r.id}
                    className="border border-red-200 bg-red-50 dark:bg-red-900/10 dark:border-red-800 rounded-lg p-4"
                  >
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-medium text-gray-900 dark:text-white">
                        {r.employee_name} - {r.trade}
                      </p>
                      <span className="text-xs text-gray-500 dark:text-gray-400">
                        {r.week_ending}
                      </span>
                    </div>
                    <p className="text-sm text-red-600 mt-1">
                      {r.compliance_notes ?? "Below prevailing wage rate"}
                    </p>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                      Paid: {formatCurrency(r.rate_straight)}/hr &middot; Gross:{" "}
                      {formatCurrency(r.gross_pay)}
                    </p>
                  </div>
                ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
