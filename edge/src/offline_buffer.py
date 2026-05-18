"""SQLite WAL offline buffer for edge devices with intermittent connectivity."""
from __future__ import annotations

import json
import logging
import sqlite3
import time

logger = logging.getLogger(__name__)


class OfflineBuffer:
    """SQLite-backed buffer for storing events when MQTT is unavailable.

    Uses WAL (Write-Ahead Logging) mode for concurrent read/write
    and crash-safe operation on edge devices.
    """

    def __init__(
        self,
        device_id: str,
        db_path: str = "/tmp/constructai_buffer.db",
        max_size: int = 100_000,
    ):
        self.device_id = device_id
        self.db_path = db_path
        self.max_size = max_size
        self._conn = sqlite3.connect(db_path)

        # Enable WAL mode for better concurrency
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS event_buffer (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                topic TEXT NOT NULL,
                payload TEXT NOT NULL,
                retry_count INTEGER DEFAULT 0,
                created_at REAL DEFAULT (strftime('%s', 'now'))
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_buffer_timestamp ON event_buffer(timestamp)"
        )
        self._conn.commit()

    def store(self, payload: str, topic: str = "detections"):
        """Store an event in the offline buffer."""
        self._conn.execute(
            "INSERT INTO event_buffer (timestamp, topic, payload) VALUES (?, ?, ?)",
            (time.time(), topic, payload),
        )
        self._conn.commit()

        # Evict old entries if buffer is full.
        # M-57: upgrade from INFO to WARN. Eviction means we're discarding
        # detection events (possibly safety-critical) because the device
        # hasn't been able to flush to MQTT — ops needs this at warning
        # level to trigger alerts, not INFO which gets filtered.
        count = self._conn.execute("SELECT COUNT(*) FROM event_buffer").fetchone()[0]
        if count > self.max_size:
            evict_count = count - self.max_size
            self._conn.execute(
                "DELETE FROM event_buffer WHERE id IN "
                "(SELECT id FROM event_buffer ORDER BY timestamp ASC LIMIT ?)",
                (evict_count,),
            )
            self._conn.commit()
            logger.warning(
                "offline_buffer: evicted %d events (buffer full at %d); "
                "upstream flush is not keeping up",
                evict_count,
                self.max_size,
            )

    def drain(self, batch_size: int = 100) -> list[dict]:
        """Retrieve and remove a batch of events from the buffer.

        Returns a list of dicts with 'id', 'topic', and 'payload' keys.
        """
        cursor = self._conn.execute(
            "SELECT id, topic, payload FROM event_buffer ORDER BY timestamp ASC LIMIT ?",
            (batch_size,),
        )
        rows = cursor.fetchall()

        events = []
        ids = []
        for row_id, topic, payload in rows:
            events.append({"id": row_id, "topic": topic, "payload": payload})
            ids.append(row_id)

        if ids:
            placeholders = ",".join("?" * len(ids))
            self._conn.execute(
                f"DELETE FROM event_buffer WHERE id IN ({placeholders})",  # noqa: S608
                ids,
            )
            self._conn.commit()

        return events

    def flush_to_mqtt(self, mqtt_client, batch_size: int = 100) -> int:
        """Attempt to flush buffered events to MQTT.

        Returns the number of events successfully published.
        """
        events = self.drain(batch_size)
        published = 0

        for event in events:
            try:
                topic = f"constructai/{self.device_id}/{event['topic']}"
                mqtt_client.publish(topic, event["payload"], qos=1)
                published += 1
            except Exception:
                # Re-buffer failed events
                self.store(event["payload"], event["topic"])
                break

        if published:
            logger.info("Flushed %d/%d buffered events to MQTT", published, len(events))

        return published

    @property
    def size(self) -> int:
        """Current number of buffered events."""
        return self._conn.execute("SELECT COUNT(*) FROM event_buffer").fetchone()[0]

    def close(self):
        """Close the database connection."""
        self._conn.close()
