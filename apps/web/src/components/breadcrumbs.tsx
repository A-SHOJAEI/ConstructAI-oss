"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LABEL_MAP: Record<string, string> = {
  safety: "Safety",
  cameras: "Cameras",
  alerts: "Alerts",
  controls: "Controls",
  rfis: "RFIs",
  submittals: "Submittals",
  "daily-logs": "Daily Logs",
  "punch-list": "Punch List",
  schedule: "Schedule",
  quality: "Quality",
  projects: "Projects",
  documents: "Documents",
  estimating: "Estimating",
  reports: "Reports",
  settings: "Settings",
  admin: "Administration",
  "pay-applications": "Pay Applications",
  portfolio: "Portfolio",
  evaluation: "Evaluation",
};

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function segmentLabel(seg: string): string {
  if (LABEL_MAP[seg]) return LABEL_MAP[seg];
  if (UUID_RE.test(seg)) return `#${seg.slice(0, 8)}`;
  return seg.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function Breadcrumbs() {
  const pathname = usePathname();
  const segments = pathname.split("/").filter(Boolean);

  if (segments.length <= 1) return null;

  const crumbs = segments.map((seg, i) => ({
    label: segmentLabel(seg),
    href: "/" + segments.slice(0, i + 1).join("/"),
    isLast: i === segments.length - 1,
  }));

  return (
    <nav aria-label="Breadcrumb" className="mb-4">
      <ol className="flex items-center gap-1 text-sm text-gray-500">
        <li>
          <Link href="/" className="hover:text-gray-700">
            Home
          </Link>
        </li>
        {crumbs.map((crumb) => (
          <li key={crumb.href} className="flex items-center gap-1">
            <span className="text-gray-300">/</span>
            {crumb.isLast ? (
              <span className="text-gray-900 font-medium">{crumb.label}</span>
            ) : (
              <Link href={crumb.href} className="hover:text-gray-700">
                {crumb.label}
              </Link>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}
