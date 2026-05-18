import { expect, test, describe, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";

// Mock next/navigation BEFORE importing component
const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => "/projects",
  useSearchParams: () => ({ get: vi.fn(() => null) }),
}));

// Mock project store
vi.mock("@/stores/project-store", () => ({
  useProjectStore: () => ({
    selectedProjectId: null,
    selectedProject: null,
    setProject: vi.fn(),
    clearProject: vi.fn(),
    validateProject: vi.fn(),
  }),
}));

// Mock sonner toast
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// Must import after mocks
import ProjectsPage from "@/app/(dashboard)/projects/page";
import { renderWithProviders } from "../test-utils";

describe("Projects Page", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockPush.mockReset();
    global.fetch = vi.fn();
  });

  test("renders Projects heading", () => {
    renderWithProviders(<ProjectsPage />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Projects");
  });

  test("renders subtitle text", () => {
    renderWithProviders(<ProjectsPage />);
    expect(screen.getByText("Manage and monitor your construction projects")).toBeInTheDocument();
  });

  test("renders New Project button", () => {
    renderWithProviders(<ProjectsPage />);
    expect(screen.getByRole("button", { name: /new project/i })).toBeInTheDocument();
  });

  test("shows loading skeleton initially", () => {
    renderWithProviders(<ProjectsPage />);
    // The loading skeleton uses animate-pulse divs
    const skeletons = document.querySelectorAll(".animate-pulse");
    expect(skeletons.length).toBeGreaterThan(0);
  });

  test("shows empty state when no projects returned", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ data: [], meta: { cursor: null, has_more: false } }),
    });

    renderWithProviders(<ProjectsPage />);

    await waitFor(() => {
      expect(screen.getByText("No projects yet")).toBeInTheDocument();
    });
  });

  test("renders project cards when data loads", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        data: [
          {
            id: "proj-1",
            name: "Downtown Office Tower",
            status: "active",
            contract_value: 5000000,
            start_date: "2026-01-01",
          },
          {
            id: "proj-2",
            name: "Residential Complex",
            status: "planning",
            contract_value: 3000000,
            start_date: null,
          },
        ],
        meta: { cursor: null, has_more: false },
      }),
    });

    renderWithProviders(<ProjectsPage />);

    await waitFor(() => {
      expect(screen.getByText("Downtown Office Tower")).toBeInTheDocument();
      expect(screen.getByText("Residential Complex")).toBeInTheDocument();
    });
  });

  test("shows error state when fetch fails", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("Network error"));

    renderWithProviders(<ProjectsPage />);

    await waitFor(() => {
      expect(screen.getByText("Failed to load projects")).toBeInTheDocument();
    });
  });
});
