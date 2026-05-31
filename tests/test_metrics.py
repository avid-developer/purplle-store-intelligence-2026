# PROMPT: Create tests for retail conversion metrics covering zero-purchase stores, all-staff events, POS correlation, and re-entry deduplication.
# CHANGES MADE: Added hand-built event streams with deterministic timestamps so the assertions explain the business rules instead of mirroring implementation details.

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.logic import compute_funnel, compute_metrics
from pipeline.emit import make_event


class MetricsTests(unittest.TestCase):
    def test_all_staff_events_are_excluded(self) -> None:
        event = make_event(
            store_id="ST1008",
            camera_id="CAM_STAFF_01",
            visitor_id="VIS_staff",
            event_type="ZONE_ENTER",
            timestamp=datetime(2026, 4, 10, 8, 0, tzinfo=timezone.utc),
            zone_id="STOCK_ROOM",
            is_staff=True,
        )
        metrics = compute_metrics("ST1008", [event], [])
        self.assertEqual(metrics["unique_visitors"], 0)
        self.assertEqual(metrics["conversion_rate"], 0.0)

    def test_zero_purchase_store_has_zero_conversion(self) -> None:
        entry = make_event(
            store_id="ST1008",
            camera_id="CAM_ENTRY_01",
            visitor_id="VIS_1",
            event_type="ENTRY",
            timestamp=datetime(2026, 4, 10, 8, 0, tzinfo=timezone.utc),
        )
        metrics = compute_metrics("ST1008", [entry], [])
        self.assertEqual(metrics["unique_visitors"], 1)
        self.assertEqual(metrics["conversion_rate"], 0.0)

    def test_pos_window_marks_billing_session_converted(self) -> None:
        ts = datetime(2026, 4, 10, 8, 0, tzinfo=timezone.utc)
        events = [
            make_event(store_id="ST1008", camera_id="CAM_ENTRY_01", visitor_id="VIS_1", event_type="ENTRY", timestamp=ts),
            make_event(store_id="ST1008", camera_id="CAM_BILLING_01", visitor_id="VIS_1", event_type="BILLING_QUEUE_JOIN", timestamp=ts + timedelta(minutes=2), zone_id="CASH_COUNTER"),
        ]
        pos = [{"transaction_id": "T1", "store_id": "ST1008", "timestamp": (ts + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"), "basket_value_inr": 100}]
        metrics = compute_metrics("ST1008", events, pos)
        self.assertEqual(metrics["converted_visitors"], 1)
        self.assertEqual(metrics["conversion_rate"], 1.0)

    def test_reentry_does_not_double_count_funnel_entry(self) -> None:
        ts = datetime(2026, 4, 10, 8, 0, tzinfo=timezone.utc)
        events = [
            make_event(store_id="ST1008", camera_id="CAM_ENTRY_01", visitor_id="VIS_1", event_type="ENTRY", timestamp=ts),
            make_event(store_id="ST1008", camera_id="CAM_ENTRY_01", visitor_id="VIS_1", event_type="EXIT", timestamp=ts + timedelta(minutes=2)),
            make_event(store_id="ST1008", camera_id="CAM_ENTRY_01", visitor_id="VIS_1", event_type="REENTRY", timestamp=ts + timedelta(minutes=4)),
        ]
        funnel = compute_funnel("ST1008", events, [])
        self.assertEqual(funnel["stages"]["entry"], 1)
        self.assertEqual(funnel["reentry_sessions"], 1)


if __name__ == "__main__":
    unittest.main()

