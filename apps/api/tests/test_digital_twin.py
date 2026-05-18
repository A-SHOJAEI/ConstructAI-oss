"""Tests for digital twin integration service.

Covers twin creation from IFC/point cloud, sensor registration and readings,
anomaly detection with real construction thresholds, snapshots, and API endpoints.
"""

from __future__ import annotations

import struct
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from app.services.intelligence.digital_twin_service import (
    SENSOR_THRESHOLDS,
    VALID_SENSOR_TYPES,
    SensorAnomaly,
    SensorReading,
    TwinState,
    _extract_ifc_element_count,
    _extract_ifc_metadata,
    _parse_las_header,
    _parse_ply_header,
    _parse_point_cloud_header,
    detect_sensor_anomalies,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ifc_bytes() -> bytes:
    """Create a minimal valid IFC file for testing."""
    return (
        b"ISO-10303-21;\n"
        b"HEADER;\n"
        b"FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');\n"
        b"FILE_NAME('test_model.ifc','2024-01-15',('Architect'),('Firm'),"
        b"'ConstructAI','IFC exporter','');\n"
        b"FILE_SCHEMA(('IFC2X3'));\n"
        b"ENDSEC;\n"
        b"DATA;\n"
        b"#1=IFCPROJECT('0YvctVUKr0kugbFTf53O9L',$,'Test Project',$,$,$,$,$,$);\n"
        b"#2=IFCSITE('3De5KPuGnB8em7d0olvzAm',$,'Default Site',$,$,$,$,$,.ELEMENT.,$,$,$,$,$);\n"
        b"#3=IFCBUILDING('2FCZDorx3CBm00I8GkH0h1',$,'Main Building',$,$,$,$,$,.ELEMENT.,$,$,$);\n"
        b"#4=IFCBUILDINGSTOREY('0C87kaqBX0H8bU6aQTO_4U',$,'Level 1',$,$,$,$,$,.ELEMENT.,0.);\n"
        b"#5=IFCBUILDINGSTOREY('1D98lbrCY1I9cV7bRUP_5V',$,'Level 2',$,$,$,$,$,.ELEMENT.,3600.);\n"
        b"ENDSEC;\n"
        b"END-ISO-10303-21;\n"
    )


def _make_las_header(point_count: int = 50000) -> bytes:
    """Create a minimal LAS 1.2 header for testing."""
    # LAS 1.2 header — simplified but valid enough for our parser
    header = bytearray(227)
    # Signature
    header[0:4] = b"LASF"
    # Version
    header[24] = 1  # major
    header[25] = 2  # minor
    # Header size
    struct.pack_into("<H", header, 94, 227)
    # Legacy point count at offset 107
    struct.pack_into("<I", header, 107, point_count)
    # Scale factors (3 doubles at offset 131)
    struct.pack_into("<3d", header, 131, 0.001, 0.001, 0.001)
    # Offsets (3 doubles at offset 155)
    struct.pack_into("<3d", header, 155, 0.0, 0.0, 0.0)
    # Bounds: max_x, min_x, max_y, min_y, max_z, min_z
    struct.pack_into("<2d", header, 179, 500.0, 0.0)  # x: 0 to 500
    struct.pack_into("<2d", header, 195, 300.0, 0.0)  # y: 0 to 300
    struct.pack_into("<2d", header, 211, 50.0, -10.0)  # z: -10 to 50
    return bytes(header)


def _make_ply_header(vertex_count: int = 100000) -> bytes:
    """Create a minimal PLY header for testing."""
    header = (
        f"ply\n"
        f"format binary_little_endian 1.0\n"
        f"element vertex {vertex_count}\n"
        f"property float x\n"
        f"property float y\n"
        f"property float z\n"
        f"end_header\n"
    ).encode("ascii")
    # Add some dummy vertex data
    return header + (b"\x00" * min(vertex_count * 12, 1000))


def _make_sensor(
    sensor_id: str = "TEMP-001",
    sensor_type: str = "temperature",
    value: float | None = None,
    unit: str = "degF",
    element_id: str | None = None,
) -> MagicMock:
    """Create a mock sensor with optional latest_reading."""
    sensor = MagicMock()
    sensor.sensor_id = sensor_id
    sensor.sensor_type = sensor_type
    sensor.element_id = element_id
    sensor.location_xyz = {"x": 10.0, "y": 20.0, "z": 5.0}
    if value is not None:
        sensor.latest_reading = {
            "value": value,
            "unit": unit,
            "timestamp": datetime.now(UTC).isoformat(),
        }
    else:
        sensor.latest_reading = None
    return sensor


# ---------------------------------------------------------------------------
# TestTwinCreation — 5 tests
# ---------------------------------------------------------------------------


class TestTwinCreation:
    """Tests for creating digital twins from IFC and point cloud files."""

    @pytest.mark.asyncio
    async def test_create_twin_from_ifc_parses_entities(self):
        """IFC parsing should extract project, site, building, and storey entities."""
        from app.services.ingestion.ifc_parser import parse_ifc

        ifc_bytes = _make_ifc_bytes()
        result = parse_ifc(ifc_bytes)

        assert len(result.entities) == 5
        entity_types = {e["type"] for e in result.entities}
        assert "IFCPROJECT" in entity_types
        assert "IFCSITE" in entity_types
        assert "IFCBUILDING" in entity_types
        assert "IFCBUILDINGSTOREY" in entity_types

    @pytest.mark.asyncio
    async def test_create_twin_from_ifc_extracts_metadata(self):
        """IFC metadata extraction should capture file name and schema."""
        from app.services.ingestion.ifc_parser import parse_ifc

        ifc_bytes = _make_ifc_bytes()
        result = parse_ifc(ifc_bytes)
        metadata = _extract_ifc_metadata(result)

        assert "entity_types" in metadata
        assert metadata["entity_types"]["IFCPROJECT"] == 1
        assert metadata["entity_types"]["IFCBUILDINGSTOREY"] == 2

    @pytest.mark.asyncio
    async def test_create_twin_from_ifc_element_count(self):
        """Element count should equal number of parsed IFC entities."""
        from app.services.ingestion.ifc_parser import parse_ifc

        ifc_bytes = _make_ifc_bytes()
        result = parse_ifc(ifc_bytes)
        count = _extract_ifc_element_count(result)
        assert count == 5

    def test_parse_las_header(self):
        """LAS header parsing should extract point count and bounds."""
        header_bytes = _make_las_header(point_count=75000)
        meta = _parse_las_header(header_bytes)

        assert meta["format"] == "LAS"
        assert meta["version"] == "1.2"
        assert meta["point_count"] == 75000
        assert "bounds" in meta
        assert meta["bounds"]["min"]["x"] == 0.0
        assert meta["bounds"]["max"]["x"] == 500.0
        assert meta["bounds"]["min"]["z"] == -10.0
        assert meta["bounds"]["max"]["z"] == 50.0

    def test_parse_ply_header(self):
        """PLY header parsing should extract vertex count."""
        ply_bytes = _make_ply_header(vertex_count=250000)
        meta = _parse_ply_header(ply_bytes)

        assert meta["format"] == "PLY"
        assert meta["point_count"] == 250000


# ---------------------------------------------------------------------------
# TestSensorRegistration — 4 tests
# ---------------------------------------------------------------------------


class TestSensorRegistration:
    """Tests for sensor registration and validation."""

    def test_valid_sensor_types(self):
        """All defined threshold types should be valid sensor types."""
        assert set(SENSOR_THRESHOLDS.keys()) == VALID_SENSOR_TYPES
        assert "temperature" in VALID_SENSOR_TYPES
        assert "humidity" in VALID_SENSOR_TYPES
        assert "concrete_cure" in VALID_SENSOR_TYPES
        assert "vibration" in VALID_SENSOR_TYPES
        assert "strain" in VALID_SENSOR_TYPES
        assert "dust" in VALID_SENSOR_TYPES
        assert "noise" in VALID_SENSOR_TYPES

    def test_sensor_threshold_units(self):
        """Each sensor type should have a defined unit (lives in
        SENSOR_UNITS now — SENSOR_THRESHOLDS holds only pure-float
        thresholds)."""
        from app.services.intelligence.digital_twin_service import SENSOR_UNITS

        for sensor_type in SENSOR_THRESHOLDS:
            assert sensor_type in SENSOR_UNITS, f"{sensor_type} missing unit"

    def test_sensor_threshold_values_reasonable(self):
        """Threshold values should be physically reasonable."""
        # Temperature: warn at 90F is reasonable for outdoor construction
        assert SENSOR_THRESHOLDS["temperature"]["warn_high"] == 90.0
        assert SENSOR_THRESHOLDS["temperature"]["alert_high"] == 100.0

        # OSHA noise PEL
        assert SENSOR_THRESHOLDS["noise"]["warn_high"] == 85.0
        assert SENSOR_THRESHOLDS["noise"]["alert_high"] == 90.0

        # ACI 306 concrete cold-weather threshold
        assert SENSOR_THRESHOLDS["concrete_cure"]["warn_low"] == 60.0
        assert SENSOR_THRESHOLDS["concrete_cure"]["alert_low"] == 40.0

    def test_invalid_sensor_type_rejected_by_schema(self):
        """Schema validation should reject invalid sensor types."""
        from app.schemas.digital_twin import SensorRegisterRequest

        with pytest.raises(Exception):
            SensorRegisterRequest(
                sensor_id="X-001",
                sensor_type="invalid_type",
                location_xyz={"x": 0, "y": 0, "z": 0},
            )


# ---------------------------------------------------------------------------
# TestSensorReadings — 6 tests
# ---------------------------------------------------------------------------


class TestSensorReadings:
    """Tests for sensor reading ingestion."""

    def test_sensor_reading_dataclass(self):
        """SensorReading should store value, unit, and timestamp."""
        reading = SensorReading(
            sensor_id="TEMP-001",
            sensor_type="temperature",
            value=82.5,
            unit="degF",
            timestamp=datetime.now(UTC),
        )
        assert reading.value == 82.5
        assert reading.unit == "degF"

    def test_sensor_reading_with_element(self):
        """SensorReading should optionally link to an IFC element."""
        reading = SensorReading(
            sensor_id="STRAIN-001",
            sensor_type="strain",
            value=45.0,
            unit="%",
            timestamp=datetime.now(UTC),
            element_id="#42",
        )
        assert reading.element_id == "#42"

    def test_batch_reading_schema_validation(self):
        """Batch request schema should accept valid readings list."""
        from app.schemas.digital_twin import SensorBatchRequest, SensorReadingRequest

        batch = SensorBatchRequest(
            readings=[
                SensorReadingRequest(sensor_id="T-1", value=72.0, unit="degF"),
                SensorReadingRequest(sensor_id="H-1", value=55.0, unit="%"),
                SensorReadingRequest(sensor_id="V-1", value=1.2, unit="in/s"),
            ]
        )
        assert len(batch.readings) == 3

    def test_batch_reading_empty_rejected(self):
        """Batch request with empty readings should be rejected."""
        from app.schemas.digital_twin import SensorBatchRequest

        with pytest.raises(Exception):
            SensorBatchRequest(readings=[])

    def test_reading_timestamp_optional(self):
        """Timestamp should be optional in reading request."""
        from app.schemas.digital_twin import SensorReadingRequest

        reading = SensorReadingRequest(sensor_id="TEMP-001", value=85.0, unit="degF")
        assert reading.timestamp is None

    def test_reading_with_timestamp(self):
        """Explicit timestamp should be preserved."""
        from app.schemas.digital_twin import SensorReadingRequest

        ts = datetime(2025, 7, 15, 14, 30, 0, tzinfo=UTC)
        reading = SensorReadingRequest(
            sensor_id="TEMP-001",
            value=85.0,
            unit="degF",
            timestamp=ts,
        )
        assert reading.timestamp == ts


# ---------------------------------------------------------------------------
# TestAnomalyDetection — 8 tests
# ---------------------------------------------------------------------------


class TestAnomalyDetection:
    """Tests for sensor anomaly detection against construction thresholds."""

    def test_normal_temperature_no_anomaly(self):
        """Temperature within range should produce no anomaly."""
        sensors = [_make_sensor("T-1", "temperature", 75.0)]
        anomalies = detect_sensor_anomalies(sensors)
        assert len(anomalies) == 0

    def test_high_temperature_warning(self):
        """Temperature above 90F should produce a warning."""
        sensors = [_make_sensor("T-1", "temperature", 92.0)]
        anomalies = detect_sensor_anomalies(sensors)
        assert len(anomalies) == 1
        assert anomalies[0].level == "warning"
        assert anomalies[0].threshold == 90.0

    def test_extreme_temperature_alert(self):
        """Temperature above 100F should produce an alert."""
        sensors = [_make_sensor("T-1", "temperature", 105.0)]
        anomalies = detect_sensor_anomalies(sensors)
        assert len(anomalies) == 1
        assert anomalies[0].level == "alert"
        assert anomalies[0].threshold == 100.0

    def test_cold_concrete_cure_warning(self):
        """Concrete cure temperature below 60F should warn (ACI 306)."""
        sensors = [_make_sensor("CURE-1", "concrete_cure", 55.0, "degF")]
        anomalies = detect_sensor_anomalies(sensors)
        assert len(anomalies) == 1
        assert anomalies[0].level == "warning"
        assert anomalies[0].sensor_type == "concrete_cure"

    def test_freezing_concrete_cure_alert(self):
        """Concrete cure temperature below 40F should alert — hydration stops."""
        sensors = [_make_sensor("CURE-1", "concrete_cure", 35.0, "degF")]
        anomalies = detect_sensor_anomalies(sensors)
        assert len(anomalies) == 1
        assert anomalies[0].level == "alert"
        assert anomalies[0].threshold == 40.0

    def test_vibration_alert(self):
        """Vibration above 4.0 in/s PPV should alert — structural damage risk."""
        sensors = [_make_sensor("VIB-1", "vibration", 4.5, "in/s")]
        anomalies = detect_sensor_anomalies(sensors)
        assert len(anomalies) == 1
        assert anomalies[0].level == "alert"

    def test_dust_warning(self):
        """Dust above 5 mg/m3 triggers OSHA PEL warning."""
        sensors = [_make_sensor("DUST-1", "dust", 7.5, "mg/m3")]
        anomalies = detect_sensor_anomalies(sensors)
        assert len(anomalies) == 1
        assert anomalies[0].level == "warning"
        assert anomalies[0].threshold == 5.0

    def test_multiple_sensors_multiple_anomalies(self):
        """Multiple sensors with issues should each produce anomalies."""
        sensors = [
            _make_sensor("T-1", "temperature", 95.0),
            _make_sensor("T-2", "temperature", 72.0),  # normal
            _make_sensor("V-1", "vibration", 3.0),  # warning
            _make_sensor("N-1", "noise", 92.0),  # alert
            _make_sensor("H-1", "humidity", 50.0),  # normal
        ]
        anomalies = detect_sensor_anomalies(sensors)
        # T-1: warning, V-1: warning, N-1: alert
        assert len(anomalies) == 3
        levels = {a.level for a in anomalies}
        assert "warning" in levels
        assert "alert" in levels


# ---------------------------------------------------------------------------
# TestAnomalyDetectionDicts — 2 tests (dict-based sensor input)
# ---------------------------------------------------------------------------


class TestAnomalyDetectionDicts:
    """Tests for anomaly detection with dict-based sensor data."""

    def test_dict_sensor_anomaly(self):
        """Anomaly detection should work with plain dict sensors."""
        sensors = [
            {
                "sensor_id": "T-1",
                "sensor_type": "temperature",
                "latest_reading": {"value": 102.0, "unit": "degF"},
                "element_id": None,
                "location_xyz": {"x": 1, "y": 2, "z": 3},
            }
        ]
        anomalies = detect_sensor_anomalies(sensors)
        assert len(anomalies) == 1
        assert anomalies[0].level == "alert"

    def test_dict_sensor_no_reading(self):
        """Sensor dict with no latest_reading should be skipped."""
        sensors = [
            {
                "sensor_id": "T-1",
                "sensor_type": "temperature",
                "latest_reading": None,
            }
        ]
        anomalies = detect_sensor_anomalies(sensors)
        assert len(anomalies) == 0


# ---------------------------------------------------------------------------
# TestSnapshots — 4 tests
# ---------------------------------------------------------------------------


class TestSnapshots:
    """Tests for twin snapshot creation and state."""

    def test_twin_state_dataclass(self):
        """TwinState should aggregate model, sensors, and anomalies."""
        state = TwinState(
            twin_id="abc-123",
            project_id="proj-456",
            name="Main Building",
            source_type="ifc",
            status="ready",
            element_count=500,
            bounds=None,
            coordinate_system="IFC2X3",
            metadata={"schema": ["IFC2X3"]},
            sensors=[],
            anomalies=[],
            latest_snapshot=None,
        )
        assert state.twin_id == "abc-123"
        assert state.element_count == 500
        assert state.anomalies == []

    def test_twin_state_with_anomalies(self):
        """TwinState should include detected anomalies."""
        anomaly = SensorAnomaly(
            sensor_id="T-1",
            sensor_type="temperature",
            value=105.0,
            unit="degF",
            level="alert",
            threshold=100.0,
            message="temperature reading 105.0degF exceeds alert threshold 100.0degF",
        )
        state = TwinState(
            twin_id="abc-123",
            project_id="proj-456",
            name="Test Twin",
            source_type="ifc",
            status="ready",
            element_count=10,
            bounds=None,
            coordinate_system=None,
            metadata={},
            sensors=[],
            anomalies=[anomaly],
        )
        assert len(state.anomalies) == 1
        assert state.anomalies[0].level == "alert"

    def test_snapshot_schema(self):
        """Snapshot create request should accept schedule overlay and photos."""
        from app.schemas.digital_twin import SnapshotCreateRequest

        req = SnapshotCreateRequest(
            schedule_overlay={"act-1": 45.0, "act-2": 80.0},
            photo_urls=["https://example.com/photo1.jpg"],
            notes="End of day snapshot",
        )
        assert req.schedule_overlay["act-1"] == 45.0
        assert len(req.photo_urls) == 1

    def test_snapshot_minimal_request(self):
        """Snapshot should work with no optional fields."""
        from app.schemas.digital_twin import SnapshotCreateRequest

        req = SnapshotCreateRequest()
        assert req.schedule_overlay is None
        assert req.photo_urls is None
        assert req.notes is None


# ---------------------------------------------------------------------------
# TestTwinState — 4 tests
# ---------------------------------------------------------------------------


class TestTwinState:
    """Tests for twin state retrieval and response formatting."""

    def test_twin_response_schema(self):
        """DigitalTwinResponse should serialize from ORM attributes."""
        from app.schemas.digital_twin import DigitalTwinResponse

        now = datetime.now(UTC)
        resp = DigitalTwinResponse(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            name="Test Model",
            source_type="ifc",
            s3_key="twins/proj/twin/model.ifc",
            file_size_bytes=1024000,
            element_count=150,
            coordinate_system="IFC2X3",
            bounds=None,
            metadata_={"entity_types": {"IFCPROJECT": 1}},
            status="ready",
            created_at=now,
            updated_at=now,
        )
        assert resp.name == "Test Model"
        assert resp.file_size_bytes == 1024000

    def test_twin_list_response_schema(self):
        """DigitalTwinListResponse should wrap list with count."""
        from app.schemas.digital_twin import DigitalTwinListResponse

        resp = DigitalTwinListResponse(data=[], count=0)
        assert resp.count == 0

    def test_sensor_response_schema(self):
        """SensorResponse should format from ORM attributes."""
        from app.schemas.digital_twin import SensorResponse

        now = datetime.now(UTC)
        resp = SensorResponse(
            id=uuid.uuid4(),
            sensor_id="TEMP-001",
            sensor_type="temperature",
            location_xyz={"x": 10.0, "y": 20.0, "z": 5.0},
            element_id="#42",
            latest_reading={"value": 78.0, "unit": "degF"},
            last_updated=now,
            created_at=now,
        )
        assert resp.sensor_type == "temperature"
        assert resp.element_id == "#42"

    def test_state_response_schema(self):
        """TwinStateResponse should contain all sections."""
        from app.schemas.digital_twin import SensorAnomalyResponse, TwinStateResponse

        resp = TwinStateResponse(
            twin_id="abc",
            project_id="def",
            name="Model",
            source_type="ifc",
            status="ready",
            element_count=100,
            sensors=[{"sensor_id": "T-1", "sensor_type": "temperature"}],
            anomalies=[
                SensorAnomalyResponse(
                    sensor_id="T-1",
                    sensor_type="temperature",
                    value=95.0,
                    unit="degF",
                    level="warning",
                    threshold=90.0,
                    message="warning",
                )
            ],
        )
        assert len(resp.sensors) == 1
        assert len(resp.anomalies) == 1


# ---------------------------------------------------------------------------
# TestPointCloudParsing — 4 tests
# ---------------------------------------------------------------------------


class TestPointCloudParsing:
    """Tests for point cloud header parsing."""

    def test_invalid_las_signature(self):
        """Non-LAS bytes should report error."""
        meta = _parse_las_header(b"NOT_LAS" + b"\x00" * 250)
        assert "error" in meta

    def test_las_too_small(self):
        """File smaller than LAS header should report error."""
        meta = _parse_las_header(b"LASF" + b"\x00" * 50)
        assert "error" in meta

    def test_ply_no_header(self):
        """PLY without end_header should report error."""
        meta = _parse_ply_header(b"ply\nformat ascii 1.0\n")
        assert "error" in meta

    def test_point_cloud_dispatch(self):
        """Dispatcher should route to correct parser by extension."""
        las_bytes = _make_las_header(1000)
        meta = _parse_point_cloud_header(las_bytes, "scan.las")
        assert meta["format"] == "LAS"

        meta_e57 = _parse_point_cloud_header(b"", "scan.e57")
        assert meta_e57["format"] == "E57"

        meta_unknown = _parse_point_cloud_header(b"", "scan.xyz")
        assert meta_unknown["format"] == "unknown"


# ---------------------------------------------------------------------------
# TestEndpoints — 6 tests (schema/request validation for routes)
# ---------------------------------------------------------------------------


class TestEndpoints:
    """Tests for API endpoint request/response validation."""

    def test_sensor_register_request_valid(self):
        """Valid sensor registration request should parse."""
        from app.schemas.digital_twin import SensorRegisterRequest

        req = SensorRegisterRequest(
            sensor_id="TEMP-FLOOR-1",
            sensor_type="temperature",
            location_xyz={"x": 15.5, "y": 22.3, "z": 3.0},
            element_id="#100",
        )
        assert req.sensor_type == "temperature"

    def test_sensor_register_missing_xyz_key(self):
        """Location missing a coordinate should be rejected."""
        from app.schemas.digital_twin import SensorRegisterRequest

        with pytest.raises(Exception):
            SensorRegisterRequest(
                sensor_id="T-1",
                sensor_type="temperature",
                location_xyz={"x": 0, "y": 0},  # missing z
            )

    def test_reading_request_valid(self):
        """Valid single reading request should parse."""
        from app.schemas.digital_twin import SensorReadingRequest

        req = SensorReadingRequest(sensor_id="TEMP-001", value=78.5, unit="degF")
        assert req.value == 78.5

    def test_snapshot_create_with_overlay(self):
        """Snapshot with schedule overlay should parse correctly."""
        from app.schemas.digital_twin import SnapshotCreateRequest

        req = SnapshotCreateRequest(
            schedule_overlay={
                "activity-1": 25.0,
                "activity-2": 60.0,
                "activity-3": 100.0,
            },
            photo_urls=[
                "https://s3.example.com/photo1.jpg",
                "https://s3.example.com/photo2.jpg",
            ],
            notes="Weekly progress snapshot",
        )
        assert len(req.schedule_overlay) == 3
        assert req.schedule_overlay["activity-3"] == 100.0

    def test_twin_response_with_bounds(self):
        """Twin response should serialize bounds JSONB correctly."""
        from app.schemas.digital_twin import DigitalTwinResponse

        now = datetime.now(UTC)
        bounds = {
            "min": {"x": 0.0, "y": 0.0, "z": -5.0},
            "max": {"x": 500.0, "y": 300.0, "z": 50.0},
        }
        resp = DigitalTwinResponse(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            name="Site Scan",
            source_type="point_cloud",
            s3_key="twins/proj/twin/scan.las",
            file_size_bytes=500_000_000,
            element_count=1_000_000,
            bounds=bounds,
            metadata_={},
            status="ready",
            created_at=now,
            updated_at=now,
        )
        assert resp.bounds["min"]["z"] == -5.0
        assert resp.bounds["max"]["x"] == 500.0

    def test_anomaly_response_schema(self):
        """SensorAnomalyResponse should format correctly."""
        from app.schemas.digital_twin import SensorAnomalyResponse

        resp = SensorAnomalyResponse(
            sensor_id="DUST-1",
            sensor_type="dust",
            value=12.0,
            unit="mg/m3",
            level="alert",
            threshold=10.0,
            message="dust reading 12.0mg/m3 exceeds alert threshold 10.0mg/m3",
            element_id=None,
        )
        assert resp.level == "alert"
        assert "12.0" in resp.message
