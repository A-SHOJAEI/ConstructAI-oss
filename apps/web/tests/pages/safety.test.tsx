import { expect, test, describe, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";

// Mock next/navigation BEFORE importing component
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/safety",
  useSearchParams: () => ({ get: vi.fn(() => null) }),
}));

// Mock sonner toast
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
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

// Mock the safety API
vi.mock("@/lib/safety-api", () => ({
  safetyApi: {
    getStats: vi.fn().mockResolvedValue({
      total: 10,
      by_priority: { P1_critical: 2, P2_high: 3 },
      acknowledged_count: 4,
      false_positive_count: 1,
    }),
    getCameras: vi.fn().mockResolvedValue([]),
    getAlerts: vi.fn().mockResolvedValue([]),
  },
}));

// Mock the websocket hook
vi.mock("@/hooks/use-websocket", () => ({
  useSafetyWebSocket: () => ({
    connected: false,
    lastAlert: null,
    lastDetection: null,
    alerts: [],
    error: null,
  }),
}));

// Mock child components to avoid complex dependencies
vi.mock("@/components/safety/camera-grid", () => ({
  CameraGrid: () => <div data-testid="camera-grid">Camera Grid</div>,
}));

vi.mock("@/components/safety/alert-timeline", () => ({
  AlertTimeline: () => <div data-testid="alert-timeline">Alert Timeline</div>,
}));

vi.mock("@/components/safety/alert-detail-modal", () => ({
  AlertDetailModal: () => <div data-testid="alert-detail-modal" />,
}));

vi.mock("@/components/safety/predictive-risk-panel", () => ({
  PredictiveRiskPanel: () => <div data-testid="predictive-risk-panel">Predictive Risk</div>,
}));

import SafetyDashboardPage from "@/app/(dashboard)/safety/page";
import { renderWithProviders } from "../test-utils";

describe("Safety Dashboard Page", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockProjectId = "550e8400-e29b-41d4-a716-446655440000";
    global.fetch = vi.fn();
  });

  test("renders Safety Monitoring heading", () => {
    renderWithProviders(<SafetyDashboardPage />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Safety Monitoring");
  });

  test("renders subtitle text", () => {
    renderWithProviders(<SafetyDashboardPage />);
    expect(screen.getByText("Real-time construction site safety dashboard")).toBeInTheDocument();
  });

  test("shows 'No project selected' when no project is selected", () => {
    mockProjectId = null;
    renderWithProviders(<SafetyDashboardPage />);
    expect(screen.getByText("No project selected")).toBeInTheDocument();
  });

  test("renders connection status indicator", () => {
    renderWithProviders(<SafetyDashboardPage />);
    expect(screen.getByText("Disconnected")).toBeInTheDocument();
  });

  test("renders Camera Feeds section", () => {
    renderWithProviders(<SafetyDashboardPage />);
    expect(screen.getByText("Camera Feeds")).toBeInTheDocument();
    expect(screen.getByTestId("camera-grid")).toBeInTheDocument();
  });

  test("renders Alert Timeline section", () => {
    renderWithProviders(<SafetyDashboardPage />);
    // The h2 heading and the mock component both contain "Alert Timeline", so use getAllByText
    const matches = screen.getAllByText("Alert Timeline");
    expect(matches.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByTestId("alert-timeline")).toBeInTheDocument();
  });

  test("renders Predictive Risk panel", () => {
    renderWithProviders(<SafetyDashboardPage />);
    expect(screen.getByTestId("predictive-risk-panel")).toBeInTheDocument();
  });
});
