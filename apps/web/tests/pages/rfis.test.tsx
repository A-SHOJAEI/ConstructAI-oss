import { expect, test, describe, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";

// Mock next/navigation BEFORE importing component
const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => "/rfis",
  useSearchParams: () => ({ get: vi.fn(() => null) }),
}));

// Mock sonner toast
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// Mock child components that have their own complex imports
vi.mock("@/components/rfis/create-rfi-dialog", () => ({
  CreateRfiDialog: () => <div data-testid="create-rfi-dialog" />,
}));

vi.mock("@/components/rfis/ai-resolution-badge", () => ({
  AIResolutionBadge: ({ aiStatus }: { aiStatus: string | null }) => (
    <span data-testid="ai-badge">{aiStatus ?? "none"}</span>
  ),
}));

vi.mock("@/components/rfis/draft-response-viewer", () => ({
  DraftResponseViewer: () => <div data-testid="draft-viewer" />,
}));

// We'll set up different project store mocks per test via dynamic import
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

import RFIsPage from "@/app/(dashboard)/rfis/page";
import { renderWithProviders } from "../test-utils";

describe("RFIs Page", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockPush.mockReset();
    mockProjectId = "550e8400-e29b-41d4-a716-446655440000";
    global.fetch = vi.fn();
  });

  test("renders RFIs heading", () => {
    renderWithProviders(<RFIsPage />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("RFIs");
  });

  test("renders subtitle text", () => {
    renderWithProviders(<RFIsPage />);
    expect(screen.getByText(/Requests for Information/i)).toBeInTheDocument();
  });

  test("shows 'No project selected' when no project is selected", () => {
    mockProjectId = null;
    renderWithProviders(<RFIsPage />);
    expect(screen.getByText("No project selected")).toBeInTheDocument();
  });

  test("renders New RFI button", () => {
    renderWithProviders(<RFIsPage />);
    expect(screen.getByRole("button", { name: /new rfi/i })).toBeInTheDocument();
  });

  test("renders Export button", () => {
    renderWithProviders(<RFIsPage />);
    expect(screen.getByRole("button", { name: /export/i })).toBeInTheDocument();
  });

  test("renders filter controls (status and priority dropdowns, search)", () => {
    renderWithProviders(<RFIsPage />);
    // Status filter
    expect(screen.getByDisplayValue("All Statuses")).toBeInTheDocument();
    // Priority filter
    expect(screen.getByDisplayValue("All Priorities")).toBeInTheDocument();
    // Search input
    expect(screen.getByPlaceholderText("Search RFIs...")).toBeInTheDocument();
  });

  test("shows loading state initially", () => {
    renderWithProviders(<RFIsPage />);
    expect(screen.getByText("Loading RFIs...")).toBeInTheDocument();
  });

  test("shows empty state when no RFIs returned", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ data: [], meta: { cursor: null, has_more: false } }),
    });

    renderWithProviders(<RFIsPage />);

    await waitFor(() => {
      expect(screen.getByText("No RFIs")).toBeInTheDocument();
    });
  });
});
