from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev
from typing import Any

from app.models import parse_timestamp
from app.storage import load_layout


def _event_dt(event: dict[str, Any]) -> datetime:
    return parse_timestamp(event["timestamp"])


def _pos_dt(row: dict[str, Any]) -> datetime:
    return parse_timestamp(row["timestamp"])


def _non_staff(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if not event.get("is_staff")]


def _group_by_visitor(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in _non_staff(events):
        grouped[event["visitor_id"]].append(event)
    for visitor_events in grouped.values():
        visitor_events.sort(key=_event_dt)
    return grouped


def converted_visitors(events: list[dict[str, Any]], pos: list[dict[str, Any]]) -> set[str]:
    grouped = _group_by_visitor(events)
    pos_times = [_pos_dt(row) for row in pos]
    converted: set[str] = set()
    for visitor_id, visitor_events in grouped.items():
        if any(event.get("metadata", {}).get("converted") is True for event in visitor_events):
            converted.add(visitor_id)
            continue
        billing_times = [
            _event_dt(event)
            for event in visitor_events
            if event["event_type"] in {"BILLING_QUEUE_JOIN", "ZONE_ENTER", "ZONE_DWELL"}
            and (event.get("zone_id") or "").upper() in {"BILLING", "CASH_COUNTER", "CHECKOUT"}
        ]
        for billing_time in billing_times:
            if any(timedelta(0) <= pos_time - billing_time <= timedelta(minutes=5) for pos_time in pos_times):
                converted.add(visitor_id)
                break
    return converted


def compute_metrics(store_id: str, events: list[dict[str, Any]], pos: list[dict[str, Any]]) -> dict[str, Any]:
    visitors = _group_by_visitor(events)
    unique_visitors = len(visitors)
    converted = converted_visitors(events, pos)
    zone_dwell: dict[str, list[int]] = defaultdict(list)
    queue_depth = 0
    queue_joins = 0
    queue_abandons = 0

    for event in _non_staff(events):
        metadata = event.get("metadata", {})
        if event["event_type"] == "ZONE_DWELL" and event.get("zone_id"):
            zone_dwell[event["zone_id"]].append(int(event.get("dwell_ms") or 0))
        if event["event_type"] == "BILLING_QUEUE_JOIN":
            queue_joins += 1
            queue_depth = int(metadata.get("queue_depth") or queue_depth or 0)
        if event["event_type"] == "BILLING_QUEUE_ABANDON":
            queue_abandons += 1

    avg_dwell_by_zone = {
        zone: {
            "avg_dwell_ms": round(mean(values), 2),
            "samples": len(values),
        }
        for zone, values in sorted(zone_dwell.items())
    }
    conversion_rate = 0.0 if unique_visitors == 0 else round(len(converted) / unique_visitors, 4)
    abandonment_rate = 0.0 if queue_joins == 0 else round(queue_abandons / queue_joins, 4)

    return {
        "store_id": store_id,
        "unique_visitors": unique_visitors,
        "conversion_rate": conversion_rate,
        "converted_visitors": len(converted),
        "avg_dwell_by_zone": avg_dwell_by_zone,
        "current_queue_depth": queue_depth,
        "billing_abandonment_rate": abandonment_rate,
        "event_count": len(events),
        "pos_transaction_count": len(pos),
    }


def compute_funnel(store_id: str, events: list[dict[str, Any]], pos: list[dict[str, Any]]) -> dict[str, Any]:
    grouped = _group_by_visitor(events)
    converted = converted_visitors(events, pos)
    stages = {
        "entry": 0,
        "zone_visit": 0,
        "billing_queue": 0,
        "purchase": 0,
    }
    reentries = 0

    for visitor_id, visitor_events in grouped.items():
        event_types = {event["event_type"] for event in visitor_events}
        zones = {(event.get("zone_id") or "").upper() for event in visitor_events}
        if "ENTRY" in event_types or event_types:
            stages["entry"] += 1
        if any(event["event_type"] in {"ZONE_ENTER", "ZONE_DWELL"} and (event.get("zone_id") or "").upper() not in {"BILLING", "CASH_COUNTER"} for event in visitor_events):
            stages["zone_visit"] += 1
        if "BILLING_QUEUE_JOIN" in event_types or "BILLING" in zones or "CASH_COUNTER" in zones:
            stages["billing_queue"] += 1
        if visitor_id in converted:
            stages["purchase"] += 1
        if "REENTRY" in event_types:
            reentries += 1

    ordered = ["entry", "zone_visit", "billing_queue", "purchase"]
    dropoffs: dict[str, float] = {}
    for prev, current in zip(ordered, ordered[1:]):
        base = stages[prev]
        dropoffs[f"{prev}_to_{current}"] = 0.0 if base == 0 else round((base - stages[current]) / base, 4)

    return {
        "store_id": store_id,
        "unit": "session",
        "stages": stages,
        "dropoff": dropoffs,
        "reentry_sessions": reentries,
    }


def compute_heatmap(store_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    visitors = _group_by_visitor(events)
    zone_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"visits": 0, "dwell_ms": 0})
    for event in _non_staff(events):
        zone_id = event.get("zone_id")
        if not zone_id or (zone_id or "").upper() in {"BILLING", "CASH_COUNTER"}:
            continue
        if event["event_type"] in {"ZONE_ENTER", "ZONE_DWELL"}:
            zone_stats[zone_id]["visits"] += 1
            zone_stats[zone_id]["dwell_ms"] += int(event.get("dwell_ms") or 0)

    layout = load_layout()
    for zone in layout.get("stores", {}).get(store_id, {}).get("zones", []):
        zone_stats[zone["zone_id"]]

    raw_scores = {}
    for zone_id, stats in zone_stats.items():
        visits = stats["visits"]
        avg_dwell = stats["dwell_ms"] / visits if visits else 0
        raw_scores[zone_id] = visits * 0.6 + (avg_dwell / 1000.0) * 0.4
    max_score = max(raw_scores.values(), default=0)

    zones = []
    for zone_id, stats in sorted(zone_stats.items()):
        visits = stats["visits"]
        avg_dwell = 0 if visits == 0 else stats["dwell_ms"] / visits
        zones.append(
            {
                "zone_id": zone_id,
                "visits": visits,
                "avg_dwell_ms": round(avg_dwell, 2),
                "score": 0 if max_score == 0 else round(raw_scores[zone_id] / max_score * 100, 2),
            }
        )
    return {
        "store_id": store_id,
        "data_confidence": "LOW" if len(visitors) < 20 else "OK",
        "session_count": len(visitors),
        "zones": zones,
    }


def compute_anomalies(store_id: str, events: list[dict[str, Any]], pos: list[dict[str, Any]]) -> dict[str, Any]:
    anomalies: list[dict[str, Any]] = []
    non_staff = _non_staff(events)
    if not non_staff:
        return {"store_id": store_id, "anomalies": []}

    queue_depths = [
        int(event.get("metadata", {}).get("queue_depth") or 0)
        for event in non_staff
        if event["event_type"] == "BILLING_QUEUE_JOIN"
    ]
    latest_queue = queue_depths[-1] if queue_depths else 0
    if latest_queue >= 5 or (len(queue_depths) >= 4 and latest_queue > mean(queue_depths) + 2 * pstdev(queue_depths)):
        anomalies.append(
            {
                "type": "BILLING_QUEUE_SPIKE",
                "severity": "CRITICAL" if latest_queue >= 8 else "WARN",
                "observed_value": latest_queue,
                "suggested_action": "Open another billing counter or move staff to checkout until queue depth normalises.",
            }
        )

    metrics = compute_metrics(store_id, events, pos)
    latest_ts = max(_event_dt(event) for event in non_staff)
    recent_events = [event for event in non_staff if latest_ts - _event_dt(event) <= timedelta(hours=1)]
    if recent_events and metrics["unique_visitors"] >= 5:
        recent_conversion = compute_metrics(store_id, recent_events, pos)["conversion_rate"]
        baseline = metrics["conversion_rate"]
        if baseline >= 0.1 and recent_conversion < baseline * 0.65:
            anomalies.append(
                {
                    "type": "CONVERSION_DROP",
                    "severity": "WARN",
                    "observed_value": recent_conversion,
                    "baseline": baseline,
                    "suggested_action": "Check assortment availability and billing queue friction for the current hour.",
                }
            )

    heatmap = compute_heatmap(store_id, events)
    recent_zone_visits = {
        event.get("zone_id")
        for event in non_staff
        if event.get("zone_id")
        and latest_ts - _event_dt(event) <= timedelta(minutes=30)
        and event["event_type"] in {"ZONE_ENTER", "ZONE_DWELL"}
    }
    for zone in heatmap["zones"]:
        if zone["visits"] > 0 and zone["zone_id"] not in recent_zone_visits:
            anomalies.append(
                {
                    "type": "DEAD_ZONE",
                    "severity": "INFO",
                    "zone_id": zone["zone_id"],
                    "suggested_action": "Review shelf visibility, staff coverage, or in-store offers for this zone.",
                }
            )

    return {"store_id": store_id, "anomalies": anomalies}


def compute_health(last_event_times: dict[str, str]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    stores = {}
    stale = False
    for store_id, timestamp in sorted(last_event_times.items()):
        parsed = parse_timestamp(timestamp)
        lag_seconds = max(0, math.floor((now - parsed).total_seconds()))
        status = "STALE_FEED" if lag_seconds > 600 else "OK"
        stale = stale or status == "STALE_FEED"
        stores[store_id] = {
            "last_event_timestamp": timestamp,
            "lag_seconds": lag_seconds,
            "status": status,
        }
    return {
        "status": "DEGRADED" if stale else "OK",
        "checked_at": now.isoformat().replace("+00:00", "Z"),
        "stores": stores,
    }

