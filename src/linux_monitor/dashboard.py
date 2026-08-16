from __future__ import annotations

import math
from datetime import datetime
from typing import Callable

from .config import Config
from .monitoring import MonitorHub
from .monitors.battery import BatteryInfo
from .monitors.storage import StorageInfo
from .monitors.system import human_bytes, human_duration
from .state import StateStore


class Dashboard:
    def __init__(self, config: Config, state: StateStore, monitors: MonitorHub):
        self.config = config
        self.state = state
        self.monitors = monitors

    @staticmethod
    def _safe(label: str, probe: Callable[[], str]) -> str:
        try:
            return probe()
        except Exception:
            return f"{label}: unavailable"

    def render(self, view: str) -> str:
        renderers = {
            "health": self.health,
            "cpu": self.cpu,
            "memory": self.memory,
            "battery": self.battery,
            "temperature": self.temperature,
            "storage": self.storage,
            "services": self.services,
            "systemd": self.systemd,
            "power": self.power,
        }
        return renderers.get(view, self.health)()

    def health(self) -> str:
        lines = ["SYSTEM HEALTH", ""]
        lines.append(self._safe("CPU", lambda: f"CPU: {self.monitors.refresh_cpu().usage_percent:.1f}%"))

        def memory_lines() -> str:
            info = self.monitors.refresh_memory()
            return f"RAM: {human_bytes(info.used)} / {human_bytes(info.total)}\nSwap: {human_bytes(info.swap_used)}"

        lines.append(self._safe("Memory", memory_lines))

        def temperature_line() -> str:
            sensors = self.monitors.refresh_temperature()
            return "Temperature: unavailable" if not sensors else f"Temperature: {max(v for _, v in sensors):.1f}°C"

        lines.append(self._safe("Temperature", temperature_line))

        def disk_line() -> str:
            disk = self.monitors.refresh_storage(self.config.storage_paths[0])
            return f"Disk: {human_bytes(disk.free)} free"

        lines.append(self._safe("Disk", disk_line))

        def battery_line() -> str:
            battery = self.monitors.refresh_battery()
            return "Battery: unavailable" if battery is None else f"Battery: {battery.percent}% {battery.status.lower()}"

        lines.append(self._safe("Battery", battery_line))
        lines.append(
            self._safe(
                "Uptime",
                lambda: f"Uptime: {human_duration(self.monitors.refresh_power().uptime_seconds)}",
            )
        )

        def failed_line() -> str:
            services = self.monitors.refresh_services()
            failed_services = sum(state != "active" for state in services.values())
            failed_units = len(self.monitors.refresh_systemd())
            return f"Failed services/units: {failed_services}/{failed_units}"

        lines.append(self._safe("Failed services/units", failed_line))
        return "\n".join(lines)

    def cpu(self) -> str:
        try:
            info = self.monitors.refresh_cpu()
        except Exception:
            return "CPU\n\nCPU information is unavailable."
        return "\n".join(
            [
                "CPU",
                "",
                f"Usage: {info.usage_percent:.1f}%",
                "Load (1/5/15m): " + " / ".join(f"{value:.2f}" for value in info.load),
            ]
        )

    def memory(self) -> str:
        try:
            info = self.monitors.refresh_memory()
        except Exception:
            return "Memory\n\nMemory information is unavailable."
        lines = [
            "Memory",
            "",
            f"RAM used: {human_bytes(info.used)} / {human_bytes(info.total)} ({info.used_percent:.1f}%)",
            f"RAM available: {human_bytes(info.available)}",
            f"Swap used: {human_bytes(info.swap_used)} / {human_bytes(info.swap_total)}",
        ]
        try:
            minutes = max(5, math.ceil(self.config.poll_interval / 60) + 1)
            lines.append(f"Recent OOM events: {len(self.monitors.refresh_oom(minutes))}")
        except Exception:
            lines.append("Recent OOM events: unavailable")
        return "\n".join(lines)

    def battery(self) -> str:
        def render(info: BatteryInfo | None) -> str:
            if info is None:
                return "Battery\n\nNo battery interface is available."
            lines = ["Battery", "", f"Charge: {info.percent}%", f"Status: {info.status}"]
            if info.health_percent is not None:
                lines.append(f"Health/capacity: {info.health_percent:.1f}% of design capacity")
            elif info.current_full is not None:
                lines.append(f"Current full capacity: {info.current_full} {info.unit}")
            else:
                lines.append("Health/capacity: unavailable")
            return "\n".join(lines)

        try:
            return render(self.monitors.refresh_battery())
        except Exception:
            return "Battery\n\nBattery information is unavailable."

    def temperature(self) -> str:
        try:
            sensors = self.monitors.refresh_temperature()
        except Exception:
            sensors = []
        if not sensors:
            return "Temperature\n\nNo readable temperature sensors are available."
        return "Temperature\n\n" + "\n".join(f"{name}: {value:.1f}°C" for name, value in sensors[:20])

    @staticmethod
    def _storage_lines(info: StorageInfo) -> list[str]:
        return [
            f"{info.path}: {human_bytes(info.used)} / {human_bytes(info.total)} used",
            f"Free: {human_bytes(info.free)}",
            f"Usage: {info.used_percent:.1f}%",
        ]

    def storage(self) -> str:
        lines = ["Storage", ""]
        for path in self.config.storage_paths:
            try:
                lines.extend(self._storage_lines(self.monitors.refresh_storage(path)))
            except Exception:
                lines.append(f"{path}: unavailable")
            lines.append("")
        return "\n".join(lines).rstrip()

    def services(self) -> str:
        if not self.config.services:
            return "Services\n\nNo services are configured."
        try:
            states = self.monitors.refresh_services()
        except Exception:
            return "Services\n\nService information is unavailable."
        lines = ["Services", ""]
        for name, service_state in states.items():
            marker = "✓" if service_state == "active" else "✗"
            lines.append(f"{marker} {name}: {service_state}")
        return "\n".join(lines)

    def systemd(self) -> str:
        try:
            units = self.monitors.refresh_systemd()
        except Exception:
            return "Systemd\n\nFailed-unit information is unavailable."
        lines = ["Systemd", "", f"Failed units: {len(units)}"]
        lines.extend(f"• {unit}" for unit in units[:20])
        if len(units) > 20:
            lines.append(f"…and {len(units) - 20} more")
        return "\n".join(lines)

    def power(self) -> str:
        lines = ["Power", ""]

        def power_lines() -> str:
            current = self.monitors.refresh_power()
            return (
                f"Current uptime: {human_duration(current.uptime_seconds)}\n"
                f"Last boot: {current.boot_time.strftime('%Y-%m-%d %H:%M:%S %Z')}"
            )

        lines.append(self._safe("Power", power_lines))
        history = self.state.get("power", {}).get("history", [])
        if history:
            lines.extend(["", "Recent monitor events:"])
            for item in history[-5:]:
                stamp = datetime.fromtimestamp(item["at"]).astimezone().strftime("%m-%d %H:%M")
                lines.append(f"• {stamp} — {item['event']}")
        return "\n".join(lines)
