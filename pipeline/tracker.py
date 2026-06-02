from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CAMERA_ROLES = {
    "CAM 1": {"camera_id": "CAM_MAIN_01", "role": "main_floor", "zones": ["GOOD_VIBES", "DERMDOC", "MAKEUP_UNIT"]},
    "CAM 2": {"camera_id": "CAM_MAIN_02", "role": "main_floor", "zones": ["LAKME", "FACES_CANADA", "MAYBELLINE"]},
    "CAM 3": {"camera_id": "CAM_ENTRY_01", "role": "entry", "zones": ["ENTRY"]},
    "CAM 4": {"camera_id": "CAM_STAFF_01", "role": "staff_backroom", "zones": ["STOCK_ROOM"]},
    "CAM 5": {"camera_id": "CAM_BILLING_01", "role": "billing", "zones": ["CASH_COUNTER"]},
    "ENTRY 1": {"camera_id": "CAM_ENTRY_01", "role": "entry", "zones": ["ENTRY"]},
    "ENTRY 2": {"camera_id": "CAM_ENTRY_02", "role": "entry", "zones": ["ENTRY"]},
    "BILLING_AREA": {"camera_id": "CAM_BILLING_01", "role": "billing", "zones": ["CASH_COUNTER"]},
    "BILLING": {"camera_id": "CAM_BILLING_01", "role": "billing", "zones": ["CASH_COUNTER"]},
    "ZONE": {"camera_id": "CAM_MAIN_01", "role": "main_floor", "zones": ["GOOD_VIBES", "DERMDOC", "MAKEUP_UNIT", "FACES_CANADA"]},
}


@dataclass(frozen=True)
class ClipInfo:
    path: Path
    camera_id: str
    role: str
    zones: list[str]
    duration_seconds: float
    fps: float
    width: int
    height: int
    created_at: datetime


def camera_profile(path: Path) -> dict[str, Any]:
    stem = path.stem.upper()
    for key, profile in CAMERA_ROLES.items():
        if key in stem:
            return profile
    return {"camera_id": stem.replace(" ", "_"), "role": "unknown", "zones": ["FOH"]}


def probe_clip(path: Path) -> ClipInfo:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_entries",
        "format=duration:format_tags=creation_time:stream=width,height,r_frame_rate",
        str(path),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)
    video_stream = next((stream for stream in payload.get("streams", []) if stream.get("width")), {})
    duration = float(payload.get("format", {}).get("duration") or 0)
    created = payload.get("format", {}).get("tags", {}).get("creation_time")
    created_at = datetime.now(timezone.utc) if not created else datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(timezone.utc)
    fps = _parse_rate(video_stream.get("r_frame_rate", "0/1"))
    profile = camera_profile(path)
    return ClipInfo(
        path=path,
        camera_id=profile["camera_id"],
        role=profile["role"],
        zones=profile["zones"],
        duration_seconds=duration,
        fps=fps,
        width=int(video_stream.get("width") or 0),
        height=int(video_stream.get("height") or 0),
        created_at=created_at,
    )


def probe_clips(clips_dir: Path) -> list[ClipInfo]:
    clips = sorted(clips_dir.rglob("*.mp4"))
    return [probe_clip(path) for path in clips]


def sample_people_counts(path: Path, sample_every_seconds: int = 10, max_samples: int = 18) -> list[dict[str, Any]]:
    """Best-effort person count from sampled frames.

    OpenCV HOG is intentionally optional. The API and pipeline still run without it, but
    when the package is available this gives the generated event stream a real video signal
    that changes with the input clips instead of relying only on POS timing.
    """
    try:
        import cv2  # type: ignore
    except Exception:
        return []

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return []
    fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    total_seconds = int(total_frames / fps) if fps else 0
    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    samples = []
    for second in range(0, max(total_seconds, 1), max(1, sample_every_seconds)):
        if len(samples) >= max_samples:
            break
        capture.set(cv2.CAP_PROP_POS_MSEC, second * 1000)
        ok, frame = capture.read()
        if not ok:
            continue
        resized = cv2.resize(frame, (640, 360))
        rects, weights = hog.detectMultiScale(resized, winStride=(8, 8), padding=(8, 8), scale=1.05)
        confident = [float(weight) for weight in weights if float(weight) > 0.2]
        samples.append(
            {
                "second": second,
                "count": len(confident),
                "avg_confidence": round(sum(confident) / len(confident), 3) if confident else 0.35,
            }
        )
    capture.release()
    return samples


def _parse_rate(value: str) -> float:
    if "/" not in value:
        return float(value or 0)
    numerator, denominator = value.split("/", 1)
    denominator_f = float(denominator or 1)
    return 0.0 if denominator_f == 0 else float(numerator) / denominator_f
