"use client";

import { useState, useRef, useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore, type Project } from "@/stores/project-store";
import { ChevronDown, Search, FolderOpen, Check } from "lucide-react";

interface ProjectsResponse {
  data: Project[];
  meta?: { cursor: string | null; has_more: boolean };
}

export function ProjectSelector() {
  const { selectedProjectId, selectedProject, setProject } = useProjectStore();
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState("");
  const dropdownRef = useRef<HTMLDivElement>(null);

  const { data } = useQuery<ProjectsResponse>({
    queryKey: ["projects"],
    queryFn: () => apiClient.get<ProjectsResponse>("/api/v1/projects/"),
  });

  const projects = useMemo(() => data?.data ?? [], [data]);
  const filtered = projects.filter((p) => p.name.toLowerCase().includes(search.toLowerCase()));

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Auto-select first project if none selected and projects are available
  useEffect(() => {
    if (!selectedProjectId && projects.length > 0) {
      setProject(projects[0]);
    }
  }, [selectedProjectId, projects, setProject]);

  const statusDot: Record<string, string> = {
    active: "bg-green-500",
    planning: "bg-blue-500",
    on_hold: "bg-yellow-500",
    completed: "bg-gray-400",
  };

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        className="flex items-center gap-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors max-w-[240px]"
      >
        <FolderOpen
          className="h-4 w-4 shrink-0 text-gray-400 dark:text-gray-500"
          aria-hidden="true"
        />
        <span className="truncate">{selectedProject?.name ?? "Select project"}</span>
        <ChevronDown
          className="h-3.5 w-3.5 shrink-0 text-gray-400 dark:text-gray-500"
          aria-hidden="true"
        />
      </button>

      {isOpen && (
        <div className="absolute left-0 top-full z-50 mt-1 w-72 rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 shadow-lg">
          <div className="p-2">
            <div className="flex items-center gap-2 rounded-md border border-gray-200 dark:border-gray-600 px-2.5 py-1.5">
              <Search className="h-4 w-4 text-gray-400 dark:text-gray-500" aria-hidden="true" />
              <input
                type="text"
                placeholder="Search projects..."
                aria-label="Search projects"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full border-0 bg-transparent text-sm outline-none placeholder:text-gray-400 dark:placeholder:text-gray-500 dark:text-gray-200"
                autoFocus
              />
            </div>
          </div>
          <div className="max-h-60 overflow-y-auto px-1 pb-1" role="listbox" aria-label="Projects">
            {filtered.length === 0 && (
              <p className="px-3 py-4 text-center text-sm text-gray-500 dark:text-gray-400">
                No projects found
              </p>
            )}
            {filtered.map((project) => (
              <button
                key={project.id}
                role="option"
                aria-selected={project.id === selectedProjectId}
                onClick={() => {
                  setProject(project);
                  setIsOpen(false);
                  setSearch("");
                }}
                className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm hover:bg-gray-50 dark:hover:bg-gray-700"
              >
                <span
                  className={`h-2 w-2 shrink-0 rounded-full ${statusDot[project.status] ?? "bg-gray-400"}`}
                  aria-hidden="true"
                />
                <span className="sr-only">{project.status} - </span>
                <span className="flex-1 truncate font-medium text-gray-700 dark:text-gray-200">
                  {project.name}
                </span>
                {project.id === selectedProjectId && (
                  <Check className="h-4 w-4 shrink-0 text-blue-600" aria-hidden="true" />
                )}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
