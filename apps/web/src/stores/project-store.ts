import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface Project {
  id: string;
  name: string;
  status: string;
  project_number?: string | null;
  contract_value?: number | null;
  start_date?: string | null;
  end_date?: string | null;
}

interface ProjectState {
  selectedProjectId: string | null;
  selectedProject: Project | null;
  setProject: (project: Project) => void;
  clearProject: () => void;
  validateProject: (projectIds: string[]) => void;
}

export const useProjectStore = create<ProjectState>()(
  persist(
    (set) => ({
      selectedProjectId: null,
      selectedProject: null,
      setProject: (project: Project) =>
        set({ selectedProjectId: project.id, selectedProject: project }),
      clearProject: () => set({ selectedProjectId: null, selectedProject: null }),
      validateProject: (projectIds: string[]) =>
        set((state) => {
          if (state.selectedProjectId && !projectIds.includes(state.selectedProjectId)) {
            return { selectedProjectId: null, selectedProject: null };
          }
          return state;
        }),
    }),
    {
      name: "constructai-project",
      partialize: (state) => ({
        selectedProjectId: state.selectedProjectId,
        selectedProject: state.selectedProject
          ? {
              id: state.selectedProject.id,
              name: state.selectedProject.name,
              status: state.selectedProject.status,
            }
          : null,
      }),
    },
  ),
);
