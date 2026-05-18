"""Tests for the ZoneEnforcer point-in-polygon zone checker.

Detects when a person, equipment, or pedestrian crosses into a zone
that's restricted for them. Pin every zone-type rule:

- ``restricted`` / ``crane_swing`` / ``excavation``: person → breach.
- ``ppe_required``: missing PPE → per-item violation.
- ``equipment_only``: person → breach (zone is for vehicles only).
- ``pedestrian_only``: non-person → breach (no equipment in walkways).
"""

from __future__ import annotations

from app.services.vision.detector import Detection
from app.services.vision.zone_enforcer import ZoneEnforcer

# A 100x100 square at origin — used by every test that needs a polygon.
_SQUARE_POLY = [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0]]


def _person_at(x: int, y: int) -> Detection:
    return Detection(
        class_name="person",
        confidence=0.9,
        bbox=(x - 10, y - 10, x + 10, y + 10),  # centered at (x, y)
    )


def _equipment_at(x: int, y: int, klass: str = "excavator") -> Detection:
    return Detection(
        class_name=klass,
        confidence=0.9,
        bbox=(x - 10, y - 10, x + 10, y + 10),
    )


def _zone(zone_id: str, zone_type: str, **extra) -> dict:
    z = {
        "id": zone_id,
        "zone_type": zone_type,
        "polygon_points": _SQUARE_POLY,
        "ppe_requirements": [],
        "severity_override": None,
    }
    z.update(extra)
    return z


# ---- load_zones / clear_zones ------------------------------------------


def test_load_zones_replaces_existing_for_camera():
    e = ZoneEnforcer()
    e.load_zones("cam-1", [_zone("z1", "restricted")])
    assert len(e.zones["cam-1"]) == 1
    e.load_zones("cam-1", [_zone("z2", "ppe_required")])
    # Re-loading replaces (not appends):
    assert len(e.zones["cam-1"]) == 1
    assert e.zones["cam-1"][0]["zone_id"] == "z2"


def test_clear_zones_for_specific_camera():
    e = ZoneEnforcer()
    e.load_zones("cam-1", [_zone("z1", "restricted")])
    e.load_zones("cam-2", [_zone("z2", "restricted")])
    e.clear_zones("cam-1")
    assert "cam-1" not in e.zones
    assert "cam-2" in e.zones


def test_clear_all_zones_when_no_camera_given():
    e = ZoneEnforcer()
    e.load_zones("cam-1", [_zone("z1", "restricted")])
    e.load_zones("cam-2", [_zone("z2", "restricted")])
    e.clear_zones()
    assert e.zones == {}


# ---- restricted-zone breaches ------------------------------------------


def test_person_inside_restricted_zone_is_breach():
    e = ZoneEnforcer()
    e.load_zones("cam-1", [_zone("z1", "restricted")])
    out = e.check_detection("cam-1", _person_at(50, 50))
    assert len(out) == 1
    assert out[0]["violation"] == "zone_breach"
    assert out[0]["zone_type"] == "restricted"


def test_person_outside_restricted_zone_is_clean():
    e = ZoneEnforcer()
    e.load_zones("cam-1", [_zone("z1", "restricted")])
    out = e.check_detection("cam-1", _person_at(500, 500))  # far outside
    assert out == []


def test_crane_swing_zone_breach():
    """Same rules as restricted: person inside crane swing radius =
    P1 critical breach."""
    e = ZoneEnforcer()
    e.load_zones("cam-1", [_zone("z1", "crane_swing")])
    out = e.check_detection("cam-1", _person_at(50, 50))
    assert out[0]["zone_type"] == "crane_swing"


def test_excavation_zone_breach():
    e = ZoneEnforcer()
    e.load_zones("cam-1", [_zone("z1", "excavation")])
    out = e.check_detection("cam-1", _person_at(50, 50))
    assert out[0]["zone_type"] == "excavation"


def test_equipment_inside_restricted_zone_not_a_breach():
    """Restricted zones target person presence — equipment passing
    through doesn't trigger."""
    e = ZoneEnforcer()
    e.load_zones("cam-1", [_zone("z1", "restricted")])
    out = e.check_detection("cam-1", _equipment_at(50, 50, "excavator"))
    assert out == []


# ---- PPE-required zones -------------------------------------------------


def test_ppe_required_no_hardhat_is_violation():
    e = ZoneEnforcer()
    e.load_zones(
        "cam-1",
        [_zone("z1", "ppe_required", ppe_requirements=["hardhat"])],
    )
    person = _person_at(50, 50)
    person.attributes = {"ppe": {"hardhat": False}}
    out = e.check_detection("cam-1", person)
    assert len(out) == 1
    assert out[0]["violation"] == "missing_hardhat"


def test_ppe_required_all_present_no_violation():
    e = ZoneEnforcer()
    e.load_zones(
        "cam-1",
        [_zone("z1", "ppe_required", ppe_requirements=["hardhat", "vest"])],
    )
    person = _person_at(50, 50)
    person.attributes = {"ppe": {"hardhat": True, "vest": True}}
    out = e.check_detection("cam-1", person)
    assert out == []


def test_ppe_required_multiple_missing_emits_one_violation_per_item():
    e = ZoneEnforcer()
    e.load_zones(
        "cam-1",
        [_zone("z1", "ppe_required", ppe_requirements=["hardhat", "vest", "gloves"])],
    )
    person = _person_at(50, 50)
    person.attributes = {"ppe": {"hardhat": False, "vest": False, "gloves": True}}
    out = e.check_detection("cam-1", person)
    violation_types = {v["violation"] for v in out}
    assert violation_types == {"missing_hardhat", "missing_vest"}


def test_ppe_required_no_ppe_data_treats_all_as_missing():
    """If the detection has no ``ppe`` attributes, the enforcer assumes
    each required item is missing — defensive default."""
    e = ZoneEnforcer()
    e.load_zones(
        "cam-1",
        [_zone("z1", "ppe_required", ppe_requirements=["hardhat"])],
    )
    person = _person_at(50, 50)
    person.attributes = {}  # no ppe key
    out = e.check_detection("cam-1", person)
    assert len(out) == 1
    assert out[0]["violation"] == "missing_hardhat"


# ---- equipment_only / pedestrian_only -----------------------------------


def test_person_in_equipment_only_zone_is_breach():
    """Equipment-only zones (loading bays, equipment yards) → people
    walking in is a breach."""
    e = ZoneEnforcer()
    e.load_zones("cam-1", [_zone("z1", "equipment_only")])
    out = e.check_detection("cam-1", _person_at(50, 50))
    assert len(out) == 1
    assert out[0]["zone_type"] == "equipment_only"


def test_equipment_in_equipment_only_zone_clean():
    e = ZoneEnforcer()
    e.load_zones("cam-1", [_zone("z1", "equipment_only")])
    out = e.check_detection("cam-1", _equipment_at(50, 50))
    assert out == []


def test_equipment_in_pedestrian_only_zone_is_breach():
    """Pedestrian-only zones (sidewalks, walkways) → equipment driving
    in is a breach."""
    e = ZoneEnforcer()
    e.load_zones("cam-1", [_zone("z1", "pedestrian_only")])
    out = e.check_detection("cam-1", _equipment_at(50, 50, "loader"))
    assert len(out) == 1
    assert out[0]["zone_type"] == "pedestrian_only"


def test_person_in_pedestrian_only_zone_is_clean():
    e = ZoneEnforcer()
    e.load_zones("cam-1", [_zone("z1", "pedestrian_only")])
    out = e.check_detection("cam-1", _person_at(50, 50))
    assert out == []


# ---- severity override propagation -------------------------------------


def test_severity_override_propagates_to_violation():
    """If a zone declares an explicit severity_override (e.g. for a
    high-risk crane swing), the violation must carry it forward so
    the severity classifier picks it up."""
    e = ZoneEnforcer()
    e.load_zones(
        "cam-1",
        [_zone("z1", "restricted", severity_override="P1_critical")],
    )
    out = e.check_detection("cam-1", _person_at(50, 50))
    assert out[0]["severity_override"] == "P1_critical"


# ---- multiple zones / camera isolation ---------------------------------


def test_multiple_overlapping_zones_each_emit_violation():
    e = ZoneEnforcer()
    e.load_zones(
        "cam-1",
        [
            _zone("z1", "restricted"),
            _zone("z2", "ppe_required", ppe_requirements=["hardhat"]),
        ],
    )
    person = _person_at(50, 50)
    person.attributes = {"ppe": {"hardhat": False}}
    out = e.check_detection("cam-1", person)
    # Both zones contain the person → 2 distinct violations.
    assert len(out) == 2


def test_zone_isolation_per_camera():
    """A zone loaded on cam-1 must not affect detections on cam-2."""
    e = ZoneEnforcer()
    e.load_zones("cam-1", [_zone("z1", "restricted")])
    out = e.check_detection("cam-2", _person_at(50, 50))
    # cam-2 has no zones loaded → no violations.
    assert out == []
