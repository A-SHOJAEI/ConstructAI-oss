import { expect, test, describe, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";

// Mock next/navigation BEFORE importing component
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/documents",
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

import DocumentsPage from "@/app/(dashboard)/documents/page";
import { renderWithProviders } from "../test-utils";

describe("Documents Page", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockProjectId = "550e8400-e29b-41d4-a716-446655440000";
    global.fetch = vi.fn();
  });

  test("renders Documents heading", () => {
    renderWithProviders(<DocumentsPage />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Documents");
  });

  test("renders subtitle text", () => {
    renderWithProviders(<DocumentsPage />);
    expect(
      screen.getByText("Upload, manage, and track construction documents"),
    ).toBeInTheDocument();
  });

  test("shows 'No project selected' when no project is selected", () => {
    mockProjectId = null;
    renderWithProviders(<DocumentsPage />);
    expect(screen.getByText("No project selected")).toBeInTheDocument();
  });

  test("renders drag-and-drop upload area", () => {
    renderWithProviders(<DocumentsPage />);
    expect(screen.getByText("Drag and drop files here, or click to browse")).toBeInTheDocument();
  });

  test("renders supported file types text", () => {
    renderWithProviders(<DocumentsPage />);
    expect(screen.getByText(/Supports PDF, DWG, IFC, XLSX/i)).toBeInTheDocument();
  });

  test("renders status filter buttons", () => {
    renderWithProviders(<DocumentsPage />);
    expect(screen.getByRole("button", { name: "All" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Processing" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Complete" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Failed" })).toBeInTheDocument();
  });

  test("shows loading state initially", () => {
    renderWithProviders(<DocumentsPage />);
    expect(screen.getByText("Loading documents...")).toBeInTheDocument();
  });

  test("shows empty state when no documents returned", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ items: [], total: 0 }),
    });

    renderWithProviders(<DocumentsPage />);

    await waitFor(() => {
      expect(screen.getByText("No documents uploaded")).toBeInTheDocument();
    });
  });
});
