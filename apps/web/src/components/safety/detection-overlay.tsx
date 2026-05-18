"use client";
import type { DetectionEvent } from "@/lib/safety-api";

interface DetectionOverlayProps {
  detections: DetectionEvent[];
  width: number;
  height: number;
}

const SEVERITY_COLORS: Record<string, string> = {
  zone_breach: "#ef4444",
  missing_hardhat: "#f97316",
  missing_vest: "#eab308",
  unauthorized_person: "#ef4444",
  equipment_violation: "#f97316",
  default: "#3b82f6",
};

export function DetectionOverlay({ detections, width, height }: DetectionOverlayProps) {
  return (
    <svg
      className="absolute inset-0 pointer-events-none"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
    >
      {detections.map((det, idx) => {
        const [x1, y1, x2, y2] = det.bbox;
        const color = SEVERITY_COLORS[det.violation_type || "default"] || SEVERITY_COLORS.default;
        const truncatedName =
          det.class_name?.length > 50 ? det.class_name.slice(0, 50) + "..." : det.class_name;

        return (
          <g key={`${det.track_id || idx}-${det.timestamp}`}>
            <rect
              x={x1}
              y={y1}
              width={x2 - x1}
              height={y2 - y1}
              fill="none"
              stroke={color}
              strokeWidth={2}
              strokeDasharray={det.violation_type ? undefined : "4 2"}
            />
            <rect
              x={x1}
              y={y1 - 18}
              width={Math.max((truncatedName?.length || 6) * 8, 60)}
              height={18}
              fill={color}
              opacity={0.8}
            />
            <text x={x1 + 4} y={y1 - 4} fill="white" fontSize={12} fontFamily="monospace">
              {truncatedName} {(det.confidence * 100).toFixed(0)}%
            </text>
            {det.track_id && (
              <text x={x1 + 4} y={y2 + 14} fill={color} fontSize={10} fontFamily="monospace">
                ID: {det.track_id}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
