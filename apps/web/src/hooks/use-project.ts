import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useProjectStore } from "@/stores/project-store";

export function useProject() {
  const { selectedProjectId, selectedProject } = useProjectStore();
  return {
    projectId: selectedProjectId,
    project: selectedProject,
    isReady: selectedProjectId !== null,
  };
}

export function useRequiredProject() {
  const { selectedProjectId, selectedProject } = useProjectStore();
  const router = useRouter();

  useEffect(() => {
    if (!selectedProjectId) {
      router.replace("/projects");
    }
  }, [selectedProjectId, router]);

  if (!selectedProjectId) {
    return { projectId: null, project: null, isReady: false as const };
  }

  return {
    projectId: selectedProjectId,
    project: selectedProject!,
    isReady: true as const,
  };
}
