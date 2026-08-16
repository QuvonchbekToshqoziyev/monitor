from __future__ import annotations

import logging
import threading
from typing import Any

from ..dashboard import Dashboard
from ..state import StateStore
from .auth import AuthorizationGate
from .client import TelegramClient, TelegramError
from .keyboard import dashboard_keyboard
from .users import UserManager


class TelegramBot:
    def __init__(
        self,
        client: TelegramClient,
        dashboard: Dashboard,
        state: StateStore,
        users: UserManager,
    ):
        self.client = client
        self.dashboard = dashboard
        self.state = state
        self.authorization = AuthorizationGate(users)
        self.logger = logging.getLogger(__name__)

    def run(self, stop: threading.Event) -> None:
        backoff = 2
        while not stop.is_set():
            offset = int(self.state.get("telegram_offset", 0))
            try:
                updates = self.client.get_updates(offset)
                for update in updates:
                    update_id = int(update.get("update_id", 0))
                    try:
                        self.handle(update)
                    except Exception:
                        self.logger.exception("failed to handle Telegram update %s", update_id)
                    self.state.set("telegram_offset", update_id + 1)
                backoff = 2
            except TelegramError as exc:
                self.logger.warning("%s; retrying", exc)
                stop.wait(backoff)
                backoff = min(backoff * 2, 60)

    def handle(self, update: dict[str, Any]) -> None:
        if not self.authorization.allows(update):
            return
        self._route_authorized(update)

    def _route_authorized(self, update: dict[str, Any]) -> None:
        """All present and future Telegram handlers must remain behind handle()."""
        if "callback_query" in update:
            callback = update["callback_query"]
            data = str(callback.get("data", ""))
            view = data.removeprefix("view:") if data.startswith("view:") else "health"
            message = callback.get("message", {})
            chat_id = self._private_chat_id(message)
            if chat_id is None:
                return
            self.client.answer_callback(str(callback["id"]))
            try:
                self.client.edit(
                    chat_id,
                    int(message["message_id"]),
                    self.dashboard.render(view),
                    dashboard_keyboard(),
                )
            except TelegramError as exc:
                # Telegram rejects edits when the text is unchanged; the dashboard remains valid.
                self.logger.info("dashboard edit skipped: %s", exc)
            return

        message = update.get("message", {})
        chat_id = self._private_chat_id(message)
        if chat_id is None:
            return
        text = str(message.get("text", "")).strip()
        command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if command in ("/start", "/status"):
            self.client.send(chat_id, self.dashboard.render("health"), dashboard_keyboard())
        elif command == "/help":
            self.client.send(
                chat_id,
                "Linux Monitor\n\n/start — open dashboard\n/status — system health\n/help — commands",
                dashboard_keyboard(),
            )

    @staticmethod
    def _private_chat_id(message: object) -> int | None:
        if not isinstance(message, dict):
            return None
        chat = message.get("chat")
        if not isinstance(chat, dict) or chat.get("type") != "private":
            return None
        chat_id = chat.get("id")
        return chat_id if isinstance(chat_id, int) and not isinstance(chat_id, bool) else None


class OutboxWorker:
    def __init__(self, client: TelegramClient, state: StateStore, chat_id: int):
        self.client = client
        self.state = state
        self.chat_id = chat_id
        self.logger = logging.getLogger(__name__)

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            pending = self.state.pending()
            if not pending:
                stop.wait(5)
                continue
            item = pending[0]
            try:
                self.client.send(self.chat_id, str(item["text"]))
                self.state.delivered(str(item["id"]))
            except TelegramError as exc:
                self.logger.warning("%s; alert remains queued", exc)
                stop.wait(15)
