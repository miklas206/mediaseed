"""Explicit, bounded NordicBytes event observations; no inferred upload factors."""
import time
from datetime import datetime


def global_events(data, now=None):
    events = data.get("events")
    rows = events.get("global") if isinstance(events, dict) else None
    if not isinstance(rows, list) or len(rows) > 20:
        return None
    now = time.time() if now is None else now
    result = []
    for row in rows:
        if not isinstance(row, dict) or row.get("type") != "global_freeleech":
            continue
        end = row.get("ends_at")
        try:
            if not isinstance(end, str) or len(end) > 80:
                raise ValueError()
            parsed = datetime.fromisoformat(end.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError()
            timestamp = parsed.timestamp()
        except (ValueError, OverflowError):
            return None
        if timestamp > now:
            result.append({"type": "global_freeleech", "endsAt": parsed.isoformat()})
    return result
