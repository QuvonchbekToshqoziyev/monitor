from __future__ import annotations

from typing import Any


def dashboard_keyboard() -> dict[str, Any]:
    def button(text: str, view: str) -> dict[str, str]:
        return {"text": text, "callback_data": f"view:{view}"}

    return {
        "inline_keyboard": [
            [button("System Health", "health"), button("CPU", "cpu")],
            [button("Memory", "memory"), button("Battery", "battery")],
            [button("Temperature", "temperature"), button("Storage", "storage")],
            [button("Services", "services"), button("Systemd", "systemd")],
            [button("Power", "power"), button("Refresh", "health")],
        ]
    }
