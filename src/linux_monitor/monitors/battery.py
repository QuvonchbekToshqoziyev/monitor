from __future__ import annotations

import glob
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BatteryInfo:
    percent: int
    status: str
    current_full: int | None = None
    design_full: int | None = None
    unit: str | None = None

    @property
    def health_percent(self) -> float | None:
        if self.current_full is None or not self.design_full:
            return None
        return round(100 * self.current_full / self.design_full, 1)


def _integer(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def battery_info() -> BatteryInfo | None:
    batteries = sorted(glob.glob("/sys/class/power_supply/BAT*"))
    if not batteries:
        return None
    root = Path(batteries[0])
    percent = _integer(root / "capacity")
    if percent is None:
        return None
    try:
        status = (root / "status").read_text(encoding="utf-8").strip()
    except OSError:
        status = "Unknown"
    for prefix, unit in (("energy", "uWh"), ("charge", "uAh")):
        current = _integer(root / f"{prefix}_full")
        design = _integer(root / f"{prefix}_full_design")
        if current is not None or design is not None:
            return BatteryInfo(percent, status, current, design, unit)
    return BatteryInfo(percent, status)

