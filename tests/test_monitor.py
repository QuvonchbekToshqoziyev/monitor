from __future__ import annotations

import logging
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from linux_monitor.config import Config
from linux_monitor.events import EventDetector, NotificationPolicy
from linux_monitor.monitoring import MonitorHub
from linux_monitor.main import build_runner
from linux_monitor.monitors.battery import BatteryInfo
from linux_monitor.monitors.services import service_state
from linux_monitor.monitors.storage import StorageInfo
from linux_monitor.monitors.system import MemoryInfo
from linux_monitor.runner import SafeRunner
from linux_monitor.state import StateStore


def make_config(state_path: Path, **changes: object) -> Config:
    config = Config(
        token="test",
        chat_id=1,
        allowed_user_ids=(1,),
        allowlist_path=state_path.with_name("telegram-users.json"),
        poll_interval=60,
        battery_thresholds=(20, 10, 5),
        storage_critical_free_percent=10,
        storage_recovery_free_percent=12,
        temperature_critical_celsius=90,
        temperature_recovery_celsius=80,
        memory_critical_percent=90,
        memory_recovery_percent=85,
        memory_sustained_polls=3,
        notify_charger_events=False,
        notify_storage_recovery=True,
        notify_resume=True,
        services=("ssh.service",),
        storage_paths=("/",),
        state_path=state_path,
        log_level="INFO",
        log_path=None,
    )
    return replace(config, **changes)


class DetectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.state = StateStore(Path(self.temporary.name) / "state.json")
        self.config = make_config(self.state.path)
        self.detector = EventDetector(self.state, self.config)
        self.notifications = NotificationPolicy(self.state, self.config)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def ids(self) -> list[str]:
        return [item["id"] for item in self.state.pending()]

    def publish(self, events: list[object]) -> None:
        self.notifications.publish(events)  # type: ignore[arg-type]

    def test_alert_deduplication(self) -> None:
        disk = StorageInfo("/", 1000, 950, 50)
        self.publish(self.detector.check_storage(disk))
        restarted_state = StateStore(self.state.path)
        restarted = EventDetector(restarted_state, self.config)
        NotificationPolicy(restarted_state, self.config).publish(restarted.check_storage(disk))
        self.assertEqual(self.ids(), ["storage:low:/"])

    def test_battery_thresholds(self) -> None:
        for percent in (19, 18, 9, 4):
            self.publish(self.detector.check_battery(BatteryInfo(percent, "Discharging")))
        self.assertEqual(self.ids(), ["battery:20", "battery:10", "battery:5"])

    def test_battery_threshold_rearms_after_recovery(self) -> None:
        self.publish(self.detector.check_battery(BatteryInfo(20, "Discharging")))
        self.state.delivered("battery:20")
        self.publish(self.detector.check_battery(BatteryInfo(25, "Discharging")))
        self.publish(self.detector.check_battery(BatteryInfo(20, "Discharging")))
        self.assertEqual(self.ids(), ["battery:20"])

    def test_storage_threshold_and_recovery(self) -> None:
        self.publish(self.detector.check_storage(StorageInfo("/", 1000, 900, 100)))
        self.publish(self.detector.check_storage(StorageInfo("/", 1000, 890, 110)))
        self.publish(self.detector.check_storage(StorageInfo("/", 1000, 910, 90)))
        self.publish(self.detector.check_storage(StorageInfo("/", 1000, 870, 130)))
        self.assertEqual(self.ids(), ["storage:low:/", "storage:recovered:/"])

    def test_service_state_changes(self) -> None:
        for service_status in ("active", "failed", "failed", "active"):
            self.publish(self.detector.check_services({"ssh.service": service_status}))
        self.assertEqual(
            self.ids(),
            ["service:ssh.service:failed", "service:ssh.service:active"],
        )

    def test_temperature_transition_and_recovery(self) -> None:
        for value in (89, 91, 92, 80):
            self.publish(self.detector.check_temperature([("package", value)]))
        self.assertEqual(self.ids(), ["temperature:critical", "temperature:recovered"])

    def test_memory_must_remain_critical(self) -> None:
        critical = MemoryInfo(total=1000, available=50, swap_total=0, swap_free=0)
        recovered = MemoryInfo(total=1000, available=200, swap_total=0, swap_free=0)
        for _ in range(3):
            self.publish(self.detector.check_memory(critical))
        self.publish(self.detector.check_memory(critical))
        self.publish(self.detector.check_memory(recovered))
        self.assertEqual(self.ids(), ["memory:critical", "memory:recovered"])

    def test_charger_event_is_detected_but_optional(self) -> None:
        self.publish(self.detector.check_battery(BatteryInfo(70, "Discharging")))
        events = self.detector.check_battery(BatteryInfo(71, "Charging"))
        self.assertEqual([event.kind for event in events], ["charger_connected"])
        self.publish(events)
        self.assertEqual(self.ids(), [])


class MonitorHubTest(unittest.TestCase):
    @patch("linux_monitor.monitoring.memory_info")
    def test_refresh_maintains_current_state(self, probe: Mock) -> None:
        expected = MemoryInfo(total=1000, available=400, swap_total=100, swap_free=50)
        probe.return_value = expected
        hub = MonitorHub((), ("/",))
        self.assertEqual(hub.refresh_memory(), expected)
        snapshot = hub.snapshot()
        self.assertEqual(snapshot.memory, expected)
        self.assertIn("memory", snapshot.sampled_at)

    def test_background_runner_contains_every_monitor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(Path(directory) / "state.json")
            config = make_config(state.path)
            hub = MonitorHub(config.services, config.storage_paths)
            detector = EventDetector(state, config)
            runner = build_runner(config, hub, detector, NotificationPolicy(state, config))
            self.assertEqual(
                set(runner.checks),
                {"cpu", "memory", "temperature", "power", "battery", "storage:/", "services", "systemd", "oom"},
            )


class SafeRunnerTest(unittest.TestCase):
    def test_monitor_failure_does_not_stop_other_checks(self) -> None:
        called: list[str] = []

        def broken() -> None:
            raise RuntimeError("sensor unavailable")

        logger = Mock(spec=logging.Logger)
        runner = SafeRunner(
            {"broken": broken, "healthy": lambda: called.append("healthy")},
            logger,
        )
        self.assertEqual(runner.run_once(), ["broken"])
        self.assertEqual(runner.run_once(), ["broken"])
        self.assertEqual(called, ["healthy", "healthy"])
        logger.exception.assert_called_once()


class ServiceProbeTest(unittest.TestCase):
    @patch("linux_monitor.monitors.services.subprocess.run")
    def test_user_service_uses_user_manager(self, run: object) -> None:
        run.return_value.stdout = "active\n"  # type: ignore[attr-defined]
        run.return_value.returncode = 0  # type: ignore[attr-defined]
        self.assertEqual(service_state("user:linux-monitor.service"), "active")
        self.assertEqual(
            run.call_args.args[0],  # type: ignore[attr-defined]
            ["systemctl", "--user", "is-active", "linux-monitor.service"],
        )


if __name__ == "__main__":
    unittest.main()
