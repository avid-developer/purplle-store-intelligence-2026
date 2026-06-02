# CHOICES

## 1. Detection Model and Pipeline

Options considered were YOLOv8/YOLOv9, OpenCV HOG, a VLM-assisted frame classifier, and a rule-based event generator using video metadata plus POS timing. AI initially recommended YOLOv8 with ByteTrack because it is a common baseline for retail people counting. I would use that in a production pilot, but I did not make it the mandatory path here. The final Resource Center provides two store archives with entry, zone, billing, and layout files, and the repository must run cleanly for reviewers without downloading model weights. A hard YOLO dependency would add installation and weight-management risk while still being uncertain on overhead, blurred CCTV frames.

The chosen design is a two-layer pipeline. The first layer probes every clip and maps cameras into business roles: entry, main floor, billing, and staff/backroom. The second layer emits session events by correlating POS timing with camera roles and layout zones. If `opencv-python-headless` is installed, the pipeline also samples frames using OpenCV HOG and records that signal in the manifest. The output confidence reflects this compromise: POS-correlated events get moderate confidence, clip-presence estimates get lower confidence, and staff/backroom events are flagged as staff instead of silently discarded.

This choice optimizes for acceptance-gate reliability and honest uncertainty. It is not claimed to be a final production CV model. It is a replaceable detection adapter that emits the required event schema and gives the API real, varying input.

## 2. Event Schema Rationale

The prompt defines a strict schema, but the final Resource Center `sample_events.jsonl` uses a different practical dialect with fields such as `id_token`, `store_code`, `event_timestamp`, `zone_entered`, and `queue_completed`. The main decision was whether to force callers to transform that file manually or make ingestion bilingual. AI suggested keeping the internal schema strict while adding a narrow adapter at the API boundary, and I agreed because it preserves scorer-facing consistency without rejecting official sample input.

The API validates required identifiers, event type, timestamp, zone requirement for zone events, non-negative dwell time, boolean staff flag, confidence range, and metadata shape after adaptation. It keeps low-confidence events instead of filtering them because confidence calibration is part of the scoring criteria. Deduplication happens at `event_id`; official queue events reuse `queue_event_id`, and generated events use deterministic UUIDv5 IDs from event content. That makes repeated pipeline runs idempotent, which is important for retries and for reviewers rerunning the same command.

I kept `metadata` extensible for queue depth, SKU zone, session sequence, conversion hints, source labels, original sample-event demographics, queue timestamps, and previous exit timestamps. This avoids schema churn while still supporting the required analytics: queue anomalies, SKU-zone heatmaps, re-entry handling, and POS conversion correlation.

## 3. API Architecture Choice

The main options were FastAPI with Pydantic, Flask, or a standard-library HTTP server. AI initially leaned toward FastAPI because the prompt mentions it and reviewers may be familiar with it. I chose the standard library plus SQLite. The reason is operational simplicity: `docker compose up` starts the API without pip installing packages, there is no ASGI server to configure, and every endpoint remains straightforward to inspect.

SQLite is sufficient for the expected review workload: batches of 500 events, a few clips, and point-in-time analytics. The storage boundary is still clean. `app/storage.py` owns persistence, `app/models.py` owns validation, and `app/logic.py` owns business computation. If this had to support 40 stores in live production, the first upgrade would be PostgreSQL or ClickHouse for event storage and a message queue in front of ingestion. The current design intentionally keeps that as an implementation swap rather than forcing distributed infrastructure into a take-home submission.

The API computes metrics from stored events on every request. That keeps correctness obvious and avoids stale cached aggregates. For larger deployments I would materialize rolling aggregates, but only after adding replay-safe event offsets and more operational observability.
