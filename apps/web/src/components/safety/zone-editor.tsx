"use client";
import { useCallback, useRef, useState } from "react";
import type { SafetyZone } from "@/lib/safety-api";

interface ZoneEditorProps {
  zones: SafetyZone[];
  width: number;
  height: number;
  onZoneCreate?: (points: number[][]) => void;
  onZoneSelect?: (zone: SafetyZone) => void;
  editable?: boolean;
}

const ZONE_COLORS: Record<string, string> = {
  restricted: "#ef4444",
  crane_swing: "#f97316",
  excavation: "#eab308",
  ppe_required: "#3b82f6",
  equipment_only: "#8b5cf6",
  pedestrian_only: "#10b981",
  general: "#6b7280",
};

export function ZoneEditor({
  zones,
  width,
  height,
  onZoneCreate,
  onZoneSelect,
  editable = false,
}: ZoneEditorProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [points, setPoints] = useState<number[][]>([]);
  const [isDrawing, setIsDrawing] = useState(false);

  const handleClick = useCallback(
    (e: React.MouseEvent<SVGSVGElement>) => {
      if (!editable || !isDrawing) return;
      const svg = svgRef.current;
      if (!svg) return;

      const rect = svg.getBoundingClientRect();
      const x = ((e.clientX - rect.left) / rect.width) * width;
      const y = ((e.clientY - rect.top) / rect.height) * height;

      setPoints((prev) => [...prev, [Math.round(x), Math.round(y)]]);
    },
    [editable, isDrawing, width, height],
  );

  const handleDoubleClick = useCallback(() => {
    if (points.length >= 3) {
      onZoneCreate?.(points);
    }
    setPoints([]);
    setIsDrawing(false);
  }, [points, onZoneCreate]);

  const toPointsString = (pts: number[][]) => pts.map(([x, y]) => `${x},${y}`).join(" ");

  return (
    <div className="relative">
      <svg
        ref={svgRef}
        className={`absolute inset-0 z-10 ${isDrawing ? "cursor-crosshair" : "cursor-default"}`}
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        onClick={handleClick}
        onDoubleClick={handleDoubleClick}
      >
        {/* Existing zones */}
        {zones.map((zone) => {
          const color = ZONE_COLORS[zone.zone_type] || ZONE_COLORS.general;
          return (
            <g key={zone.id} onClick={() => onZoneSelect?.(zone)}>
              <polygon
                points={toPointsString(zone.polygon_points)}
                fill={color}
                fillOpacity={0.15}
                stroke={color}
                strokeWidth={2}
              />
              <text
                x={zone.polygon_points[0]?.[0] || 0}
                y={(zone.polygon_points[0]?.[1] || 0) - 5}
                fill={color}
                fontSize={12}
                fontWeight="bold"
              >
                {zone.name}
              </text>
            </g>
          );
        })}

        {/* Drawing in progress */}
        {isDrawing && points.length > 0 && (
          <g>
            <polyline
              points={toPointsString(points)}
              fill="none"
              stroke="#3b82f6"
              strokeWidth={2}
              strokeDasharray="4 2"
            />
            {points.map(([x, y], idx) => (
              <circle key={idx} cx={x} cy={y} r={4} fill="#3b82f6" />
            ))}
          </g>
        )}
      </svg>

      {editable && (
        <div className="absolute bottom-2 right-2 z-20 flex gap-2">
          {!isDrawing ? (
            <button
              onClick={() => setIsDrawing(true)}
              className="px-3 py-1.5 bg-blue-600 text-white text-sm rounded-md hover:bg-blue-700"
            >
              Draw Zone
            </button>
          ) : (
            <button
              onClick={() => {
                setPoints([]);
                setIsDrawing(false);
              }}
              className="px-3 py-1.5 bg-red-600 text-white text-sm rounded-md hover:bg-red-700"
            >
              Cancel
            </button>
          )}
        </div>
      )}
    </div>
  );
}
