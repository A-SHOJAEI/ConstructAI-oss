from __future__ import annotations

from app.services.safety.severity_classifier import classify_severity


class TestSeverityClassifier:
    def test_restricted_zone_breach_is_p1(self):
        assert classify_severity("restricted", "zone_breach") == "P1_critical"

    def test_crane_zone_breach_is_p1(self):
        assert classify_severity("crane_swing", "zone_breach") == "P1_critical"

    def test_missing_hardhat_ppe_zone_is_p2(self):
        assert classify_severity("ppe_required", "missing_hardhat") == "P2_high"

    def test_missing_vest_general_is_p4(self):
        assert classify_severity("general", "missing_vest") == "P4_low"

    def test_unknown_combination_defaults_p5(self):
        assert classify_severity("unknown", "unknown") == "P5_info"

    def test_severity_override(self):
        result = classify_severity("general", "zone_breach", severity_override="P1_critical")
        assert result == "P1_critical"

    def test_low_confidence_downgrades(self):
        result = classify_severity("restricted", "zone_breach", confidence=0.4)
        assert result == "P2_high"
