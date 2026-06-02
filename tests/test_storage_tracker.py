# PROMPT: Cover storage helpers and video probing helpers without requiring real network or real CCTV files.
# CHANGES MADE: Used temporary CSV/JSON files and mocked ffprobe output so tests validate parsing and alias behavior deterministically.

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import storage
from pipeline import tracker


class StorageTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        storage.DB_PATH = Path(self.tmp.name) / "store.db"
        storage.init_db()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_load_pos_csv_and_fetch(self) -> None:
        csv_path = Path(self.tmp.name) / "pos.csv"
        csv_path.write_text(
            "store_id,transaction_id,timestamp,basket_value_inr,department,sku_zone,item_count\n"
            "ST1008,T1,2026-04-10T08:00:00Z,123.45,makeup,Lipstick,2\n"
        )
        self.assertEqual(storage.load_pos_csv(csv_path), 1)
        rows = storage.fetch_pos("ST1008")
        self.assertEqual(rows[0]["transaction_id"], "T1")
        self.assertEqual(rows[0]["item_count"], 2)

    def test_load_resource_center_pos_csv(self) -> None:
        csv_path = Path(self.tmp.name) / "pos_resource.csv"
        csv_path.write_text(
            "order_id,order_date,order_time,store_id,product_id,brand_name,total_amount\n"
            "1,10-04-2026,12:15:05,ST1008,399945,Faces Canada,302.33\n"
        )
        self.assertEqual(storage.load_pos_csv(csv_path), 1)
        rows = storage.fetch_pos("ST1008")
        self.assertEqual(rows[0]["transaction_id"], "1")
        self.assertEqual(rows[0]["timestamp"], "2026-04-10T06:45:05Z")
        self.assertEqual(rows[0]["basket_value_inr"], 302.33)
        self.assertEqual(rows[0]["sku_zone"], "Faces Canada")

    def test_layout_alias_resolution(self) -> None:
        layout_path = Path(self.tmp.name) / "layout.json"
        layout_path.write_text(json.dumps({"stores": {"ST1008": {"aliases": ["STORE_BLR_002"], "zones": []}}}))
        storage.LAYOUT_PATH = layout_path
        self.assertEqual(storage.resolve_store_id("STORE_BLR_002"), "ST1008")
        self.assertEqual(storage.resolve_store_id("UNKNOWN"), "UNKNOWN")

    def test_probe_clip_parses_ffprobe_json(self) -> None:
        payload = {
            "format": {"duration": "12.5", "tags": {"creation_time": "2026-04-10T08:00:00Z"}},
            "streams": [{"width": 1920, "height": 1080, "r_frame_rate": "30000/1001"}],
        }
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(payload), stderr="")
        with patch("pipeline.tracker.subprocess.run", Mock(return_value=completed)):
            clip = tracker.probe_clip(Path("CAM 3.mp4"))
        self.assertEqual(clip.camera_id, "CAM_ENTRY_01")
        self.assertEqual(round(clip.fps, 2), 29.97)
        self.assertEqual(clip.duration_seconds, 12.5)

    def test_camera_profile_and_sample_failure_path(self) -> None:
        self.assertEqual(tracker.camera_profile(Path("CAM 5.mp4"))["role"], "billing")
        self.assertEqual(tracker.camera_profile(Path("billing_area.mp4"))["role"], "billing")
        self.assertEqual(tracker.camera_profile(Path("entry 2.mp4"))["camera_id"], "CAM_ENTRY_02")
        self.assertEqual(tracker.camera_profile(Path("zone.mp4"))["role"], "main_floor")
        self.assertEqual(tracker._parse_rate("25/1"), 25.0)
        self.assertEqual(tracker.sample_people_counts(Path("missing.mp4")), [])


if __name__ == "__main__":
    unittest.main()
