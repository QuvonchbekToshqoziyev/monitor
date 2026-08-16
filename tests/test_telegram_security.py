from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from linux_monitor.config import load_config
from linux_monitor.main import main
from linux_monitor.state import StateStore
from linux_monitor.telegram.auth import AuthorizationGate, extract_sender_user_id
from linux_monitor.telegram.bot import TelegramBot
from linux_monitor.telegram.users import UserManager


def message(user_id: object, text: str, username: str = "person", chat_id: int = 900) -> dict:
    return {
        "update_id": 1,
        "message": {
            "from": {"id": user_id, "username": username},
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


def callback(user_id: object, data: str = "view:cpu", chat_id: int = 900) -> dict:
    return {
        "update_id": 2,
        "callback_query": {
            "id": "callback-1",
            "from": {"id": user_id, "username": "person"},
            "data": data,
            "message": {"message_id": 44, "chat": {"id": chat_id, "type": "private"}},
        },
    }


class TelegramBoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.users = UserManager(root / "users.json", (100,))
        self.state = StateStore(root / "state.json")
        self.client = Mock()
        self.dashboard = Mock()
        self.dashboard.render.return_value = "dashboard"
        self.bot = TelegramBot(self.client, self.dashboard, self.state, self.users)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_allowed_user_can_use_every_command(self) -> None:
        for command in ("/start", "/status", "/help"):
            with self.subTest(command=command):
                self.client.reset_mock()
                self.dashboard.reset_mock()
                self.dashboard.render.return_value = "dashboard"
                self.bot.handle(message(100, command, chat_id=700))
                self.client.send.assert_called_once()
                self.assertEqual(self.client.send.call_args.args[0], 700)

    def test_allowed_user_can_use_dashboard_callback(self) -> None:
        self.bot.handle(callback(100, "view:temperature", chat_id=701))
        self.client.answer_callback.assert_called_once_with("callback-1")
        self.client.edit.assert_called_once()
        self.assertEqual(self.client.edit.call_args.args[:2], (701, 44))
        self.dashboard.render.assert_called_once_with("temperature")

    def test_unknown_messages_are_completely_silent(self) -> None:
        before = self.state.pending()
        for text in ("/start", "/status", "/help", "hello", "/future-action"):
            self.bot.handle(message(999, text))
        self.assertEqual(self.client.mock_calls, [])
        self.dashboard.render.assert_not_called()
        self.assertEqual(self.state.pending(), before)

    def test_unknown_callback_is_not_acknowledged_or_routed(self) -> None:
        self.bot.handle(callback(999))
        self.assertEqual(self.client.mock_calls, [])
        self.dashboard.render.assert_not_called()

    def test_authorization_precedes_all_routing(self) -> None:
        with patch.object(self.bot, "_route_authorized") as route:
            self.bot.handle(message(999, "/start"))
            route.assert_not_called()
            self.bot.handle(callback(999))
            route.assert_not_called()
            self.bot.handle(message(100, "/start"))
            route.assert_called_once()

    def test_unknown_future_update_cannot_reach_router(self) -> None:
        update = {"inline_query": {"id": "q", "from": {"id": 999}, "query": "run"}}
        with patch.object(self.bot, "_route_authorized") as route:
            self.bot.handle(update)
            route.assert_not_called()

    def test_username_never_grants_access(self) -> None:
        self.bot.handle(message(999, "/start", username="authorized-name"))
        self.assertEqual(self.client.mock_calls, [])
        self.assertFalse(self.users.is_allowed(999))

    def test_changed_username_does_not_remove_access(self) -> None:
        self.bot.handle(message(100, "/status", username="completely-new-name"))
        self.client.send.assert_called_once()

    def test_missing_and_malformed_sender_ids_fail_closed(self) -> None:
        malformed = (
            {},
            {"message": {"chat": {"id": 1, "type": "private"}, "text": "/start"}},
            message("100", "/start"),
            message(True, "/start"),
            message(-100, "/start"),
        )
        for update in malformed:
            self.bot.handle(update)
        self.assertEqual(self.client.mock_calls, [])
        self.dashboard.render.assert_not_called()

    def test_forged_callback_data_cannot_bypass_sender_check(self) -> None:
        self.bot.handle(callback(999, "view:cpu:user_id=100"))
        self.assertEqual(self.client.mock_calls, [])

    def test_forged_composite_update_uses_routed_callback_sender(self) -> None:
        update = callback(999)
        update["message"] = message(100, "/status")["message"]
        self.bot.handle(update)
        self.assertEqual(self.client.mock_calls, [])
        self.dashboard.render.assert_not_called()

    def test_allowed_user_in_group_does_not_expose_monitoring(self) -> None:
        update = message(100, "/status")
        update["message"]["chat"]["type"] = "group"
        self.bot.handle(update)
        self.assertEqual(self.client.mock_calls, [])
        self.dashboard.render.assert_not_called()


class IdentityExtractionTest(unittest.TestCase):
    def test_known_update_shapes_use_numeric_sender_only(self) -> None:
        self.assertEqual(extract_sender_user_id(message(123, "/start")), 123)
        self.assertEqual(extract_sender_user_id(callback(123)), 123)
        self.assertEqual(extract_sender_user_id({"poll_answer": {"user": {"id": 123}}}), 123)
        self.assertIsNone(extract_sender_user_id({"callback_query": {"data": "user_id=123"}}))

    def test_gate_is_default_deny(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gate = AuthorizationGate(UserManager(Path(directory) / "users.json"))
            self.assertFalse(gate.allows(message(123, "/start")))


class UserManagerTest(unittest.TestCase):
    def test_add_remove_duplicate_and_restart_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.json"
            users = UserManager(path, (100,))
            self.assertTrue(users.add_user(200))
            self.assertFalse(users.add_user(200))
            self.assertEqual(users.list_users(), (100, 200))
            restarted = UserManager(path, (999,))
            self.assertEqual(restarted.list_users(), (100, 200))
            self.assertTrue(restarted.remove_user(100))
            self.assertFalse(restarted.remove_user(100))
            self.assertEqual(UserManager(path).list_users(), (200,))

    def test_malformed_ids_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            users = UserManager(Path(directory) / "users.json")
            for value in ("100", 0, -1, True, None, 1.5):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    users.add_user(value)
                self.assertFalse(users.is_allowed(value))

    def test_corrupt_allowlist_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.json"
            path.write_text("not-json", encoding="utf-8")
            with self.assertRaises(ValueError):
                UserManager(path, (100,))

    def test_insecure_allowlist_permissions_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.json"
            path.write_text('{"allowed_user_ids": [100]}', encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                UserManager(path)

    def test_allowlist_file_is_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.json"
            UserManager(path, (100,))
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)


class AllowlistConfigTest(unittest.TestCase):
    def test_config_rejects_malformed_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                '[telegram]\nchat_id = 1\nallowed_user_ids = ["100"]\n[monitor]\n',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_config(path, require_token=False)

    def test_local_cli_manages_users_without_bot_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            users_path = root / "users.json"
            config_path.write_text(
                "\n".join(
                    (
                        "[telegram]",
                        "chat_id = 1",
                        "allowed_user_ids = [100]",
                        f'allowlist_path = "{users_path}"',
                        "[monitor]",
                    )
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            with patch("sys.argv", ["linux-monitor", "--config", str(config_path), "--allow-user", "200"]):
                with redirect_stdout(output):
                    main()
            self.assertEqual(UserManager(users_path).list_users(), (100, 200))
            self.assertIn("added: 200", output.getvalue())


if __name__ == "__main__":
    unittest.main()
