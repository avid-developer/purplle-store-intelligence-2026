from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path


def read_events(path: Path) -> list[dict]:
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def post_batch(api_url: str, events: list[dict]) -> dict:
    request = urllib.request.Request(
        api_url.rstrip("/") + "/events/ingest",
        data=json.dumps({"events": events}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest JSONL events into the Store Intelligence API.")
    parser.add_argument("events", type=Path)
    parser.add_argument("--api-url", default="http://localhost:8000")
    args = parser.parse_args()
    events = read_events(args.events)
    totals = {"accepted": 0, "duplicates": 0, "rejected": 0}
    for start in range(0, len(events), 100):
        result = post_batch(args.api_url, events[start : start + 100])
        totals["accepted"] += result.get("accepted", 0)
        totals["duplicates"] += result.get("duplicates", 0)
        totals["rejected"] += len(result.get("rejected", []))
    print(json.dumps(totals, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

