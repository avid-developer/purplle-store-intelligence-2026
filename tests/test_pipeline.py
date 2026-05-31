# PROMPT: Create pipeline tests that verify generated events are schema-valid and respond to POS input rather than being static canned output.
# CHANGES MADE: Avoided requiring actual video files by using synthetic ClipInfo records and temporary POS-like transactions.

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.models import validate_event
from pipeline.detect import generate_events
from pipeline.tracker import ClipInfo


class PipelineTests(unittest.TestCase):
    def test_generate_events_from_transaction_and_clip_manifest(self) -> None:
        clip = ClipInfo(
            path=Path("CAM 3.mp4"),
            camera_id="CAM_ENTRY_01",
            role="entry",
            zones=["ENTRY"],
            duration_seconds=120,
            fps=25,
            width=1920,
            height=1080,
            created_at=datetime(2026, 4, 10, 8, 0, tzinfo=timezone.utc),
        )
        transaction = {
            "store_id": "ST1008",
            "transaction_id": "TXN_TEST",
            "timestamp": "2026-04-10T08:30:00Z",
            "basket_value_inr": "1000",
            "department": "makeup",
            "sku_zone": "Lipstick",
            "item_count": "2",
        }
        events = generate_events([clip], [transaction], "ST1008")
        self.assertGreaterEqual(len(events), 5)
        self.assertIn("BILLING_QUEUE_JOIN", {event["event_type"] for event in events})
        for index, event in enumerate(events):
            normalized, error = validate_event(event, index=index)
            self.assertIsNone(error)
            self.assertIsNotNone(normalized)


if __name__ == "__main__":
    unittest.main()

