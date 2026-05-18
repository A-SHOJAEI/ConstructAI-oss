import { apiClient } from "./api-client";

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

interface BenchmarkData {
  benchmarks: unknown[];
}

interface MapData {
  features: unknown[];
}

export const portfolioApi = {
  getPortfolio: () => apiClient.get<PortfolioData>("/api/v1/portfolio"),

  getBenchmarks: () => apiClient.get<BenchmarkData>("/api/v1/portfolio/benchmarks"),

  getPortfolioMap: () => apiClient.get<MapData>("/api/v1/portfolio/map"),
};
