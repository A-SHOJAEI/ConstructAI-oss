"use client";
import { useState } from "react";
import type { SafetyAlert } from "@/lib/safety-api";

interface AlertTimelineProps {
  alerts: SafetyAlert[];
  onAlertClick?: (alert: SafetyAlert) => void;
}

const PRIORITY_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  P1_critical: { bg: "bg-red-100", text: "text-red-800", label: "CRITICAL" },
  P2_high: { bg: "bg-orange-100", text: "text-orange-800", label: "HIGH" },
  P3_medium: { bg: "bg-yellow-100", text: "text-yellow-800", label: "MEDIUM" },
  P4_low: { bg: "bg-blue-100", text: "text-blue-800", label: "LOW" },
  P5_info: { bg: "bg-gray-100", text: "text-gray-800", label: "INFO" },
};

export function AlertTimeline({ alerts, onAlertClick }: AlertTimelineProps) {
  const [filter, setFilter] = useState<string>("all");

  const filteredAlerts = filter === "all" ? alerts : alerts.filter((a) => a.priority === filter);

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 p-3 border-b border-gray-200 dark:border-gray-700">
        <span className="text-sm font-medium text-gray-700 dark:text-gray-300">Filter:</span>
        {["all", "P1_critical", "P2_high", "P3_medium"].map((p) => (
          <button
            key={p}
            onClick={() => setFilter(p)}
            className={`px-2 py-1 text-xs rounded-full transition-colors ${
              filter === p
                ? "bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900"
                : "bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-300 dark:hover:bg-gray-600"
            }`}
          >
            {p === "all" ? "All" : PRIORITY_STYLES[p]?.label || p}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto">
        {filteredAlerts.length === 0 ? (
          <div className="text-center py-8 text-gray-500 dark:text-gray-400 text-sm">
            No alerts to display
          </div>
        ) : (
          <div className="divide-y divide-gray-100 dark:divide-gray-700">
            {filteredAlerts.map((alert) => {
              const style = PRIORITY_STYLES[alert.priority] || PRIORITY_STYLES.P5_info;
              const time = new Date(alert.created_at).toLocaleTimeString();

              return (
                <div
                  key={alert.id}
                  className="p-3 hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer transition-colors"
                  onClick={() => onAlertClick?.(alert)}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span
                      className={`px-2 py-0.5 rounded-full text-xs font-medium ${style.bg} ${style.text}`}
                    >
                      {style.label}
                    </span>
                    <span className="text-xs text-gray-400 dark:text-gray-500">{time}</span>
                  </div>
                  <p className="text-sm text-gray-900 dark:text-gray-100">{alert.description}</p>
                  <div className="flex items-center gap-2 mt-1">
                    <span className="text-xs text-gray-500 dark:text-gray-400">
                      {alert.alert_type}
                    </span>
                    {alert.is_acknowledged && (
                      <span className="text-xs text-green-600">Acknowledged</span>
                    )}
                    {alert.is_false_positive && (
                      <span className="text-xs text-orange-600">False Positive</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
