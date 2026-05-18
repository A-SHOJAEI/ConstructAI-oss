"""
Create safety data showing a realistic construction site:

- 4 camera configurations
- 3 safety zones (crane exclusion, loading zone, fall hazard perimeter)
- 15 safety alerts over the past 30 days
- 2 marked as false positives (demonstrating feedback loop)
"""
import json
import random
from datetime import datetime, timedelta, timezone

from app.database import async_session
from app.models import Camera, SafetyZone, SafetyAlert

random.seed(42)


async def seed_safety(ctx: dict) -> dict:
    project_id = ctx["project_id"]
    now = datetime.now(timezone.utc)

    async with async_session() as db:
        # --- Cameras ---
        cam_data = [
            ("North Gate Camera", "rtsp://demo:demo@192.168.1.101/stream1", "north_gate"),
            ("Crane Zone Camera", "rtsp://demo:demo@192.168.1.102/stream1", "crane_zone"),
            ("Loading Dock Camera", "rtsp://demo:demo@192.168.1.103/stream1", "loading_dock"),
            ("Rooftop Camera", "rtsp://demo:demo@192.168.1.104/stream1", "rooftop"),
        ]
        cameras = []
        for name, url, loc in cam_data:
            cam = Camera(
                project_id=project_id,
                name=name,
                stream_url=url,
                location_description=loc,
                is_active=True,
                fps_setting=15,
                resolution="1080p",
                config={"zone": loc},
            )
            db.add(cam)
            cameras.append(cam)
        await db.flush()

        cam_map = {c.location_description: c for c in cameras}

        # --- Safety Zones ---
        zone_data = [
            ("Crane Exclusion Zone", "crane_swing", cam_map["crane_zone"].id,
             [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
             ["hard_hat", "safety_vest", "safety_glasses"]),
            ("Loading Dock Zone", "restricted", cam_map["loading_dock"].id,
             [[0.05, 0.2], [0.95, 0.2], [0.95, 0.8], [0.05, 0.8]],
             ["hard_hat", "safety_vest"]),
            ("Fall Hazard Perimeter", "restricted", cam_map["rooftop"].id,
             [[0.0, 0.0], [1.0, 0.0], [1.0, 0.3], [0.0, 0.3]],
             ["hard_hat", "safety_vest", "harness"]),
        ]
        zones = []
        for name, ztype, camera_id, polygon, ppe in zone_data:
            zone = SafetyZone(
                camera_id=camera_id,
                project_id=project_id,
                name=name,
                zone_type=ztype,
                polygon_points=polygon,
                ppe_requirements=ppe,
                is_active=True,
            )
            db.add(zone)
            zones.append(zone)
        await db.flush()

        zone_map = {z.name: z for z in zones}

        # --- Safety Alerts (15 over 30 days) ---
        alert_defs = [
            ("P1_critical", "zone_breach", "crane_zone", "Crane Exclusion Zone",
             "Worker entered crane exclusion zone during active lift operation"),
            ("P1_critical", "fall_detected", "rooftop", "Fall Hazard Perimeter",
             "Potential fall detected at rooftop perimeter - worker near unguarded edge"),
            ("P2_high", "ppe_violation", "north_gate", None,
             "Worker entered site without hard hat through north gate"),
            ("P2_high", "ppe_violation", "loading_dock", "Loading Dock Zone",
             "Worker at loading dock without hard hat during delivery"),
            ("P2_high", "ppe_violation", "north_gate", None,
             "Multiple workers (3) without hard hats at north gate"),
            ("P2_high", "ppe_violation", "crane_zone", "Crane Exclusion Zone",
             "Worker near crane zone without hard hat"),
            ("P3_medium", "ppe_violation", "north_gate", None,
             "Worker missing safety vest at north gate"),
            ("P3_medium", "ppe_violation", "loading_dock", "Loading Dock Zone",
             "Worker missing safety vest during material unloading"),
            ("P3_medium", "ppe_violation", "rooftop", "Fall Hazard Perimeter",
             "Worker on rooftop without visible safety vest"),
            ("P3_medium", "ppe_violation", "north_gate", None,
             "Worker missing safety vest - repeat observation"),
            ("P3_medium", "ppe_violation", "crane_zone", "Crane Exclusion Zone",
             "Worker near crane zone without safety vest"),
            ("P4_low", "other", "loading_dock", None,
             "Housekeeping - debris accumulation near loading dock entrance"),
            ("P4_low", "other", "north_gate", None,
             "Temporary fencing loose at north gate - maintenance needed"),
            ("P4_low", "other", "rooftop", None,
             "Safety netting appears to have minor gap at SE corner"),
            ("P4_low", "other", "loading_dock", None,
             "Delivery vehicle parked outside designated zone"),
        ]

        for i, (priority, alert_type, cam_loc, zone_name, desc) in enumerate(alert_defs):
            days_ago = random.randint(1, 30)
            created = now - timedelta(days=days_ago, hours=random.randint(0, 8))
            is_fp = i in [6, 13]  # Mark 2 as false positives
            cam = cam_map[cam_loc]
            zone = zone_map.get(zone_name) if zone_name else None

            alert = SafetyAlert(
                project_id=project_id,
                camera_id=cam.id,
                zone_id=zone.id if zone else None,
                priority=priority,
                alert_type=alert_type,
                description=desc,
                detections=[{"class": alert_type, "confidence": round(random.uniform(0.78, 0.98), 3)}],
                confidence=round(random.uniform(0.78, 0.98), 3),
                is_false_positive=is_fp,
                is_acknowledged=is_fp or (i < 5),
                created_at=created,
                metadata={"seeded": True, "alert_index": i},
            )
            db.add(alert)

        await db.commit()

    return {
        "camera_ids": [str(c.id) for c in cameras],
        "zone_ids": [str(z.id) for z in zones],
    }
