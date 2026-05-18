"""Tests for the SQLite WAL offline buffer."""
from __future__ import annotations

import json
import os
import tempfile

from edge.src.offline_buffer import OfflineBuffer


class TestOfflineBuffer:
    def test_store_and_drain(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            buf = OfflineBuffer("test-device", db_path=db_path)
            buf.store(json.dumps({"event": "test1"}))
            buf.store(json.dumps({"event": "test2"}))
            assert buf.size == 2

            events = buf.drain(batch_size=10)
            assert len(events) == 2
            assert buf.size == 0
            buf.close()
        finally:
            os.unlink(db_path)

    def test_drain_empty(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            buf = OfflineBuffer("test-device", db_path=db_path)
            events = buf.drain()
            assert len(events) == 0
            buf.close()
        finally:
            os.unlink(db_path)

    def test_eviction(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            buf = OfflineBuffer("test-device", db_path=db_path, max_size=5)
            for i in range(10):
                buf.store(json.dumps({"event": f"test{i}"}))
            assert buf.size <= 5
            buf.close()
        finally:
            os.unlink(db_path)

    def test_fifo_order(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            buf = OfflineBuffer("test-device", db_path=db_path)
            for i in range(5):
                buf.store(json.dumps({"idx": i}))
            events = buf.drain()
            payloads = [json.loads(e["payload"]) for e in events]
            assert payloads[0]["idx"] == 0
            assert payloads[-1]["idx"] == 4
            buf.close()
        finally:
            os.unlink(db_path)
