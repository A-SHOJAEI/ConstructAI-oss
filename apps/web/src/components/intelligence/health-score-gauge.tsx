"use client";

interface HealthScoreGaugeProps {
  score: number;
  status: string;
}

export function HealthScoreGauge({ score, status }: HealthScoreGaugeProps) {
  const clampedScore = Math.max(0, Math.min(100, score));
  const color = clampedScore >= 70 ? "#22c55e" : clampedScore >= 40 ? "#f59e0b" : "#ef4444";
  const statusColor =
    clampedScore >= 70
      ? "text-green-700 bg-green-100"
      : clampedScore >= 40
        ? "text-amber-700 bg-amber-100"
        : "text-red-700 bg-red-100";

  // SVG arc for semicircle gauge
  const radius = 70;
  const circumference = Math.PI * radius;
  const progress = (clampedScore / 100) * circumference;

  return (
    <div className="flex flex-col items-center">
      <svg width="180" height="100" viewBox="0 0 180 100">
        {/* Background arc */}
        <path
          d="M 10 90 A 70 70 0 0 1 170 90"
          fill="none"
          stroke="#e5e7eb"
          strokeWidth="12"
          strokeLinecap="round"
        />
        {/* Progress arc */}
        <path
          d="M 10 90 A 70 70 0 0 1 170 90"
          fill="none"
          stroke={color}
          strokeWidth="12"
          strokeLinecap="round"
          strokeDasharray={`${progress} ${circumference}`}
        />
        {/* Score text */}
        <text
          x="90"
          y="75"
          textAnchor="middle"
          className="text-3xl font-bold"
          fill={color}
          fontSize="32"
        >
          {clampedScore}
        </text>
        <text x="90" y="95" textAnchor="middle" fill="#6b7280" fontSize="11">
          / 100
        </text>
      </svg>
      <span
        className={`mt-2 inline-flex items-center px-3 py-1 rounded-full text-xs font-medium capitalize ${statusColor}`}
      >
        {status.replace("_", " ")}
      </span>
    </div>
  );
}
