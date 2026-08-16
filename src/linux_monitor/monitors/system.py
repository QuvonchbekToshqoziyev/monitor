from __future__ import annotations

import glob
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path


def _read_proc_stat() -> tuple[int, int]:
    fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()[1:]
    values = [int(value) for value in fields]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


class CpuProbe:
    def __init__(self):
        self._previous: tuple[int, int] | None = None
        self._lock = threading.Lock()

    def usage_percent(self) -> float:
        with self._lock:
            current = _read_proc_stat()
            if self._previous is None:
                self._previous = current
                time.sleep(0.1)
                current = _read_proc_stat()
            total_delta = current[0] - self._previous[0]
            idle_delta = current[1] - self._previous[1]
            self._previous = current
            return 0.0 if total_delta <= 0 else round(100 * (1 - idle_delta / total_delta), 1)


@dataclass(frozen=True)
class MemoryInfo:
    total: int
    available: int
    swap_total: int
    swap_free: int

    @property
    def used(self) -> int:
        return self.total - self.available

    @property
    def swap_used(self) -> int:
        return self.swap_total - self.swap_free

    @property
    def used_percent(self) -> float:
        return round(100 * self.used / self.total, 1) if self.total else 0.0


def memory_info() -> MemoryInfo:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        name, raw = line.split(":", 1)
        values[name] = int(raw.strip().split()[0]) * 1024
    return MemoryInfo(
        total=values["MemTotal"],
        available=values.get("MemAvailable", values.get("MemFree", 0)),
        swap_total=values.get("SwapTotal", 0),
        swap_free=values.get("SwapFree", 0),
    )


def uptime_seconds() -> float:
    return float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])


def load_average() -> tuple[float, float, float]:
    return os.getloadavg()


def temperatures() -> list[tuple[str, float]]:
    found: list[tuple[str, float]] = []
    for raw_path in glob.glob("/sys/class/hwmon/hwmon*/temp*_input"):
        path = Path(raw_path)
        try:
            value = float(path.read_text(encoding="utf-8").strip())
            celsius = value / 1000 if abs(value) > 500 else value
            if not -20 <= celsius <= 150:
                continue
            label_path = path.with_name(path.name.replace("_input", "_label"))
            chip_path = path.parent / "name"
            label = label_path.read_text(encoding="utf-8").strip() if label_path.exists() else path.stem
            chip = chip_path.read_text(encoding="utf-8").strip() if chip_path.exists() else "sensor"
            name = f"{chip} {label}"
            if any(
                marker in name.lower()
                for marker in ("coretemp", "k10temp", "zenpower", "cpu", "package", "tctl", "tdie")
            ):
                found.append((name, round(celsius, 1)))
        except (OSError, ValueError):
            continue
    if found:
        return found
    for index, raw_path in enumerate(glob.glob("/sys/class/thermal/thermal_zone*/temp"), 1):
        try:
            path = Path(raw_path)
            zone_type = (path.parent / "type").read_text(encoding="utf-8").strip()
            if not any(marker in zone_type.lower() for marker in ("cpu", "pkg", "x86", "soc")):
                continue
            value = float(path.read_text(encoding="utf-8").strip())
            celsius = value / 1000 if abs(value) > 500 else value
            if -20 <= celsius <= 150:
                found.append((zone_type or f"thermal zone {index}", round(celsius, 1)))
        except (OSError, ValueError):
            continue
    return found


def human_bytes(value: int | float) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(amount) < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{amount:.0f} B"
        amount /= 1024
    raise AssertionError("unreachable")


def human_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    days, total = divmod(total, 86400)
    hours, total = divmod(total, 3600)
    minutes, _ = divmod(total, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)
