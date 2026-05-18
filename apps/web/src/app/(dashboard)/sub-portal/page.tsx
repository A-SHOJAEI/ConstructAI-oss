"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { Users, ClipboardList, Truck, DollarSign, ShieldCheck, Send } from "lucide-react";
import { toast } from "sonner";

interface SubProfile {
  id: string;
  company_name: string;
  contact_name: string;
  email: string;
  trade: string;
  license_number: string | null;
  insurance_expiry: string | null;
  prequalified: boolean;
}

interface SOVScope {
  id: string;
  line_number: number;
  description: string;
  scheduled_value: number;
  work_completed_pct: number;
  retainage_pct: number;
}

interface ManpowerEntry {
  date: string;
  trade: string;
  headcount: number;
  hours: number;
}

interface DeliveryReceipt {
  id: string;
  material: string;
  quantity: number;
  unit: string;
  supplier: string;
  received_date: string;
  status: "pending" | "accepted" | "rejected";
}

interface PaymentStatus {
  pay_app_number: number;
  amount: number;
  status: "submitted" | "approved" | "paid";
  date: string;
}

interface SubPortalData {
  profile: SubProfile | null;
  sov_scope: SOVScope[];
  recent_manpower: ManpowerEntry[];
  deliveries: DeliveryReceipt[];
  payments: PaymentStatus[];
  safety_briefing_languages: string[];
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

const deliveryStatusColors: Record<string, string> = {
  pending: "bg-yellow-100 text-yellow-800",
  accepted: "bg-green-100 text-green-800",
  rejected: "bg-red-100 text-red-800",
};

const paymentStatusColors: Record<string, string> = {
  submitted: "bg-blue-100 text-blue-800",
  approved: "bg-green-100 text-green-800",
  paid: "bg-emerald-100 text-emerald-800",
};

type Tab = "sov" | "manpower" | "deliveries" | "payments" | "safety";

export default function SubPortalPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<Tab>("sov");
  const [mpDate, setMpDate] = useState(new Date().toISOString().split("T")[0]);
  const [mpHeadcount, setMpHeadcount] = useState("");
  const [mpHours, setMpHours] = useState("");
  const [briefingLang, setBriefingLang] = useState("en");

  const { data, isLoading, error } = useQuery<SubPortalData>({
    queryKey: ["sub-portal", projectId],
    queryFn: () => apiClient.get<SubPortalData>(`/api/v1/projects/${projectId}/sub-portal`),
    enabled: !!projectId && isValidUUID(projectId),
  });

  const manpowerMutation = useMutation({
    mutationFn: (entry: { date: string; headcount: number; hours: number }) =>
      apiClient.post(`/api/v1/projects/${projectId}/sub-portal/manpower`, entry),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sub-portal", projectId] });
      toast.success("Manpower submitted");
      setMpHeadcount("");
      setMpHours("");
    },
    onError: () => toast.error("Failed to submit manpower"),
  });

  const briefingMutation = useMutation({
    mutationFn: (language: string) =>
      apiClient.get<{ url: string }>(
        `/api/v1/projects/${projectId}/sub-portal/safety-briefing?language=${language}`,
      ),
    onSuccess: (result) => {
      if (result.url) window.open(result.url, "_blank");
      toast.success("Safety briefing opened");
    },
    onError: () => toast.error("Failed to load safety briefing"),
  });

  if (!projectId) return <NoProjectSelected />;

  const profile = data?.profile;
  const sovScope = data?.sov_scope ?? [];
  const manpower = data?.recent_manpower ?? [];
  const deliveries = data?.deliveries ?? [];
  const payments = data?.payments ?? [];
  const languages = data?.safety_briefing_languages ?? [
    "en",
    "es",
    "pt",
    "zh",
    "ko",
    "vi",
    "tl",
    "fr",
  ];

  return (
    <div className="p-4 md:p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Subcontractor Portal</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          SOV scope, manpower reporting, deliveries, payments, and safety briefings
        </p>
      </div>

      {/* Profile Card */}
      {profile && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
          <div className="flex items-center gap-3 mb-4">
            <Users className="h-6 w-6 text-blue-500" />
            <div>
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
                {profile.company_name}
              </h2>
              <p className="text-sm text-gray-500 dark:text-gray-400">
                {profile.contact_name} &middot; {profile.trade}
              </p>
            </div>
            {profile.prequalified && (
              <span className="ml-auto inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
                Prequalified
              </span>
            )}
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <div>
              <p className="text-gray-500 dark:text-gray-400">Email</p>
              <p className="text-gray-900 dark:text-white">{profile.email}</p>
            </div>
            <div>
              <p className="text-gray-500 dark:text-gray-400">License #</p>
              <p className="text-gray-900 dark:text-white">{profile.license_number ?? "N/A"}</p>
            </div>
            <div>
              <p className="text-gray-500 dark:text-gray-400">Insurance Expiry</p>
              <p
                className={`${profile.insurance_expiry && new Date(profile.insurance_expiry) < new Date() ? "text-red-600" : "text-gray-900 dark:text-white"}`}
              >
                {profile.insurance_expiry
                  ? new Date(profile.insurance_expiry).toLocaleDateString()
                  : "N/A"}
              </p>
            </div>
            <div>
              <p className="text-gray-500 dark:text-gray-400">Trade</p>
              <p className="text-gray-900 dark:text-white">{profile.trade}</p>
            </div>
          </div>
        </div>
      )}

      {isLoading && (
        <div className="p-8 text-center text-gray-500 dark:text-gray-400">
          Loading portal data...
        </div>
      )}
      {error && (
        <div className="p-4 text-red-800 bg-red-50 rounded-lg">Failed to load portal data</div>
      )}

      {/* Tabs */}
      {!isLoading && !error && (
        <>
          <div className="border-b border-gray-200 dark:border-gray-700">
            <nav className="flex gap-4 overflow-x-auto">
              {[
                { key: "sov" as Tab, label: "SOV Scope", icon: ClipboardList },
                { key: "manpower" as Tab, label: "Manpower", icon: Users },
                { key: "deliveries" as Tab, label: "Deliveries", icon: Truck },
                { key: "payments" as Tab, label: "Payments", icon: DollarSign },
                { key: "safety" as Tab, label: "Safety Briefing", icon: ShieldCheck },
              ].map((tab) => (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
                  className={`flex items-center gap-2 pb-3 px-1 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
                    activeTab === tab.key
                      ? "border-blue-600 text-blue-600"
                      : "border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700"
                  }`}
                >
                  <tab.icon className="h-4 w-4" />
                  {tab.label}
                </button>
              ))}
            </nav>
          </div>

          {/* SOV Scope */}
          {activeTab === "sov" && (
            <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
              {sovScope.length === 0 ? (
                <p className="text-sm text-gray-500 dark:text-gray-400 py-8 text-center">
                  No SOV scope assigned.
                </p>
              ) : (
                <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                  <thead className="bg-gray-50 dark:bg-gray-900">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                        #
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                        Description
                      </th>
                      <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                        Scheduled Value
                      </th>
                      <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                        % Complete
                      </th>
                      <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                        Retainage
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                    {sovScope.map((line) => (
                      <tr key={line.id} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                        <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                          {line.line_number}
                        </td>
                        <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white">
                          {line.description}
                        </td>
                        <td className="px-6 py-4 text-sm text-right text-gray-900 dark:text-white">
                          {formatCurrency(line.scheduled_value)}
                        </td>
                        <td className="px-6 py-4 text-sm text-right">
                          <div className="flex items-center justify-end gap-2">
                            <div className="w-20 bg-gray-200 dark:bg-gray-700 rounded-full h-2">
                              <div
                                className="bg-blue-500 h-2 rounded-full"
                                style={{ width: `${line.work_completed_pct}%` }}
                              />
                            </div>
                            <span className="text-gray-500 dark:text-gray-400">
                              {line.work_completed_pct.toFixed(0)}%
                            </span>
                          </div>
                        </td>
                        <td className="px-6 py-4 text-sm text-right text-gray-500 dark:text-gray-400">
                          {line.retainage_pct}%
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}

          {/* Manpower */}
          {activeTab === "manpower" && (
            <div className="space-y-4">
              <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
                <h3 className="text-md font-semibold text-gray-900 dark:text-white mb-3">
                  Submit Manpower
                </h3>
                <div className="flex flex-wrap gap-3 items-end">
                  <div>
                    <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">
                      Date
                    </label>
                    <input
                      type="date"
                      value={mpDate}
                      onChange={(e) => setMpDate(e.target.value)}
                      className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm dark:bg-gray-700 dark:text-gray-200"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">
                      Headcount
                    </label>
                    <input
                      type="number"
                      value={mpHeadcount}
                      onChange={(e) => setMpHeadcount(e.target.value)}
                      placeholder="0"
                      className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm w-24 dark:bg-gray-700 dark:text-gray-200"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">
                      Hours
                    </label>
                    <input
                      type="number"
                      value={mpHours}
                      onChange={(e) => setMpHours(e.target.value)}
                      placeholder="0"
                      className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm w-24 dark:bg-gray-700 dark:text-gray-200"
                    />
                  </div>
                  <button
                    onClick={() =>
                      manpowerMutation.mutate({
                        date: mpDate,
                        headcount: Number(mpHeadcount),
                        hours: Number(mpHours),
                      })
                    }
                    disabled={manpowerMutation.isPending || !mpHeadcount || !mpHours}
                    className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
                  >
                    <Send className="h-4 w-4" /> Submit
                  </button>
                </div>
              </div>
              {manpower.length > 0 && (
                <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
                  <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                    <thead className="bg-gray-50 dark:bg-gray-900">
                      <tr>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                          Date
                        </th>
                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                          Trade
                        </th>
                        <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                          Headcount
                        </th>
                        <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                          Hours
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                      {manpower.map((mp, i) => (
                        <tr key={i} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                          <td className="px-6 py-4 text-sm text-gray-900 dark:text-white">
                            {mp.date}
                          </td>
                          <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                            {mp.trade}
                          </td>
                          <td className="px-6 py-4 text-sm text-right text-gray-900 dark:text-white">
                            {mp.headcount}
                          </td>
                          <td className="px-6 py-4 text-sm text-right text-gray-500 dark:text-gray-400">
                            {mp.hours}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* Deliveries */}
          {activeTab === "deliveries" && (
            <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
              {deliveries.length === 0 ? (
                <p className="text-sm text-gray-500 dark:text-gray-400 py-8 text-center">
                  No delivery receipts.
                </p>
              ) : (
                <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                  <thead className="bg-gray-50 dark:bg-gray-900">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                        Material
                      </th>
                      <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                        Qty
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                        Supplier
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                        Received
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                        Status
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                    {deliveries.map((d) => (
                      <tr key={d.id} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                        <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white">
                          {d.material}
                        </td>
                        <td className="px-6 py-4 text-sm text-right text-gray-500 dark:text-gray-400">
                          {d.quantity} {d.unit}
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                          {d.supplier}
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                          {new Date(d.received_date).toLocaleDateString()}
                        </td>
                        <td className="px-6 py-4">
                          <span
                            className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${deliveryStatusColors[d.status]}`}
                          >
                            {d.status}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}

          {/* Payments */}
          {activeTab === "payments" && (
            <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
              {payments.length === 0 ? (
                <p className="text-sm text-gray-500 dark:text-gray-400 py-8 text-center">
                  No payment records.
                </p>
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
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                        Status
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                        Date
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                    {payments.map((p, i) => (
                      <tr key={i} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                        <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white">
                          #{p.pay_app_number}
                        </td>
                        <td className="px-6 py-4 text-sm text-right font-medium text-gray-900 dark:text-white">
                          {formatCurrency(p.amount)}
                        </td>
                        <td className="px-6 py-4">
                          <span
                            className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${paymentStatusColors[p.status]}`}
                          >
                            {p.status}
                          </span>
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                          {new Date(p.date).toLocaleDateString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}

          {/* Safety Briefing */}
          {activeTab === "safety" && (
            <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
              <div className="flex items-center gap-2 mb-4">
                <ShieldCheck className="h-5 w-5 text-green-500" />
                <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
                  Safety Briefing Translation
                </h2>
              </div>
              <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
                Download or view the safety briefing in your preferred language.
              </p>
              <div className="flex gap-3 items-end">
                <div>
                  <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">
                    Language
                  </label>
                  <select
                    value={briefingLang}
                    onChange={(e) => setBriefingLang(e.target.value)}
                    className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm dark:bg-gray-700 dark:text-gray-200"
                  >
                    {languages.map((lang) => (
                      <option key={lang} value={lang}>
                        {{
                          en: "English",
                          es: "Spanish",
                          pt: "Portuguese",
                          zh: "Chinese",
                          ko: "Korean",
                          vi: "Vietnamese",
                          tl: "Tagalog",
                          fr: "French",
                        }[lang] ?? lang}
                      </option>
                    ))}
                  </select>
                </div>
                <button
                  onClick={() => briefingMutation.mutate(briefingLang)}
                  disabled={briefingMutation.isPending}
                  className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50"
                >
                  <ShieldCheck className="h-4 w-4" />
                  {briefingMutation.isPending ? "Loading..." : "View Briefing"}
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
