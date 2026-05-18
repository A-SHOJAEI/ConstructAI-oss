"use client";

import type { BidWithDecision } from "@/lib/bid-api";
import { MapPin, Calendar, DollarSign } from "lucide-react";

interface BidCardProps {
  bid: BidWithDecision;
  onClick: () => void;
}

export function BidCard({ bid, onClick }: BidCardProps) {
  const score = bid.latest_decision?.ai_score;
  const scoreColor =
    score != null
      ? score >= 70
        ? "bg-green-100 text-green-800 border-green-300"
        : score >= 40
          ? "bg-amber-100 text-amber-800 border-amber-300"
          : "bg-red-100 text-red-800 border-red-300"
      : "bg-gray-100 text-gray-500 border-gray-200";

  return (
    <button
      onClick={onClick}
      className="w-full text-left bg-white rounded-lg border border-gray-200 p-3 hover:shadow-md transition-shadow"
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        <h4 className="text-sm font-semibold text-gray-900 line-clamp-2">{bid.project_name}</h4>
        {score != null && (
          <span
            className={`shrink-0 inline-flex items-center justify-center w-8 h-8 rounded-full border text-xs font-bold ${scoreColor}`}
          >
            {score}
          </span>
        )}
      </div>

      {bid.owner_name && <p className="text-xs text-gray-500 mb-1 truncate">{bid.owner_name}</p>}

      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-gray-500 mt-2">
        {bid.location && (
          <span className="flex items-center gap-1">
            <MapPin className="h-3 w-3" /> {bid.location}
          </span>
        )}
        {bid.estimated_value > 0 && (
          <span className="flex items-center gap-1">
            <DollarSign className="h-3 w-3" />
            {(bid.estimated_value / 1_000_000).toFixed(1)}M
          </span>
        )}
        {bid.bid_due_date && (
          <span className="flex items-center gap-1">
            <Calendar className="h-3 w-3" />
            {new Date(bid.bid_due_date).toLocaleDateString()}
          </span>
        )}
      </div>

      <div className="flex gap-1.5 mt-2">
        <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-gray-100 text-gray-600 capitalize">
          {bid.project_type.replace("_", " ")}
        </span>
        <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-50 text-blue-700 capitalize">
          {bid.delivery_method.replace("_", " ")}
        </span>
      </div>
    </button>
  );
}
