"use client";

import { useQuery } from "@tanstack/react-query";
import { drawingsApi } from "@/lib/drawings-api";
import type { DrawingSet, DrawingSetWithDrawings } from "@/lib/drawings-api";
import { PenTool, ChevronDown, ChevronRight, FileText, Layers } from "lucide-react";
import { useState } from "react";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";

const disciplineColors: Record<string, string> = {
  architectural: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300",
  structural: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300",
  mechanical: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300",
  electrical: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-300",
  plumbing: "bg-cyan-100 text-cyan-800 dark:bg-cyan-900 dark:text-cyan-300",
  civil: "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-300",
  landscape: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-300",
};

function DrawingSetAccordion({ set, projectId }: { set: DrawingSet; projectId: string }) {
  const [expanded, setExpanded] = useState(false);

  const { data: fullSet } = useQuery({
    queryKey: ["drawing-set", projectId, set.id],
    queryFn: () => drawingsApi.getSet(projectId, set.id),
    enabled: expanded,
  });

  const drawings = (fullSet as DrawingSetWithDrawings)?.drawings ?? [];

  return (
    <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between p-4 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
      >
        <div className="flex items-center gap-3">
          {expanded ? (
            <ChevronDown className="h-4 w-4 text-gray-400 dark:text-gray-500" />
          ) : (
            <ChevronRight className="h-4 w-4 text-gray-400 dark:text-gray-500" />
          )}
          <Layers className="h-5 w-5 text-gray-500 dark:text-gray-400" />
          <div className="text-left">
            <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">{set.name}</h3>
            {set.description && (
              <p className="text-xs text-gray-500 dark:text-gray-400">{set.description}</p>
            )}
          </div>
        </div>
        <span
          className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium capitalize ${disciplineColors[set.discipline.toLowerCase()] ?? "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300"}`}
        >
          {set.discipline}
        </span>
      </button>

      {expanded && (
        <div className="border-t border-gray-100 dark:border-gray-800 px-4 py-3">
          {drawings.length === 0 ? (
            <p className="text-xs text-gray-400 dark:text-gray-500 py-2">No drawings in this set</p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
              {drawings.map((d) => (
                <div
                  key={d.id}
                  className="flex items-center gap-3 p-3 bg-gray-50 dark:bg-gray-800 rounded-lg border border-gray-100 dark:border-gray-700"
                >
                  <FileText className="h-5 w-5 text-gray-400 dark:text-gray-500 shrink-0" />
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
                      {d.sheet_number}
                    </p>
                    <p className="text-xs text-gray-500 dark:text-gray-400 truncate">{d.title}</p>
                  </div>
                  <span
                    className={`shrink-0 inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium capitalize ${
                      d.status === "current"
                        ? "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300"
                        : d.status === "superseded"
                          ? "bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-300"
                          : "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400"
                    }`}
                  >
                    {d.status}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function DrawingsPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const [disciplineFilter, setDisciplineFilter] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["drawing-sets", projectId],
    queryFn: () => drawingsApi.listSets(projectId!),
    enabled: !!projectId,
  });

  const sets = data?.data ?? [];
  const disciplines = [...new Set(sets.map((s) => s.discipline))];

  if (!projectId) return <NoProjectSelected />;

  const filtered = disciplineFilter
    ? sets.filter((s) => s.discipline.toLowerCase() === disciplineFilter.toLowerCase())
    : sets;

  return (
    <div className="p-4 md:p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <PenTool className="h-6 w-6 text-violet-600" />
          <div>
            <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Drawings</h1>
            <p className="text-sm text-gray-500 dark:text-gray-400">{sets.length} drawing sets</p>
          </div>
        </div>

        {disciplines.length > 1 && (
          <div className="flex gap-1">
            <button
              onClick={() => setDisciplineFilter("")}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium ${
                !disciplineFilter
                  ? "bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900"
                  : "bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-300 dark:hover:bg-gray-600"
              }`}
            >
              All
            </button>
            {disciplines.map((d) => (
              <button
                key={d}
                onClick={() => setDisciplineFilter(d)}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium capitalize ${
                  disciplineFilter === d
                    ? "bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900"
                    : "bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-300 dark:hover:bg-gray-600"
                }`}
              >
                {d}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Drawing Sets */}
      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-4 animate-pulse"
            >
              <div className="h-5 bg-gray-200 dark:bg-gray-700 rounded w-48" />
            </div>
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-12 text-center">
          <PenTool className="h-10 w-10 text-gray-300 dark:text-gray-600 mx-auto mb-2" />
          <p className="text-gray-500 dark:text-gray-400 text-sm">No drawing sets found</p>
        </div>
      ) : (
        <div className="space-y-3">
          {filtered.map((set) => (
            <DrawingSetAccordion key={set.id} set={set} projectId={projectId} />
          ))}
        </div>
      )}
    </div>
  );
}
