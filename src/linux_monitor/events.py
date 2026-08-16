from __future__ import annotations

import time
from dataclasses import dataclass

from .config import Config
from .monitors.battery import BatteryInfo
from .monitors.oom import OomEvent
from .monitors.power import SuspendProbe, boot_id
from .monitors.storage import StorageInfo
from .monitors.system import MemoryInfo
from .state import StateStore


@dataclass(frozen=True)
class MonitorEvent:
    event_id: str
    kind: str
    message: str


class NotificationPolicy:
    """The only layer that decides which detected events enter Telegram's outbox."""

    def __init__(self, state: StateStore, config: Config):
        self.state = state
        self.config = config

    def publish(self, events: list[MonitorEvent]) -> int:
        optional = {
            "charger_connected": self.config.notify_charger_events,
            "charger_disconnected": self.config.notify_charger_events,
            "storage_recovered": self.config.notify_storage_recovery,
            "resume": self.config.notify_resume,
        }
        queued = 0
        for event in events:
            if optional.get(event.kind, True) and self.state.enqueue(event.event_id, event.message):
                queued += 1
        return queued


class EventDetector:
    """Turns state transitions into events without knowing about Telegram."""

    def __init__(self, state: StateStore, config: Config):
        self.state = state
        self.config = config
        self.suspend = SuspendProbe()

    def begin_power_session(self) -> list[MonitorEvent]:
        events: list[MonitorEvent] = []
        current = boot_id()
        previous = self.state.get("power", {})
        history = list(previous.get("history", []))
        if previous.get("boot_id") and previous["boot_id"] != current:
            unexpected = not previous.get("clean_shutdown", False)
            outcome = "unexpected shutdown detected" if unexpected else "normal boot"
            events.append(
                MonitorEvent(
                    f"boot:{current}",
                    "unexpected_boot" if unexpected else "boot",
                    "⚠️ The previous session ended unexpectedly. The laptop has booted again."
                    if unexpected
                    else "🔌 The laptop has booted.",
                )
            )
            history.append({"at": int(time.time()), "event": outcome})
        elif not previous.get("boot_id"):
            history.append({"at": int(time.time()), "event": "monitor started"})
        self.state.set(
            "power",
            {"boot_id": current, "clean_shutdown": False, "history": history[-8:]},
        )
        return events

    def end_power_session(self) -> None:
        power = self.state.get("power", {})
        power["clean_shutdown"] = True
        power.setdefault("history", []).append({"at": int(time.time()), "event": "clean stop"})
        power["history"] = power["history"][-8:]
        self.state.set("power", power)

    def check_resume(self) -> list[MonitorEvent]:
        elapsed = self.suspend.resumed_after()
        if elapsed is None:
            return []
        now = int(time.time())
        power = self.state.get("power", {})
        power.setdefault("history", []).append({"at": now, "event": "resumed"})
        power["history"] = power["history"][-8:]
        self.state.set("power", power)
        return [
            MonitorEvent(
                f"resume:{now}",
                "resume",
                f"⏯️ Laptop resumed after about {max(1, round(elapsed / 60))} minute(s).",
            )
        ]

    def check_battery(self, battery: BatteryInfo | None) -> list[MonitorEvent]:
        if battery is None:
            return []
        events: list[MonitorEvent] = []
        alerted = set(self.state.get("battery_alerted", []))
        alerted = {threshold for threshold in alerted if battery.percent <= threshold}
        if battery.status.lower() == "discharging":
            crossed = {threshold for threshold in self.config.battery_thresholds if battery.percent <= threshold}
            new = crossed - alerted
            if new:
                threshold = min(new)
                events.append(
                    MonitorEvent(
                        f"battery:{threshold}",
                        "battery_threshold",
                        f"🔋 Battery is at {battery.percent}% (threshold {threshold}%).",
                    )
                )
                alerted.update(crossed)
        else:
            alerted.clear()
        self.state.set("battery_alerted", sorted(alerted, reverse=True))

        connection = self._charger_connection(battery.status)
        previous = self.state.get("charger_connected")
        if connection is not None and previous is not None and connection != previous:
            events.append(
                MonitorEvent(
                    f"charger:{'connected' if connection else 'disconnected'}:{int(time.time())}",
                    "charger_connected" if connection else "charger_disconnected",
                    "🔌 Charger connected." if connection else "🔋 Charger disconnected.",
                )
            )
        if connection is not None:
            self.state.set("charger_connected", connection)
        return events

    @staticmethod
    def _charger_connection(status: str) -> bool | None:
        normalized = status.lower()
        if normalized == "discharging":
            return False
        if normalized in {"charging", "full", "not charging"}:
            return True
        return None

    def check_temperature(self, sensors: list[tuple[str, float]]) -> list[MonitorEvent]:
        if not sensors:
            return []
        hottest = max(value for _, value in sensors)
        critical = bool(self.state.get("temperature_critical", False))
        if not critical and hottest >= self.config.temperature_critical_celsius:
            self.state.set("temperature_critical", True)
            return [
                MonitorEvent(
                    "temperature:critical",
                    "temperature_critical",
                    f"🌡️ CPU temperature is critical: {hottest:.1f}°C.",
                )
            ]
        if critical and hottest <= self.config.temperature_recovery_celsius:
            self.state.set("temperature_critical", False)
            return [
                MonitorEvent(
                    "temperature:recovered",
                    "temperature_recovered",
                    f"✅ CPU temperature returned to normal: {hottest:.1f}°C.",
                )
            ]
        return []

    def check_memory(self, memory: MemoryInfo) -> list[MonitorEvent]:
        status = self.state.get("memory_condition", {"critical": False, "consecutive": 0})
        critical = bool(status.get("critical", False))
        consecutive = int(status.get("consecutive", 0))
        events: list[MonitorEvent] = []
        if memory.used_percent >= self.config.memory_critical_percent:
            consecutive += 1
            if not critical and consecutive >= self.config.memory_sustained_polls:
                critical = True
                events.append(
                    MonitorEvent(
                        "memory:critical",
                        "memory_critical",
                        f"🧠 RAM usage is critically high: {memory.used_percent:.1f}%.",
                    )
                )
        elif memory.used_percent <= self.config.memory_recovery_percent:
            consecutive = 0
            if critical:
                critical = False
                events.append(
                    MonitorEvent(
                        "memory:recovered",
                        "memory_recovered",
                        f"✅ RAM usage returned to normal: {memory.used_percent:.1f}%.",
                    )
                )
        elif not critical:
            consecutive = 0
        self.state.set("memory_condition", {"critical": critical, "consecutive": consecutive})
        return events

    def check_storage(self, storage: StorageInfo) -> list[MonitorEvent]:
        statuses = self.state.get("storage_low", {})
        was_low = bool(statuses.get(storage.path, False))
        is_low = (
            storage.free_percent < self.config.storage_recovery_free_percent
            if was_low
            else storage.free_percent <= self.config.storage_critical_free_percent
        )
        events: list[MonitorEvent] = []
        if is_low and not was_low:
            events.append(
                MonitorEvent(
                    f"storage:low:{storage.path}",
                    "storage_critical",
                    f"💾 Storage is critical on {storage.path}: {storage.free_percent:.1f}% free.",
                )
            )
        elif was_low and not is_low:
            events.append(
                MonitorEvent(
                    f"storage:recovered:{storage.path}",
                    "storage_recovered",
                    f"✅ Storage recovered on {storage.path}: {storage.free_percent:.1f}% free.",
                )
            )
        statuses[storage.path] = is_low
        self.state.set("storage_low", statuses)
        return events

    def check_services(self, current: dict[str, str]) -> list[MonitorEvent]:
        previous = self.state.get("service_states", {})
        events: list[MonitorEvent] = []
        for name, service_state in current.items():
            old = previous.get(name)
            if old == service_state:
                continue
            if service_state == "active":
                if old is not None:
                    events.append(
                        MonitorEvent(f"service:{name}:active", "service_recovered", f"✅ Service recovered: {name}.")
                    )
            else:
                events.append(
                    MonitorEvent(
                        f"service:{name}:{service_state}",
                        "service_unhealthy",
                        f"⚠️ Service {name} is {service_state}.",
                    )
                )
        self.state.set("service_states", current)
        return events

    def check_failed_units(self, units: list[str]) -> list[MonitorEvent]:
        previous = set(self.state.get("failed_units", []))
        current = set(units)
        events = [
            MonitorEvent(f"systemd:failed:{unit}", "systemd_failed", f"⚠️ New failed systemd unit: {unit}.")
            for unit in sorted(current - previous)
        ]
        self.state.set("failed_units", sorted(current))
        return events

    def check_oom(self, events: list[OomEvent]) -> list[MonitorEvent]:
        seen = list(self.state.get("oom_seen", []))
        known = set(seen)
        detected: list[MonitorEvent] = []
        for event in events:
            if event.event_id in known:
                continue
            detected.append(
                MonitorEvent(f"oom:{event.event_id}", "oom", "🚨 Linux OOM killer activity was detected.")
            )
            seen.append(event.event_id)
            known.add(event.event_id)
        self.state.set("oom_seen", seen[-200:])
        return detected
