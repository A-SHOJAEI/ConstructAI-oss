import { expect, test, describe, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";

// Mock next/navigation BEFORE importing component
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/estimating",
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

import EstimatingPage from "@/app/(dashboard)/estimating/page";
import { renderWithProviders } from "../test-utils";

describe("Estimating Page", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockProjectId = "550e8400-e29b-41d4-a716-446655440000";
    global.fetch = vi.fn();
  });

  test("renders Estimating heading", () => {
    renderWithProviders(<EstimatingPage />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Estimating");
  });

  test("renders subtitle text", () => {
    renderWithProviders(<EstimatingPage />);
    expect(screen.getByText(/Cost estimates, parametric model predictions/i)).toBeInTheDocument();
  });

  test("shows 'No project selected' when no project is selected", () => {
    mockProjectId = null;
    renderWithProviders(<EstimatingPage />);
    expect(screen.getByText("No project selected")).toBeInTheDocument();
  });

  test("renders Parametric Cost Calculator section", () => {
    renderWithProviders(<EstimatingPage />);
    expect(screen.getByText("Parametric Cost Calculator")).toBeInTheDocument();
  });

  test("renders calculator input fields", () => {
    renderWithProviders(<EstimatingPage />);
    expect(screen.getByText("Building Type")).toBeInTheDocument();
    expect(screen.getByText("Gross Area (SF)")).toBeInTheDocument();
    expect(screen.getByText("Stories")).toBeInTheDocument();
    expect(screen.getByText("Quality")).toBeInTheDocument();
    expect(screen.getByText("Location Factor")).toBeInTheDocument();
  });

  test("renders Predict Cost button", () => {
    renderWithProviders(<EstimatingPage />);
    expect(screen.getByRole("button", { name: /predict cost/i })).toBeInTheDocument();
  });

  test("shows empty estimates state when no estimates returned", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ estimates: [], total: 0 }),
    });

    renderWithProviders(<EstimatingPage />);

    await waitFor(() => {
      expect(screen.getByText("No estimates")).toBeInTheDocument();
    });
  });
});
