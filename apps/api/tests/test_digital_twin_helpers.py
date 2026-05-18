"""Tests for the pure helpers in services/intelligence/digital_twin_service.

Pin the IFC metadata extractors, point-cloud header parsers (LAS, PLY,
E57, unknown), the sensor-anomaly threshold logic, and the dataclasses.
All pure compute — no DB.
"""

from __future__ import annotations

import struct
from datetime import UTC, datetime

import pytest

from app.services.intelligence.digital_twin_service import (
    POINT_CLOUD_FORMATS,
    SENSOR_THRESHOLDS,
    SENSOR_UNITS,
    VALID_SENSOR_TYPES,
    VALID_SOURCE_TYPES,
    SensorAnomaly,
    SensorReading,
    TwinState,
    _extract_element_bounds,
    _extract_ifc_bounds,
    _extract_ifc_element_count,
    _extract_ifc_metadata,
    _parse_las_header,
    _parse_ply_header,
    _parse_point_cloud_header,
    detect_sensor_anomalies,
)

# =========================================================================
# Threshold / units invariants
# =========================================================================


def test_sensor_thresholds_cover_seven_canonical_types():
    expected = {
        "temperature",
        "humidity",
        "concrete_cure",
        "vibration",
        "strain",
        "dust",
        "noise",
    }
    assert set(SENSOR_THRESHOLDS.keys()) == expected
    assert expected == VALID_SENSOR_TYPES


def test_sensor_units_one_per_threshold():
    """Every sensor type with a threshold must have a corresponding unit
    string — otherwise the anomaly message would be ambiguous."""
    for sensor_type in SENSOR_THRESHOLDS:
        assert sensor_type in SENSOR_UNITS


def test_valid_source_types_canonical():
    assert {"ifc", "revit", "point_cloud", "photogrammetry"} == VALID_SOURCE_TYPES


def test_point_cloud_formats_canonical():
    assert {".las", ".laz", ".ply", ".e57"} == POINT_CLOUD_FORMATS


def test_concrete_cure_uses_low_thresholds():
    """Concrete cure is the only canonical type with `*_low` thresholds —
    the rest are upper-bound. Pin it so a refactor doesn't drop the
    cold-weather guard."""
    cur = SENSOR_THRESHOLDS["concrete_cure"]
    assert "warn_low" in cur
    assert "alert_low" in cur
    assert cur["alert_low"] < cur["warn_low"]


# =========================================================================
# Dataclasses
# =========================================================================


def test_sensor_reading_dataclass_required_fields():
    r = SensorReading(
        sensor_id="s-1",
        sensor_type="temperature",
        value=72.5,
        unit="degF",
        timestamp=datetime.now(UTC),
    )
    assert r.element_id is None
    assert r.location_xyz is None


def test_sensor_anomaly_dataclass_optional_fields_default_none():
    a = SensorAnomaly(
        sensor_id="s-1",
        sensor_type="dust",
        value=12.0,
        unit="mg/m3",
        level="alert",
        threshold=10.0,
        message="too dusty",
    )
    assert a.element_id is None
    assert a.location_xyz is None


def test_twin_state_default_collections_are_independent():
    """Mutating one TwinState's lists must NOT affect another's — guards
    against the classic ``[]`` default-argument bug."""
    a = TwinState(
        twin_id="a",
        project_id="p",
        name="A",
        source_type="ifc",
        status="ready",
        element_count=0,
        bounds=None,
        coordinate_system=None,
        metadata={},
    )
    b = TwinState(
        twin_id="b",
        project_id="p",
        name="B",
        source_type="ifc",
        status="ready",
        element_count=0,
        bounds=None,
        coordinate_system=None,
        metadata={},
    )
    a.sensors.append({"x": 1})
    a.anomalies.append(
        SensorAnomaly(
            sensor_id="s",
            sensor_type="temperature",
            value=110,
            unit="degF",
            level="alert",
            threshold=100,
            message="hot",
        )
    )
    assert b.sensors == []
    assert b.anomalies == []


# =========================================================================
# IFC metadata extractors
# =========================================================================


class _FakeIFC:
    """Stand-in for IfcParseResult — duck-typed to what the helpers need."""

    def __init__(self, metadata=None, entities=None):
        if metadata is not None:
            self.metadata = metadata
        if entities is not None:
            self.entities = entities


def test_extract_ifc_metadata_includes_entity_type_counts():
    res = _FakeIFC(
        metadata={"schema": "IFC4"},
        entities=[
            {"type": "IFCWALL"},
            {"type": "IFCWALL"},
            {"type": "IFCSLAB"},
            {"type": "unknown"},  # missing-type fallback path
            {},  # no type at all → "unknown"
        ],
    )
    out = _extract_ifc_metadata(res)
    assert out["schema"] == "IFC4"
    assert out["entity_types"]["IFCWALL"] == 2
    assert out["entity_types"]["IFCSLAB"] == 1
    # Both entries with no type / "unknown" type → "unknown"
    assert out["entity_types"]["unknown"] == 2


def test_extract_ifc_metadata_handles_missing_attributes():
    """IFC parser may return a result without entities or metadata — the
    helper must not crash."""
    res = object()
    assert _extract_ifc_metadata(res) == {}


def test_extract_ifc_element_count():
    res = _FakeIFC(entities=[{}, {}, {}])
    assert _extract_ifc_element_count(res) == 3


def test_extract_ifc_element_count_no_entities_returns_zero():
    assert _extract_ifc_element_count(object()) == 0


def test_extract_ifc_bounds_lightweight_parser_returns_none():
    """Documented behavior: the regex-based parser does not extract
    geometry, so this helper always returns None."""
    res = _FakeIFC(metadata={"schema": "IFC4"}, entities=[{"type": "IFCSITE"}])
    assert _extract_ifc_bounds(res) is None


# =========================================================================
# _extract_element_bounds
# =========================================================================


def test_extract_element_bounds_from_placement_with_dimensions():
    res = _FakeIFC(
        entities=[
            {
                "id": "wall-1",
                "placement": {"x": 10.0, "y": 5.0, "z": 0.0},
                "dimensions": {"length": 4.0, "width": 0.2, "height": 3.0},
            }
        ]
    )
    bounds = _extract_element_bounds(res)
    assert bounds is not None
    b = bounds["wall-1"]
    # length=4, width=0.2, height=3 → halve and apply around (10,5,0):
    assert b["min_x"] == 8.0  # 10 - 4/2
    assert b["max_x"] == 12.0
    assert b["min_y"] == 4.9
    assert b["max_y"] == 5.1
    assert b["min_z"] == -1.5
    assert b["max_z"] == 1.5


def test_extract_element_bounds_from_explicit_bbox():
    """``bounding_box`` overrides placement-derived bounds."""
    res = _FakeIFC(
        entities=[
            {
                "id": "slab-1",
                "bounding_box": {
                    "min": {"x": 0, "y": 0, "z": 0},
                    "max": {"x": 100, "y": 100, "z": 0.2},
                },
            }
        ]
    )
    bounds = _extract_element_bounds(res)
    assert bounds is not None
    assert bounds["slab-1"]["max_x"] == 100.0
    assert bounds["slab-1"]["max_z"] == 0.2


def test_extract_element_bounds_no_geometry_returns_none():
    res = _FakeIFC(entities=[{"id": "x", "type": "IFCWALL"}])  # no placement / bbox
    assert _extract_element_bounds(res) is None


def test_extract_element_bounds_uses_global_id_when_id_missing():
    res = _FakeIFC(
        entities=[
            {
                "global_id": "g-uuid-123",
                "placement": {"x": 1.0, "y": 2.0, "z": 3.0},
            }
        ]
    )
    bounds = _extract_element_bounds(res)
    assert bounds is not None
    assert "g-uuid-123" in bounds


def test_extract_element_bounds_skips_entity_without_id():
    """An entity with neither ``id`` nor ``global_id`` is unindexable —
    the helper should skip rather than choking."""
    res = _FakeIFC(
        entities=[
            {"placement": {"x": 1, "y": 1, "z": 1}},
            {
                "id": "wall-2",
                "placement": {"x": 0, "y": 0, "z": 0},
            },
        ]
    )
    bounds = _extract_element_bounds(res)
    assert bounds is not None
    assert list(bounds) == ["wall-2"]


# =========================================================================
# _parse_las_header
# =========================================================================


def _fake_las_header(
    *,
    signature: bytes = b"LASF",
    version_major: int = 1,
    version_minor: int = 2,
    legacy_point_count: int = 1_000_000,
    bounds: tuple = (10.0, 0.0, 20.0, 5.0, 30.0, -1.0),  # max_x, min_x, max_y, min_y, max_z, min_z
) -> bytes:
    """Hand-build a minimally-valid LAS 1.x header (227+ bytes)."""
    buf = bytearray(255)  # enough room for LAS 1.4 too
    buf[0:4] = signature
    buf[24] = version_major
    buf[25] = version_minor
    # header size at 94 (uint16) — value doesn't matter for our parser
    struct.pack_into("<H", buf, 94, 227)
    # legacy point count at 107 (uint32)
    struct.pack_into("<I", buf, 107, legacy_point_count)
    # scales at 131 (3 doubles)
    struct.pack_into("<3d", buf, 131, 0.001, 0.001, 0.001)
    # offsets at 155 (3 doubles)
    struct.pack_into("<3d", buf, 155, 0.0, 0.0, 0.0)
    # max_x, min_x at 179
    struct.pack_into("<2d", buf, 179, bounds[0], bounds[1])
    # max_y, min_y at 195
    struct.pack_into("<2d", buf, 195, bounds[2], bounds[3])
    # max_z, min_z at 211
    struct.pack_into("<2d", buf, 211, bounds[4], bounds[5])
    return bytes(buf)


def test_parse_las_header_valid_extracts_point_count_and_bounds():
    header = _fake_las_header(legacy_point_count=42_000)
    out = _parse_las_header(header)
    assert out["format"] == "LAS"
    assert out["version"] == "1.2"
    assert out["point_count"] == 42_000
    assert out["bounds"]["max"]["x"] == 10.0
    assert out["bounds"]["min"]["x"] == 0.0


def test_parse_las_header_too_small_returns_error():
    out = _parse_las_header(b"LASF")  # 4 bytes — way too small
    assert "error" in out
    assert "too small" in out["error"].lower()


def test_parse_las_header_invalid_signature():
    header = _fake_las_header(signature=b"BADX")
    out = _parse_las_header(header)
    assert "error" in out
    assert "Invalid LAS signature" in out["error"]


def test_parse_las_header_version_1_4_uses_64bit_count():
    """LAS 1.4 stores a 64-bit point count at offset 247 — values
    greater than 2^32 should be picked up from there, not from the
    32-bit field at 107."""
    header = bytearray(_fake_las_header(version_major=1, version_minor=4, legacy_point_count=0))
    big = 5_000_000_000  # > 2^32, won't fit in legacy uint32
    struct.pack_into("<Q", header, 247, big)
    out = _parse_las_header(bytes(header))
    assert out["version"] == "1.4"
    assert out["point_count"] == big


# =========================================================================
# _parse_ply_header
# =========================================================================


def test_parse_ply_header_extracts_vertex_count():
    header = (
        b"ply\n"
        b"format ascii 1.0\n"
        b"element vertex 12345\n"
        b"property float x\n"
        b"end_header\n"
        b"...binary data..."
    )
    out = _parse_ply_header(header)
    assert out["format"] == "PLY"
    assert out["point_count"] == 12345
    assert "ascii" in out["ply_format"]


def test_parse_ply_header_no_end_header_returns_error():
    out = _parse_ply_header(b"ply\nformat ascii 1.0\nelement vertex 100\n")
    assert "error" in out


def test_parse_ply_header_malformed_count_falls_through():
    """If 'element vertex' is followed by a non-int token, the helper
    should not crash — it just skips the count."""
    header = b"ply\nelement vertex notanumber\nend_header\n"
    out = _parse_ply_header(header)
    assert out["format"] == "PLY"
    assert "point_count" not in out


# =========================================================================
# _parse_point_cloud_header — dispatch
# =========================================================================


def test_dispatch_las_extension():
    header = _fake_las_header()
    out = _parse_point_cloud_header(header, "scan.las")
    assert out["format"] == "LAS"


def test_dispatch_laz_extension_uses_las_parser():
    """LAZ is compressed-LAS — same header layout, same parser."""
    header = _fake_las_header()
    out = _parse_point_cloud_header(header, "scan.laz")
    assert out["format"] == "LAS"


def test_dispatch_ply_extension():
    header = b"ply\nelement vertex 1\nend_header\n"
    out = _parse_point_cloud_header(header, "scan.ply")
    assert out["format"] == "PLY"


def test_dispatch_e57_extension_returns_stub():
    """E57 needs libE57 to parse; the helper returns a documented stub."""
    out = _parse_point_cloud_header(b"\x00\x00", "scan.e57")
    assert out["format"] == "E57"
    assert "note" in out


def test_dispatch_unknown_extension():
    out = _parse_point_cloud_header(b"\x00", "scan.xyz")
    assert out["format"] == "unknown"


def test_dispatch_filename_case_insensitive():
    """Real-world filenames may have uppercase extensions — the dispatcher
    must not silently skip them."""
    header = _fake_las_header()
    out = _parse_point_cloud_header(header, "SCAN.LAS")
    assert out["format"] == "LAS"


# =========================================================================
# detect_sensor_anomalies
# =========================================================================


def _sensor_dict(sensor_type: str, value: float, sensor_id: str = "s-1", **extra) -> dict:
    base = {
        "sensor_id": sensor_id,
        "sensor_type": sensor_type,
        "latest_reading": {"value": value},
    }
    base.update(extra)
    return base


def test_detect_anomaly_high_alert_threshold():
    """Temperature ≥ 100°F is an alert (OSHA dangerous-heat threshold)."""
    sensors = [_sensor_dict("temperature", 105.0)]
    out = detect_sensor_anomalies(sensors)
    assert len(out) == 1
    assert out[0].level == "alert"
    assert out[0].threshold == 100.0
    assert out[0].unit == "degF"


def test_detect_anomaly_high_warn_threshold():
    """Temperature 92°F is in warn range (90 ≤ x < 100)."""
    sensors = [_sensor_dict("temperature", 92.0)]
    out = detect_sensor_anomalies(sensors)
    assert len(out) == 1
    assert out[0].level == "warning"
    assert out[0].threshold == 90.0


def test_detect_anomaly_below_thresholds_emits_nothing():
    """Temperature 75°F is well below the warn threshold of 90."""
    sensors = [_sensor_dict("temperature", 75.0)]
    assert detect_sensor_anomalies(sensors) == []


def test_detect_anomaly_low_alert_concrete_cure():
    """Concrete cure ≤ 40°F is an alert (concrete won't hydrate)."""
    sensors = [_sensor_dict("concrete_cure", 35.0)]
    out = detect_sensor_anomalies(sensors)
    assert len(out) == 1
    assert out[0].level == "alert"
    assert out[0].threshold == 40.0
    # Message must indicate "below" for low-threshold violations.
    assert "below" in out[0].message.lower()


def test_detect_anomaly_low_warn_concrete_cure():
    """Concrete cure 50°F warns (40 < x < 60)."""
    sensors = [_sensor_dict("concrete_cure", 50.0)]
    out = detect_sensor_anomalies(sensors)
    assert len(out) == 1
    assert out[0].level == "warning"


def test_detect_anomaly_unknown_sensor_type_skipped():
    """An unrecognized sensor_type has no thresholds — must skip silently
    (no exception, no false positive)."""
    sensors = [_sensor_dict("unknown_sensor", 999.0)]
    assert detect_sensor_anomalies(sensors) == []


def test_detect_anomaly_no_value_skipped():
    sensors = [{"sensor_id": "s", "sensor_type": "temperature", "latest_reading": {}}]
    assert detect_sensor_anomalies(sensors) == []


def test_detect_anomaly_no_reading_skipped():
    sensors = [{"sensor_id": "s", "sensor_type": "temperature"}]
    assert detect_sensor_anomalies(sensors) == []


def test_detect_anomaly_supports_orm_objects():
    """ORM-style sensor objects (with ``latest_reading`` attr) work the
    same as dicts."""

    class FakeSensor:
        sensor_id = "orm-1"
        sensor_type = "dust"
        latest_reading = {"value": 12.0}  # > alert_high (10)
        element_id = "wall-3"
        location_xyz = {"x": 1, "y": 2, "z": 3}

    out = detect_sensor_anomalies([FakeSensor()])
    assert len(out) == 1
    assert out[0].level == "alert"
    assert out[0].element_id == "wall-3"
    assert out[0].location_xyz == {"x": 1, "y": 2, "z": 3}


def test_detect_anomaly_emits_per_sensor():
    """Two sensors, each over alert → two anomalies."""
    sensors = [
        _sensor_dict("temperature", 105.0, sensor_id="t1"),
        _sensor_dict("noise", 95.0, sensor_id="n1"),
    ]
    out = detect_sensor_anomalies(sensors)
    assert len(out) == 2
    ids = {a.sensor_id for a in out}
    assert ids == {"t1", "n1"}


def test_detect_anomaly_dict_sensor_missing_id_uses_empty_string():
    sensors = [{"sensor_type": "noise", "latest_reading": {"value": 95.0}}]
    out = detect_sensor_anomalies(sensors)
    assert len(out) == 1
    # SensorAnomaly.sensor_id is required → empty string fallback per code.
    assert out[0].sensor_id == ""


def test_detect_anomaly_strain_warning_threshold():
    """Strain at 80% capacity → warn; at 95%+ → alert."""
    sensors = [_sensor_dict("strain", 82.0)]
    out = detect_sensor_anomalies(sensors)
    assert len(out) == 1
    assert out[0].level == "warning"


@pytest.mark.parametrize(
    "sensor_type,alert_value",
    [
        ("temperature", 100.5),
        ("humidity", 86.0),
        ("vibration", 4.5),
        ("strain", 96.0),
        ("dust", 11.0),
        ("noise", 91.0),
    ],
)
def test_detect_anomaly_alert_threshold_each_sensor_type(sensor_type, alert_value):
    """Pin alert behavior for every upper-bound sensor type."""
    out = detect_sensor_anomalies([_sensor_dict(sensor_type, alert_value)])
    assert len(out) == 1
    assert out[0].level == "alert"
    assert out[0].sensor_type == sensor_type
