from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


RESOURCE_EVENT_NAMESPACE = uuid.UUID("99e5b706-7956-43e0-8c9d-8f06b81d8e78")

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

RESOURCE_EVENT_TYPE_MAP = {
    "entry": "ENTRY",
    "exit": "EXIT",
    "zone_entered": "ZONE_ENTER",
    "zone_exited": "ZONE_EXIT",
    "queue_completed": "BILLING_QUEUE_JOIN",
    "queue_abandoned": "BILLING_QUEUE_ABANDON",
}


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

    adapted = adapt_resource_center_event(raw, index)
    if adapted is not None:
        raw = adapted

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


def adapt_resource_center_event(raw: dict[str, Any], index: int = 0) -> dict[str, Any] | None:
    """Accept the June 2026 Resource Center sample-event dialect.

    The updated `sample_events.jsonl` does not use the strict PDF field names. It is
    still authoritative challenge input, so ingestion normalizes it into the internal
    event schema while preserving the original fields in metadata.
    """
    if all(field in raw for field in ["event_id", "store_id", "camera_id", "visitor_id", "timestamp"]):
        return None

    source_type = raw.get("event_type")
    if not isinstance(source_type, str):
        return None
    mapped_type = RESOURCE_EVENT_TYPE_MAP.get(source_type.strip().lower())
    if not mapped_type:
        return None

    timestamp = _first_string(raw, ["timestamp", "event_timestamp", "event_time", "queue_join_ts", "queue_exit_ts"])
    if not timestamp:
        return None

    store_id = _normalise_store_id(_first_string(raw, ["store_id", "store_code"]) or "ST1008")
    camera_id = _first_string(raw, ["camera_id"]) or _camera_for_event(mapped_type)
    visitor_id = _resource_visitor_id(raw)
    zone_id = _resource_zone_id(raw, mapped_type)
    metadata = _resource_metadata(raw, source_type, index)
    dwell_ms = _resource_dwell_ms(raw)
    if source_type.strip().lower() == "queue_completed":
        metadata["converted"] = True

    confidence = raw.get("confidence")
    if not isinstance(confidence, (int, float)):
        confidence = 0.82 if mapped_type in {"ENTRY", "EXIT"} else 0.76

    return {
        "event_id": _resource_event_id(raw, index),
        "store_id": store_id,
        "camera_id": str(camera_id).strip(),
        "visitor_id": visitor_id,
        "event_type": mapped_type,
        "timestamp": timestamp,
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": bool(raw.get("is_staff", False)),
        "confidence": float(confidence),
        "metadata": metadata,
    }


def _first_string(raw: dict[str, Any], fields: list[str]) -> str | None:
    for field in fields:
        value = raw.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _normalise_store_id(value: str) -> str:
    stripped = value.strip()
    lowered = stripped.lower()
    if lowered.startswith("store_") and stripped.split("_", 1)[1].isdigit():
        return f"ST{stripped.split('_', 1)[1]}"
    return stripped


def _resource_visitor_id(raw: dict[str, Any]) -> str:
    value = raw.get("visitor_id") or raw.get("id_token")
    if isinstance(value, str) and value.strip():
        return value.strip()
    if raw.get("track_id") is not None:
        return f"TRACK_{raw['track_id']}"
    if raw.get("queue_event_id") is not None:
        return f"QUEUE_{str(raw['queue_event_id'])[:8]}"
    return f"VIS_{str(uuid.uuid5(RESOURCE_EVENT_NAMESPACE, json.dumps(raw, sort_keys=True, default=str)))[:8]}"


def _resource_event_id(raw: dict[str, Any], index: int) -> str:
    value = raw.get("event_id") or raw.get("queue_event_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    stable = json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)
    return str(uuid.uuid5(RESOURCE_EVENT_NAMESPACE, f"{index}:{stable}"))


def _resource_zone_id(raw: dict[str, Any], event_type: str) -> str | None:
    zone = _first_string(raw, ["zone_id"])
    if zone:
        return zone
    if event_type in {"BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON"}:
        return "CASH_COUNTER"
    if event_type in {"ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL"}:
        return "FOH"
    return None


def _resource_dwell_ms(raw: dict[str, Any]) -> int:
    value = raw.get("dwell_ms")
    if isinstance(value, int) and value >= 0:
        return value
    wait_seconds = raw.get("wait_seconds")
    if isinstance(wait_seconds, (int, float)) and wait_seconds >= 0:
        return int(wait_seconds * 1000)
    return 0


def _camera_for_event(event_type: str) -> str:
    if event_type in {"ENTRY", "EXIT", "REENTRY"}:
        return "CAM_ENTRY_01"
    if event_type in {"BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON"}:
        return "CAM_BILLING_01"
    return "CAM_MAIN_01"


def _resource_metadata(raw: dict[str, Any], source_type: str, index: int) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source_schema": "hackerearth_resource_center_sample",
        "source_event_type": source_type,
        "source_index": index,
    }
    for key in [
        "track_id",
        "gender_pred",
        "gender",
        "age_pred",
        "age",
        "age_bucket",
        "is_face_hidden",
        "group_id",
        "group_size",
        "zone_name",
        "zone_type",
        "is_revenue_zone",
        "zone_hotspot_x",
        "zone_hotspot_y",
        "queue_join_ts",
        "queue_served_ts",
        "queue_exit_ts",
        "queue_position_at_join",
        "abandoned",
    ]:
        if key in raw:
            metadata[key] = raw[key]
    if "queue_position_at_join" in raw:
        metadata["queue_depth"] = raw["queue_position_at_join"]
    return metadata
