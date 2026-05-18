"use client";

import { useState, useMemo, Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { controlsApi } from "@/lib/controls-api";
import type { EVMSnapshot } from "@/lib/controls-api";
import { SCurveChart } from "@/components/controls/s-curve-chart";
import { EVMTrendChart } from "@/components/controls/evm-trend-chart";
import { EACForecastChart } from "@/components/controls/eac-forecast-chart";
import { MonteCarloHistogram } from "@/components/controls/monte-carlo-histogram";
import { WeatherStrip } from "@/components/controls/weather-strip";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";

function getMetricColor(value: number | null): string {
  if (value === null) return "text-gray-400";
  if (value > 0.95) return "text-green-600";
  if (value > 0.85) return "text-yellow-600";
  return "text-red-600";
}

function getMetricBg(value: number | null): string {
  if (value === null) return "bg-gray-50 border-gray-200";
  if (value > 0.95) return "bg-green-50 border-green-200";
  if (value > 0.85) return "bg-yellow-50 border-yellow-200";
  return "bg-red-50 border-red-200";
}

function formatCurrency(value: number | null): string {
  if (value === null || value === undefined) return "N/A";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value);
}

const tabs = ["Overview", "Forecast", "Weather"] as const;
type Tab = (typeof tabs)[number];

export default function ControlsPage() {
  return (
    <Suspense fallback={<div className="p-6 text-gray-400">Loading...</div>}>
      <ControlsPageContent />
    </Suspense>
  );
}

function ControlsPageContent() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const [activeTab, setActiveTab] = useState<Tab>("Overview");

  // EVM snapshots (list for trend data)
  const { data: snapshotsRes, isLoading: snapLoading } = useQuery({
    queryKey: ["evm-snapshots", projectId],
    queryFn: () => controlsApi.evmSnapshots(projectId!),
    enabled: !!projectId,
  });

  // S-curve data
  const { data: scurveData } = useQuery({
    queryKey: ["s-curve", projectId],
    queryFn: () => controlsApi.scurve(projectId!),
    enabled: !!projectId,
  });

  // Change orders (from controls endpoint)
  const { data: cosRes, isLoading: coLoading } = useQuery({
    queryKey: ["change-orders", projectId],
    queryFn: () => controlsApi.changeOrders(projectId!),
    enabled: !!projectId,
  });

  const isValidUUID = (id: string) =>
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);

  // Fetch weather forecast from the scheduling weather API
  const { data: weatherResponse } = useQuery({
    queryKey: ["weather-forecast", projectId],
    queryFn: async () => {
      try {
        const today = new Date();
        const endDate = new Date();
        endDate.setDate(today.getDate() + 6);
        const startStr = today.toISOString().split("T")[0];
        const endStr = endDate.toISOString().split("T")[0];
        const res = await controlsApi.weatherForecast(projectId!, startStr, endStr);
        return res;
      } catch {
        return null;
      }
    },
    enabled: !!projectId && isValidUUID(projectId),
    staleTime: 30 * 60 * 1000, // 30 minutes
  });

  // Use API weather data or fall back to static placeholder if unavailable
  const weatherForecast = useMemo(() => {
    if (weatherResponse?.forecast && weatherResponse.forecast.length > 0) {
      return weatherResponse.forecast;
    }
    // Static fallback so the page remains functional when the weather API is unavailable
    return Array.from({ length: 7 }, (_, i) => {
      const d = new Date();
      d.setDate(d.getDate() + i);
      return {
        date: d.toISOString().split("T")[0],
        temperature_high: 65,
        temperature_low: 45,
        conditions: "clear",
        precipitation_inches: 0,
        wind_mph: 8,
        impact_score: 90,
      };
    });
  }, [weatherResponse]);

  // Derived data (computed after all hooks to avoid hook-order violations)
  const snapshots = snapshotsRes?.data ?? [];
  const latest: EVMSnapshot | null = snapshots.length
    ? snapshots.reduce((a, b) => (a.snapshot_date > b.snapshot_date ? a : b))
    : null;
  const changeOrders = cosRes?.data ?? [];

  if (!projectId) return <NoProjectSelected />;

  const coStatusColors: Record<string, string> = {
    pending: "bg-yellow-100 text-yellow-800",
    approved: "bg-green-100 text-green-800",
    rejected: "bg-red-100 text-red-800",
    draft: "bg-gray-100 text-gray-800",
  };

  return (
    <div className="p-4 md:p-6 space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Project Controls</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Earned value, cost forecasting, and schedule risk analysis
        </p>
      </div>

      {/* SPI / CPI Hero Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div
          className={`rounded-lg border-2 p-6 ${getMetricBg(latest ? Number(latest.spi) : null)}`}
        >
          <p className="text-sm font-medium text-gray-600 dark:text-gray-300 uppercase tracking-wider">
            Schedule Performance Index (SPI)
          </p>
          {snapLoading ? (
            <div className="h-12 bg-gray-200 dark:bg-gray-700 rounded w-24 mt-2 animate-pulse" />
          ) : (
            <p
              className={`text-5xl font-bold mt-2 ${getMetricColor(latest ? Number(latest.spi) : null)}`}
            >
              {latest ? Number(latest.spi).toFixed(2) : "N/A"}
            </p>
          )}
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-2">{`> 0.95 On Schedule · > 0.85 Caution · < 0.85 Behind`}</p>
        </div>
        <div
          className={`rounded-lg border-2 p-6 ${getMetricBg(latest ? Number(latest.cpi) : null)}`}
        >
          <p className="text-sm font-medium text-gray-600 dark:text-gray-300 uppercase tracking-wider">
            Cost Performance Index (CPI)
          </p>
          {snapLoading ? (
            <div className="h-12 bg-gray-200 dark:bg-gray-700 rounded w-24 mt-2 animate-pulse" />
          ) : (
            <p
              className={`text-5xl font-bold mt-2 ${getMetricColor(latest ? Number(latest.cpi) : null)}`}
            >
              {latest ? Number(latest.cpi).toFixed(2) : "N/A"}
            </p>
          )}
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-2">{`> 0.95 Under Budget · > 0.85 Caution · < 0.85 Over Budget`}</p>
        </div>
      </div>

      {/* EVM Summary Cards */}
      {latest && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[
            { label: "Planned Value", value: formatCurrency(Number(latest.pv)) },
            { label: "Earned Value", value: formatCurrency(Number(latest.ev)) },
            { label: "Actual Cost", value: formatCurrency(Number(latest.ac)) },
            { label: "EAC", value: formatCurrency(Number(latest.eac)) },
          ].map((m) => (
            <div
              key={m.label}
              className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4"
            >
              <p className="text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                {m.label}
              </p>
              <p className="text-lg font-bold text-gray-900 dark:text-white mt-1">{m.value}</p>
            </div>
          ))}
        </div>
      )}

      {/* Tabs */}
      <div className="border-b border-gray-200 dark:border-gray-700">
        <nav className="flex gap-4">
          {tabs.map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`pb-3 px-1 text-sm font-medium border-b-2 transition-colors ${
                activeTab === tab
                  ? "border-primary text-primary"
                  : "border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
              }`}
            >
              {tab}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab Content */}
      {activeTab === "Overview" && (
        <div className="space-y-6">
          <SCurveChart data={scurveData?.data_points ?? []} bac={Number(scurveData?.bac ?? 0)} />
          <EVMTrendChart snapshots={snapshots} />

          {/* Change Orders Table */}
          <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Change Orders</h2>
            </div>
            {coLoading && (
              <div className="p-8 text-center text-gray-500 dark:text-gray-400">Loading...</div>
            )}
            {!coLoading && changeOrders.length === 0 && (
              <div className="p-8 text-center text-gray-500 dark:text-gray-400">
                No change orders found.
              </div>
            )}
            {!coLoading && changeOrders.length > 0 && (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                  <thead className="bg-gray-50 dark:bg-gray-900">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                        Title
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                        Status
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                        Amount
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                        Schedule Impact
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase">
                        Created
                      </th>
                    </tr>
                  </thead>
                  <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
                    {changeOrders.map((co) => (
                      <tr key={co.id} className="hover:bg-gray-50 dark:hover:bg-gray-700">
                        <td className="px-6 py-4 text-sm font-medium text-gray-900 dark:text-white">
                          {co.title}
                        </td>
                        <td className="px-6 py-4">
                          <span
                            className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${coStatusColors[co.status] ?? "bg-gray-100 text-gray-800"}`}
                          >
                            {co.status}
                          </span>
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-900 dark:text-white">
                          {formatCurrency(co.approved_amount)}
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                          {co.schedule_impact_days}d
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-500 dark:text-gray-400">
                          {new Date(co.created_at).toLocaleDateString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {activeTab === "Forecast" && (
        <div className="space-y-6">
          <EACForecastChart latest={latest} />
          <MonteCarloHistogram histogramData={[]} p50={0} p80={0} p90={0} meanDuration={0} />
          <p className="text-xs text-gray-500 dark:text-gray-400 text-center">
            Click &quot;Run Monte Carlo&quot; to generate schedule risk simulation data.
          </p>
        </div>
      )}

      {activeTab === "Weather" && <WeatherStrip forecast={weatherForecast} />}
    </div>
  );
}
