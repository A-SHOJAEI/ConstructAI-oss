"use client";

interface RiskScoreGaugeProps {
  score: number;
  categories: Record<string, number>;
}

function getScoreColor(score: number): string {
  if (score <= 30) return "#22c55e";
  if (score <= 60) return "#f59e0b";
  return "#ef4444";
}

function getScoreLabel(score: number): string {
  if (score <= 30) return "Low";
  if (score <= 60) return "Moderate";
  if (score <= 80) return "High";
  return "Critical";
}

export function RiskScoreGauge({ score, categories }: RiskScoreGaugeProps) {
  const color = getScoreColor(score);
  const label = getScoreLabel(score);
  const circumference = Math.PI * 80;
  const progress = (score / 100) * circumference;

  const sortedCategories = Object.entries(categories)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 5);

  return (
    <div className="flex flex-col items-center">
      {/* SVG Gauge */}
      <svg width="180" height="100" viewBox="0 0 180 100">
        {/* Background arc */}
        <path
          d="M 10 90 A 80 80 0 0 1 170 90"
          fill="none"
          stroke="#e5e7eb"
          strokeWidth="12"
          strokeLinecap="round"
        />
        {/* Progress arc */}
        <path
          d="M 10 90 A 80 80 0 0 1 170 90"
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
          {score}
        </text>
        <text x="90" y="95" textAnchor="middle" fill="#6b7280" fontSize="12">
          {label} Risk
        </text>
      </svg>

      {/* Category Breakdown */}
      <div className="w-full mt-4 space-y-2">
        {sortedCategories.map(([cat, val]) => (
          <div key={cat} className="flex items-center gap-2">
            <span className="text-xs text-gray-500 w-20 capitalize">{cat.replace("_", " ")}</span>
            <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
              <div
                className="h-full rounded-full transition-all"
                style={{ width: `${val}%`, backgroundColor: getScoreColor(val) }}
              />
            </div>
            <span className="text-xs font-medium w-8 text-right">{val}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
