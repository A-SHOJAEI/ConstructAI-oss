interface AgentMetric {
  agent_name: string;
  accuracy: number | null;
  avg_latency_ms: number | null;
  total_cost_usd: number | null;
  error_rate: number | null;
  total_invocations: number;
}

export function AgentMetricsTable({
  metrics,
  onSelectAgent,
}: {
  metrics: AgentMetric[];
  onSelectAgent: (name: string) => void;
}) {
  if (metrics.length === 0) {
    return <p className="text-gray-500 text-center py-4">No agent metrics available.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left">
            <th scope="col" className="py-2 px-3">
              Agent
            </th>
            <th scope="col" className="py-2 px-3">
              Accuracy
            </th>
            <th scope="col" className="py-2 px-3">
              Avg Latency
            </th>
            <th scope="col" className="py-2 px-3">
              Total Cost
            </th>
            <th scope="col" className="py-2 px-3">
              Error Rate
            </th>
            <th scope="col" className="py-2 px-3">
              Invocations
            </th>
          </tr>
        </thead>
        <tbody>
          {metrics.map((m) => (
            <tr
              key={m.agent_name}
              className="border-b hover:bg-blue-50 cursor-pointer"
              onClick={() => onSelectAgent(m.agent_name)}
            >
              <td className="py-2 px-3 font-medium">{m.agent_name.replace(/_/g, " ")}</td>
              <td className="py-2 px-3">
                {m.accuracy !== null ? (m.accuracy * 100).toFixed(1) + "%" : "—"}
              </td>
              <td className="py-2 px-3">
                {m.avg_latency_ms !== null ? m.avg_latency_ms.toFixed(0) + "ms" : "—"}
              </td>
              <td className="py-2 px-3">
                {m.total_cost_usd !== null ? "$" + m.total_cost_usd.toFixed(4) : "—"}
              </td>
              <td className="py-2 px-3">
                {m.error_rate !== null ? (m.error_rate * 100).toFixed(1) + "%" : "—"}
              </td>
              <td className="py-2 px-3">{m.total_invocations}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
