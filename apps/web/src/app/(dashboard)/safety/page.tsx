"use client";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { CameraGrid } from "@/components/safety/camera-grid";
import { AlertTimeline } from "@/components/safety/alert-timeline";
import { AlertDetailModal } from "@/components/safety/alert-detail-modal";
import { PredictiveRiskPanel } from "@/components/safety/predictive-risk-panel";
import { useSafetyWebSocket } from "@/hooks/use-websocket";
import type { SafetyAlert, SafetyStats } from "@/lib/safety-api";
import { safetyApi } from "@/lib/safety-api";
import { useProjectStore } from "@/stores/project-store";
import { NoProjectSelected } from "@/components/no-project-selected";

export default function SafetyDashboardPage() {
  const projectId = useProjectStore((s) => s.selectedProjectId);
  const [stats, setStats] = useState<SafetyStats | null>(null);
  const [selectedAlert, setSelectedAlert] = useState<SafetyAlert | null>(null);

  const { connected, alerts } = useSafetyWebSocket({
    projectId: projectId ?? "",
    enabled: !!projectId,
  });

  useEffect(() => {
    if (!projectId) return;
    const loadStats = async () => {
      try {
        const data = await safetyApi.getStats(projectId);
        setStats(data);
      } catch {
        toast.error("Failed to load safety stats.");
      }
    };
    loadStats();
    const interval = setInterval(loadStats, 30000);
    return () => clearInterval(interval);
  }, [projectId]);

  if (!projectId) return <NoProjectSelected />;

  return (
    <div
      className="min-h-screen bg-gray-50 dark:bg-gray-900"
      role="main"
      aria-label="Safety monitoring dashboard"
    >
      <header className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 px-6 py-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Safety Monitoring</h1>
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
              Real-time construction site safety dashboard
            </p>
          </div>
          <div className="flex items-center gap-4">
            <span
              role="status"
              aria-live="polite"
              className={`inline-flex items-center px-3 py-1 rounded-full text-sm font-medium ${
                connected ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"
              }`}
            >
              {connected ? "Connected" : "Disconnected"}
            </span>
          </div>
        </div>
      </header>

      {stats && (
        <div
          className="px-6 py-4 grid grid-cols-2 md:grid-cols-5 gap-4"
          role="region"
          aria-label="Safety statistics"
        >
          <div className="bg-white dark:bg-gray-800 rounded-lg p-4 shadow-sm">
            <p className="text-sm text-gray-500 dark:text-gray-400">Total Alerts</p>
            <p className="text-2xl font-bold text-gray-900 dark:text-white">
              <span className="sr-only">Total alerts: </span>
              {stats.total_alerts}
            </p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg p-4 shadow-sm">
            <p className="text-sm text-gray-500 dark:text-gray-400">Critical</p>
            <p className="text-2xl font-bold text-red-600">
              <span className="sr-only">Critical alerts: </span>
              {stats.alerts_by_priority?.P1_critical || 0}
            </p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg p-4 shadow-sm">
            <p className="text-sm text-gray-500 dark:text-gray-400">High</p>
            <p className="text-2xl font-bold text-orange-600">
              <span className="sr-only">High priority alerts: </span>
              {stats.alerts_by_priority?.P2_high || 0}
            </p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg p-4 shadow-sm">
            <p className="text-sm text-gray-500 dark:text-gray-400">Acknowledged</p>
            <p className="text-2xl font-bold text-green-600">
              <span className="sr-only">Acknowledged alerts: </span>
              {stats.acknowledged_count}
            </p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg p-4 shadow-sm">
            <p className="text-sm text-gray-500 dark:text-gray-400">False Positives</p>
            <p className="text-2xl font-bold text-gray-600">
              <span className="sr-only">False positive alerts: </span>
              {stats.false_positive_count}
            </p>
          </div>
        </div>
      )}

      {/* Predictive Safety Panel */}
      <div className="px-6 py-4">
        <PredictiveRiskPanel projectId={projectId} />
      </div>

      <div className="px-6 py-4 grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">Camera Feeds</h2>
          <CameraGrid projectId={projectId} />
        </div>
        <div
          className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden"
          role="log"
          aria-live="polite"
          aria-label="Safety alert timeline"
        >
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white p-4 border-b border-gray-200 dark:border-gray-700">
            Alert Timeline
          </h2>
          <AlertTimeline alerts={alerts} onAlertClick={(alert) => setSelectedAlert(alert)} />
        </div>
      </div>

      {selectedAlert && (
        <AlertDetailModal
          alert={selectedAlert}
          onClose={() => setSelectedAlert(null)}
          onUpdate={(updated) => {
            setSelectedAlert(null);
            void updated;
          }}
        />
      )}
    </div>
  );
}
