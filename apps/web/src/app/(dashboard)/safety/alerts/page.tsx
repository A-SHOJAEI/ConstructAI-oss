"use client";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { AlertDetailModal } from "@/components/safety/alert-detail-modal";
import type { SafetyAlert } from "@/lib/safety-api";
import { safetyApi } from "@/lib/safety-api";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";

const PRIORITY_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  P1_critical: { bg: "bg-red-100", text: "text-red-800", label: "CRITICAL" },
  P2_high: { bg: "bg-orange-100", text: "text-orange-800", label: "HIGH" },
  P3_medium: { bg: "bg-yellow-100", text: "text-yellow-800", label: "MEDIUM" },
  P4_low: { bg: "bg-blue-100", text: "text-blue-800", label: "LOW" },
  P5_info: { bg: "bg-gray-100", text: "text-gray-800", label: "INFO" },
};

export default function AlertsPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const [alerts, setAlerts] = useState<SafetyAlert[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedAlert, setSelectedAlert] = useState<SafetyAlert | null>(null);
  const [priorityFilter, setPriorityFilter] = useState<string>("");
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [sortColumn, setSortColumn] = useState<string>("created_at");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("desc");

  const loadAlerts = useCallback(async () => {
    if (!projectId) return;
    try {
      const params: Record<string, string | number> = { project_id: projectId, limit: 50 };
      if (priorityFilter) params.priority = priorityFilter;
      if (typeFilter) params.alert_type = typeFilter;
      const response = await safetyApi.listAlerts(params);
      const sorted = [...response.data].sort((a, b) => {
        let aVal: string | number = "";
        let bVal: string | number = "";
        if (sortColumn === "priority") {
          const order: Record<string, number> = {
            P1_critical: 0,
            P2_high: 1,
            P3_medium: 2,
            P4_low: 3,
            P5_info: 4,
          };
          aVal = order[a.priority] ?? 5;
          bVal = order[b.priority] ?? 5;
        } else if (sortColumn === "confidence") {
          aVal = a.confidence;
          bVal = b.confidence;
        } else if (sortColumn === "created_at") {
          aVal = new Date(a.created_at).getTime();
          bVal = new Date(b.created_at).getTime();
        } else if (sortColumn === "alert_type") {
          aVal = a.alert_type;
          bVal = b.alert_type;
        }
        if (aVal < bVal) return sortDirection === "asc" ? -1 : 1;
        if (aVal > bVal) return sortDirection === "asc" ? 1 : -1;
        return 0;
      });
      setAlerts(sorted);
    } catch {
      toast.error("Failed to load safety alerts.");
    } finally {
      setLoading(false);
    }
  }, [projectId, priorityFilter, typeFilter, sortColumn, sortDirection]);

  useEffect(() => {
    loadAlerts();
  }, [loadAlerts]);

  if (!projectId) return <NoProjectSelected />;

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 p-6">
      <h1 className="text-2xl font-bold text-gray-900 dark:text-white mb-6">Safety Alerts</h1>

      <div className="flex items-center gap-4 mb-6">
        <select
          value={priorityFilter}
          onChange={(e) => setPriorityFilter(e.target.value)}
          className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm dark:bg-gray-700 dark:text-gray-200"
        >
          <option value="">All Priorities</option>
          <option value="P1_critical">Critical</option>
          <option value="P2_high">High</option>
          <option value="P3_medium">Medium</option>
          <option value="P4_low">Low</option>
          <option value="P5_info">Info</option>
        </select>

        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-md text-sm dark:bg-gray-700 dark:text-gray-200"
        >
          <option value="">All Types</option>
          <option value="zone_breach">Zone Breach</option>
          <option value="missing_ppe">Missing PPE</option>
          <option value="unauthorized_person">Unauthorized Person</option>
          <option value="equipment_violation">Equipment Violation</option>
          <option value="fall_hazard">Fall Hazard</option>
        </select>
      </div>

      {loading ? (
        <div className="text-center py-12 text-gray-500 dark:text-gray-400">Loading alerts...</div>
      ) : (
        <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
            <thead className="bg-gray-50 dark:bg-gray-900">
              <tr>
                {[
                  { key: "priority", label: "Priority" },
                  { key: "alert_type", label: "Type" },
                  { key: "", label: "Description" },
                  { key: "confidence", label: "Confidence" },
                  { key: "created_at", label: "Time" },
                  { key: "", label: "Status" },
                ].map((col, idx) => (
                  <th
                    key={idx}
                    className={`px-4 py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase ${col.key ? "cursor-pointer select-none hover:text-gray-700 dark:hover:text-gray-200" : ""}`}
                    onClick={() => {
                      if (!col.key) return;
                      if (sortColumn === col.key) {
                        setSortDirection((d) => (d === "asc" ? "desc" : "asc"));
                      } else {
                        setSortColumn(col.key);
                        setSortDirection("asc");
                      }
                    }}
                  >
                    <span className="inline-flex items-center gap-1">
                      {col.label}
                      {col.key && sortColumn === col.key && (
                        <svg className="h-3 w-3" viewBox="0 0 12 12" fill="currentColor">
                          {sortDirection === "asc" ? (
                            <path d="M6 2L10 8H2L6 2Z" />
                          ) : (
                            <path d="M6 10L2 4H10L6 10Z" />
                          )}
                        </svg>
                      )}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {alerts.map((alert) => {
                const style = PRIORITY_STYLES[alert.priority] || PRIORITY_STYLES.P5_info;
                return (
                  <tr
                    key={alert.id}
                    className="hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer"
                    onClick={() => setSelectedAlert(alert)}
                  >
                    <td className="px-4 py-3">
                      <span
                        className={`px-2 py-0.5 rounded-full text-xs font-medium ${style.bg} ${style.text}`}
                      >
                        {style.label}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-900 dark:text-gray-200">
                      {alert.alert_type}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-900 dark:text-gray-200 max-w-md truncate">
                      {alert.description}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-500 dark:text-gray-400">
                      {(alert.confidence * 100).toFixed(0)}%
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-500 dark:text-gray-400">
                      {new Date(alert.created_at).toLocaleString()}
                    </td>
                    <td className="px-4 py-3 text-sm">
                      {alert.is_acknowledged ? (
                        <span className="text-green-600">Acknowledged</span>
                      ) : (
                        <span className="text-yellow-600">Pending</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {alerts.length === 0 && (
            <div className="text-center py-12 text-gray-500 dark:text-gray-400">
              No alerts found
            </div>
          )}
        </div>
      )}

      {selectedAlert && (
        <AlertDetailModal
          alert={selectedAlert}
          onClose={() => setSelectedAlert(null)}
          onUpdate={() => {
            setSelectedAlert(null);
            loadAlerts();
          }}
        />
      )}
    </div>
  );
}
