"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { ShieldCheck, TrendingDown, Download, FileText, AlertTriangle } from "lucide-react";
import { toast } from "sonner";

interface SafetyMetrics {
  trir: number;
  dart_rate: number;
  total_recordable_incidents: number;
  days_away: number;
  total_hours_worked: number;
  lost_time_incidents: number;
  near_misses: number;
}

interface EMRData {
  current_emr: number;
  industry_avg: number;
  projected_emr: number;
  premium_impact_pct: number;
  years: { year: number; emr: number }[];
}

interface LossRun {
  id: string;
  claim_date: string;
  description: string;
  status: "open" | "closed" | "reserved";
  incurred_amount: number;
  paid_amount: number;
  reserved_amount: number;
}

interface RiskProfile {
  overall_risk: "low" | "medium" | "high";
  risk_factors: { factor: string; score: number; max_score: number }[];
  recommendations: string[];
}

interface InsuranceData {
  safety_metrics: SafetyMetrics;
  emr: EMRData;
  loss_runs: LossRun[];
  risk_profile: RiskProfile;
}

const isValidUUID = (id: string) =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);

const riskColors: Record<string, string> = {
  low: "bg-green-100 text-green-800",
  medium: "bg-yellow-100 text-yellow-800",
  high: "bg-red-100 text-red-800",
};

const claimStatusColors: Record<string, string> = {
  open: "bg-blue-100 text-blue-800",
  closed: "bg-gray-100 text-gray-800",
  reserved: "bg-yellow-100 text-yellow-800",
};

function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
  }).format(value);
}

export default function InsurancePage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);

  const { data, isLoading, error } = useQuery<InsuranceData>({
    queryKey: ["insurance", projectId],
    queryFn: () => apiClient.get<InsuranceData>(`/api/v1/projects/${projectId}/insurance`),
    enabled: !!projectId && isValidUUID(projectId),
  });

  const handleExport = async (type: "loss-run" | "osha-300") => {
    if (!projectId || !isValidUUID(projectId)) return;
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${baseUrl}/api/v1/projects/${projectId}/insurance/export/${type}`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error("Export failed");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${type}_export.pdf`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success(`${type === "loss-run" ? "Loss Run" : "OSHA 300"} exported`);
    } catch {
      toast.error("Export failed");
    }
  };

  if (!projectId) return <NoProjectSelected />;

  const metrics = data?.safety_metrics;
  const emr = data?.emr;
  const lossRuns = data?.loss_runs ?? [];
  const risk = data?.risk_profile;

  return (
    <div className="p-4 md:p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Insurance Export</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            Safety metrics, EMR calculator, loss runs, and OSHA 300 export
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => handleExport("loss-run")}
            className="flex items-center gap-2 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700"
          >
            <Download className="h-4 w-4" /> Loss Run
          </button>
          <button
            onClick={() => handleExport("osha-300")}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
          >
            <FileText className="h-4 w-4" /> OSHA 300
          </button>
        </div>
      </div>

      {isLoading && (
        <div className="p-8 text-center text-gray-500 dark:text-gray-400">
          Loading insurance data...
        </div>
      )}
      {error && (
        <div className="p-4 text-red-800 bg-red-50 rounded-lg">Failed to load insurance data</div>
      )}

      {/* Safety Metrics */}
      {metrics && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <p className="text-sm text-gray-500 dark:text-gray-400">TRIR</p>
            <p
              className={`text-3xl font-bold mt-1 ${metrics.trir <= 3.0 ? "text-green-600" : "text-red-600"}`}
            >
              {metrics.trir.toFixed(2)}
            </p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <p className="text-sm text-gray-500 dark:text-gray-400">DART Rate</p>
            <p
              className={`text-3xl font-bold mt-1 ${metrics.dart_rate <= 2.0 ? "text-green-600" : "text-red-600"}`}
            >
              {metrics.dart_rate.toFixed(2)}
            </p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <p className="text-sm text-gray-500 dark:text-gray-400">Recordable Incidents</p>
            <p className="text-3xl font-bold text-gray-900 dark:text-white mt-1">
              {metrics.total_recordable_incidents}
            </p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <p className="text-sm text-gray-500 dark:text-gray-400">Near Misses</p>
            <p className="text-3xl font-bold text-yellow-600 mt-1">{metrics.near_misses}</p>
          </div>
        </div>
      )}

      {/* EMR Calculator */}
      {emr && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
          <div className="flex items-center gap-2 mb-4">
            <TrendingDown className="h-5 w-5 text-blue-500" />
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
              Experience Modification Rate (EMR)
            </h2>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
            <div>
              <p className="text-sm text-gray-500 dark:text-gray-400">Current EMR</p>
              <p
                className={`text-3xl font-bold ${emr.current_emr <= 1.0 ? "text-green-600" : "text-red-600"}`}
              >
                {emr.current_emr.toFixed(2)}
              </p>
            </div>
            <div>
              <p className="text-sm text-gray-500 dark:text-gray-400">Industry Average</p>
              <p className="text-3xl font-bold text-gray-900 dark:text-white">
                {emr.industry_avg.toFixed(2)}
              </p>
            </div>
            <div>
              <p className="text-sm text-gray-500 dark:text-gray-400">Projected EMR</p>
              <p
                className={`text-3xl font-bold ${emr.projected_emr <= emr.current_emr ? "text-green-600" : "text-orange-600"}`}
              >
                {emr.projected_emr.toFixed(2)}
              </p>
            </div>
            <div>
              <p className="text-sm text-gray-500 dark:text-gray-400">Premium Impact</p>
              <p
                className={`text-3xl font-bold ${emr.premium_impact_pct <= 0 ? "text-green-600" : "text-red-600"}`}
              >
                {emr.premium_impact_pct >= 0 ? "+" : ""}
                {emr.premium_impact_pct.toFixed(1)}%
              </p>
            </div>
          </div>
          {emr.years.length > 0 && (
            <div className="mt-4 flex gap-2 items-end h-24">
              {emr.years.map((y) => (
                <div key={y.year} className="flex flex-col items-center flex-1">
                  <div
                    className={`w-full rounded-t ${y.emr <= 1.0 ? "bg-green-400" : "bg-red-400"}`}
                    style={{ height: `${(y.emr / 2) * 100}%` }}
                  />
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">{y.year}</p>
                  <p className="text-xs font-medium">{y.emr.toFixed(2)}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Loss Runs */}
      {!isLoading && !error && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
              Loss Run History
            </h2>
          </div>
          {lossRuns.length === 0 ? (
            <div className="text-center py-12">
              <ShieldCheck className="mx-auto h-12 w-12 text-green-400" />
              <p className="mt-2 text-sm text-green-600 font-medium">No claims on record</p>
            </div>
          ) : (
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
              <thead className="bg-gray-50 dark:bg-gray-900">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Date
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Description
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Status
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Incurred
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Paid
                  </th>
                  <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Reserved
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                {lossRuns.map((lr) => (
                  <tr key={lr.id} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                    <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                      {new Date(lr.claim_date).toLocaleDateString()}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-900 dark:text-white max-w-xs truncate">
                      {lr.description}
                    </td>
                    <td className="px-6 py-4">
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${claimStatusColors[lr.status]}`}
                      >
                        {lr.status}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-sm text-right text-gray-900 dark:text-white">
                      {formatCurrency(lr.incurred_amount)}
                    </td>
                    <td className="px-6 py-4 text-sm text-right text-gray-500 dark:text-gray-400">
                      {formatCurrency(lr.paid_amount)}
                    </td>
                    <td className="px-6 py-4 text-sm text-right text-gray-500 dark:text-gray-400">
                      {formatCurrency(lr.reserved_amount)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Risk Profile */}
      {risk && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Risk Profile</h2>
            <span
              className={`inline-flex items-center px-3 py-1 rounded-full text-sm font-medium ${riskColors[risk.overall_risk]}`}
            >
              {risk.overall_risk} risk
            </span>
          </div>
          <div className="space-y-3 mb-4">
            {risk.risk_factors.map((rf, i) => (
              <div key={i} className="flex items-center gap-3">
                <div className="w-40 text-sm text-gray-700 dark:text-gray-300">{rf.factor}</div>
                <div className="flex-1 bg-gray-100 dark:bg-gray-700 rounded-full h-4 overflow-hidden">
                  <div
                    className={`h-4 rounded-full ${rf.score / rf.max_score > 0.7 ? "bg-red-500" : rf.score / rf.max_score > 0.4 ? "bg-yellow-500" : "bg-green-500"}`}
                    style={{ width: `${(rf.score / rf.max_score) * 100}%` }}
                  />
                </div>
                <div className="w-16 text-sm text-right text-gray-500 dark:text-gray-400">
                  {rf.score}/{rf.max_score}
                </div>
              </div>
            ))}
          </div>
          {risk.recommendations.length > 0 && (
            <div>
              <h3 className="text-sm font-medium text-gray-900 dark:text-white mb-2">
                Recommendations
              </h3>
              <ul className="space-y-1">
                {risk.recommendations.map((rec, i) => (
                  <li
                    key={i}
                    className="flex items-start gap-2 text-sm text-gray-600 dark:text-gray-400"
                  >
                    <AlertTriangle className="h-4 w-4 text-yellow-500 mt-0.5 flex-shrink-0" />
                    {rec}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
