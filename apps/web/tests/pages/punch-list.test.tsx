import { expect, test, describe, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";

// Mock next/navigation BEFORE importing component
const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => "/punch-list",
  useSearchParams: () => ({ get: vi.fn(() => null) }),
}));

// Mock sonner toast
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// Mock child components
vi.mock("@/components/punch-list/create-punch-list-dialog", () => ({
  CreatePunchListDialog: () => <div data-testid="create-punch-list-dialog" />,
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

import PunchListPage from "@/app/(dashboard)/punch-list/page";
import { renderWithProviders } from "../test-utils";

describe("Punch List Page", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockPush.mockReset();
    mockProjectId = "550e8400-e29b-41d4-a716-446655440000";
    global.fetch = vi.fn();
  });

  test("renders Punch List heading", () => {
    renderWithProviders(<PunchListPage />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Punch List");
  });

  test("renders subtitle text", () => {
    renderWithProviders(<PunchListPage />);
    expect(screen.getByText("Track and resolve punch list items")).toBeInTheDocument();
  });

  test("shows 'No project selected' when no project is selected", () => {
    mockProjectId = null;
    renderWithProviders(<PunchListPage />);
    expect(screen.getByText("No project selected")).toBeInTheDocument();
  });

  test("renders New Item button", () => {
    renderWithProviders(<PunchListPage />);
    expect(screen.getByRole("button", { name: /new item/i })).toBeInTheDocument();
  });

  test("renders Export button", () => {
    renderWithProviders(<PunchListPage />);
    expect(screen.getByRole("button", { name: /export/i })).toBeInTheDocument();
  });

  test("renders filter controls (status, priority dropdowns, and search)", () => {
    renderWithProviders(<PunchListPage />);
    expect(screen.getByDisplayValue("All Statuses")).toBeInTheDocument();
    expect(screen.getByDisplayValue("All Priorities")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Search items...")).toBeInTheDocument();
  });

  test("shows empty state when no items returned", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ data: [], meta: { cursor: null, has_more: false } }),
    });

    renderWithProviders(<PunchListPage />);

    await waitFor(() => {
      expect(
        screen.getByText("No punch list items found. Create one to get started."),
      ).toBeInTheDocument();
    });
  });
});
