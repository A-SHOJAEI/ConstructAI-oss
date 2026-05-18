"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { Camera, AlertTriangle, X } from "lucide-react";

interface DetectionBox {
  class_name: string;
  confidence: number;
  bbox: number[];
  track_id?: number | null;
  violation_type?: string | null;
}

interface Detection {
  id: string;
  alert_type: string;
  description: string;
  confidence: number;
  frame_s3_key: string | null;
  detections: DetectionBox[];
  osha_reference: string | null;
  created_at: string;
  is_acknowledged: boolean;
  priority: string;
}

// `frame_s3_key` already declared above; we re-use it as the image URL when
// the seed/UI stores a path like "/safety-demo/alert-X.jpg".

interface CVDetectionsGalleryProps {
  projectId: string;
}

const typeColors: Record<string, string> = {
  ppe_violation: "bg-red-100 text-red-800",
  unauthorized_entry: "bg-orange-100 text-orange-800",
  zone_intrusion: "bg-amber-100 text-amber-800",
  equipment_proximity: "bg-yellow-100 text-yellow-800",
};

// YOLO detector source resolution. Detection bboxes are stored in
// 1280x720 source space; the SVG viewBox scales them to fit the rendered
// image. When `frameUrl` is present we render the real captured frame
// underneath the bbox overlay; otherwise fall back to a stylized backdrop.
const SRC_W = 1280;
const SRC_H = 720;

function FramePreview({
  detections,
  frameUrl,
  compact,
}: {
  detections: DetectionBox[];
  frameUrl: string | null;
  compact?: boolean;
}) {
  return (
    <div className="relative w-full overflow-hidden rounded bg-slate-800">
      {frameUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={frameUrl} alt="Camera frame" className="block w-full h-auto" />
      ) : (
        <div className="w-full bg-slate-700" style={{ aspectRatio: `${SRC_W} / ${SRC_H}` }} />
      )}
      <svg
        viewBox={`0 0 ${SRC_W} ${SRC_H}`}
        preserveAspectRatio="none"
        className="absolute inset-0 w-full h-full pointer-events-none"
        role="img"
        aria-label="YOLO detection bounding boxes"
      >
        {!compact && (
          <text
            x="16"
            y="28"
            fontSize="18"
            fill="#fef3c7"
            fontFamily="monospace"
            stroke="#000"
            strokeWidth="0.5"
          >
            CAM-01 · YOLOv8-L
          </text>
        )}
        {(detections ?? []).map((det, i) => {
          const [x, y, bw, bh] = det.bbox?.length >= 4 ? det.bbox : [0, 0, 0, 0];
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
                width={bw}
                height={bh}
                fill="none"
                stroke={color}
                strokeWidth={compact ? 3 : 4}
              />
              {!compact && (
                <>
                  <rect x={x} y={Math.max(y - 28, 0)} width={labelW} height={28} fill={color} />
                  <text
                    x={x + 8}
                    y={Math.max(y - 8, 20)}
                    fontSize="18"
                    fill="white"
                    fontFamily="system-ui"
                    fontWeight="600"
                  >
                    {label}
                  </text>
                </>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function CVDetectionsGallery({ projectId }: CVDetectionsGalleryProps) {
  const [filter, setFilter] = useState<string>("");
  const [selectedDetection, setSelectedDetection] = useState<Detection | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["safety-detections", projectId, filter],
    queryFn: () =>
      apiClient.get<{ data: Detection[] }>(
        `/api/v1/safety/alerts?project_id=${projectId}&limit=12${filter ? `&alert_type=${filter}` : ""}`,
      ),
  });

  const detections = data?.data ?? [];
  const types = ["ppe_violation", "unauthorized_entry", "zone_intrusion"];

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-medium text-gray-700">Recent CV Detections</h4>
        <div className="flex gap-1">
          <button
            onClick={() => setFilter("")}
            className={`px-2 py-1 rounded text-xs ${!filter ? "bg-gray-900 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}
          >
            All
          </button>
          {types.map((t) => (
            <button
              key={t}
              onClick={() => setFilter(t)}
              className={`px-2 py-1 rounded text-xs capitalize ${filter === t ? "bg-gray-900 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}
            >
              {t.replace(/_/g, " ")}
            </button>
          ))}
        </div>
      </div>

      {isLoading && (
        <div className="text-center py-8 text-gray-400 text-sm">Loading detections...</div>
      )}

      {!isLoading && detections.length === 0 && (
        <div className="text-center py-8">
          <Camera className="h-8 w-8 text-gray-300 mx-auto mb-2" />
          <p className="text-sm text-gray-400">No recent detections</p>
        </div>
      )}

      {!isLoading && detections.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
          {detections.map((det) => (
            <button
              key={det.id}
              onClick={() => setSelectedDetection(det)}
              className="bg-gray-50 rounded-lg border border-gray-200 overflow-hidden text-left hover:border-gray-300 transition-colors"
            >
              <div className="h-24 bg-gray-200 flex items-center justify-center overflow-hidden">
                {det.detections && det.detections.length > 0 ? (
                  <FramePreview
                    detections={det.detections}
                    frameUrl={det.frame_s3_key ?? null}
                    compact
                  />
                ) : (
                  <AlertTriangle className="h-6 w-6 text-gray-300" />
                )}
              </div>
              <div className="p-2">
                <span
                  className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${typeColors[det.alert_type] ?? "bg-gray-100 text-gray-800"}`}
                >
                  {det.alert_type.replace(/_/g, " ")}
                </span>
                <p className="text-xs text-gray-500 mt-1 truncate">
                  {Math.round(det.confidence * 100)}% confidence
                </p>
                <p className="text-[10px] text-gray-400">
                  {new Date(det.created_at).toLocaleDateString()}
                </p>
              </div>
            </button>
          ))}
        </div>
      )}

      {/* Detail Modal */}
      {selectedDetection && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          role="dialog"
          aria-modal="true"
          aria-labelledby="detection-detail-title"
          onClick={(e) => {
            if (e.target === e.currentTarget) setSelectedDetection(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") setSelectedDetection(null);
          }}
        >
          <div className="bg-white rounded-xl shadow-xl w-full max-w-xl m-4 p-6 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <h3 id="detection-detail-title" className="font-semibold text-gray-900">
                Detection Detail
              </h3>
              <button
                onClick={() => setSelectedDetection(null)}
                className="text-gray-400 hover:text-gray-600"
                aria-label="Close dialog"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="space-y-3">
              <div className="flex items-center gap-2 flex-wrap">
                <span
                  className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${typeColors[selectedDetection.alert_type] ?? "bg-gray-100 text-gray-800"}`}
                >
                  {selectedDetection.alert_type.replace(/_/g, " ")}
                </span>
                <span
                  className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${selectedDetection.is_acknowledged ? "bg-green-100 text-green-800" : "bg-yellow-100 text-yellow-800"}`}
                >
                  {selectedDetection.is_acknowledged ? "Acknowledged" : "Pending"}
                </span>
              </div>

              {selectedDetection.detections && selectedDetection.detections.length > 0 && (
                <div className="bg-gray-50 dark:bg-gray-800 rounded p-2">
                  <FramePreview
                    detections={selectedDetection.detections}
                    frameUrl={selectedDetection.frame_s3_key ?? null}
                  />
                </div>
              )}

              <p className="text-sm text-gray-700">{selectedDetection.description}</p>

              {selectedDetection.osha_reference && (
                <div className="bg-amber-50 border border-amber-200 rounded p-2">
                  <p className="text-xs font-medium text-amber-700 uppercase">OSHA Reference</p>
                  <p className="text-sm text-amber-900 font-mono mt-0.5">
                    {selectedDetection.osha_reference}
                  </p>
                </div>
              )}

              <div className="grid grid-cols-2 gap-2 text-xs text-gray-500">
                <div>
                  Confidence:{" "}
                  <span className="font-medium text-gray-700">
                    {Math.round(selectedDetection.confidence * 100)}%
                  </span>
                </div>
                <div>
                  Priority:{" "}
                  <span className="font-medium text-gray-700 capitalize">
                    {selectedDetection.priority}
                  </span>
                </div>
                <div>
                  Detected:{" "}
                  <span className="font-medium text-gray-700">
                    {new Date(selectedDetection.created_at).toLocaleString()}
                  </span>
                </div>
              </div>

              {selectedDetection.detections && selectedDetection.detections.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-gray-500 uppercase mb-1">
                    Detected Classes ({selectedDetection.detections.length})
                  </p>
                  <ul className="space-y-1">
                    {selectedDetection.detections.map((d, i) => (
                      <li
                        key={i}
                        className="flex items-center justify-between bg-gray-50 rounded px-2 py-1 text-xs"
                      >
                        <span className="font-mono text-gray-700">{d.class_name}</span>
                        <span className="text-gray-500">
                          {(d.confidence * 100).toFixed(1)}%
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
