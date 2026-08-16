from __future__ import annotations

import argparse
import logging
import logging.handlers
import math
import signal
import threading

from .config import Config, load_config
from .dashboard import Dashboard
from .events import EventDetector, NotificationPolicy
from .monitoring import MonitorHub
from .runner import SafeRunner
from .state import StateStore
from .telegram.bot import OutboxWorker, TelegramBot
from .telegram.client import TelegramClient
from .telegram.users import UserManager, validate_user_id


def configure_logging(config: Config) -> None:
    level = getattr(logging, config.log_level, logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if config.log_path:
        config.log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                config.log_path,
                maxBytes=2_000_000,
                backupCount=2,
            )
        )
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def build_runner(
    config: Config,
    monitors: MonitorHub,
    detector: EventDetector,
    notifications: NotificationPolicy,
) -> SafeRunner:
    oom_minutes = max(5, math.ceil(config.poll_interval / 60) + 1)

    def memory_check() -> None:
        notifications.publish(detector.check_memory(monitors.refresh_memory()))

    def temperature_check() -> None:
        notifications.publish(detector.check_temperature(monitors.refresh_temperature()))

    def power_check() -> None:
        monitors.refresh_power()
        notifications.publish(detector.check_resume())

    return SafeRunner(
        {
            "cpu": monitors.refresh_cpu,
            "memory": memory_check,
            "temperature": temperature_check,
            "power": power_check,
            "battery": lambda: notifications.publish(
                detector.check_battery(monitors.refresh_battery())
            ),
            **{
                f"storage:{path}": (
                    lambda monitored_path=path: notifications.publish(
                        detector.check_storage(monitors.refresh_storage(monitored_path))
                    )
                )
                for path in config.storage_paths
            },
            "services": lambda: notifications.publish(
                detector.check_services(monitors.refresh_services())
            ),
            "systemd": lambda: notifications.publish(
                detector.check_failed_units(monitors.refresh_systemd())
            ),
            "oom": lambda: notifications.publish(
                detector.check_oom(monitors.refresh_oom(oom_minutes))
            ),
        }
    )


def run(config_path: str | None = None) -> None:
    config = load_config(config_path)
    configure_logging(config)
    logger = logging.getLogger(__name__)
    state = StateStore(config.state_path)
    users = UserManager(config.allowlist_path, config.allowed_user_ids)
    monitors = MonitorHub(config.services, config.storage_paths)
    detector = EventDetector(state, config)
    notifications = NotificationPolicy(state, config)
    notifications.publish(detector.begin_power_session())

    dashboard = Dashboard(config, state, monitors)
    client = TelegramClient(config.token)
    bot = TelegramBot(client, dashboard, state, users)
    outbox = OutboxWorker(client, state, config.chat_id)
    runner = build_runner(config, monitors, detector, notifications)
    stop = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        logger.info("received signal %s; stopping", signum)
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    threads = [
        threading.Thread(target=bot.run, args=(stop,), name="telegram-poller", daemon=True),
        threading.Thread(target=outbox.run, args=(stop,), name="telegram-outbox", daemon=True),
    ]
    for thread in threads:
        thread.start()
    logger.info("monitor started")
    try:
        while not stop.is_set():
            runner.run_once()
            stop.wait(config.poll_interval)
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=6)
        detector.end_power_session()
        logger.info("monitor stopped cleanly")


def main() -> None:
    parser = argparse.ArgumentParser(description="Linux laptop monitor")
    parser.add_argument("--config", help="path to TOML configuration")
    management = parser.add_mutually_exclusive_group()
    management.add_argument("--allow-user", type=int, metavar="USER_ID")
    management.add_argument("--remove-user", type=int, metavar="USER_ID")
    management.add_argument("--list-users", action="store_true")
    args = parser.parse_args()
    try:
        if args.allow_user is not None or args.remove_user is not None or args.list_users:
            config = load_config(args.config, require_token=False)
            users = UserManager(config.allowlist_path, config.allowed_user_ids)
            if args.allow_user is not None:
                user_id = validate_user_id(args.allow_user)
                print(f"{'added' if users.add_user(user_id) else 'already allowed'}: {user_id}")
            elif args.remove_user is not None:
                user_id = validate_user_id(args.remove_user)
                print(f"{'removed' if users.remove_user(user_id) else 'not present'}: {user_id}")
            else:
                for user_id in users.list_users():
                    print(user_id)
            return
        run(args.config)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
