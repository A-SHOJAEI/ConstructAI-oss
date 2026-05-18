"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";

interface Activity {
  id: string;
  code: string;
  name: string;
  duration: number;
  start_date: string;
  finish_date: string;
  status: string;
  total_float: number | null;
  is_critical: boolean;
}

interface ActivitiesResponse {
  items: Activity[];
  total: number;
}

const statusColors: Record<string, string> = {
  not_started: "bg-gray-100 text-gray-800",
  in_progress: "bg-blue-100 text-blue-800",
  completed: "bg-green-100 text-green-800",
  delayed: "bg-red-100 text-red-800",
};

export default function SchedulePage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const { data, isLoading, error } = useQuery<ActivitiesResponse>({
    queryKey: ["schedule-activities", projectId],
    queryFn: () =>
      apiClient.get<ActivitiesResponse>(`/api/v1/scheduling/activities?project_id=${projectId}`),
    enabled: !!projectId,
  });

  const activities = data?.items ?? [];

  if (!projectId) return <NoProjectSelected />;

  // Summary stats
  const totalActivities = activities.length;
  const criticalActivities = activities.filter((a) => a.is_critical);
  const criticalPathLength = criticalActivities.reduce((sum, a) => sum + a.duration, 0);
  const completedOrOnTrack = activities.filter(
    (a) => a.status === "completed" || a.status === "in_progress",
  ).length;
  const onTrackPercentage =
    totalActivities > 0 ? ((completedOrOnTrack / totalActivities) * 100).toFixed(1) : "0";

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Schedule</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Project scheduling and activity tracking
        </p>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">Total Activities</p>
          <p className="text-3xl font-bold text-gray-900 dark:text-white">
            {isLoading ? "—" : totalActivities}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">Critical Path Length</p>
          <p className="text-3xl font-bold text-red-600">
            {isLoading ? "—" : `${criticalPathLength} days`}
          </p>
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">On-Track</p>
          <p className="text-3xl font-bold text-green-600">
            {isLoading ? "—" : `${onTrackPercentage}%`}
          </p>
        </div>
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-8 text-center">
          <p className="text-gray-500 dark:text-gray-400">Loading schedule data...</p>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-800">
          <p className="font-medium">Failed to load schedule</p>
          <p className="text-sm mt-1">{(error as Error).message}</p>
        </div>
      )}

      {/* Empty State */}
      {!isLoading && !error && activities.length === 0 && (
        <div className="text-center py-12 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-2">
            No activities found
          </h3>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Import a schedule file to get started.
          </p>
        </div>
      )}

      {/* Activity Table */}
      {!isLoading && !error && activities.length > 0 && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
              <thead className="bg-gray-50 dark:bg-gray-900">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    Code
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    Name
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    Duration
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    Start
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    Finish
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    Status
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    Float
                  </th>
                </tr>
              </thead>
              <tbody className="bg-white dark:bg-gray-800 divide-y divide-gray-200 dark:divide-gray-700">
                {activities.map((activity) => (
                  <tr
                    key={activity.id}
                    className={`hover:bg-gray-50 dark:hover:bg-gray-700 ${
                      activity.is_critical
                        ? "bg-red-50 hover:bg-red-100 dark:bg-red-900/20 dark:hover:bg-red-900/30"
                        : ""
                    }`}
                  >
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-mono text-gray-900 dark:text-white">
                      {activity.code}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900 dark:text-white">
                      <span className="flex items-center gap-2">
                        {activity.name}
                        {activity.is_critical && (
                          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-red-600 text-white">
                            Critical
                          </span>
                        )}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                      {activity.duration}d
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                      {new Date(activity.start_date).toLocaleDateString()}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                      {new Date(activity.finish_date).toLocaleDateString()}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                          statusColors[activity.status] ?? "bg-gray-100 text-gray-800"
                        }`}
                      >
                        {activity.status?.replace("_", " ")}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 dark:text-gray-400">
                      {activity.total_float !== null ? `${activity.total_float}d` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
