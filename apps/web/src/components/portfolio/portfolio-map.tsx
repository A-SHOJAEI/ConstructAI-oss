interface MapProject {
  project_id: string;
  project_name: string;
  spi?: number | null;
  cpi?: number | null;
  overall_health?: string;
  latitude?: number | null;
  longitude?: number | null;
}

function healthDot(health: string | undefined): string {
  switch (health) {
    case "good":
      return "bg-green-500";
    case "at_risk":
      return "bg-yellow-500";
    case "critical":
      return "bg-red-500";
    default:
      return "bg-gray-400";
  }
}

export function PortfolioMap({ projects }: { projects: MapProject[] }) {
  if (projects.length === 0) {
    return (
      <p className="text-gray-500 text-center py-8">No projects with location data available.</p>
    );
  }

  return (
    <div>
      <p className="text-sm text-gray-500 mb-4">
        Map visualization requires a mapping library (e.g., Mapbox, Leaflet). Showing project list
        with health indicators.
      </p>
      <div className="grid grid-cols-2 gap-3">
        {projects.map((p) => (
          <div key={p.project_id} className="flex items-center gap-3 p-3 border rounded-lg">
            <div className={`w-3 h-3 rounded-full ${healthDot(p.overall_health)}`} />
            <div>
              <p className="font-medium text-sm">{p.project_name}</p>
              <p className="text-xs text-gray-500">
                SPI: {p.spi?.toFixed(2) ?? "—"} | CPI: {p.cpi?.toFixed(2) ?? "—"}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
