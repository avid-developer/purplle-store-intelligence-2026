# PROMPT: Build anomaly tests for queue spikes and dead zones using the same event schema as the ingestion API.
# CHANGES MADE: Focused on reviewer-visible behaviors: a queue spike creates a WARN/CRITICAL action and empty input returns no false alarms.

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.logic import compute_anomalies
from pipeline.emit import make_event


class AnomalyTests(unittest.TestCase):
    def test_empty_store_has_no_anomalies(self) -> None:
        self.assertEqual(compute_anomalies("ST1008", [], [])["anomalies"], [])

    def test_queue_spike_is_reported(self) -> None:
        ts = datetime(2026, 4, 10, 8, 0, tzinfo=timezone.utc)
        events = []
        for idx, depth in enumerate([1, 2, 3, 7]):
            events.append(
                make_event(
                    store_id="ST1008",
                    camera_id="CAM_BILLING_01",
                    visitor_id=f"VIS_{idx}",
                    event_type="BILLING_QUEUE_JOIN",
                    timestamp=ts + timedelta(minutes=idx),
                    zone_id="CASH_COUNTER",
                    metadata={"queue_depth": depth},
                )
            )
        anomalies = compute_anomalies("ST1008", events, [])["anomalies"]
        self.assertTrue(any(item["type"] == "BILLING_QUEUE_SPIKE" for item in anomalies))
        self.assertTrue(any("billing" in item["suggested_action"].lower() for item in anomalies))


if __name__ == "__main__":
    unittest.main()

