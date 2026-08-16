from __future__ import annotations

import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime

from .monitors.battery import BatteryInfo, battery_info
from .monitors.oom import OomEvent, recent_oom_events
from .monitors.power import boot_time
from .monitors.services import service_states
from .monitors.storage import StorageInfo, storage_info
from .monitors.system import CpuProbe, MemoryInfo, load_average, memory_info, temperatures, uptime_seconds
from .monitors.systemd import failed_units


@dataclass(frozen=True)
class CpuState:
    usage_percent: float
    load: tuple[float, float, float]


@dataclass(frozen=True)
class PowerState:
    uptime_seconds: float
    boot_time: datetime


@dataclass
class MachineState:
    sampled_at: dict[str, float] = field(default_factory=dict)
    cpu: CpuState | None = None
    memory: MemoryInfo | None = None
    temperatures: list[tuple[str, float]] = field(default_factory=list)
    battery: BatteryInfo | None = None
    storage: dict[str, StorageInfo] = field(default_factory=dict)
    power: PowerState | None = None
    services: dict[str, str] = field(default_factory=dict)
    failed_units: list[str] = field(default_factory=list)
    oom_events: list[OomEvent] = field(default_factory=list)


class MonitorHub:
    """The single probe/state interface used by background checks and Telegram."""

    def __init__(self, services: tuple[str, ...], storage_paths: tuple[str, ...]):
        self.services = services
        self.storage_paths = storage_paths
        self.cpu_probe = CpuProbe()
        self._state = MachineState()
        self._lock = threading.RLock()

    def _store(self, name: str, value: object) -> None:
        with self._lock:
            setattr(self._state, name, value)
            self._state.sampled_at[name] = time.time()

    def snapshot(self) -> MachineState:
        with self._lock:
            return deepcopy(self._state)

    def refresh_cpu(self) -> CpuState:
        value = CpuState(self.cpu_probe.usage_percent(), load_average())
        self._store("cpu", value)
        return value

    def refresh_memory(self) -> MemoryInfo:
        value = memory_info()
        self._store("memory", value)
        return value

    def refresh_temperature(self) -> list[tuple[str, float]]:
        value = temperatures()
        self._store("temperatures", value)
        return value

    def refresh_battery(self) -> BatteryInfo | None:
        value = battery_info()
        self._store("battery", value)
        return value

    def refresh_storage(self, path: str) -> StorageInfo:
        value = storage_info(path)
        with self._lock:
            self._state.storage[path] = value
            self._state.sampled_at[f"storage:{path}"] = time.time()
        return value

    def refresh_power(self) -> PowerState:
        value = PowerState(uptime_seconds(), boot_time())
        self._store("power", value)
        return value

    def refresh_services(self) -> dict[str, str]:
        value = service_states(self.services)
        self._store("services", value)
        return value

    def refresh_systemd(self) -> list[str]:
        value = failed_units()
        self._store("failed_units", value)
        return value

    def refresh_oom(self, minutes: int) -> list[OomEvent]:
        value = recent_oom_events(minutes)
        self._store("oom_events", value)
        return value

