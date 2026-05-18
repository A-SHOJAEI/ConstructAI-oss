/**
 * M-63: project-store was completely untested despite holding global
 * selection state that every dashboard page reads from. These tests cover
 * the core operations and — most importantly — the silent clear-on-validate
 * behavior that bit users whose org membership changed.
 */
import { beforeEach, describe, expect, test } from "vitest";
import { useProjectStore } from "@/stores/project-store";

const PROJECT_A = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "Project A",
  status: "active",
};

const PROJECT_B = {
  id: "22222222-2222-2222-2222-222222222222",
  name: "Project B",
  status: "active",
};

describe("project-store", () => {
  beforeEach(() => {
    useProjectStore.getState().clearProject();
    localStorage.clear();
  });

  test("setProject stores both id and full object", () => {
    useProjectStore.getState().setProject(PROJECT_A);
    const s = useProjectStore.getState();
    expect(s.selectedProjectId).toBe(PROJECT_A.id);
    expect(s.selectedProject?.name).toBe("Project A");
  });

  test("clearProject nulls both fields", () => {
    useProjectStore.getState().setProject(PROJECT_A);
    useProjectStore.getState().clearProject();
    const s = useProjectStore.getState();
    expect(s.selectedProjectId).toBeNull();
    expect(s.selectedProject).toBeNull();
  });

  test("validateProject keeps selection when id is in allowed list", () => {
    useProjectStore.getState().setProject(PROJECT_A);
    useProjectStore.getState().validateProject([PROJECT_A.id, PROJECT_B.id]);
    expect(useProjectStore.getState().selectedProjectId).toBe(PROJECT_A.id);
  });

  test("validateProject clears selection when id is not in allowed list", () => {
    // User used to belong to org A, picked Project A. Now org membership
    // changed and Project A is no longer accessible — store must clear.
    useProjectStore.getState().setProject(PROJECT_A);
    useProjectStore.getState().validateProject([PROJECT_B.id]);
    const s = useProjectStore.getState();
    expect(s.selectedProjectId).toBeNull();
    expect(s.selectedProject).toBeNull();
  });

  test("validateProject with empty list clears any current selection", () => {
    useProjectStore.getState().setProject(PROJECT_A);
    useProjectStore.getState().validateProject([]);
    expect(useProjectStore.getState().selectedProjectId).toBeNull();
  });

  test("validateProject is a no-op when nothing is selected", () => {
    useProjectStore.getState().validateProject([PROJECT_A.id]);
    expect(useProjectStore.getState().selectedProjectId).toBeNull();
  });

  test("persists only the minimal project shape", async () => {
    useProjectStore.getState().setProject({
      ...PROJECT_A,
      contract_value: 5_000_000,
      start_date: "2024-01-01",
    });
    // Flush persist middleware to localStorage.
    await new Promise((r) => setTimeout(r, 0));
    const raw = localStorage.getItem("constructai-project");
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw as string);
    // partialize should have dropped contract_value / start_date.
    expect(parsed.state.selectedProject).toEqual({
      id: PROJECT_A.id,
      name: PROJECT_A.name,
      status: PROJECT_A.status,
    });
  });
});
