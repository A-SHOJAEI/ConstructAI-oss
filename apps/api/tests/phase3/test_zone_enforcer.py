from __future__ import annotations

from app.services.vision.detector import Detection
from app.services.vision.zone_enforcer import ZoneEnforcer
from tests.fixtures.sample_zones import (
    MOCK_EQUIPMENT_ZONE,
    MOCK_PPE_ZONE,
    MOCK_RESTRICTED_ZONE,
)


class TestZoneEnforcer:
    def setup_method(self):
        self.enforcer = ZoneEnforcer()

    def test_point_inside_restricted_zone(self):
        self.enforcer.load_zones("cam1", [MOCK_RESTRICTED_ZONE])
        det = Detection(class_name="person", confidence=0.9, bbox=(150, 200, 250, 350))
        violations = self.enforcer.check_detection("cam1", det)
        assert len(violations) == 1
        assert violations[0]["violation"] == "zone_breach"

    def test_point_outside_zone(self):
        self.enforcer.load_zones("cam1", [MOCK_RESTRICTED_ZONE])
        det = Detection(class_name="person", confidence=0.9, bbox=(400, 400, 500, 500))
        violations = self.enforcer.check_detection("cam1", det)
        assert len(violations) == 0

    def test_ppe_zone_missing_hardhat(self):
        self.enforcer.load_zones("cam1", [MOCK_PPE_ZONE])
        det = Detection(
            class_name="person",
            confidence=0.9,
            bbox=(100, 100, 200, 300),
            attributes={"ppe": {"hardhat": False, "vest": True}},
        )
        violations = self.enforcer.check_detection("cam1", det)
        assert any(v["violation"] == "missing_hardhat" for v in violations)

    def test_ppe_zone_compliant(self):
        self.enforcer.load_zones("cam1", [MOCK_PPE_ZONE])
        det = Detection(
            class_name="person",
            confidence=0.9,
            bbox=(100, 100, 200, 300),
            attributes={"ppe": {"hardhat": True, "vest": True}},
        )
        violations = self.enforcer.check_detection("cam1", det)
        assert len(violations) == 0

    def test_equipment_only_zone_person(self):
        self.enforcer.load_zones("cam1", [MOCK_EQUIPMENT_ZONE])
        det = Detection(class_name="person", confidence=0.9, bbox=(75, 75, 125, 125))
        violations = self.enforcer.check_detection("cam1", det)
        assert len(violations) == 1
        assert violations[0]["violation"] == "zone_breach"

    def test_no_zones_configured(self):
        det = Detection(class_name="person", confidence=0.9, bbox=(100, 100, 200, 300))
        violations = self.enforcer.check_detection("cam1", det)
        assert len(violations) == 0
