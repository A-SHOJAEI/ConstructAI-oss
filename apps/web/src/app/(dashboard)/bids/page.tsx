"use client";

import { useQuery } from "@tanstack/react-query";
import { bidApi } from "@/lib/bid-api";
import type { BidWithDecision } from "@/lib/bid-api";
import { BidCard } from "@/components/bids/bid-card";
import { BidDetailDialog } from "@/components/bids/bid-detail-dialog";
import { Gavel, TrendingUp, Target, DollarSign } from "lucide-react";
import { useState } from "react";
import { useAuth } from "@/hooks/use-auth";

const columns = [
  { key: "evaluating", label: "Evaluating", color: "border-blue-400" },
  { key: "pursuing", label: "Pursuing", color: "border-green-400" },
  { key: "submitted", label: "Submitted", color: "border-amber-400" },
  { key: "won", label: "Won / Lost", color: "border-purple-400" },
];

export default function BidPipelinePage() {
  const { user } = useAuth();
  const orgId = user?.org_id ?? "";
  const [selectedBid, setSelectedBid] = useState<BidWithDecision | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["bids", orgId],
    queryFn: () => bidApi.list(orgId),
    enabled: !!orgId,
  });

  const { data: analytics } = useQuery({
    queryKey: ["bid-analytics", orgId],
    queryFn: () => bidApi.analytics(orgId),
    enabled: !!orgId,
  });

  const bids = data?.data ?? [];

  const grouped: Record<string, BidWithDecision[]> = {
    evaluating: [],
    pursuing: [],
    submitted: [],
    won: [],
  };

  bids.forEach((bid) => {
    const status = bid.status.toLowerCase();
    if (status === "won" || status === "lost") {
      grouped.won.push(bid);
    } else if (grouped[status]) {
      grouped[status].push(bid);
    } else {
      grouped.evaluating.push(bid);
    }
  });

  return (
    <div className="p-4 md:p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Gavel className="h-6 w-6 text-indigo-600" />
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Bid Pipeline</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Track and evaluate bid opportunities
          </p>
        </div>
      </div>

      {/* Analytics Stats */}
      {analytics && (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 shadow-sm">
            <div className="flex items-center gap-2 mb-1">
              <Target className="h-4 w-4 text-gray-400" />
              <p className="text-sm text-gray-500 dark:text-gray-400">Total Opportunities</p>
            </div>
            <p className="text-2xl font-bold text-gray-900 dark:text-white">
              {analytics.total_opportunities}
            </p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 shadow-sm">
            <div className="flex items-center gap-2 mb-1">
              <TrendingUp className="h-4 w-4 text-gray-400" />
              <p className="text-sm text-gray-500 dark:text-gray-400">Win Rate</p>
            </div>
            <p className="text-2xl font-bold text-green-600">
              {isNaN(analytics.win_rate) ? 0 : Math.round(analytics.win_rate * 100)}%
            </p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 shadow-sm">
            <div className="flex items-center gap-2 mb-1">
              <DollarSign className="h-4 w-4 text-gray-400" />
              <p className="text-sm text-gray-500 dark:text-gray-400">Pipeline Value</p>
            </div>
            <p className="text-2xl font-bold text-gray-900 dark:text-white">
              ${(bids.reduce((s, b) => s + b.estimated_value, 0) / 1_000_000).toFixed(1)}M
            </p>
          </div>
          <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 shadow-sm">
            <div className="flex items-center gap-2 mb-1">
              <Gavel className="h-4 w-4 text-gray-400" />
              <p className="text-sm text-gray-500 dark:text-gray-400">Avg AI Score</p>
            </div>
            <p className="text-2xl font-bold text-purple-600">
              {Math.round(analytics.avg_ai_score)}
            </p>
          </div>
        </div>
      )}

      {/* Kanban Board */}
      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="bg-gray-50 dark:bg-gray-800 rounded-lg p-4 animate-pulse">
              <div className="h-5 bg-gray-200 dark:bg-gray-700 rounded w-24 mb-4" />
              <div className="space-y-3">
                <div className="h-24 bg-gray-200 dark:bg-gray-700 rounded" />
                <div className="h-24 bg-gray-200 dark:bg-gray-700 rounded" />
              </div>
            </div>
          ))}
        </div>
      ) : bids.length === 0 ? (
        <div className="text-center py-12 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
          <svg
            className="mx-auto h-12 w-12 text-gray-400"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={1.5}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M12 6v12m-3-2.818l.879.659c1.171.879 3.07.879 4.242 0 1.172-.879 1.172-2.303 0-3.182C13.536 12.219 12.768 12 12 12c-.725 0-1.45-.22-2.003-.659-1.106-.879-1.106-2.303 0-3.182s2.9-.879 4.006 0l.415.33M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
            />
          </svg>
          <h3 className="mt-2 text-sm font-semibold text-gray-900 dark:text-white">No bids</h3>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            Get started by adding a new bid opportunity.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          {columns.map((col) => (
            <div key={col.key} className="bg-gray-50 dark:bg-gray-800 rounded-lg p-3">
              <div className={`flex items-center gap-2 mb-3 pb-2 border-b-2 ${col.color}`}>
                <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-200">
                  {col.label}
                </h3>
                <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-gray-200 dark:bg-gray-600 text-[10px] font-medium text-gray-600 dark:text-gray-300">
                  {grouped[col.key].length}
                </span>
              </div>
              <div className="space-y-2 min-h-[200px]">
                {grouped[col.key].map((bid) => (
                  <BidCard key={bid.id} bid={bid} onClick={() => setSelectedBid(bid)} />
                ))}
                {grouped[col.key].length === 0 && (
                  <p className="text-xs text-gray-400 dark:text-gray-500 text-center py-8">
                    No bids
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {selectedBid && <BidDetailDialog bid={selectedBid} onClose={() => setSelectedBid(null)} />}
    </div>
  );
}
