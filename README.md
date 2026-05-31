# Store Intelligence System

Containerised Store Intelligence API and event pipeline for the Purplle Tech Challenge 2026 Round 2.

The supplied resources did not match the full problem-statement manifest. The actual ZIP contains five short CCTV clips named `CAM 1.mp4` through `CAM 5.mp4`; the POS data and layout were supplied separately as CSV/XLSX. This implementation treats `ST1008` as the canonical store and accepts `STORE_BLR_002` as an alias because that ID appears in the problem statement.

## Run the API

```bash
docker compose up --build
curl http://localhost:8000/health
curl http://localhost:8000/stores/ST1008/metrics
```

The API is available at `http://localhost:8000`. The live dashboard is served at `http://localhost:8000/dashboard`.

## Generate and Ingest Events

The repository does not include the challenge videos or raw POS export. Place the extracted videos in `clips/` or pass the extracted folder directly:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-detection.txt
.venv/bin/python -m pipeline.detect --clips-dir "/path/to/CCTV Footage" --pos data/pos_transactions.csv --output data/sample_events.jsonl
.venv/bin/python scripts/ingest_jsonl.py data/sample_events.jsonl --api-url http://localhost:8000
```

For a compressed live replay into the API:

```bash
.venv/bin/python -m pipeline.detect --clips-dir "/path/to/CCTV Footage" --pos data/pos_transactions.csv --output data/sample_events.jsonl --api-url http://localhost:8000 --realtime
```

## API Endpoints

- `POST /events/ingest` accepts a JSON array or `{"events": [...]}` with up to 500 events. Inserts are idempotent by `event_id` and malformed records are returned in a `rejected` list.
- `GET /stores/{id}/metrics` returns visitors, conversion rate, dwell by zone, queue depth, and abandonment.
- `GET /stores/{id}/funnel` returns session-level Entry -> Zone Visit -> Billing Queue -> Purchase counts and drop-off.
- `GET /stores/{id}/heatmap` returns normalized zone visit and dwell scores with a data-confidence flag.
- `GET /stores/{id}/anomalies` returns active queue, conversion, and dead-zone anomalies.
- `GET /health` returns service status and last event timestamp per store.

## Tests

```bash
python -m unittest discover -s tests
```

Each test file starts with the required prompt and change block. The tests cover idempotent ingestion, malformed event handling, empty store behavior, all-staff exclusion, zero-purchase conversion, re-entry funnel deduplication, and anomaly generation.

## Data Handling

The original POS CSV contains customer names and phone numbers, so it is intentionally not committed. `data/pos_transactions.csv` is a sanitized transaction-level extract containing only store ID, transaction ID, timestamp, basket value, department, SKU zone, and item count. The videos are excluded by `.gitignore` and `.dockerignore`.

## Architecture Notes

The API uses Python's standard library HTTP server plus SQLite. That keeps `docker compose up` dependency-free and predictable for reviewer machines. The detection pipeline optionally uses OpenCV HOG sampling when `opencv-python-headless` is installed, but it can still emit schema-valid events from the provided POS timing, clip metadata, and camera role assumptions when model dependencies are unavailable.

