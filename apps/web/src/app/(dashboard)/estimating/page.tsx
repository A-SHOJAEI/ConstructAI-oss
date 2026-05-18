"use client";

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";
import { Calculator, TrendingUp, Play, DollarSign } from "lucide-react";
import { toast } from "sonner";

interface CostEstimate {
  id: string;
  name: string;
  building_type: string;
  gross_area_sf: number;
  total_cost: number;
  cost_per_sf: number;
  confidence_level: string;
  status: "draft" | "in_progress" | "complete";
  created_at: string;
}

interface ParametricResult {
  predicted_cost_per_sf: number;
  total_predicted_cost: number;
  confidence_intervals: {
    level: string;
    lower: number;
    upper: number;
  }[];
  model_version: string;
  is_heuristic_fallback: boolean;
}

interface MonteCarloStatus {
  id: string;
  status: "running" | "complete" | "failed";
  iterations: number;
  p50: number | null;
  p80: number | null;
  p90: number | null;
  mean: number | null;
}

interface EstimatesData {
  estimates: CostEstimate[];
  total: number;
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

const statusColors: Record<string, string> = {
  draft: "bg-gray-100 text-gray-800",
  in_progress: "bg-yellow-100 text-yellow-800",
  complete: "bg-green-100 text-green-800",
};

const BUILDING_TYPES = [
  "office",
  "residential_multifamily",
  "residential_single",
  "retail",
  "warehouse",
  "industrial",
  "healthcare",
  "education_k12",
  "education_higher",
  "hotel",
  "mixed_use",
  "parking_structure",
  "data_center",
  "laboratory",
];

const QUALITY_LEVELS = [
  { value: 1, label: "Economy" },
  { value: 2, label: "Standard" },
  { value: 3, label: "Above Average" },
  { value: 4, label: "Premium" },
  { value: 5, label: "Luxury" },
];

export default function EstimatingPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);

  // Calculator state
  const [buildingType, setBuildingType] = useState("office");
  const [grossArea, setGrossArea] = useState("50000");
  const [numStories, setNumStories] = useState("3");
  const [qualityLevel, setQualityLevel] = useState("3");
  const [locationFactor, setLocationFactor] = useState("1.0");

  const { data, isLoading, error } = useQuery<EstimatesData>({
    queryKey: ["estimates", projectId],
    queryFn: () =>
      apiClient.get<EstimatesData>(`/api/v1/estimating/estimates?project_id=${projectId}`),
    enabled: !!projectId && isValidUUID(projectId),
  });

  const parametricMutation = useMutation({
    mutationFn: (payload: {
      building_type: string;
      gross_area: number;
      num_stories: number;
      quality_level: number;
      location_factor: number;
    }) =>
      apiClient.post<ParametricResult>(
        `/api/v1/estimating/estimates/parametric-predict`,
        {
          project_id: projectId,
          ...payload,
        },
        { timeoutMs: 60_000 },
      ),
  });

  const monteCarloMutation = useMutation({
    mutationFn: (estimateId: string) =>
      apiClient.post<MonteCarloStatus>(
        `/api/v1/projects/${projectId}/estimates/${estimateId}/monte-carlo`,
      ),
    onSuccess: () => toast.success("Monte Carlo simulation started"),
    onError: () => toast.error("Failed to start Monte Carlo simulation"),
  });

  const handlePredict = () => {
    const area = Number(grossArea);
    if (!area || area <= 0) {
      toast.error("Enter a valid gross area");
      return;
    }
    parametricMutation.mutate({
      building_type: buildingType,
      gross_area: area,
      num_stories: Number(numStories) || 1,
      quality_level: Number(qualityLevel) || 3,
      location_factor: Number(locationFactor) || 1.0,
    });
  };

  if (!projectId) return <NoProjectSelected />;

  const estimates = data?.estimates ?? [];
  const result = parametricMutation.data;

  return (
    <div className="p-4 md:p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Estimating</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Cost estimates, parametric model predictions, and Monte Carlo analysis
        </p>
      </div>

      {/* Parametric Model Calculator */}
      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
        <div className="flex items-center gap-2 mb-4">
          <Calculator className="h-5 w-5 text-blue-500" />
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
            Parametric Cost Calculator
          </h2>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-4">
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">
              Building Type
            </label>
            <select
              value={buildingType}
              onChange={(e) => setBuildingType(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm dark:bg-gray-700 dark:text-gray-200"
            >
              {BUILDING_TYPES.map((bt) => (
                <option key={bt} value={bt}>
                  {bt.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">
              Gross Area (SF)
            </label>
            <input
              type="number"
              value={grossArea}
              onChange={(e) => setGrossArea(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm dark:bg-gray-700 dark:text-gray-200"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Stories</label>
            <input
              type="number"
              value={numStories}
              onChange={(e) => setNumStories(e.target.value)}
              min="1"
              max="100"
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm dark:bg-gray-700 dark:text-gray-200"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Quality</label>
            <select
              value={qualityLevel}
              onChange={(e) => setQualityLevel(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm dark:bg-gray-700 dark:text-gray-200"
            >
              {QUALITY_LEVELS.map((q) => (
                <option key={q.value} value={q.value}>
                  {q.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">
              Location Factor
            </label>
            <input
              type="number"
              value={locationFactor}
              onChange={(e) => setLocationFactor(e.target.value)}
              step="0.01"
              min="0.5"
              max="2.0"
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm dark:bg-gray-700 dark:text-gray-200"
            />
          </div>
        </div>
        <button
          onClick={handlePredict}
          disabled={parametricMutation.isPending}
          className="flex items-center gap-2 px-6 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
        >
          <TrendingUp className="h-4 w-4" />
          {parametricMutation.isPending ? "Predicting..." : "Predict Cost"}
        </button>

        {parametricMutation.error && (
          <p className="mt-3 text-sm text-red-600">Prediction failed. Please try again.</p>
        )}

        {/* Prediction Result */}
        {result && (
          <div className="mt-6 border-t border-gray-200 dark:border-gray-700 pt-4">
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-4">
              <div className="p-4 bg-blue-50 dark:bg-blue-900/20 rounded-lg">
                <p className="text-xs text-gray-500 dark:text-gray-400 uppercase">Predicted $/SF</p>
                <p className="text-2xl font-bold text-blue-600">
                  {formatCurrency(result.predicted_cost_per_sf)}
                </p>
              </div>
              <div className="p-4 bg-green-50 dark:bg-green-900/20 rounded-lg">
                <p className="text-xs text-gray-500 dark:text-gray-400 uppercase">
                  Total Predicted Cost
                </p>
                <p className="text-2xl font-bold text-green-600">
                  {formatCurrency(result.total_predicted_cost)}
                </p>
              </div>
              <div className="p-4 bg-gray-50 dark:bg-gray-700 rounded-lg">
                <p className="text-xs text-gray-500 dark:text-gray-400 uppercase">Model</p>
                <p className="text-lg font-medium text-gray-900 dark:text-white">
                  {result.model_version}
                  {result.is_heuristic_fallback && (
                    <span className="text-xs text-yellow-600 ml-2">(heuristic)</span>
                  )}
                </p>
              </div>
            </div>

            {/* Confidence Intervals */}
            {result.confidence_intervals.length > 0 && (
              <div>
                <h3 className="text-sm font-medium text-gray-900 dark:text-white mb-2">
                  Confidence Intervals
                </h3>
                <div className="space-y-2">
                  {result.confidence_intervals.map((ci) => {
                    const range = ci.upper - ci.lower;
                    const midpoint = (ci.lower + ci.upper) / 2;
                    const maxUpper = Math.max(...result.confidence_intervals.map((c) => c.upper));
                    return (
                      <div key={ci.level} className="flex items-center gap-3">
                        <div className="w-12 text-sm text-gray-500 dark:text-gray-400">
                          {ci.level}
                        </div>
                        <div className="flex-1 relative h-6 bg-gray-100 dark:bg-gray-700 rounded">
                          <div
                            className="absolute h-6 bg-blue-200 dark:bg-blue-800 rounded"
                            style={{
                              left: `${(ci.lower / maxUpper) * 100}%`,
                              width: `${(range / maxUpper) * 100}%`,
                            }}
                          />
                          <div
                            className="absolute h-6 w-0.5 bg-blue-600"
                            style={{ left: `${(midpoint / maxUpper) * 100}%` }}
                          />
                        </div>
                        <div className="w-56 text-xs text-gray-500 dark:text-gray-400 text-right">
                          {formatCurrency(ci.lower)} - {formatCurrency(ci.upper)}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {isLoading && (
        <div className="p-8 text-center text-gray-500 dark:text-gray-400">Loading estimates...</div>
      )}
      {error && (
        <div className="p-4 text-red-800 bg-red-50 rounded-lg">Failed to load estimates</div>
      )}

      {/* Estimates List */}
      {!isLoading && !error && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Cost Estimates</h2>
          </div>
          {estimates.length === 0 ? (
            <div className="text-center py-12">
              <DollarSign className="mx-auto h-12 w-12 text-gray-400" />
              <h3 className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">
                No estimates
              </h3>
              <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                Use the parametric calculator above or create an estimate from takeoffs.
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                <thead className="bg-gray-50 dark:bg-gray-900">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Name
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Type
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Area (SF)
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      $/SF
                    </th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Total Cost
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Status
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Created
                    </th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                  {estimates.map((est) => (
                    <tr key={est.id} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                      <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white">
                        {est.name}
                      </td>
                      <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400 capitalize">
                        {est.building_type.replace(/_/g, " ")}
                      </td>
                      <td className="px-6 py-4 text-sm text-right text-gray-500 dark:text-gray-400">
                        {est.gross_area_sf.toLocaleString()}
                      </td>
                      <td className="px-6 py-4 text-sm text-right text-gray-900 dark:text-white">
                        {formatCurrency(est.cost_per_sf)}
                      </td>
                      <td className="px-6 py-4 text-sm text-right font-medium text-gray-900 dark:text-white">
                        {formatCurrency(est.total_cost)}
                      </td>
                      <td className="px-6 py-4">
                        <span
                          className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${statusColors[est.status]}`}
                        >
                          {est.status.replace("_", " ")}
                        </span>
                      </td>
                      <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                        {new Date(est.created_at).toLocaleDateString()}
                      </td>
                      <td className="px-6 py-4">
                        <button
                          onClick={() => monteCarloMutation.mutate(est.id)}
                          disabled={monteCarloMutation.isPending}
                          className="flex items-center gap-1 px-3 py-1 text-xs font-medium text-blue-600 border border-blue-300 rounded hover:bg-blue-50 disabled:opacity-50"
                        >
                          <Play className="h-3 w-3" /> Monte Carlo
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
