"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import type { BidWithDecision } from "@/lib/bid-api";
import { bidApi } from "@/lib/bid-api";
import { X, Loader2, Sparkles, ThumbsUp, ThumbsDown } from "lucide-react";
import { useState } from "react";
import { useAuth } from "@/hooks/use-auth";

interface BidDetailDialogProps {
  bid: BidWithDecision;
  onClose: () => void;
}

const factorLabels: Record<string, string> = {
  profit_potential: "Profit Potential",
  win_probability: "Win Probability",
  resource_fit: "Resource Fit",
  risk_level: "Risk Level",
  strategic_value: "Strategic Value",
  relationship: "Client Relationship",
};

export function BidDetailDialog({ bid, onClose }: BidDetailDialogProps) {
  const { user } = useAuth();
  const orgId = user?.org_id ?? "";
  const queryClient = useQueryClient();
  const [notes, setNotes] = useState("");

  const scoreMutation = useMutation({
    mutationFn: () => bidApi.score(orgId, bid.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["bids"] });
    },
    onError: () => {
      toast.error("Failed to score bid. Please try again.");
    },
  });

  const decideMutation = useMutation({
    mutationFn: (decision: string) => bidApi.decide(orgId, bid.id, decision, notes || undefined),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["bids"] });
      onClose();
    },
    onError: () => {
      toast.error("Failed to submit decision. Please try again.");
    },
  });

  const decision = bid.latest_decision;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      role="dialog"
      aria-modal="true"
      aria-labelledby="bid-detail-dialog-title"
      onKeyDown={(e) => {
        if (e.key === "Tab") {
          const focusable = e.currentTarget.querySelectorAll<HTMLElement>(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
          );
          if (focusable.length === 0) return;
          const first = focusable[0];
          const last = focusable[focusable.length - 1];
          if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
        }
        if (e.key === "Escape") {
          onClose();
        }
      }}
    >
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg m-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <h3 id="bid-detail-dialog-title" className="font-semibold text-gray-900">
            Bid Details
          </h3>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600"
            aria-label="Close dialog"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="p-4 space-y-4">
          <div>
            <h4 className="text-lg font-semibold text-gray-900">{bid.project_name}</h4>
            {bid.owner_name && <p className="text-sm text-gray-500">{bid.owner_name}</p>}
          </div>

          <div className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <p className="text-gray-500">Value</p>
              <p className="font-medium text-gray-900">
                ${(bid.estimated_value / 1_000_000).toFixed(2)}M
              </p>
            </div>
            <div>
              <p className="text-gray-500">Due Date</p>
              <p className="font-medium text-gray-900">
                {bid.bid_due_date ? new Date(bid.bid_due_date).toLocaleDateString() : "N/A"}
              </p>
            </div>
            <div>
              <p className="text-gray-500">Type</p>
              <p className="font-medium text-gray-900 capitalize">
                {bid.project_type.replace("_", " ")}
              </p>
            </div>
            <div>
              <p className="text-gray-500">Delivery</p>
              <p className="font-medium text-gray-900 capitalize">
                {bid.delivery_method.replace("_", " ")}
              </p>
            </div>
          </div>

          {bid.description && <p className="text-sm text-gray-600">{bid.description}</p>}

          {/* AI Score Section */}
          <div className="border-t border-gray-100 pt-4">
            <div className="flex items-center justify-between mb-3">
              <h4 className="text-sm font-semibold text-gray-900 flex items-center gap-1.5">
                <Sparkles className="h-4 w-4 text-purple-500" /> AI Analysis
              </h4>
              {!decision && (
                <button
                  onClick={() => scoreMutation.mutate()}
                  disabled={scoreMutation.isPending}
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-purple-600 text-white rounded-lg text-xs font-medium hover:bg-purple-700 disabled:opacity-50"
                >
                  {scoreMutation.isPending ? (
                    <>
                      <Loader2 className="h-3 w-3 animate-spin" /> Scoring...
                    </>
                  ) : (
                    "Run AI Score"
                  )}
                </button>
              )}
            </div>

            {decision && (
              <div className="space-y-3">
                <div className="flex items-center gap-3">
                  <div className="text-center">
                    <p className="text-3xl font-bold text-purple-600">{decision.ai_score}</p>
                    <p className="text-[10px] text-gray-500 uppercase">Score</p>
                  </div>
                  <div className="text-center">
                    <p className="text-3xl font-bold text-blue-600">
                      {Math.round(decision.win_probability * 100)}%
                    </p>
                    <p className="text-[10px] text-gray-500 uppercase">Win Prob</p>
                  </div>
                  <div className="flex-1">
                    <span
                      className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium capitalize ${
                        decision.ai_recommendation === "pursue"
                          ? "bg-green-100 text-green-800"
                          : decision.ai_recommendation === "pass"
                            ? "bg-red-100 text-red-800"
                            : "bg-amber-100 text-amber-800"
                      }`}
                    >
                      {decision.ai_recommendation}
                    </span>
                  </div>
                </div>

                {decision.ai_reasoning && (
                  <p className="text-xs text-gray-600">{decision.ai_reasoning}</p>
                )}

                {/* Factor scores */}
                {Object.keys(decision.factor_scores).length > 0 && (
                  <div className="space-y-1.5">
                    {Object.entries(decision.factor_scores).map(([key, val]) => (
                      <div key={key} className="flex items-center gap-2">
                        <span className="text-xs text-gray-500 w-28 truncate">
                          {factorLabels[key] ?? key.replace("_", " ")}
                        </span>
                        <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-purple-500 rounded-full"
                            style={{ width: `${val}%` }}
                          />
                        </div>
                        <span className="text-xs font-medium text-gray-700 w-6 text-right">
                          {val}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Decision Section */}
          {decision && !decision.human_decision && (
            <div className="border-t border-gray-100 pt-4 space-y-3">
              <h4 className="text-sm font-semibold text-gray-900">Your Decision</h4>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Optional notes..."
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                rows={2}
              />
              <div className="flex gap-2">
                <button
                  onClick={() => decideMutation.mutate("pursue")}
                  disabled={decideMutation.isPending}
                  className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50"
                >
                  <ThumbsUp className="h-4 w-4" /> Pursue
                </button>
                <button
                  onClick={() => decideMutation.mutate("pass")}
                  disabled={decideMutation.isPending}
                  className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 bg-red-600 text-white rounded-lg text-sm font-medium hover:bg-red-700 disabled:opacity-50"
                >
                  <ThumbsDown className="h-4 w-4" /> Pass
                </button>
              </div>
            </div>
          )}

          {decision?.human_decision && (
            <div className="border-t border-gray-100 pt-4">
              <p className="text-sm text-gray-600">
                Decision:{" "}
                <span className="font-medium capitalize text-gray-900">
                  {decision.human_decision}
                </span>
              </p>
              {decision.human_notes && (
                <p className="text-xs text-gray-500 mt-1">{decision.human_notes}</p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
