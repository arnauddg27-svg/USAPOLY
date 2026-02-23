import json
import os
from datetime import datetime, timezone

from polyedge.paths import HEALTH_PATH


def _parse_timestamp(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None


def main() -> int:
    try:
        max_stale = int(os.getenv("HEALTH_MAX_STALE_SEC", "180"))
    except ValueError:
        max_stale = 180
    max_stale = max(1, max_stale)
    try:
        with open(HEALTH_PATH, encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return 1

    if not isinstance(payload, dict):
        return 1

    ts = _parse_timestamp(payload.get("timestamp", ""))
    if ts is None:
        return 1

    age_sec = (datetime.now(timezone.utc) - ts).total_seconds()
    if age_sec > max_stale:
        return 1

    status = str(payload.get("status", "")).lower()
    if status == "stopped":
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
