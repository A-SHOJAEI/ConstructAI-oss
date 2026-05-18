import { expect, test, describe, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";

// Mock next/navigation BEFORE importing component
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/controls",
  useSearchParams: () => ({ get: vi.fn(() => null) }),
}));

let mockProjectId: string | null = "550e8400-e29b-41d4-a716-446655440000";

vi.mock("@/stores/project-store", () => ({
  useProjectStore: (selector?: (state: Record<string, unknown>) => unknown) => {
    const state = {
      selectedProjectId: mockProjectId,
      selectedProject: mockProjectId
        ? { id: mockProjectId, name: "Test Project", status: "active" }
        : null,
      setProject: vi.fn(),
      clearProject: vi.fn(),
    };
    if (typeof selector === "function") return selector(state);
    return state;
  },
}));

vi.mock("@/components/no-project-selected", () => ({
  NoProjectSelected: () => (
    <div>
      <h2>No project selected</h2>
      <p>Select a project from the dropdown in the header to view this page.</p>
    </div>
  ),
}));

// Mock controls API
vi.mock("@/lib/controls-api", () => ({
  controlsApi: {
    evmSnapshots: vi.fn().mockResolvedValue({ data: [] }),
    scurve: vi.fn().mockResolvedValue({ data_points: [], bac: 0 }),
    changeOrders: vi.fn().mockResolvedValue({ data: [] }),
    weatherForecast: vi.fn().mockResolvedValue({ forecast: [] }),
  },
}));

// Mock chart components to avoid complex rendering dependencies
vi.mock("@/components/controls/s-curve-chart", () => ({
  SCurveChart: () => <div data-testid="s-curve-chart">S-Curve Chart</div>,
}));

vi.mock("@/components/controls/evm-trend-chart", () => ({
  EVMTrendChart: () => <div data-testid="evm-trend-chart">EVM Trend Chart</div>,
}));

vi.mock("@/components/controls/eac-forecast-chart", () => ({
  EACForecastChart: () => <div data-testid="eac-forecast-chart">EAC Forecast Chart</div>,
}));

vi.mock("@/components/controls/monte-carlo-histogram", () => ({
  MonteCarloHistogram: () => <div data-testid="monte-carlo-histogram">Monte Carlo Histogram</div>,
}));

vi.mock("@/components/controls/weather-strip", () => ({
  WeatherStrip: () => <div data-testid="weather-strip">Weather Strip</div>,
}));

import ControlsPage from "@/app/(dashboard)/controls/page";
import { renderWithProviders } from "../test-utils";

describe("Controls Page", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockProjectId = "550e8400-e29b-41d4-a716-446655440000";
    global.fetch = vi.fn();
  });

  test("renders Project Controls heading", () => {
    renderWithProviders(<ControlsPage />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Project Controls");
  });

  test("renders subtitle text", () => {
    renderWithProviders(<ControlsPage />);
    expect(
      screen.getByText("Earned value, cost forecasting, and schedule risk analysis"),
    ).toBeInTheDocument();
  });

  test("shows 'No project selected' when no project is selected", () => {
    mockProjectId = null;
    renderWithProviders(<ControlsPage />);
    expect(screen.getByText("No project selected")).toBeInTheDocument();
  });

  test("renders SPI and CPI metric labels", () => {
    renderWithProviders(<ControlsPage />);
    expect(screen.getByText(/Schedule Performance Index/i)).toBeInTheDocument();
    expect(screen.getByText(/Cost Performance Index/i)).toBeInTheDocument();
  });

  test("renders tab navigation (Overview, Forecast, Weather)", () => {
    renderWithProviders(<ControlsPage />);
    expect(screen.getByRole("button", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Forecast" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Weather" })).toBeInTheDocument();
  });

  test("renders Overview tab content by default (charts and change orders)", () => {
    renderWithProviders(<ControlsPage />);
    expect(screen.getByTestId("s-curve-chart")).toBeInTheDocument();
    expect(screen.getByTestId("evm-trend-chart")).toBeInTheDocument();
    expect(screen.getByText("Change Orders")).toBeInTheDocument();
  });

  test("shows loading skeletons for SPI and CPI while data is loading", () => {
    renderWithProviders(<ControlsPage />);
    // While snapshots are loading, the SPI/CPI cards show animate-pulse loading skeletons
    const skeletons = document.querySelectorAll(".animate-pulse");
    expect(skeletons.length).toBeGreaterThanOrEqual(2);
  });
});
