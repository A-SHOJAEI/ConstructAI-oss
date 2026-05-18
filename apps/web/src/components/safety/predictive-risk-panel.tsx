"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { RiskScoreGauge } from "./risk-score-gauge";
import { RiskTrendChart } from "./risk-trend-chart";
import { CVDetectionsGallery } from "./cv-detections-gallery";
import { ShieldAlert, ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";

interface RiskScore {
  overall_score: number;
  category_scores: Record<string, number>;
  top_risks: { category: string; description: string; score: number }[];
  safety_briefing: string | null;
}

interface PredictiveRiskPanelProps {
  projectId: string;
}

export function PredictiveRiskPanel({ projectId }: PredictiveRiskPanelProps) {
  const [showBriefing, setShowBriefing] = useState(false);

  const { data: riskScore } = useQuery({
    queryKey: ["risk-score", projectId],
    queryFn: () => apiClient.get<RiskScore>(`/api/v1/projects/${projectId}/safety/risk-score`),
  });

  const { data: trends } = useQuery({
    queryKey: ["risk-trends", projectId],
    queryFn: () =>
      apiClient.get<{ data: { date: string; score: number }[] }>(
        `/api/v1/projects/${projectId}/safety/trends?days=14`,
      ),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <ShieldAlert className="h-5 w-5 text-amber-600" />
        <h2 className="text-lg font-semibold text-gray-900">Predictive Safety</h2>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Risk Gauge */}
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <h3 className="text-sm font-medium text-gray-700 mb-3">Today&apos;s Risk Score</h3>
          {riskScore ? (
            <RiskScoreGauge
              score={riskScore.overall_score}
              categories={riskScore.category_scores}
            />
          ) : (
            <div className="flex items-center justify-center h-48 text-gray-400 text-sm">
              Loading risk score...
            </div>
          )}
        </div>

        {/* Risk Trend */}
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <RiskTrendChart trends={trends?.data ?? []} />
        </div>
      </div>

      {/* Safety Briefing */}
      {riskScore?.safety_briefing && (
        <button
          onClick={() => setShowBriefing(!showBriefing)}
          className="w-full bg-amber-50 border border-amber-200 rounded-lg p-3 text-left"
        >
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-amber-800">Today&apos;s Safety Briefing</span>
            {showBriefing ? (
              <ChevronUp className="h-4 w-4 text-amber-600" />
            ) : (
              <ChevronDown className="h-4 w-4 text-amber-600" />
            )}
          </div>
          {showBriefing && (
            <p className="text-sm text-amber-700 mt-2 whitespace-pre-wrap">
              {riskScore.safety_briefing}
            </p>
          )}
        </button>
      )}

      {/* CV Detections Gallery */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <CVDetectionsGallery projectId={projectId} />
      </div>
    </div>
  );
}
