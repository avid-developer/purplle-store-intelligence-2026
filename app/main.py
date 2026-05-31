from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from app.logic import compute_anomalies, compute_funnel, compute_health, compute_heatmap, compute_metrics
from app.models import validate_event
from app.storage import (
    StorageUnavailable,
    fetch_all_last_event_times,
    fetch_events,
    fetch_pos,
    init_db,
    load_pos_csv,
    resolve_store_id,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT_DIR / "web"
MAX_BATCH_SIZE = 500


def bootstrap() -> None:
    init_db()
    load_pos_csv()


def _json_response(handler: BaseHTTPRequestHandler, status: int, body: dict[str, Any], trace_id: str) -> None:
    payload = json.dumps(body, indent=2, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("X-Trace-Id", trace_id)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "content-type")
    handler.end_headers()
    handler.wfile.write(payload)


def _error(code: str, message: str, trace_id: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "trace_id": trace_id}}


class StoreRequestHandler(BaseHTTPRequestHandler):
    server_version = "StoreIntelligence/1.0"

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.end_headers()

    def do_POST(self) -> None:
        trace_id = self.headers.get("X-Trace-Id", str(uuid.uuid4()))
        start = time.perf_counter()
        status = 500
        event_count = 0
        store_id = None
        try:
            if self.path.rstrip("/") != "/events/ingest":
                status = 404
                _json_response(self, status, _error("not_found", "endpoint not found", trace_id), trace_id)
                return
            length = int(self.headers.get("Content-Length", "0") or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError as exc:
                status = 400
                _json_response(self, status, _error("invalid_json", str(exc), trace_id), trace_id)
                return

            raw_events = payload.get("events") if isinstance(payload, dict) else payload
            if not isinstance(raw_events, list):
                status = 400
                _json_response(self, status, _error("invalid_payload", "expected a JSON array or {'events': [...]} payload", trace_id), trace_id)
                return
            if len(raw_events) > MAX_BATCH_SIZE:
                status = 413
                _json_response(self, status, _error("batch_too_large", "batches are limited to 500 events", trace_id), trace_id)
                return

            accepted_events: list[dict[str, Any]] = []
            rejected = []
            for index, raw in enumerate(raw_events):
                normalized, validation_error = validate_event(raw, index)
                if validation_error:
                    rejected.append(validation_error.__dict__)
                elif normalized:
                    accepted_events.append(normalized)
            event_count = len(raw_events)
            if accepted_events:
                store_id = accepted_events[0]["store_id"]
            accepted, duplicates = self.server.storage_insert_events(accepted_events)
            status = 200
            _json_response(
                self,
                status,
                {
                    "trace_id": trace_id,
                    "status": "partial_success" if rejected else "ok",
                    "accepted": accepted,
                    "duplicates": len(duplicates),
                    "duplicate_event_ids": duplicates[:20],
                    "rejected": rejected,
                },
                trace_id,
            )
        except StorageUnavailable as exc:
            status = 503
            _json_response(self, status, _error("database_unavailable", str(exc), trace_id), trace_id)
        finally:
            self._log_structured(trace_id, start, status, store_id, event_count)

    def do_GET(self) -> None:
        trace_id = self.headers.get("X-Trace-Id", str(uuid.uuid4()))
        start = time.perf_counter()
        status = 500
        store_id = None
        try:
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path in {"/", "/dashboard"}:
                status = self._serve_static("dashboard.html", trace_id)
                return
            if path == "/health":
                status = 200
                _json_response(self, status, compute_health(fetch_all_last_event_times()), trace_id)
                return
            match = re.fullmatch(r"/stores/([^/]+)/(metrics|funnel|heatmap|anomalies)", path)
            if not match:
                status = 404
                _json_response(self, status, _error("not_found", "endpoint not found", trace_id), trace_id)
                return
            store_id = resolve_store_id(match.group(1))
            endpoint = match.group(2)
            events = fetch_events(store_id)
            pos = fetch_pos(store_id)
            if endpoint == "metrics":
                body = compute_metrics(store_id, events, pos)
            elif endpoint == "funnel":
                body = compute_funnel(store_id, events, pos)
            elif endpoint == "heatmap":
                body = compute_heatmap(store_id, events)
            else:
                body = compute_anomalies(store_id, events, pos)
            status = 200
            _json_response(self, status, body, trace_id)
        except StorageUnavailable as exc:
            status = 503
            _json_response(self, status, _error("database_unavailable", str(exc), trace_id), trace_id)
        finally:
            self._log_structured(trace_id, start, status, store_id, 0)

    def _serve_static(self, filename: str, trace_id: str) -> int:
        path = WEB_DIR / filename
        if not path.exists():
            _json_response(self, 404, _error("not_found", "dashboard asset missing", trace_id), trace_id)
            return 404
        payload = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "text/html")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Trace-Id", trace_id)
        self.end_headers()
        self.wfile.write(payload)
        return 200

    def _log_structured(self, trace_id: str, start: float, status: int, store_id: str | None, event_count: int) -> None:
        record = {
            "trace_id": trace_id,
            "endpoint": self.path.split("?", 1)[0],
            "method": self.command,
            "store_id": store_id,
            "latency_ms": round((time.perf_counter() - start) * 1000, 2),
            "event_count": event_count,
            "status_code": status,
        }
        print(json.dumps(record, sort_keys=True), file=sys.stdout, flush=True)

    def log_message(self, format: str, *args: Any) -> None:
        return


def run(host: str = "0.0.0.0", port: int = 8000) -> None:
    bootstrap()
    server = ThreadingHTTPServer((host, port), StoreRequestHandler)
    server.storage_insert_events = __import__("app.storage", fromlist=["insert_events"]).insert_events
    print(json.dumps({"message": "store intelligence api started", "host": host, "port": port}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    run(port=int(os.environ.get("PORT", "8000")))

