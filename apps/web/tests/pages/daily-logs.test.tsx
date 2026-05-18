import { expect, test, describe, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";

// Mock next/navigation BEFORE importing component
const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => "/daily-logs",
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

import DailyLogsPage from "@/app/(dashboard)/daily-logs/page";
import { renderWithProviders } from "../test-utils";

describe("Daily Logs Page", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockPush.mockReset();
    mockProjectId = "550e8400-e29b-41d4-a716-446655440000";
    global.fetch = vi.fn();
  });

  test("renders Daily Logs heading", () => {
    renderWithProviders(<DailyLogsPage />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Daily Logs");
  });

  test("renders subtitle text", () => {
    renderWithProviders(<DailyLogsPage />);
    expect(screen.getByText("Field reports and daily activity tracking")).toBeInTheDocument();
  });

  test("shows 'No project selected' when no project is selected", () => {
    mockProjectId = null;
    renderWithProviders(<DailyLogsPage />);
    expect(screen.getByText("No project selected")).toBeInTheDocument();
  });

  test("renders New Log button", () => {
    renderWithProviders(<DailyLogsPage />);
    expect(screen.getByRole("button", { name: /new log/i })).toBeInTheDocument();
  });

  test("renders Export button", () => {
    renderWithProviders(<DailyLogsPage />);
    expect(screen.getByRole("button", { name: /export/i })).toBeInTheDocument();
  });

  test("renders Copy Previous button", () => {
    renderWithProviders(<DailyLogsPage />);
    expect(screen.getByRole("button", { name: /copy previous/i })).toBeInTheDocument();
  });

  test("renders status filter dropdown", () => {
    renderWithProviders(<DailyLogsPage />);
    expect(screen.getByDisplayValue("All Statuses")).toBeInTheDocument();
  });

  test("shows empty state when no logs returned", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ data: [], meta: { cursor: null, has_more: false } }),
    });

    renderWithProviders(<DailyLogsPage />);

    await waitFor(() => {
      expect(
        screen.getByText("No daily logs found. Create one to get started."),
      ).toBeInTheDocument();
    });
  });
});
