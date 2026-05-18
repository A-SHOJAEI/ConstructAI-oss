"use client";
import type { SafetyZone } from "@/lib/safety-api";

interface ZoneDisplayProps {
  zones: SafetyZone[];
  width: number;
  height: number;
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

export function ZoneDisplay({ zones, width, height }: ZoneDisplayProps) {
  return (
    <svg
      className="absolute inset-0 pointer-events-none"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
    >
      {zones.map((zone) => {
        const color = ZONE_COLORS[zone.zone_type] || ZONE_COLORS.general;
        const pointsStr = zone.polygon_points.map(([x, y]) => `${x},${y}`).join(" ");

        return (
          <g key={zone.id}>
            <polygon
              points={pointsStr}
              fill={color}
              fillOpacity={0.1}
              stroke={color}
              strokeWidth={2}
            />
            <text
              x={zone.polygon_points[0]?.[0] || 0}
              y={(zone.polygon_points[0]?.[1] || 0) - 5}
              fill={color}
              fontSize={11}
              fontWeight="bold"
            >
              {zone.name} ({zone.zone_type})
            </text>
          </g>
        );
      })}
    </svg>
  );
}
