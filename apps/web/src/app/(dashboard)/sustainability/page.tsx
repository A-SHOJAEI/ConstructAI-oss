"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { Leaf, BarChart3, Recycle, Award } from "lucide-react";

interface CarbonByDivision {
  division: string;
  division_name: string;
  embodied_carbon_kgco2e: number;
  percentage: number;
}

interface LEEDCredit {
  credit_id: string;
  credit_name: string;
  category: string;
  points_possible: number;
  points_achieved: number;
  status: "achieved" | "pending" | "not_started" | "not_applicable";
}

interface SalvagedMaterial {
  id: string;
  material_name: string;
  quantity: number;
  unit: string;
  source: string;
  carbon_saved_kgco2e: number;
  date_salvaged: string;
}

interface SustainabilityData {
  total_embodied_carbon_kgco2e: number;
  carbon_per_sf: number;
  gross_area_sf: number;
  carbon_by_division: CarbonByDivision[];
  leed_credits: LEEDCredit[];
  salvaged_materials: SalvagedMaterial[];
  total_carbon_saved_kgco2e: number;
  leed_points_achieved: number;
  leed_points_possible: number;
}

const isValidUUID = (id: string) =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);

const leedStatusColors: Record<string, string> = {
  achieved: "bg-green-100 text-green-800",
  pending: "bg-yellow-100 text-yellow-800",
  not_started: "bg-gray-100 text-gray-800",
  not_applicable: "bg-gray-50 text-gray-500",
};

type Tab = "carbon" | "leed" | "salvaged";

export default function SustainabilityPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const [activeTab, setActiveTab] = useState<Tab>("carbon");

  const { data, isLoading, error } = useQuery<SustainabilityData>({
    queryKey: ["sustainability", projectId],
    queryFn: () =>
      apiClient.get<SustainabilityData>(`/api/v1/projects/${projectId}/sustainability`),
    enabled: !!projectId && isValidUUID(projectId),
  });

  if (!projectId) return <NoProjectSelected />;

  const maxCarbon = Math.max(
    ...(data?.carbon_by_division?.map((d) => d.embodied_carbon_kgco2e) ?? [1]),
  );

  return (
    <div className="p-4 md:p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Sustainability</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Embodied carbon tracking, LEED credit evaluation, and salvaged materials
        </p>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <Leaf className="h-5 w-5 text-green-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Total Embodied Carbon</p>
          </div>
          <p className="text-2xl font-bold text-gray-900 dark:text-white mt-1">
            {isLoading
              ? "..."
              : data
                ? `${(data.total_embodied_carbon_kgco2e / 1000).toFixed(0)} tCO2e`
                : "N/A"}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <BarChart3 className="h-5 w-5 text-blue-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Carbon / SF</p>
          </div>
          <p className="text-2xl font-bold text-gray-900 dark:text-white mt-1">
            {isLoading ? "..." : data ? `${data.carbon_per_sf.toFixed(1)} kgCO2e` : "N/A"}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <Award className="h-5 w-5 text-yellow-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">LEED Points</p>
          </div>
          <p className="text-2xl font-bold text-gray-900 dark:text-white mt-1">
            {isLoading
              ? "..."
              : data
                ? `${data.leed_points_achieved} / ${data.leed_points_possible}`
                : "N/A"}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <div className="flex items-center gap-2">
            <Recycle className="h-5 w-5 text-emerald-500" />
            <p className="text-sm text-gray-500 dark:text-gray-400">Carbon Saved (Salvage)</p>
          </div>
          <p className="text-2xl font-bold text-emerald-600 mt-1">
            {isLoading
              ? "..."
              : data
                ? `${(data.total_carbon_saved_kgco2e / 1000).toFixed(1)} tCO2e`
                : "N/A"}
          </p>
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200 dark:border-gray-700">
        <nav className="flex gap-4">
          {[
            { key: "carbon" as Tab, label: "Carbon by Division" },
            { key: "leed" as Tab, label: "LEED Credits" },
            { key: "salvaged" as Tab, label: "Salvaged Materials" },
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
          Loading sustainability data...
        </div>
      )}
      {error && (
        <div className="p-4 text-red-800 bg-red-50 rounded-lg">
          Failed to load sustainability data
        </div>
      )}

      {/* Carbon by Division */}
      {!isLoading && !error && activeTab === "carbon" && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6 space-y-4">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
            Embodied Carbon by CSI Division
          </h2>
          {!data?.carbon_by_division || data.carbon_by_division.length === 0 ? (
            <p className="text-sm text-gray-500 dark:text-gray-400 py-4 text-center">
              No carbon data available yet.
            </p>
          ) : (
            <div className="space-y-3">
              {data.carbon_by_division.map((div) => (
                <div key={div.division} className="flex items-center gap-4">
                  <div
                    className="w-40 text-sm text-gray-700 dark:text-gray-300 truncate"
                    title={div.division_name}
                  >
                    {div.division} - {div.division_name}
                  </div>
                  <div className="flex-1 bg-gray-100 dark:bg-gray-700 rounded-full h-5 overflow-hidden">
                    <div
                      className="bg-green-500 h-5 rounded-full transition-all"
                      style={{ width: `${(div.embodied_carbon_kgco2e / maxCarbon) * 100}%` }}
                    />
                  </div>
                  <div className="w-32 text-sm text-right text-gray-900 dark:text-white font-medium">
                    {(div.embodied_carbon_kgco2e / 1000).toFixed(1)} tCO2e
                  </div>
                  <div className="w-16 text-sm text-right text-gray-500 dark:text-gray-400">
                    {div.percentage.toFixed(1)}%
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* LEED Credits */}
      {!isLoading && !error && activeTab === "leed" && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          {!data?.leed_credits || data.leed_credits.length === 0 ? (
            <p className="text-sm text-gray-500 dark:text-gray-400 py-8 text-center">
              No LEED credit data available.
            </p>
          ) : (
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
              <thead className="bg-gray-50 dark:bg-gray-900">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Credit
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Category
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Points
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Status
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                {data.leed_credits.map((credit) => (
                  <tr key={credit.credit_id} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                    <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white">
                      {credit.credit_id} - {credit.credit_name}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                      {credit.category}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-900 dark:text-white">
                      {credit.points_achieved} / {credit.points_possible}
                    </td>
                    <td className="px-6 py-4">
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${leedStatusColors[credit.status] ?? "bg-gray-100 text-gray-800"}`}
                      >
                        {credit.status.replace("_", " ")}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Salvaged Materials */}
      {!isLoading && !error && activeTab === "salvaged" && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          {!data?.salvaged_materials || data.salvaged_materials.length === 0 ? (
            <p className="text-sm text-gray-500 dark:text-gray-400 py-8 text-center">
              No salvaged materials tracked yet.
            </p>
          ) : (
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
              <thead className="bg-gray-50 dark:bg-gray-900">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Material
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Quantity
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Source
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Carbon Saved
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                    Date
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                {data.salvaged_materials.map((mat) => (
                  <tr key={mat.id} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                    <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white">
                      {mat.material_name}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                      {mat.quantity.toLocaleString()} {mat.unit}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                      {mat.source}
                    </td>
                    <td className="px-6 py-4 text-sm text-emerald-600 font-medium">
                      {mat.carbon_saved_kgco2e.toFixed(0)} kgCO2e
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                      {new Date(mat.date_salvaged).toLocaleDateString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
