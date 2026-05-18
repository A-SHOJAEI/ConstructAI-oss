import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

// Mock next/navigation
const mockReplace = vi.fn();
const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: mockReplace, back: vi.fn() }),
  usePathname: () => "/test",
  useSearchParams: () => new URLSearchParams(),
}));

// Import after mocks are set up
import { useProject, useRequiredProject } from "@/hooks/use-project";
import { useProjectStore } from "@/stores/project-store";

describe("useProject", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockReplace.mockReset();
    mockPush.mockReset();
    // Clear the project store before each test
    useProjectStore.getState().clearProject();
  });

  it("returns null projectId when no project is selected", () => {
    const { result } = renderHook(() => useProject());

    expect(result.current.projectId).toBeNull();
    expect(result.current.project).toBeNull();
    expect(result.current.isReady).toBe(false);
  });

  it("returns project data when a project is selected", () => {
    // Set a project in the store before rendering
    act(() => {
      useProjectStore.getState().setProject({
        id: "proj-123",
        name: "Test Bridge",
        status: "active",
      });
    });

    const { result } = renderHook(() => useProject());

    expect(result.current.projectId).toBe("proj-123");
    expect(result.current.project).toEqual({
      id: "proj-123",
      name: "Test Bridge",
      status: "active",
    });
    expect(result.current.isReady).toBe(true);
  });

  it("updates when project store changes", () => {
    const { result } = renderHook(() => useProject());

    expect(result.current.isReady).toBe(false);

    act(() => {
      useProjectStore.getState().setProject({
        id: "proj-456",
        name: "Office Tower",
        status: "planning",
      });
    });

    expect(result.current.projectId).toBe("proj-456");
    expect(result.current.project?.name).toBe("Office Tower");
    expect(result.current.isReady).toBe(true);
  });

  it("resets when project is cleared", () => {
    act(() => {
      useProjectStore.getState().setProject({
        id: "proj-789",
        name: "School Renovation",
        status: "active",
      });
    });

    const { result } = renderHook(() => useProject());
    expect(result.current.isReady).toBe(true);

    act(() => {
      useProjectStore.getState().clearProject();
    });

    expect(result.current.projectId).toBeNull();
    expect(result.current.project).toBeNull();
    expect(result.current.isReady).toBe(false);
  });
});

describe("useRequiredProject", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockReplace.mockReset();
    mockPush.mockReset();
    useProjectStore.getState().clearProject();
  });

  it("redirects to /projects when no project is selected", () => {
    renderHook(() => useRequiredProject());

    expect(mockReplace).toHaveBeenCalledWith("/projects");
  });

  it("returns isReady false when no project is selected", () => {
    const { result } = renderHook(() => useRequiredProject());

    expect(result.current.isReady).toBe(false);
    expect(result.current.projectId).toBeNull();
    expect(result.current.project).toBeNull();
  });

  it("does not redirect when project is selected", () => {
    act(() => {
      useProjectStore.getState().setProject({
        id: "proj-123",
        name: "Highway Bridge",
        status: "active",
      });
    });

    renderHook(() => useRequiredProject());

    expect(mockReplace).not.toHaveBeenCalled();
  });

  it("returns project data and isReady true when project is selected", () => {
    act(() => {
      useProjectStore.getState().setProject({
        id: "proj-123",
        name: "Highway Bridge",
        status: "active",
      });
    });

    const { result } = renderHook(() => useRequiredProject());

    expect(result.current.isReady).toBe(true);
    expect(result.current.projectId).toBe("proj-123");
    expect(result.current.project).toEqual({
      id: "proj-123",
      name: "Highway Bridge",
      status: "active",
    });
  });
});
