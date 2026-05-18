"""Stage 5: UQLM-style confidence scoring."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ConfidenceScorer:
    """UQLM-style claim-level confidence scoring.

    Decomposes output into claims and scores consistency.
    In production, samples multiple regenerations per claim.
    """

    def __init__(self, num_samples: int = 5):
        self.num_samples = num_samples

    async def score(
        self,
        agent_output: dict,
        agent_name: str,
    ) -> dict:
        """Score confidence of agent output.

        Returns overall confidence, per-claim scores,
        and routing recommendation.
        """
        claims = self._extract_claims(agent_output)
        if not claims:
            return {
                "overall_confidence": 1.0,
                "claim_scores": [],
                "routing_recommendation": "auto_approve",
            }

        claim_scores = []
        total_confidence = 0.0

        for claim in claims:
            # In production, sample N regenerations
            # and compute consistency score.
            # For now, use heuristic based on claim type.
            confidence = self._heuristic_confidence(
                claim,
                agent_name,
            )
            claim_scores.append(
                {
                    "claim": claim["text"],
                    "confidence": confidence,
                    "consistent": confidence >= 0.80,
                }
            )
            total_confidence += confidence

        overall = total_confidence / len(claims) if claims else 1.0

        recommendation = self._get_recommendation(
            overall,
            agent_name,
        )

        return {
            "overall_confidence": round(overall, 3),
            "claim_scores": claim_scores,
            "routing_recommendation": recommendation,
        }

    def _extract_claims(self, output: dict) -> list[dict]:
        """Extract verifiable claims from agent output."""
        claims: list[dict] = []
        if not output:
            return claims
        # Numeric values are verifiable claims
        for key, value in output.items():
            if key in ("raw_text", "format", "metadata"):
                continue
            if isinstance(value, int | float):
                claims.append(
                    {
                        "text": f"{key}={value}",
                        "type": "numeric",
                        "field": key,
                    }
                )
            elif isinstance(value, str) and len(value) > 10:
                claims.append(
                    {
                        "text": f"{key}: {value[:100]}",
                        "type": "text",
                        "field": key,
                    }
                )
        return claims

    def _heuristic_confidence(
        self,
        claim: dict,
        agent_name: str,
    ) -> float:
        """Heuristic confidence based on claim type."""
        base = 0.85
        if claim["type"] == "numeric":
            base = 0.90
        if agent_name in ("safety_alert", "change_order_impact"):
            base -= 0.05
        if agent_name == "daily_report":
            base += 0.05
        return min(1.0, max(0.0, base))

    def _get_recommendation(
        self,
        confidence: float,
        agent_name: str,
    ) -> str:
        """Get routing recommendation based on confidence."""
        from app.services.guardrails.routing_decision import (
            decide_route,
        )

        return decide_route(confidence, agent_name, [])
