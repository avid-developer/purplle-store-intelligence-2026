from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any


EVENT_NAMESPACE = uuid.UUID("a6e1bfca-a584-49e6-9d0a-558a72ddff38")


def visitor_token(seed: str) -> str:
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
    return f"VIS_{digest}"


def deterministic_event_id(*parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    return str(uuid.uuid5(EVENT_NAMESPACE, raw))


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def make_event(
    *,
    store_id: str,
    camera_id: str,
    visitor_id: str,
    event_type: str,
    timestamp: datetime,
    zone_id: str | None = None,
    dwell_ms: int = 0,
    is_staff: bool = False,
    confidence: float = 0.8,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = metadata or {}
    event_id = deterministic_event_id(store_id, camera_id, visitor_id, event_type, iso_utc(timestamp), zone_id or "", dwell_ms, metadata.get("session_seq", ""))
    return {
        "event_id": event_id,
        "store_id": store_id,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": iso_utc(timestamp),
        "zone_id": zone_id,
        "dwell_ms": int(dwell_ms),
        "is_staff": bool(is_staff),
        "confidence": round(float(confidence), 3),
        "metadata": metadata,
    }

