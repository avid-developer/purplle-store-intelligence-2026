# PROMPT: Test pipeline file I/O helpers, raw POS normalization, JSONL writing, and HTTP replay batching.
# CHANGES MADE: Mocked urllib to avoid external calls and wrote tiny temporary inputs that still match the challenge CSV shape.

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from pipeline import detect
from pipeline.emit import make_event


class PipelineIoTests(unittest.TestCase):
    def test_load_sanitized_and_raw_transactions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sanitized = Path(tmp) / "pos.csv"
            sanitized.write_text(
                "store_id,transaction_id,timestamp,basket_value_inr,department,sku_zone,item_count\n"
                "ST1008,T1,2026-04-10T08:00:00Z,50,skin,Sheet Mask,1\n"
            )
            self.assertEqual(detect.load_transactions(sanitized)[0]["transaction_id"], "T1")

            raw = Path(tmp) / "raw.csv"
            raw.write_text(
                "invoice_number,order_id,order_date,order_time,store_id,total_amount,dep_name,sub_category\n"
                "INV1,O1,10-04-2026,12:00:00,ST1008,75,makeup,Lipstick\n"
                "INV1,O1,10-04-2026,12:00:00,ST1008,25,makeup,Lipstick\n"
            )
            rows = detect.load_transactions(raw)
            self.assertEqual(rows[0]["transaction_id"], "INV1")
            self.assertEqual(rows[0]["basket_value_inr"], "100.00")

    def test_write_jsonl_and_replay(self) -> None:
        event = make_event(
            store_id="ST1008",
            camera_id="CAM_ENTRY_01",
            visitor_id="VIS_io",
            event_type="ENTRY",
            timestamp=datetime(2026, 4, 10, 8, 0, tzinfo=timezone.utc),
        )
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "events.jsonl"
            detect.write_jsonl([event], output)
            self.assertEqual(json.loads(output.read_text())["event_id"], event["event_id"])

        fake_response = Mock()
        fake_response.__enter__ = Mock(return_value=fake_response)
        fake_response.__exit__ = Mock(return_value=False)
        fake_response.read = Mock(return_value=b'{"accepted": 1}')
        with patch("pipeline.detect.urllib.request.urlopen", Mock(return_value=fake_response)) as urlopen:
            detect.ingest_events([event], "http://api.test")
        self.assertEqual(urlopen.call_count, 1)


if __name__ == "__main__":
    unittest.main()

