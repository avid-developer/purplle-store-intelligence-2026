# PROMPT: Add integration tests for the REST API covering ingest, metrics, health, dashboard serving, malformed JSON, and unknown routes.
# CHANGES MADE: Started the standard-library HTTP server on an ephemeral port with a temporary SQLite database so the tests exercise the real request handler.

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

from app import main, storage
from pipeline.emit import make_event


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        storage.DB_PATH = Path(self.tmp.name) / "store.db"
        storage.init_db()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), main.StoreRequestHandler)
        self.server.storage_insert_events = storage.insert_events
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.tmp.cleanup()

    def test_ingest_and_metrics_round_trip(self) -> None:
        event = make_event(
            store_id="ST1008",
            camera_id="CAM_ENTRY_01",
            visitor_id="VIS_api",
            event_type="ENTRY",
            timestamp=datetime(2026, 4, 10, 8, 0, tzinfo=timezone.utc),
            confidence=0.8,
        )
        result = self.post("/events/ingest", {"events": [event]})
        self.assertEqual(result["accepted"], 1)
        metrics = self.get("/stores/STORE_BLR_002/metrics")
        self.assertEqual(metrics["store_id"], "ST1008")
        self.assertEqual(metrics["unique_visitors"], 1)

    def test_health_and_dashboard_are_served(self) -> None:
        health = self.get("/health")
        self.assertIn("status", health)
        with urllib.request.urlopen(self.base + "/dashboard") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("text/html", response.headers["Content-Type"])

    def test_bad_requests_return_structured_errors(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as invalid_json:
            request = urllib.request.Request(
                self.base + "/events/ingest",
                data=b"{bad json",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(request)
        self.assertEqual(invalid_json.exception.code, 400)
        body = json.loads(invalid_json.exception.read())
        self.assertEqual(body["error"]["code"], "invalid_json")

        with self.assertRaises(urllib.error.HTTPError) as not_found:
            urllib.request.urlopen(self.base + "/missing")
        self.assertEqual(not_found.exception.code, 404)

    def post(self, path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(self.base + path) as response:
            return json.loads(response.read())


if __name__ == "__main__":
    unittest.main()
