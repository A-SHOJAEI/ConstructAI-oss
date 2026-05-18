"use client";

interface SubScoreBarsProps {
  schedule: number;
  cost: number;
  risk: number;
  productivity: number;
}

const barColor = (v: number) =>
  v >= 70 ? "bg-green-500" : v >= 40 ? "bg-amber-500" : "bg-red-500";

export function SubScoreBars({ schedule, cost, risk, productivity }: SubScoreBarsProps) {
  const items = [
    { label: "Schedule", value: schedule },
    { label: "Cost", value: cost },
    { label: "Risk", value: risk },
    { label: "Productivity", value: productivity },
  ];

  return (
    <div className="space-y-3">
      {items.map((item) => (
        <div key={item.label}>
          <div className="flex items-center justify-between mb-1">
            <span className="text-sm text-gray-600">{item.label}</span>
            <span className="text-sm font-medium text-gray-900">{item.value}</span>
          </div>
          <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${barColor(item.value)}`}
              style={{ width: `${Math.min(100, item.value)}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
