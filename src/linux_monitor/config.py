from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path


_SERVICE_NAME = re.compile(r"^[A-Za-z0-9_.@:-]+$")


@dataclass(frozen=True)
class Config:
    token: str
    chat_id: int
    allowed_user_ids: tuple[int, ...]
    allowlist_path: Path
    poll_interval: int
    battery_thresholds: tuple[int, ...]
    storage_critical_free_percent: float
    storage_recovery_free_percent: float
    temperature_critical_celsius: float
    temperature_recovery_celsius: float
    memory_critical_percent: float
    memory_recovery_percent: float
    memory_sustained_polls: int
    notify_charger_events: bool
    notify_storage_recovery: bool
    notify_resume: bool
    services: tuple[str, ...]
    storage_paths: tuple[str, ...]
    state_path: Path
    log_level: str
    log_path: Path | None


def _expand_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def load_config(path: str | Path | None = None, require_token: bool = True) -> Config:
    config_path = _expand_path(
        str(path or os.environ.get("LINUX_MONITOR_CONFIG", "config.toml"))
    )
    try:
        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ValueError(
            f"configuration file not found: {config_path}; copy config.example.toml"
        ) from exc

    telegram = raw.get("telegram", {})
    monitor = raw.get("monitor", {})
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if require_token and not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is required")

    try:
        chat_id = int(os.environ.get("TELEGRAM_CHAT_ID", telegram.get("chat_id")))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("telegram.chat_id must be an integer ID") from exc
    allowed_values = telegram.get("allowed_user_ids", [])
    if not isinstance(allowed_values, list) or any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in allowed_values
    ):
        raise ValueError("telegram.allowed_user_ids must contain positive integer IDs")
    allowed_user_ids = tuple(sorted(set(allowed_values)))
    allowlist_path = _expand_path(
        str(telegram.get("allowlist_path", "~/.local/state/linux-monitor/telegram-users.json"))
    )

    poll_interval = int(monitor.get("poll_interval_seconds", 60))
    thresholds = tuple(sorted({int(v) for v in monitor.get("battery_thresholds", [20, 10, 5])}, reverse=True))
    free_percent = float(monitor.get("storage_critical_free_percent", 10))
    storage_recovery = float(monitor.get("storage_recovery_free_percent", 12))
    temperature_critical = float(monitor.get("temperature_critical_celsius", 90))
    temperature_recovery = float(monitor.get("temperature_recovery_celsius", 80))
    memory_critical = float(monitor.get("memory_critical_percent", 90))
    memory_recovery = float(monitor.get("memory_recovery_percent", 85))
    memory_sustained = int(monitor.get("memory_sustained_polls", 3))
    services = tuple(str(v) for v in monitor.get("services", []))
    storage_paths = tuple(str(v) for v in monitor.get("storage_paths", ["/"]))

    if poll_interval < 15:
        raise ValueError("monitor.poll_interval_seconds must be at least 15")
    if not thresholds or any(value <= 0 or value >= 100 for value in thresholds):
        raise ValueError("battery thresholds must be between 1 and 99")
    if not 0 < free_percent < 100:
        raise ValueError("storage_critical_free_percent must be between 0 and 100")
    if not free_percent < storage_recovery < 100:
        raise ValueError("storage_recovery_free_percent must be above the critical threshold")
    if not temperature_recovery < temperature_critical:
        raise ValueError("temperature_recovery_celsius must be below the critical threshold")
    if not 0 < memory_recovery < memory_critical < 100:
        raise ValueError("memory thresholds must satisfy 0 < recovery < critical < 100")
    if memory_sustained < 1:
        raise ValueError("memory_sustained_polls must be at least 1")
    if not storage_paths:
        raise ValueError("at least one storage path is required")
    invalid_services = [name for name in services if not _SERVICE_NAME.fullmatch(name)]
    if invalid_services:
        raise ValueError(f"invalid systemd service name: {invalid_services[0]}")

    state_path = _expand_path(
        str(monitor.get("state_path", "~/.local/state/linux-monitor/state.json"))
    )
    log_path_value = monitor.get("log_path")
    return Config(
        token=token,
        chat_id=chat_id,
        allowed_user_ids=allowed_user_ids,
        allowlist_path=allowlist_path,
        poll_interval=poll_interval,
        battery_thresholds=thresholds,
        storage_critical_free_percent=free_percent,
        storage_recovery_free_percent=storage_recovery,
        temperature_critical_celsius=temperature_critical,
        temperature_recovery_celsius=temperature_recovery,
        memory_critical_percent=memory_critical,
        memory_recovery_percent=memory_recovery,
        memory_sustained_polls=memory_sustained,
        notify_charger_events=bool(monitor.get("notify_charger_events", False)),
        notify_storage_recovery=bool(monitor.get("notify_storage_recovery", True)),
        notify_resume=bool(monitor.get("notify_resume", True)),
        services=services,
        storage_paths=storage_paths,
        state_path=state_path,
        log_level=str(monitor.get("log_level", "INFO")).upper(),
        log_path=_expand_path(str(log_path_value)) if log_path_value else None,
    )
