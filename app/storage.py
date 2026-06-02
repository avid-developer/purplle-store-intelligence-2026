from __future__ import annotations

import csv
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from app.models import parse_timestamp


ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("STORE_DATA_DIR", ROOT_DIR / "data"))
DB_PATH = Path(os.environ.get("STORE_DB_PATH", DATA_DIR / "store.db"))
POS_CSV_PATH = Path(os.environ.get("STORE_POS_CSV", DATA_DIR / "pos_transactions.csv"))
LAYOUT_PATH = Path(os.environ.get("STORE_LAYOUT_PATH", DATA_DIR / "store_layout.json"))


class StorageUnavailable(RuntimeError):
    pass


def _connect() -> sqlite3.Connection:
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    except sqlite3.Error as exc:
        raise StorageUnavailable(str(exc)) from exc


@contextmanager
def connection() -> Iterable[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
    except sqlite3.Error as exc:
        raise StorageUnavailable(str(exc)) from exc
    finally:
        conn.close()


def init_db() -> None:
    with connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                store_id TEXT NOT NULL,
                camera_id TEXT NOT NULL,
                visitor_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                zone_id TEXT,
                dwell_ms INTEGER NOT NULL,
                is_staff INTEGER NOT NULL,
                confidence REAL NOT NULL,
                metadata TEXT NOT NULL,
                received_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_store_time ON events(store_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_events_store_visitor ON events(store_id, visitor_id);

            CREATE TABLE IF NOT EXISTS pos_transactions (
                transaction_id TEXT PRIMARY KEY,
                store_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                basket_value_inr REAL NOT NULL,
                department TEXT,
                sku_zone TEXT,
                item_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_pos_store_time ON pos_transactions(store_id, timestamp);
            """
        )
        conn.commit()


def load_pos_csv(path: Path = POS_CSV_PATH) -> int:
    if not path.exists():
        return 0
    inserted = 0
    with path.open(newline="") as handle, connection() as conn:
        reader = csv.DictReader(handle)
        for row in reader:
            normalized = _normalise_pos_row(row)
            conn.execute(
                """
                INSERT OR REPLACE INTO pos_transactions
                (transaction_id, store_id, timestamp, basket_value_inr, department, sku_zone, item_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["transaction_id"],
                    normalized["store_id"],
                    normalized["timestamp"],
                    normalized["basket_value_inr"],
                    normalized.get("department"),
                    normalized.get("sku_zone"),
                    normalized["item_count"],
                ),
            )
            inserted += 1
        conn.commit()
    return inserted


def _normalise_pos_row(row: dict[str, str]) -> dict[str, Any]:
    if row.get("timestamp"):
        timestamp = parse_timestamp(row["timestamp"]).isoformat().replace("+00:00", "Z")
        return {
            "transaction_id": row.get("transaction_id") or row.get("order_id") or row.get("invoice_number") or timestamp,
            "store_id": row.get("store_id") or "ST1008",
            "timestamp": timestamp,
            "basket_value_inr": float(row.get("basket_value_inr") or row.get("total_amount") or 0),
            "department": row.get("department") or row.get("dep_name") or row.get("brand_name") or None,
            "sku_zone": row.get("sku_zone") or row.get("sub_category") or row.get("brand_name") or row.get("product_id") or None,
            "item_count": int(float(row.get("item_count") or 1)),
        }

    if row.get("order_date") and row.get("order_time"):
        local_dt = datetime.strptime(f"{row['order_date']} {row['order_time']}", "%d-%m-%Y %H:%M:%S")
        timestamp = local_dt.replace(tzinfo=timezone(timedelta(hours=5, minutes=30))).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "transaction_id": row.get("transaction_id") or row.get("order_id") or row.get("invoice_number") or f"{row.get('store_id', 'STORE')}-{timestamp}",
            "store_id": row.get("store_id") or "ST1008",
            "timestamp": timestamp,
            "basket_value_inr": float(row.get("basket_value_inr") or row.get("total_amount") or 0),
            "department": row.get("department") or row.get("dep_name") or row.get("brand_name") or None,
            "sku_zone": row.get("sku_zone") or row.get("sub_category") or row.get("brand_name") or row.get("product_id") or None,
            "item_count": int(float(row.get("item_count") or 1)),
        }

    raise KeyError("POS row must include timestamp or order_date/order_time")


def load_layout(path: Path = LAYOUT_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"stores": {}}
    with path.open() as handle:
        return json.load(handle)


def resolve_store_id(store_id: str) -> str:
    layout = load_layout()
    stores = layout.get("stores", {})
    if store_id in stores:
        return store_id
    for canonical, details in stores.items():
        if store_id in details.get("aliases", []):
            return canonical
    return store_id


def insert_events(events: list[dict[str, Any]]) -> tuple[int, list[str]]:
    if not events:
        return 0, []
    accepted = 0
    duplicates: list[str] = []
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with connection() as conn:
        for event in events:
            try:
                conn.execute(
                    """
                    INSERT INTO events
                    (event_id, store_id, camera_id, visitor_id, event_type, timestamp, zone_id,
                     dwell_ms, is_staff, confidence, metadata, received_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["event_id"],
                        resolve_store_id(event["store_id"]),
                        event["camera_id"],
                        event["visitor_id"],
                        event["event_type"],
                        event["timestamp"],
                        event.get("zone_id"),
                        int(event.get("dwell_ms", 0)),
                        1 if event.get("is_staff") else 0,
                        float(event.get("confidence", 0.0)),
                        json.dumps(event.get("metadata", {}), sort_keys=True),
                        now,
                    ),
                )
                accepted += 1
            except sqlite3.IntegrityError:
                duplicates.append(event["event_id"])
        conn.commit()
    return accepted, duplicates


def fetch_events(store_id: str) -> list[dict[str, Any]]:
    canonical = resolve_store_id(store_id)
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE store_id = ? ORDER BY timestamp ASC, event_id ASC",
            (canonical,),
        ).fetchall()
    return [_row_to_event(row) for row in rows]


def fetch_all_last_event_times() -> dict[str, str]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT store_id, MAX(timestamp) AS last_ts FROM events GROUP BY store_id"
        ).fetchall()
    return {row["store_id"]: row["last_ts"] for row in rows}


def fetch_pos(store_id: str) -> list[dict[str, Any]]:
    canonical = resolve_store_id(store_id)
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM pos_transactions WHERE store_id = ? ORDER BY timestamp ASC",
            (canonical,),
        ).fetchall()
    return [dict(row) for row in rows]


def reset_db() -> None:
    with connection() as conn:
        conn.execute("DELETE FROM events")
        conn.execute("DELETE FROM pos_transactions")
        conn.commit()


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    event = dict(row)
    event["is_staff"] = bool(event["is_staff"])
    event["metadata"] = json.loads(event["metadata"] or "{}")
    return event
