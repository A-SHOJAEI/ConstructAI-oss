"use client";
import { useState } from "react";
import { toast } from "sonner";
import type { SafetyAlert, AlertDetection } from "@/lib/safety-api";
import { safetyApi } from "@/lib/safety-api";

interface AlertDetailModalProps {
  alert: SafetyAlert;
  onClose: () => void;
  onUpdate?: (alert: SafetyAlert) => void;
}

// Render a real training image as the camera frame, with YOLO detection
// boxes drawn on top. Each seeded alert points at a static image under
// /public/safety-demo/; bbox coords are stored in 1280x720 source space
// and scaled down by SVG viewBox.
const SRC_W = 1280;
const SRC_H = 720;

function DetectionFrame({
  detections,
  frameUrl,
}: {
  detections: AlertDetection[];
  frameUrl: string | null;
}) {
  return (
    <div className="relative w-full overflow-hidden rounded-lg border border-gray-200 dark:border-gray-600 bg-slate-800">
      {frameUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={frameUrl}
          alt="Camera frame"
          className="block w-full h-auto"
        />
      ) : (
        <div
          className="w-full bg-slate-700"
          style={{ aspectRatio: `${SRC_W} / ${SRC_H}` }}
        />
      )}
      <svg
        viewBox={`0 0 ${SRC_W} ${SRC_H}`}
        preserveAspectRatio="none"
        className="absolute inset-0 w-full h-full pointer-events-none"
        role="img"
        aria-label="YOLO detection bounding boxes"
      >
        <text x="16" y="28" fontSize="18" fill="#fef3c7" fontFamily="monospace" stroke="#000" strokeWidth="0.5">
          CAM-01 · YOLOv8-L · 1280x720
        </text>
        {detections.map((det, i) => {
          const [x, y, w, h] = det.bbox.length >= 4 ? det.bbox : [0, 0, 0, 0];
          const isViolation =
            (det.violation_type ?? "") !== "" ||
            det.class_name.includes("violation") ||
            det.class_name.startsWith("no_") ||
            det.class_name.startsWith("no ");
          const color = isViolation ? "#ef4444" : "#f59e0b";
          const label = `${det.class_name} ${(det.confidence * 100).toFixed(0)}%`;
          const labelW = Math.min(Math.max(label.length * 11, 140), 380);
          return (
            <g key={i}>
              <rect
                x={x}
                y={y}
                width={w}
                height={h}
                fill="none"
                stroke={color}
                strokeWidth="4"
              />
              <rect x={x} y={Math.max(y - 28, 0)} width={labelW} height={28} fill={color} />
              <text
                x={x + 8}
                y={Math.max(y - 8, 20)}
                fontSize="18"
                fill="white"
                fontFamily="system-ui, sans-serif"
                fontWeight="600"
              >
                {label}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function AlertDetailModal({ alert, onClose, onUpdate }: AlertDetailModalProps) {
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleAcknowledge = async (isFalsePositive: boolean) => {
    setSubmitting(true);
    try {
      const updated = await safetyApi.acknowledgeAlert(alert.id, {
        is_false_positive: isFalsePositive,
        notes,
      });
      onUpdate?.(updated);
      onClose();
    } catch {
      toast.error("Failed to acknowledge alert. Please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      role="dialog"
      aria-modal="true"
      aria-labelledby="alert-detail-dialog-title"
      onKeyDown={(e) => {
        if (e.key === "Tab") {
          const focusable = e.currentTarget.querySelectorAll<HTMLElement>(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
          );
          if (focusable.length === 0) return;
          const first = focusable[0];
          const last = focusable[focusable.length - 1];
          if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
        }
        if (e.key === "Escape") {
          onClose();
        }
      }}
    >
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl max-w-lg w-full mx-4 max-h-[90vh] overflow-y-auto">
        <div className="p-6">
          <div className="flex items-center justify-between mb-4">
            <h2
              id="alert-detail-dialog-title"
              className="text-lg font-semibold text-gray-900 dark:text-white"
            >
              Alert Details
            </h2>
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300"
              aria-label="Close dialog"
            >
              X
            </button>
          </div>

          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium text-gray-500 dark:text-gray-400">
                Priority
              </label>
              <p className="text-sm text-gray-900 dark:text-white">{alert.priority}</p>
            </div>

            <div>
              <label className="text-sm font-medium text-gray-500 dark:text-gray-400">Type</label>
              <p className="text-sm text-gray-900 dark:text-white">{alert.alert_type}</p>
            </div>

            <div>
              <label className="text-sm font-medium text-gray-500 dark:text-gray-400">
                Description
              </label>
              <p className="text-sm text-gray-900 dark:text-white">{alert.description}</p>
            </div>

            <div>
              <label className="text-sm font-medium text-gray-500 dark:text-gray-400">
                Confidence
              </label>
              <p className="text-sm text-gray-900 dark:text-white">
                {(alert.confidence * 100).toFixed(1)}%
              </p>
            </div>

            <div>
              <label className="text-sm font-medium text-gray-500 dark:text-gray-400">Time</label>
              <p className="text-sm text-gray-900 dark:text-white">
                {new Date(alert.created_at).toLocaleString()}
              </p>
            </div>

            {alert.detections && alert.detections.length > 0 && (
              <div>
                <label className="text-sm font-medium text-gray-500 dark:text-gray-400">
                  Frame Capture
                </label>
                <div className="mt-1">
                  <DetectionFrame
                    detections={alert.detections}
                    frameUrl={alert.frame_s3_key ?? null}
                  />
                </div>
              </div>
            )}

            {alert.osha_reference && (
              <div>
                <label className="text-sm font-medium text-gray-500 dark:text-gray-400">
                  OSHA Reference
                </label>
                <p className="text-sm text-gray-900 dark:text-white font-mono">
                  {alert.osha_reference}
                </p>
              </div>
            )}

            {alert.detections && alert.detections.length > 0 && (
              <div>
                <label className="text-sm font-medium text-gray-500 dark:text-gray-400">
                  Detections
                </label>
                <ul className="mt-1 space-y-1 text-sm text-gray-900 dark:text-white">
                  {alert.detections.map((det, i) => (
                    <li
                      key={i}
                      className="flex items-center justify-between bg-gray-50 dark:bg-gray-700 rounded px-3 py-1.5"
                    >
                      <span>{det.class_name}</span>
                      <span className="text-xs text-gray-500 dark:text-gray-400">
                        {(det.confidence * 100).toFixed(1)}%
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {!alert.is_acknowledged && (
              <div className="space-y-3 pt-4 border-t border-gray-200 dark:border-gray-700">
                <textarea
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Add response notes..."
                  className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-200 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  rows={3}
                />
                <div className="flex gap-2">
                  <button
                    onClick={() => handleAcknowledge(false)}
                    disabled={submitting}
                    className="flex-1 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 disabled:opacity-50"
                  >
                    Acknowledge
                  </button>
                  <button
                    onClick={() => handleAcknowledge(true)}
                    disabled={submitting}
                    className="flex-1 px-4 py-2 bg-orange-600 text-white text-sm font-medium rounded-md hover:bg-orange-700 disabled:opacity-50"
                  >
                    Mark False Positive
                  </button>
                </div>
              </div>
            )}

            {alert.is_acknowledged && (
              <div className="pt-4 border-t border-gray-200 dark:border-gray-700">
                <p className="text-sm text-green-600">
                  Acknowledged
                  {alert.acknowledged_at
                    ? ` at ${new Date(alert.acknowledged_at).toLocaleString()}`
                    : ""}
                </p>
                {alert.response_notes && (
                  <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                    Notes: {alert.response_notes}
                  </p>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
