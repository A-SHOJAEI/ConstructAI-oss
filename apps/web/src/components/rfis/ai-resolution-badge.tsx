"use client";

import { Bot, Sparkles, FileCheck } from "lucide-react";

interface AIResolutionBadgeProps {
  aiStatus: "unnecessary" | "draft_available" | "auto_resolved" | null;
}

const statusConfig = {
  unnecessary: {
    icon: Bot,
    label: "Unnecessary",
    className: "bg-purple-100 text-purple-800",
  },
  draft_available: {
    icon: FileCheck,
    label: "AI Draft",
    className: "bg-blue-100 text-blue-800",
  },
  auto_resolved: {
    icon: Sparkles,
    label: "AI Resolved",
    className: "bg-emerald-100 text-emerald-800",
  },
};

export function AIResolutionBadge({ aiStatus }: AIResolutionBadgeProps) {
  if (!aiStatus) return null;

  const config = statusConfig[aiStatus];
  if (!config) return null;

  const Icon = config.icon;

  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${config.className}`}
    >
      <Icon className="h-3 w-3" />
      {config.label}
    </span>
  );
}
