from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass


_OOM = re.compile(r"out of memory|oom-kill|killed process", re.IGNORECASE)


@dataclass(frozen=True)
class OomEvent:
    event_id: str
    timestamp: str


def recent_oom_events(minutes: int = 5) -> list[OomEvent]:
    result = subprocess.run(
        ["journalctl", "-k", "--no-pager", "-o", "json", "--since", f"-{minutes}min"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip() or "journalctl failed")
    events: list[OomEvent] = []
    for line in result.stdout.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = str(item.get("MESSAGE", ""))
        if not _OOM.search(message):
            continue
        timestamp = str(item.get("__REALTIME_TIMESTAMP", "unknown"))
        raw_id = str(item.get("__CURSOR") or f"{timestamp}:{message}")
        event_id = hashlib.sha256(raw_id.encode()).hexdigest()[:20]
        events.append(OomEvent(event_id, timestamp))
    return events

