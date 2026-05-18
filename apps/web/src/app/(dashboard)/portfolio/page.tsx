"use client";

import { useEffect, useState } from "react";
import { ProjectHealthGrid } from "@/components/portfolio/project-health-grid";
import { CrossProjectBenchmark } from "@/components/portfolio/cross-project-benchmark";
import { PortfolioMap } from "@/components/portfolio/portfolio-map";
import { portfolioApi } from "@/lib/portfolio-api";

interface PortfolioData {
  projects: Array<{
    project_id: string;
    project_name: string;
    spi: number | null;
    cpi: number | null;
    safety_score: number | null;
    quality_score: number | null;
    overall_health: string;
    active_risks: number;
    open_rfis: number;
  }>;
  total_projects: number;
  projects_on_track: number;
  projects_at_risk: number;
  projects_critical: number;
  portfolio_spi: number | null;
  portfolio_cpi: number | null;
}

export default function PortfolioPage() {
  const [portfolio, setPortfolio] = useState<PortfolioData | null>(null);
  const [benchmarks, setBenchmarks] = useState<{ benchmarks: Array<unknown> }>({
    benchmarks: [],
  });
  const [activeTab, setActiveTab] = useState<"grid" | "benchmark" | "map">("grid");
  const [isStub, setIsStub] = useState(false);

  useEffect(() => {
    portfolioApi
      .getPortfolio()
      .then((data) => {
        setPortfolio(data);
      })
      .catch(() => setIsStub(true));
    portfolioApi
      .getBenchmarks()
      .then(setBenchmarks)
      .catch(() => {});
  }, []);

  if (isStub) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <h2 className="text-xl font-semibold text-gray-600">Portfolio Analytics</h2>
          <p className="text-gray-400 mt-2">Coming soon — this feature is under development.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-6 text-gray-900 dark:text-white">
        Executive Portfolio Dashboard
      </h1>

      <div className="grid grid-cols-4 gap-4 mb-6">
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow p-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">Total Projects</p>
          <p className="text-3xl font-bold text-gray-900 dark:text-white">
            {portfolio?.total_projects ?? 0}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow p-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">On Track</p>
          <p className="text-3xl font-bold text-green-600">{portfolio?.projects_on_track ?? 0}</p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow p-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">At Risk</p>
          <p className="text-3xl font-bold text-yellow-600">{portfolio?.projects_at_risk ?? 0}</p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow p-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">Critical</p>
          <p className="text-3xl font-bold text-red-600">{portfolio?.projects_critical ?? 0}</p>
        </div>
      </div>

      <div className="flex gap-2 mb-4">
        {(["grid", "benchmark", "map"] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2 rounded-lg ${
              activeTab === tab
                ? "bg-blue-600 text-white"
                : "bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300"
            }`}
          >
            {tab === "grid" ? "Health Grid" : tab === "benchmark" ? "Benchmarks" : "Map View"}
          </button>
        ))}
      </div>

      <div className="bg-white dark:bg-gray-800 rounded-lg shadow p-6">
        {activeTab === "grid" && <ProjectHealthGrid projects={portfolio?.projects ?? []} />}
        {activeTab === "benchmark" && <CrossProjectBenchmark benchmarks={benchmarks.benchmarks} />}
        {activeTab === "map" && <PortfolioMap projects={portfolio?.projects ?? []} />}
      </div>
    </div>
  );
}
