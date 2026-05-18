"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  FolderKanban,
  FileText,
  CalendarDays,
  ShieldAlert,
  TrendingUp,
  ClipboardCheck,
  FileCheck,
  MessageCircleQuestion,
  // BarChart3,  // STUB: unused while Portfolio/Evaluation pages are hidden
  FileBarChart,
  // Layers,  // STUB: unused while Portfolio/Evaluation pages are hidden
  Settings,
  ShieldCheck,
  ClipboardList,
  ListChecks,
  Brain,
  Gavel,
  Receipt,
  GitPullRequest,
  PenTool,
  Users,
  X,
  Calculator,
  Ruler,
  ScanLine,
  Camera,
  Plane,
  HardHat,
  DollarSign,
  Zap,
  CreditCard,
  Shield,
  FileSignature,
  Leaf,
  Network,
  Box,
  Languages,
  RefreshCw,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

const navGroups: NavGroup[] = [
  {
    label: "",
    items: [
      { href: "/projects", label: "Projects", icon: FolderKanban },
      { href: "/documents", label: "Documents", icon: FileText },
    ],
  },
  {
    label: "Planning",
    items: [
      { href: "/estimating", label: "Estimating", icon: Calculator },
      { href: "/takeoffs", label: "Takeoffs", icon: Ruler },
      { href: "/schedule", label: "Schedule", icon: CalendarDays },
      { href: "/bids", label: "Bids", icon: Gavel },
    ],
  },
  {
    label: "Field",
    items: [
      { href: "/ambient", label: "Ambient Field", icon: ScanLine },
      { href: "/progress", label: "Progress", icon: Camera },
      { href: "/drones", label: "Drones", icon: Plane },
      { href: "/workforce", label: "Workforce", icon: HardHat },
      { href: "/sub-portal", label: "Sub Portal", icon: Users },
      { href: "/daily-logs", label: "Daily Logs", icon: ClipboardList },
      { href: "/safety", label: "Safety", icon: ShieldAlert },
    ],
  },
  {
    label: "Quality & Compliance",
    items: [
      { href: "/quality", label: "Quality", icon: ClipboardCheck },
      { href: "/rfis", label: "RFIs", icon: MessageCircleQuestion },
      { href: "/submittals", label: "Submittals", icon: FileCheck },
      { href: "/punch-list", label: "Punch List", icon: ListChecks },
      { href: "/drawings", label: "Drawings", icon: PenTool },
    ],
  },
  {
    label: "Financial",
    items: [
      { href: "/controls", label: "Controls", icon: TrendingUp },
      { href: "/cash-flow", label: "Cash Flow", icon: DollarSign },
      { href: "/instant-pay", label: "Instant Pay", icon: Zap },
      { href: "/pay-applications", label: "Pay Apps", icon: Receipt },
      { href: "/change-orders", label: "Change Orders", icon: GitPullRequest },
      { href: "/payroll", label: "Payroll", icon: CreditCard },
      { href: "/insurance", label: "Insurance", icon: Shield },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { href: "/intelligence", label: "Intelligence", icon: Brain },
      { href: "/contracts", label: "Contracts", icon: FileSignature },
      { href: "/sustainability", label: "Sustainability", icon: Leaf },
      { href: "/cross-project", label: "Cross-Project", icon: Network },
      { href: "/digital-twin", label: "Digital Twin", icon: Box },
    ],
  },
  {
    label: "Tools",
    items: [
      { href: "/translation", label: "Translation", icon: Languages },
      { href: "/sync", label: "Offline Sync", icon: RefreshCw },
      { href: "/meetings", label: "Meetings", icon: Users },
    ],
  },
  {
    label: "",
    items: [
      { href: "/reports", label: "Reports", icon: FileBarChart },
      // STUB: Removed from navigation until backend implementation is complete
      // { href: "/portfolio", label: "Portfolio", icon: Layers },
      // { href: "/evaluation", label: "Evaluation", icon: BarChart3 },
      // { href: "/feedback", label: "Feedback", icon: ... },
      { href: "/settings", label: "Settings", icon: Settings },
      { href: "/admin", label: "Admin", icon: ShieldCheck },
    ],
  },
];

interface SidebarProps {
  open?: boolean;
  onClose?: () => void;
}

export function Sidebar({ open, onClose }: SidebarProps) {
  const pathname = usePathname();

  const nav = (
    <nav className="flex flex-col gap-1 p-4 overflow-y-auto">
      {navGroups.map((group, groupIdx) => (
        <div key={groupIdx}>
          {group.label && (
            <p className="px-3 pt-4 pb-1 text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider">
              {group.label}
            </p>
          )}
          {group.items.map((item) => {
            const isActive = pathname.startsWith(item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={onClose}
                aria-current={isActive ? "page" : undefined}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-primary/10 text-primary"
                    : "text-gray-600 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-700 dark:hover:text-white",
                )}
              >
                <Icon className="h-4 w-4" aria-hidden="true" />
                {item.label}
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );

  return (
    <>
      {/* Desktop sidebar */}
      <aside className="hidden md:flex w-64 flex-col border-r border-gray-200 bg-white dark:bg-gray-800 dark:border-gray-700">
        {nav}
      </aside>

      {/* Mobile sidebar overlay */}
      {open && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="fixed inset-0 bg-black/50" onClick={onClose} aria-hidden="true" />
          <aside className="fixed inset-y-0 left-0 z-50 w-64 bg-white dark:bg-gray-800 shadow-xl flex flex-col">
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
              <span className="font-semibold text-gray-900 dark:text-white">Menu</span>
              <button
                onClick={onClose}
                className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700"
                aria-label="Close menu"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            {nav}
          </aside>
        </div>
      )}
    </>
  );
}
