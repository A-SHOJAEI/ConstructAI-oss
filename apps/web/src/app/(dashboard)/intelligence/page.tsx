"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { intelligenceApi } from "@/lib/intelligence-api";
import type { IntelligenceBrief } from "@/lib/intelligence-api";
import { HealthScoreGauge } from "@/components/intelligence/health-score-gauge";
import { SubScoreBars } from "@/components/intelligence/sub-score-bars";
import {
  Brain,
  RefreshCw,
  Loader2,
  CheckCircle,
  AlertTriangle,
  Clock,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { useState } from "react";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";

const sectionIcons: Record<string, typeof Brain> = {
  schedule: Clock,
  cost: AlertTriangle,
  risk: AlertTriangle,
  productivity: CheckCircle,
};

export default function IntelligenceBriefPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const queryClient = useQueryClient();
  const [expandedSection, setExpandedSection] = useState<string | null>(null);

  const { data: brief, isLoading } = useQuery({
    queryKey: ["intelligence-brief", projectId],
    queryFn: () => intelligenceApi.getLatest(projectId!),
    enabled: !!projectId,
    retry: 1,
  });

  const { data: history } = useQuery({
    queryKey: ["intelligence-history", projectId],
    queryFn: () => intelligenceApi.getHistory(projectId!),
    enabled: !!projectId,
  });

  const generateMutation = useMutation({
    mutationFn: () => intelligenceApi.generate(projectId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["intelligence-brief"] });
      queryClient.invalidateQueries({ queryKey: ["intelligence-history"] });
    },
  });

  if (!projectId) return <NoProjectSelected />;

  if (isLoading) {
    return (
      <div className="p-6 space-y-6">
        <div className="animate-pulse space-y-4">
          <div className="h-8 bg-gray-200 dark:bg-gray-700 rounded w-64" />
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="h-64 bg-gray-100 dark:bg-gray-700 rounded-lg" />
            <div className="h-64 bg-gray-100 dark:bg-gray-700 rounded-lg" />
          </div>
        </div>
      </div>
    );
  }

  const sections = brief
    ? [
        { key: "schedule", label: "Schedule Intelligence", content: brief.schedule_intelligence },
        { key: "cost", label: "Cost Intelligence", content: brief.cost_intelligence },
        { key: "risk", label: "Risk Intelligence", content: brief.risk_intelligence },
        {
          key: "productivity",
          label: "Productivity Intelligence",
          content: brief.productivity_intelligence,
        },
      ].filter((s) => s.content)
    : [];

  const actionItems = (brief?.action_items ?? []) as {
    title?: string;
    description?: string;
    priority?: string;
    status?: string;
  }[];

  return (
    <div className="p-4 md:p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Brain className="h-6 w-6 text-purple-600" />
          <div>
            <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Intelligence Brief</h1>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              {brief
                ? `Report date: ${new Date(brief.report_date).toLocaleDateString()}`
                : "No brief available"}
            </p>
          </div>
        </div>
        <button
          onClick={() => generateMutation.mutate()}
          disabled={generateMutation.isPending}
          className="flex items-center gap-2 px-4 py-2 bg-purple-600 text-white rounded-lg text-sm font-medium hover:bg-purple-700 disabled:opacity-50 transition-colors"
        >
          {generateMutation.isPending ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" /> Generating...
            </>
          ) : (
            <>
              <RefreshCw className="h-4 w-4" /> Generate Brief
            </>
          )}
        </button>
      </div>

      {!brief ? (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-12 text-center">
          <Brain className="h-12 w-12 text-gray-300 dark:text-gray-600 mx-auto mb-3" />
          <p className="text-gray-500 dark:text-gray-400 mb-4">
            No intelligence brief available yet.
          </p>
          <button
            onClick={() => generateMutation.mutate()}
            disabled={generateMutation.isPending}
            className="px-4 py-2 bg-purple-600 text-white rounded-lg text-sm font-medium hover:bg-purple-700"
          >
            Generate First Brief
          </button>
        </div>
      ) : (
        <>
          {/* Health Score + Sub-scores */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
              <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-4">
                Project Health
              </h2>
              <HealthScoreGauge score={brief.overall_health_score} status={brief.project_status} />
            </div>
            <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
              <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-4">
                Category Scores
              </h2>
              <SubScoreBars
                schedule={brief.schedule_health_score}
                cost={brief.cost_health_score}
                risk={brief.risk_score}
                productivity={brief.productivity_score}
              />
            </div>
          </div>

          {/* Executive Summary */}
          <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
            <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">
              Executive Summary
            </h2>
            <p className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap leading-relaxed">
              {brief.executive_summary}
            </p>
          </div>

          {/* Intelligence Sections */}
          {sections.length > 0 && (
            <div className="space-y-2">
              {sections.map((section) => {
                const Icon = sectionIcons[section.key] ?? Brain;
                const isExpanded = expandedSection === section.key;
                return (
                  <button
                    key={section.key}
                    onClick={() => setExpandedSection(isExpanded ? null : section.key)}
                    className="w-full bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 text-left"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Icon className="h-4 w-4 text-gray-500 dark:text-gray-400" />
                        <span className="text-sm font-medium text-gray-900 dark:text-white">
                          {section.label}
                        </span>
                      </div>
                      {isExpanded ? (
                        <ChevronUp className="h-4 w-4 text-gray-400" />
                      ) : (
                        <ChevronDown className="h-4 w-4 text-gray-400" />
                      )}
                    </div>
                    {isExpanded && (
                      <p className="text-sm text-gray-600 dark:text-gray-400 mt-3 whitespace-pre-wrap">
                        {section.content}
                      </p>
                    )}
                  </button>
                );
              })}
            </div>
          )}

          {/* Action Items */}
          {actionItems.length > 0 && (
            <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
              <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-4">
                Action Items ({actionItems.length})
              </h2>
              <div className="space-y-3">
                {actionItems.map((item, i) => (
                  <div
                    key={i}
                    className="flex items-start gap-3 p-3 bg-gray-50 dark:bg-gray-900 rounded-lg"
                  >
                    <div
                      className={`mt-0.5 w-2 h-2 rounded-full shrink-0 ${
                        item.priority === "high"
                          ? "bg-red-500"
                          : item.priority === "medium"
                            ? "bg-amber-500"
                            : "bg-green-500"
                      }`}
                    />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-900 dark:text-white">
                        {item.title ?? `Action Item ${i + 1}`}
                      </p>
                      {item.description && (
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                          {item.description}
                        </p>
                      )}
                    </div>
                    {item.status && (
                      <span
                        className={`shrink-0 inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium ${
                          item.status === "completed"
                            ? "bg-green-100 text-green-800"
                            : "bg-yellow-100 text-yellow-800"
                        }`}
                      >
                        {item.status}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Narrative Report */}
          {brief.narrative_report && (
            <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
              <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">
                Full Report
              </h2>
              <div className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap leading-relaxed max-h-96 overflow-y-auto">
                {brief.narrative_report}
              </div>
            </div>
          )}

          {/* History */}
          {history?.data && history.data.length > 1 && (
            <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6">
              <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3">
                Brief History
              </h2>
              <div className="space-y-2">
                {history.data.slice(0, 10).map((h: IntelligenceBrief) => (
                  <div
                    key={h.id}
                    className="flex items-center justify-between py-2 border-b border-gray-50 dark:border-gray-700 last:border-0"
                  >
                    <span className="text-sm text-gray-600 dark:text-gray-400">
                      {new Date(h.report_date).toLocaleDateString()}
                    </span>
                    <div className="flex items-center gap-3">
                      <span className="text-sm font-medium text-gray-900 dark:text-white">
                        Score: {h.overall_health_score}
                      </span>
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium capitalize ${
                          h.overall_health_score >= 70
                            ? "bg-green-100 text-green-800"
                            : h.overall_health_score >= 40
                              ? "bg-amber-100 text-amber-800"
                              : "bg-red-100 text-red-800"
                        }`}
                      >
                        {h.project_status.replace("_", " ")}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
