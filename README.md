# Store Intelligence System

Containerised Store Intelligence API and event pipeline for the Purplle Tech Challenge 2026 Round 2.

The final Resource Center update supplies a compact problem statement, `POS - sample transactions.csv`, `sample_events.jsonl`, and two store archives. This implementation treats `ST1008` as the canonical demo store, accepts `STORE_BLR_002` for the acceptance-gate endpoint, and normalizes the sample-event store identifiers `ST1076` / `store_1076` so the Resource Center examples can be ingested directly.

## Run the API

```bash
docker compose up --build
curl http://localhost:8000/health
curl http://localhost:8000/stores/ST1008/metrics
```

The API is available at `http://localhost:8000`. The live dashboard is served at `http://localhost:8000/dashboard`.

## Generate and Ingest Events

The repository does not include the official challenge videos or resource files. Download them from the HackerEarth Resource Center, extract either `Store 1` or `Store 2`, and pass that folder directly:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-detection.txt
.venv/bin/python -m pipeline.detect --clips-dir "/path/to/Store 1" --pos "/path/to/POS - sample transactions.csv" --output data/sample_events.jsonl
.venv/bin/python scripts/ingest_jsonl.py data/sample_events.jsonl --api-url http://localhost:8000
```

The updated Resource Center `sample_events.jsonl` can also be ingested as-is. The API adapts its `entry`, `exit`, `zone_entered`, `zone_exited`, `queue_completed`, and `queue_abandoned` records into the internal schema while preserving the original fields in event metadata:

```bash
.venv/bin/python scripts/ingest_jsonl.py "/path/to/sample_events.jsonl" --api-url http://localhost:8000
```

For a compressed live replay into the API:

```bash
.venv/bin/python -m pipeline.detect --clips-dir "/path/to/Store 2" --pos "/path/to/POS - sample transactions.csv" --output data/sample_events.jsonl --api-url http://localhost:8000 --realtime
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

Official Resource Center files are intentionally not committed. `data/pos_transactions.csv` and `data/sample_events.jsonl` are generated demo fixtures used to make the API runnable without redistributing the challenge dataset. ZIPs, videos, SQLite databases, and cache output are excluded by `.gitignore` and `.dockerignore`.

## Architecture Notes

The API uses Python's standard library HTTP server plus SQLite. That keeps `docker compose up` dependency-free and predictable for reviewer machines. The detection pipeline optionally uses OpenCV HOG sampling when `opencv-python-headless` is installed, but it can still emit schema-valid events from the provided POS timing, clip metadata, and camera role assumptions when model dependencies are unavailable.
