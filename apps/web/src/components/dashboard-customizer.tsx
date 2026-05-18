"use client";

import { useState, useCallback, useEffect } from "react";

interface DashboardWidget {
  id: string;
  label: string;
  enabled: boolean;
  order: number;
}

const DEFAULT_WIDGETS: DashboardWidget[] = [
  { id: "safety-alerts", label: "Safety Alerts", enabled: true, order: 0 },
  { id: "active-cameras", label: "Active Cameras", enabled: true, order: 1 },
  { id: "schedule-status", label: "Schedule Status", enabled: true, order: 2 },
  { id: "rfi-summary", label: "RFI Summary", enabled: true, order: 3 },
  { id: "weather", label: "Weather Forecast", enabled: true, order: 4 },
  { id: "daily-log", label: "Daily Log", enabled: false, order: 5 },
  { id: "quality-metrics", label: "Quality Metrics", enabled: false, order: 6 },
  { id: "productivity", label: "Productivity", enabled: false, order: 7 },
];

const STORAGE_KEY = "constructai_dashboard_layout";

export function useDashboardLayout() {
  const [widgets, setWidgets] = useState<DashboardWidget[]>(() => {
    if (typeof window === "undefined") return DEFAULT_WIDGETS;
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        // Validate the parsed data is an array of well-formed widgets
        if (
          Array.isArray(parsed) &&
          parsed.length > 0 &&
          parsed.every(
            (w: unknown) =>
              typeof w === "object" &&
              w !== null &&
              typeof (w as Record<string, unknown>).id === "string" &&
              typeof (w as Record<string, unknown>).label === "string" &&
              typeof (w as Record<string, unknown>).enabled === "boolean" &&
              typeof (w as Record<string, unknown>).order === "number",
          )
        ) {
          return parsed.map((w: Record<string, unknown>) => ({
            id: w.id as string,
            label: w.label as string,
            enabled: w.enabled as boolean,
            order: w.order as number,
          }));
        }
        return DEFAULT_WIDGETS;
      } catch {
        return DEFAULT_WIDGETS;
      }
    }
    return DEFAULT_WIDGETS;
  });

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(widgets));
  }, [widgets]);

  const toggleWidget = useCallback((id: string) => {
    setWidgets((prev) => prev.map((w) => (w.id === id ? { ...w, enabled: !w.enabled } : w)));
  }, []);

  const moveWidget = useCallback((id: string, direction: "up" | "down") => {
    setWidgets((prev) => {
      const idx = prev.findIndex((w) => w.id === id);
      if (idx < 0) return prev;
      const newIdx = direction === "up" ? idx - 1 : idx + 1;
      if (newIdx < 0 || newIdx >= prev.length) return prev;
      const next = [...prev];
      [next[idx], next[newIdx]] = [next[newIdx], next[idx]];
      return next.map((w, i) => ({ ...w, order: i }));
    });
  }, []);

  const resetLayout = useCallback(() => {
    setWidgets(DEFAULT_WIDGETS);
  }, []);

  const enabledWidgets = widgets.filter((w) => w.enabled).sort((a, b) => a.order - b.order);

  return { widgets, enabledWidgets, toggleWidget, moveWidget, resetLayout };
}

export function DashboardCustomizer({
  open,
  onClose,
  widgets,
  onToggle,
  onMove,
  onReset,
}: {
  open: boolean;
  onClose: () => void;
  widgets: DashboardWidget[];
  onToggle: (id: string) => void;
  onMove: (id: string, direction: "up" | "down") => void;
  onReset: () => void;
}) {
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Customize dashboard"
    >
      <div
        className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl dark:bg-gray-800"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
            Customize Dashboard
          </h2>
          <button
            onClick={onReset}
            className="text-xs text-blue-600 hover:underline dark:text-blue-400"
          >
            Reset to default
          </button>
        </div>

        <ul className="space-y-2">
          {widgets.map((widget, idx) => (
            <li
              key={widget.id}
              className="flex items-center justify-between rounded-lg border border-gray-200 p-3 dark:border-gray-700"
            >
              <div className="flex items-center gap-3">
                <input
                  type="checkbox"
                  checked={widget.enabled}
                  onChange={() => onToggle(widget.id)}
                  className="h-4 w-4 rounded border-gray-300"
                  aria-label={`Toggle ${widget.label}`}
                />
                <span
                  className={`text-sm ${
                    widget.enabled ? "text-gray-900 dark:text-white" : "text-gray-400"
                  }`}
                >
                  {widget.label}
                </span>
              </div>
              <div className="flex gap-1">
                <button
                  onClick={() => onMove(widget.id, "up")}
                  disabled={idx === 0}
                  className="rounded p-1 text-gray-400 hover:bg-gray-100 disabled:opacity-30 dark:hover:bg-gray-700"
                  aria-label={`Move ${widget.label} up`}
                >
                  <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M5 15l7-7 7 7"
                    />
                  </svg>
                </button>
                <button
                  onClick={() => onMove(widget.id, "down")}
                  disabled={idx === widgets.length - 1}
                  className="rounded p-1 text-gray-400 hover:bg-gray-100 disabled:opacity-30 dark:hover:bg-gray-700"
                  aria-label={`Move ${widget.label} down`}
                >
                  <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M19 9l-7 7-7-7"
                    />
                  </svg>
                </button>
              </div>
            </li>
          ))}
        </ul>

        <button
          onClick={onClose}
          className="mt-4 w-full rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
        >
          Done
        </button>
      </div>
    </div>
  );
}
