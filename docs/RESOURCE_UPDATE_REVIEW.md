# Resource Update Review

Reviewed against the final HackerEarth Resource Center visible in Chrome Beta on 2026-06-03.

## What Changed

- Deadline changed from June 3, 2026 to June 4, 2026.
- Problem statement link changed to `Purplle_Tech_Challenge_PS3f02573.pdf`.
- Resource Center changed from the earlier CCTV/POS/layout bundle to four authoritative files:
  - `POS - sample transactionsb1e826f.csv`
  - `sample_eventsbe42122.jsonl`
  - `Store 1-20260602T101818Z-3-001ec38db8.zip`
  - `Store 2-20260602T101819Z-3-001099f208.zip`
- Store archives now contain layout images and store-specific clips:
  - Store 1: `CAM 1 - zone.mp4`, `CAM 2 - zone.mp4`, `CAM 3 - entry.mp4`, `CAM 5 - billing.mp4`
  - Store 2: `entry 1.mp4`, `entry 2.mp4`, `zone.mp4`, `billing_area.mp4`
- The official sample event JSONL uses a practical schema with `entry`, `exit`, `zone_entered`, `zone_exited`, `queue_completed`, and `queue_abandoned`, rather than the exact uppercase PDF schema.
- The official POS CSV uses `order_id`, `order_date`, `order_time`, `store_id`, `product_id`, `brand_name`, and `total_amount`, rather than the simplified PDF POS schema.

## Submission Adjustments

- Added an ingestion adapter for the official sample-event schema while keeping the internal PDF schema strict.
- Added POS CSV parsing for the official Resource Center columns.
- Added clip-role mapping for the final Store 1 and Store 2 archive filenames.
- Added `ST1076` / `store_1076` aliases so Resource Center sample events resolve to the demo store layout.
- Updated README, DESIGN, and CHOICES to reference the final Resource Center instead of the earlier incomplete resources.
