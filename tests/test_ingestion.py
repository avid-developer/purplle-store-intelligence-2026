# PROMPT: Generate tests for a store-intelligence event ingestion layer that validates schema, rejects malformed events, and deduplicates idempotent event IDs.
# CHANGES MADE: Kept the tests framework-free with unittest, added a real temporary SQLite database, and asserted partial success behavior rather than only happy-path inserts.

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app import storage
from app.models import validate_event
from pipeline.emit import make_event


class IngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        storage.DB_PATH = Path(self.tmp.name) / "store.db"
        storage.init_db()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_valid_event_is_accepted_and_duplicate_is_idempotent(self) -> None:
        event = make_event(
            store_id="ST1008",
            camera_id="CAM_ENTRY_01",
            visitor_id="VIS_test",
            event_type="ENTRY",
            timestamp=datetime(2026, 4, 10, 8, 0, tzinfo=timezone.utc),
            confidence=0.8,
        )
        normalized, error = validate_event(event)
        self.assertIsNone(error)
        accepted, duplicates = storage.insert_events([normalized])
        self.assertEqual(accepted, 1)
        self.assertEqual(duplicates, [])
        accepted, duplicates = storage.insert_events([normalized])
        self.assertEqual(accepted, 0)
        self.assertEqual(duplicates, [event["event_id"]])

    def test_malformed_event_returns_validation_error(self) -> None:
        raw = {"event_id": "bad", "event_type": "NOPE"}
        normalized, error = validate_event(raw, index=2)
        self.assertIsNone(normalized)
        self.assertIsNotNone(error)
        self.assertEqual(error.index, 2)
        self.assertIn(error.code, {"missing_field", "invalid_event_type"})

    def test_zone_event_requires_zone_id(self) -> None:
        event = make_event(
            store_id="ST1008",
            camera_id="CAM_MAIN_01",
            visitor_id="VIS_test",
            event_type="ZONE_ENTER",
            timestamp=datetime(2026, 4, 10, 8, 0, tzinfo=timezone.utc),
            zone_id="GOOD_VIBES",
        )
        event["zone_id"] = None
        normalized, error = validate_event(event)
        self.assertIsNone(normalized)
        self.assertEqual(error.code, "missing_zone")


if __name__ == "__main__":
    unittest.main()

