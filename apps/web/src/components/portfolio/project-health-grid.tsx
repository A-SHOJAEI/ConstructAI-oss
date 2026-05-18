interface ProjectHealth {
  project_id: string;
  project_name: string;
  spi: number | null;
  cpi: number | null;
  safety_score: number | null;
  quality_score: number | null;
  overall_health: string;
  active_risks: number;
  open_rfis: number;
}

function healthColor(health: string): string {
  switch (health) {
    case "good":
      return "bg-green-100 text-green-800";
    case "at_risk":
      return "bg-yellow-100 text-yellow-800";
    case "critical":
      return "bg-red-100 text-red-800";
    default:
      return "bg-gray-100 text-gray-800";
  }
}

function metricColor(value: number | null, low: number, high: number): string {
  if (value === null) return "text-gray-400";
  if (value >= high) return "text-green-600";
  if (value >= low) return "text-yellow-600";
  return "text-red-600";
}

export function ProjectHealthGrid({ projects }: { projects: ProjectHealth[] }) {
  if (projects.length === 0) {
    return (
      <p className="text-gray-500 text-center py-8">
        No projects found. Create a project to see health indicators.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left">
            <th scope="col" className="py-2 px-3">
              Project
            </th>
            <th scope="col" className="py-2 px-3">
              Health
            </th>
            <th scope="col" className="py-2 px-3">
              SPI
            </th>
            <th scope="col" className="py-2 px-3">
              CPI
            </th>
            <th scope="col" className="py-2 px-3">
              Safety
            </th>
            <th scope="col" className="py-2 px-3">
              Quality
            </th>
            <th scope="col" className="py-2 px-3">
              Risks
            </th>
            <th scope="col" className="py-2 px-3">
              Open RFIs
            </th>
          </tr>
        </thead>
        <tbody>
          {projects.map((p) => (
            <tr key={p.project_id} className="border-b hover:bg-gray-50">
              <td className="py-2 px-3 font-medium">{p.project_name}</td>
              <td className="py-2 px-3">
                <span className={`px-2 py-1 rounded-full text-xs ${healthColor(p.overall_health)}`}>
                  {p.overall_health}
                </span>
              </td>
              <td className={`py-2 px-3 ${metricColor(p.spi, 0.9, 1.0)}`}>
                {p.spi?.toFixed(2) ?? "—"}
              </td>
              <td className={`py-2 px-3 ${metricColor(p.cpi, 0.9, 1.0)}`}>
                {p.cpi?.toFixed(2) ?? "—"}
              </td>
              <td className={`py-2 px-3 ${metricColor(p.safety_score, 0.7, 0.9)}`}>
                {p.safety_score?.toFixed(0) ?? "—"}
              </td>
              <td className={`py-2 px-3 ${metricColor(p.quality_score, 0.7, 0.9)}`}>
                {p.quality_score?.toFixed(0) ?? "—"}
              </td>
              <td className="py-2 px-3">{p.active_risks}</td>
              <td className="py-2 px-3">{p.open_rfis}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
