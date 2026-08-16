from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path


def boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()


def boot_time() -> datetime:
    for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines():
        if line.startswith("btime "):
            return datetime.fromtimestamp(int(line.split()[1])).astimezone()
    raise RuntimeError("kernel boot time is unavailable")


class SuspendProbe:
    """Detects resume by comparing clocks that do and do not include suspend time."""

    def __init__(self, minimum_gap_seconds: float = 30):
        self.minimum_gap = minimum_gap_seconds
        self._wall = time.time()
        self._monotonic = time.monotonic()

    def resumed_after(self) -> float | None:
        wall = time.time()
        monotonic = time.monotonic()
        gap = (wall - self._wall) - (monotonic - self._monotonic)
        self._wall, self._monotonic = wall, monotonic
        return gap if gap >= self.minimum_gap else None

