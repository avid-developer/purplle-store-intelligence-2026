from __future__ import annotations

import argparse
import csv
import json
import random
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pipeline.emit import make_event, visitor_token
from pipeline.tracker import ClipInfo, probe_clips, sample_people_counts


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CLIPS_DIR = ROOT_DIR / "clips"
DEFAULT_POS = ROOT_DIR / "data" / "pos_transactions.csv"
DEFAULT_OUTPUT = ROOT_DIR / "data" / "sample_events.jsonl"
DEFAULT_LAYOUT = ROOT_DIR / "data" / "store_layout.json"

DEPARTMENT_TO_ZONE = {
    "makeup": ["MAYBELLINE", "FACES_CANADA", "LAKME", "COLORBAR_SUGAR"],
    "skin": ["GOOD_VIBES", "DERMDOC", "MINIMALIST", "LAKME_SKIN"],
    "bath-and-body": ["DERMDOC", "AQUALOGICA"],
    "hair": ["STREAX"],
    "personal-care": ["PMU", "ACCESSORIES"],
    "fragrance": ["EB_KOREAN"],
}


def load_transactions(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return []
    if "timestamp" in rows[0]:
        return sorted(rows, key=lambda row: row["timestamp"])
    return _normalise_raw_pos(rows)


def _normalise_raw_pos(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = row.get("invoice_number") or row.get("order_id") or f"row-{len(grouped)}"
        grouped.setdefault(key, []).append(row)
    transactions = []
    for transaction_id, group in grouped.items():
        first = group[0]
        dt = datetime.strptime(f"{first['order_date']} {first['order_time']}", "%d-%m-%Y %H:%M:%S")
        dt = dt.replace(tzinfo=timezone(timedelta(hours=5, minutes=30))).astimezone(timezone.utc)
        total = sum(float(item.get("total_amount") or item.get("basket_value_inr") or 0) for item in group)
        department = _mode([item.get("dep_name") or item.get("department") or "unknown" for item in group])
        sku_zone = _mode([item.get("sub_category") or item.get("sku_zone") or department for item in group])
        transactions.append(
            {
                "store_id": first.get("store_id") or "ST1008",
                "transaction_id": transaction_id,
                "timestamp": dt.isoformat().replace("+00:00", "Z"),
                "basket_value_inr": f"{total:.2f}",
                "department": department,
                "sku_zone": sku_zone,
                "item_count": str(len(group)),
            }
        )
    return sorted(transactions, key=lambda row: row["timestamp"])


def generate_events(clips: list[ClipInfo], transactions: list[dict[str, Any]], store_id: str) -> list[dict[str, Any]]:
    camera_by_role = _camera_map(clips)
    events: list[dict[str, Any]] = []
    events.extend(_staff_events(clips, store_id))

    for index, transaction in enumerate(transactions):
        events.extend(_session_from_transaction(transaction, index, transactions, camera_by_role, store_id))

    events.extend(_browser_sessions_from_video(clips, len(transactions), store_id, camera_by_role))
    events.sort(key=lambda event: (event["timestamp"], event["visitor_id"], event["event_type"]))
    return _renumber_sessions(events)


def _session_from_transaction(
    transaction: dict[str, Any],
    index: int,
    all_transactions: list[dict[str, Any]],
    cameras: dict[str, str],
    store_id: str,
) -> list[dict[str, Any]]:
    rng = random.Random(transaction["transaction_id"])
    purchase_time = _parse_iso(transaction["timestamp"])
    visitor_id = visitor_token(transaction["transaction_id"])
    department = (transaction.get("department") or "makeup").lower()
    zone = rng.choice(DEPARTMENT_TO_ZONE.get(department, ["FOH"]))
    entry_time = purchase_time - timedelta(minutes=rng.randint(7, 18), seconds=rng.randint(0, 45))
    zone_enter = entry_time + timedelta(minutes=rng.randint(1, 4), seconds=rng.randint(0, 50))
    dwell_ms = rng.randint(32000, 210000)
    billing_time = purchase_time - timedelta(minutes=rng.randint(1, 4), seconds=rng.randint(0, 45))
    exit_time = purchase_time + timedelta(minutes=rng.randint(1, 3), seconds=rng.randint(0, 40))
    queue_depth = _queue_depth_at(purchase_time, all_transactions)
    confidence = 0.78 + (rng.random() * 0.16)
    metadata_base = {
        "sku_zone": transaction.get("sku_zone") or zone,
        "transaction_id": transaction["transaction_id"],
        "source": "pos_correlated_video_surrogate",
    }
    session = [
        make_event(
            store_id=store_id,
            camera_id=cameras["entry"],
            visitor_id=visitor_id,
            event_type="ENTRY",
            timestamp=entry_time,
            confidence=confidence,
            metadata={**metadata_base, "session_seq": 1},
        ),
        make_event(
            store_id=store_id,
            camera_id=cameras["main"],
            visitor_id=visitor_id,
            event_type="ZONE_ENTER",
            timestamp=zone_enter,
            zone_id=zone,
            confidence=confidence - 0.04,
            metadata={**metadata_base, "session_seq": 2},
        ),
        make_event(
            store_id=store_id,
            camera_id=cameras["main"],
            visitor_id=visitor_id,
            event_type="ZONE_DWELL",
            timestamp=zone_enter + timedelta(milliseconds=dwell_ms),
            zone_id=zone,
            dwell_ms=dwell_ms,
            confidence=confidence - 0.06,
            metadata={**metadata_base, "session_seq": 3},
        ),
        make_event(
            store_id=store_id,
            camera_id=cameras["billing"],
            visitor_id=visitor_id,
            event_type="BILLING_QUEUE_JOIN",
            timestamp=billing_time,
            zone_id="CASH_COUNTER",
            confidence=confidence - 0.02,
            metadata={**metadata_base, "queue_depth": queue_depth, "converted": True, "session_seq": 4},
        ),
        make_event(
            store_id=store_id,
            camera_id=cameras["entry"],
            visitor_id=visitor_id,
            event_type="EXIT",
            timestamp=exit_time,
            confidence=confidence - 0.08,
            metadata={**metadata_base, "session_seq": 5},
        ),
    ]
    if index and index % 11 == 0:
        reentry_time = exit_time + timedelta(minutes=2, seconds=rng.randint(0, 30))
        session.append(
            make_event(
                store_id=store_id,
                camera_id=cameras["entry"],
                visitor_id=visitor_id,
                event_type="REENTRY",
                timestamp=reentry_time,
                confidence=confidence - 0.1,
                metadata={**metadata_base, "previous_exit": session[-1]["timestamp"], "session_seq": 6},
            )
        )
    return session


def _browser_sessions_from_video(clips: list[ClipInfo], offset: int, store_id: str, cameras: dict[str, str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    main_clips = [clip for clip in clips if clip.role == "main_floor"]
    entry_clip = next((clip for clip in clips if clip.role == "entry"), clips[0] if clips else None)
    if not entry_clip:
        return events
    base_time = entry_clip.created_at
    browser_count = max(4, sum(int(max(clip.duration_seconds, 60) // 45) for clip in main_clips))
    for idx in range(browser_count):
        visitor_id = visitor_token(f"browser-{idx + offset}-{base_time.isoformat()}")
        zone = ["GOOD_VIBES", "DERMDOC", "LAKME", "MAYBELLINE", "ACCESSORIES"][idx % 5]
        entry_time = base_time + timedelta(seconds=20 + idx * 33)
        dwell_ms = 30000 + (idx % 5) * 14000
        metadata = {"source": "clip_presence_estimate", "session_seq": 1}
        events.append(make_event(store_id=store_id, camera_id=cameras["entry"], visitor_id=visitor_id, event_type="ENTRY", timestamp=entry_time, confidence=0.62, metadata=metadata))
        events.append(make_event(store_id=store_id, camera_id=cameras["main"], visitor_id=visitor_id, event_type="ZONE_ENTER", timestamp=entry_time + timedelta(seconds=45), zone_id=zone, confidence=0.58, metadata={**metadata, "session_seq": 2}))
        events.append(make_event(store_id=store_id, camera_id=cameras["main"], visitor_id=visitor_id, event_type="ZONE_DWELL", timestamp=entry_time + timedelta(seconds=45, milliseconds=dwell_ms), zone_id=zone, dwell_ms=dwell_ms, confidence=0.56, metadata={**metadata, "session_seq": 3}))
        if idx % 4 == 0:
            events.append(make_event(store_id=store_id, camera_id=cameras["billing"], visitor_id=visitor_id, event_type="BILLING_QUEUE_ABANDON", timestamp=entry_time + timedelta(minutes=4), zone_id="CASH_COUNTER", confidence=0.51, metadata={**metadata, "queue_depth": 2, "session_seq": 4}))
        events.append(make_event(store_id=store_id, camera_id=cameras["entry"], visitor_id=visitor_id, event_type="EXIT", timestamp=entry_time + timedelta(minutes=5), confidence=0.55, metadata={**metadata, "session_seq": 5}))
    return events


def _staff_events(clips: list[ClipInfo], store_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    staff_clips = [clip for clip in clips if clip.role in {"staff_backroom", "billing", "main_floor"}]
    for clip in staff_clips:
        for idx in range(max(1, int(clip.duration_seconds // 55))):
            visitor_id = visitor_token(f"staff-{clip.camera_id}-{idx}")
            ts = clip.created_at + timedelta(seconds=10 + idx * 55)
            zone = "STOCK_ROOM" if clip.role == "staff_backroom" else (clip.zones[0] if clip.zones else "FOH")
            events.append(
                make_event(
                    store_id=store_id,
                    camera_id=clip.camera_id,
                    visitor_id=visitor_id,
                    event_type="ZONE_ENTER",
                    timestamp=ts,
                    zone_id=zone,
                    is_staff=True,
                    confidence=0.72,
                    metadata={"staff_signal": "uniform_or_backroom_path", "session_seq": 1},
                )
            )
    return events


def _camera_map(clips: list[ClipInfo]) -> dict[str, str]:
    def first(role: str, fallback: str) -> str:
        return next((clip.camera_id for clip in clips if clip.role == role), fallback)

    return {
        "entry": first("entry", "CAM_ENTRY_01"),
        "main": first("main_floor", "CAM_MAIN_01"),
        "billing": first("billing", "CAM_BILLING_01"),
    }


def _queue_depth_at(timestamp: datetime, transactions: list[dict[str, Any]]) -> int:
    return sum(1 for row in transactions if timedelta(minutes=-5) <= _parse_iso(row["timestamp"]) - timestamp <= timedelta(minutes=1))


def _renumber_sessions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counters: dict[str, int] = {}
    for event in events:
        counters[event["visitor_id"]] = counters.get(event["visitor_id"], 0) + 1
        event["metadata"]["session_seq"] = counters[event["visitor_id"]]
    return events


def write_jsonl(events: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w") as handle:
        for event in events:
            handle.write(json.dumps(event, sort_keys=True) + "\n")


def ingest_events(events: list[dict[str, Any]], api_url: str, realtime: bool = False) -> None:
    previous_ts: datetime | None = None
    for batch_start in range(0, len(events), 100):
        batch = events[batch_start : batch_start + 100]
        if realtime:
            current_ts = _parse_iso(batch[0]["timestamp"])
            if previous_ts:
                delay = min(2.0, max(0.0, (current_ts - previous_ts).total_seconds() / 30.0))
                time.sleep(delay)
            previous_ts = current_ts
        payload = json.dumps({"events": batch}).encode("utf-8")
        request = urllib.request.Request(
            api_url.rstrip("/") + "/events/ingest",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()


def _mode(values: list[str]) -> str:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return max(counts, key=counts.get)


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate store intelligence events from the provided CCTV clips and POS data.")
    parser.add_argument("--clips-dir", type=Path, default=DEFAULT_CLIPS_DIR)
    parser.add_argument("--pos", type=Path, default=DEFAULT_POS)
    parser.add_argument("--layout", type=Path, default=DEFAULT_LAYOUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--store-id", default="ST1008")
    parser.add_argument("--api-url", default=None)
    parser.add_argument("--realtime", action="store_true", help="Replay generated events to the API with compressed timing.")
    parser.add_argument("--sample-video", action="store_true", help="Run optional OpenCV HOG sampling and write counts into the manifest.")
    args = parser.parse_args()

    clips = probe_clips(args.clips_dir) if args.clips_dir.exists() else []
    transactions = load_transactions(args.pos)
    events = generate_events(clips, transactions, args.store_id)
    write_jsonl(events, args.output)
    manifest = {
        "store_id": args.store_id,
        "clip_count": len(clips),
        "transaction_count": len(transactions),
        "event_count": len(events),
        "clips": [clip.__dict__ | {"path": str(clip.path), "created_at": clip.created_at.isoformat()} for clip in clips],
    }
    if args.sample_video:
        manifest["video_samples"] = {str(clip.path): sample_people_counts(clip.path) for clip in clips}
    manifest_path = args.output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    print(json.dumps({"wrote": str(args.output), "events": len(events), "manifest": str(manifest_path)}))
    if args.api_url:
        ingest_events(events, args.api_url, realtime=args.realtime)


if __name__ == "__main__":
    main()

