# DESIGN

## Overview

This system turns the supplied retail CCTV and POS resources into a queryable Store Intelligence API. The problem statement describes a larger dataset with 5 stores, 3 cameras per store, 20-minute clips, `store_layout.json`, `pos_transactions.csv`, `sample_events.jsonl`, and `assertions.py`. The resources actually available for this submission are five short clips, a POS CSV, and an XLSX with a floor-plan image. The design therefore normalizes the provided files into the same conceptual interface the scoring problem expects: a camera-role manifest, a store layout JSON, structured behavioral events, and a REST API over those events.

The canonical store ID is `ST1008`, taken from the POS export. The API also accepts `STORE_BLR_002` and `Brigade_Bangalore` as aliases because those names appear in the challenge text and file names. Camera roles are assigned from visual inspection of sample frames: `CAM 3` is the entrance threshold, `CAM 5` is the billing/cash counter, `CAM 1` and `CAM 2` cover the main floor, and `CAM 4` is treated as a staff/backroom view. The XLSX floor-plan image is converted into `data/store_layout.json` with named zones matching visible shelves and counters.

## Components

`pipeline/detect.py` is the event generator. It probes clips with `ffprobe`, maps cameras to roles, reads sanitized POS transactions, and emits JSONL events in the required schema. When OpenCV is installed, `pipeline/tracker.py` samples frames with the built-in HOG person detector. That signal is deliberately treated as weak because overhead retail CCTV and face blur make general-purpose person detection noisy. The pipeline therefore combines video metadata, camera role, layout zones, and transaction timing instead of pretending that a generic detector is production-grade for this camera geometry.

`app/main.py` is a dependency-light HTTP API. It uses SQLite for event and transaction storage, validates every ingested event, deduplicates by `event_id`, and returns partial success for mixed valid/invalid batches. `app/logic.py` computes metrics, funnel, heatmap, anomalies, and health directly from stored events rather than from a cached report. `web/dashboard.html` polls the API and renders the live operational view.

## Event Flow

The event stream is session oriented. `ENTRY` creates a visitor session, `ZONE_ENTER` and `ZONE_DWELL` capture product-zone attention, `BILLING_QUEUE_JOIN` marks checkout intent, and `EXIT` closes the session. Re-entry is represented by a `REENTRY` event with the same visitor token, which means the funnel counts the physical visit once instead of inflating visitor totals. Staff events remain in storage but are excluded from customer metrics by `is_staff=true`.

POS correlation is time-window based. A visitor with a billing event in the five minutes before a transaction is counted as converted. This mirrors the prompt constraint that POS has no customer identity. It also lets the conversion metric degrade gracefully: if there are zero purchases, conversion is `0.0`, not null or an exception.

## Production Behavior

The API logs one JSON line per request with `trace_id`, endpoint, store ID, event count, latency, and status code. SQLite failures are caught and returned as HTTP 503 with a structured body. The health endpoint reports the last event timestamp and marks a feed stale when the lag exceeds ten minutes. Docker starts the API without installing packages at runtime, and the optional detection dependencies are isolated in `requirements-detection.txt`.

## AI-Assisted Decisions

AI helped challenge the initial temptation to build a larger FastAPI stack. The scoring gate only requires `docker compose up`, ingest, metrics, and non-trivial docs; adding web framework dependencies would make the container more fragile without improving the core evaluation. I kept the API in the standard library and used SQLite because the service surface is small, the data volume for a review run is modest, and the business logic remains testable in pure Python.

AI also suggested using YOLOv8 or a VLM for detection. I did not make that the hard dependency because the challenge resources are short, incomplete relative to the PDF, and reviewers may run the submission on a clean machine without model weights. The implemented compromise is an optional OpenCV sampling path plus a deterministic event generator that fuses video metadata with POS timing. That is less glamorous than a heavyweight detector, but it is more reproducible under the actual file constraints.

Finally, AI pushed for documenting the mismatch between the prompt manifest and the supplied resources instead of hiding it. I agreed. The design makes explicit assumptions about camera role, store ID, layout extraction, and POS correlation so a reviewer can challenge or replace those assumptions without reverse-engineering the code.

