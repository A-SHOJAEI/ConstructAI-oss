interface Benchmark {
  metric_name?: string;
  unit?: string;
  values?: Array<{ project_name: string; value: number }>;
  industry_average?: number | null;
}

export function CrossProjectBenchmark({ benchmarks }: { benchmarks: Array<unknown> }) {
  const items = benchmarks as Benchmark[];

  if (items.length === 0) {
    return <p className="text-gray-500 text-center py-8">No benchmark data available yet.</p>;
  }

  return (
    <div className="space-y-6">
      {items.map((benchmark, idx) => (
        <div key={idx} className="border rounded-lg p-4">
          <h3 className="font-semibold mb-2">
            {benchmark.metric_name ?? "Metric"}{" "}
            {benchmark.unit && <span className="text-gray-500 text-sm">({benchmark.unit})</span>}
          </h3>
          {benchmark.industry_average !== null && benchmark.industry_average !== undefined && (
            <p className="text-sm text-gray-500 mb-2">
              Industry Average: {benchmark.industry_average}
            </p>
          )}
          <div className="space-y-1">
            {(benchmark.values ?? []).map((v, i) => (
              <div key={i} className="flex items-center gap-2">
                <span className="w-40 text-sm truncate">{v.project_name}</span>
                <div className="flex-1 bg-gray-200 rounded-full h-4">
                  <div
                    className="bg-blue-600 rounded-full h-4"
                    style={{
                      width: `${Math.min(100, (v.value / (benchmark.industry_average ?? v.value)) * 100)}%`,
                    }}
                  />
                </div>
                <span className="text-sm w-16 text-right">{v.value.toFixed(1)}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
