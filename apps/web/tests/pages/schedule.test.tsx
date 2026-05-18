import { expect, test, describe, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";

// Mock next/navigation BEFORE importing component
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/schedule",
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

import SchedulePage from "@/app/(dashboard)/schedule/page";
import { renderWithProviders } from "../test-utils";

describe("Schedule Page", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockProjectId = "550e8400-e29b-41d4-a716-446655440000";
    global.fetch = vi.fn();
  });

  test("renders Schedule heading", () => {
    renderWithProviders(<SchedulePage />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Schedule");
  });

  test("renders subtitle text", () => {
    renderWithProviders(<SchedulePage />);
    expect(screen.getByText("Project scheduling and activity tracking")).toBeInTheDocument();
  });

  test("shows 'No project selected' when no project is selected", () => {
    mockProjectId = null;
    renderWithProviders(<SchedulePage />);
    expect(screen.getByText("No project selected")).toBeInTheDocument();
  });

  test("renders summary stat cards", () => {
    renderWithProviders(<SchedulePage />);
    expect(screen.getByText("Total Activities")).toBeInTheDocument();
    expect(screen.getByText("Critical Path Length")).toBeInTheDocument();
    expect(screen.getByText("On-Track")).toBeInTheDocument();
  });

  test("shows loading state initially", () => {
    renderWithProviders(<SchedulePage />);
    expect(screen.getByText("Loading schedule data...")).toBeInTheDocument();
  });

  test("shows empty state when no activities returned", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: [], total: 0 }),
    });

    renderWithProviders(<SchedulePage />);

    await waitFor(() => {
      expect(screen.getByText("No activities found")).toBeInTheDocument();
    });
  });

  test("shows error state when fetch fails", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("Server error"));

    renderWithProviders(<SchedulePage />);

    await waitFor(() => {
      expect(screen.getByText("Failed to load schedule")).toBeInTheDocument();
    });
  });
});
