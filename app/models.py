from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


EVENT_TYPES = {
    "ENTRY",
    "EXIT",
    "ZONE_ENTER",
    "ZONE_EXIT",
    "ZONE_DWELL",
    "BILLING_QUEUE_JOIN",
    "BILLING_QUEUE_ABANDON",
    "REENTRY",
}

INSTANT_EVENTS = {"ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT", "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY"}
ZONE_EVENTS = {"ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL", "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON"}


@dataclass(frozen=True)
class ValidationError:
    index: int
    event_id: str | None
    code: str
    message: str


def parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty ISO-8601 string")
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def isoformat_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_event(raw: Any, index: int = 0) -> tuple[dict[str, Any] | None, ValidationError | None]:
    if not isinstance(raw, dict):
        return None, ValidationError(index, None, "invalid_type", "event must be a JSON object")

    event_id = raw.get("event_id")
    required_string_fields = ["event_id", "store_id", "camera_id", "visitor_id", "event_type", "timestamp"]
    for field in required_string_fields:
        if not isinstance(raw.get(field), str) or not raw.get(field).strip():
            return None, ValidationError(index, event_id if isinstance(event_id, str) else None, "missing_field", f"{field} is required")

    event_type = raw["event_type"]
    if event_type not in EVENT_TYPES:
        return None, ValidationError(index, event_id, "invalid_event_type", f"{event_type} is not supported")

    try:
        timestamp = parse_timestamp(raw["timestamp"])
    except Exception as exc:
        return None, ValidationError(index, event_id, "invalid_timestamp", str(exc))

    zone_id = raw.get("zone_id")
    if event_type in ZONE_EVENTS and not zone_id:
        return None, ValidationError(index, event_id, "missing_zone", f"{event_type} requires zone_id")
    if zone_id is not None and not isinstance(zone_id, str):
        return None, ValidationError(index, event_id, "invalid_zone", "zone_id must be a string or null")

    dwell_ms = raw.get("dwell_ms", 0)
    if not isinstance(dwell_ms, int) or dwell_ms < 0:
        return None, ValidationError(index, event_id, "invalid_dwell", "dwell_ms must be a non-negative integer")
    if event_type in INSTANT_EVENTS and event_type != "ZONE_DWELL" and dwell_ms < 0:
        return None, ValidationError(index, event_id, "invalid_dwell", "instantaneous events cannot have negative dwell")

    is_staff = raw.get("is_staff", False)
    if not isinstance(is_staff, bool):
        return None, ValidationError(index, event_id, "invalid_staff_flag", "is_staff must be true or false")

    confidence = raw.get("confidence", 0.0)
    if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
        return None, ValidationError(index, event_id, "invalid_confidence", "confidence must be between 0 and 1")

    metadata = raw.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        return None, ValidationError(index, event_id, "invalid_metadata", "metadata must be an object")

    normalized = {
        "event_id": event_id.strip(),
        "store_id": raw["store_id"].strip(),
        "camera_id": raw["camera_id"].strip(),
        "visitor_id": raw["visitor_id"].strip(),
        "event_type": event_type,
        "timestamp": isoformat_utc(timestamp),
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": float(confidence),
        "metadata": metadata,
    }
    return normalized, None

