"""Point-in-polygon zone checking for detection events."""

from __future__ import annotations

import logging

from app.services.vision.detector import Detection
from app.utils.geometry import point_in_polygon

logger = logging.getLogger(__name__)


class ZoneEnforcer:
    """Check if detected objects violate zone rules."""

    def __init__(self):
        self.zones: dict[str, list[dict]] = {}

    def load_zones(self, camera_id: str, zones: list[dict]):
        """Load zone configurations for a camera."""
        self.zones[camera_id] = []
        for zone in zones:
            self.zones[camera_id].append(
                {
                    "zone_id": zone["id"],
                    "zone_type": zone["zone_type"],
                    "polygon": zone["polygon_points"],
                    "ppe_requirements": zone.get("ppe_requirements", []),
                    "severity_override": zone.get("severity_override"),
                }
            )

    def check_detection(self, camera_id: str, detection: Detection) -> list[dict]:
        """Check if detection violates any zone rules."""
        cx = (detection.bbox[0] + detection.bbox[2]) / 2
        cy = (detection.bbox[1] + detection.bbox[3]) / 2

        violations = []
        for zone in self.zones.get(camera_id, []):
            if not point_in_polygon(cx, cy, zone["polygon"]):
                continue

            zone_type = zone["zone_type"]
            is_person = detection.class_name == "person"
            if is_person and zone_type in ("restricted", "crane_swing", "excavation"):
                violations.append(
                    {
                        "zone_id": zone["zone_id"],
                        "zone_type": zone_type,
                        "violation": "zone_breach",
                        "severity_override": zone["severity_override"],
                    }
                )
            elif zone_type == "ppe_required":
                ppe = detection.attributes.get("ppe", {})
                for req in zone["ppe_requirements"]:
                    if not ppe.get(req, False):
                        violations.append(
                            {
                                "zone_id": zone["zone_id"],
                                "zone_type": zone_type,
                                "violation": f"missing_{req}",
                                "severity_override": zone["severity_override"],
                            }
                        )
            elif (zone_type == "equipment_only" and is_person) or (
                zone_type == "pedestrian_only" and not is_person
            ):
                violations.append(
                    {
                        "zone_id": zone["zone_id"],
                        "zone_type": zone_type,
                        "violation": "zone_breach",
                        "severity_override": zone["severity_override"],
                    }
                )
        return violations

    def clear_zones(self, camera_id: str | None = None):
        if camera_id:
            self.zones.pop(camera_id, None)
        else:
            self.zones.clear()
