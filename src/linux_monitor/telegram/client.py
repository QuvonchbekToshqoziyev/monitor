from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, token: str, request_timeout: int = 15):
        self._base = f"https://api.telegram.org/bot{token}/"
        self.request_timeout = request_timeout

    def _call(self, method: str, data: dict[str, Any], timeout: int | None = None) -> Any:
        encoded: dict[str, str | int] = {}
        for key, value in data.items():
            encoded[key] = json.dumps(value) if isinstance(value, (dict, list)) else value
        request = urllib.request.Request(
            self._base + method,
            data=urllib.parse.urlencode(encoded).encode(),
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.request_timeout) as response:
                payload = json.load(response)
        except (urllib.error.URLError, socket.timeout, TimeoutError, UnicodeError, json.JSONDecodeError):
            raise TelegramError(f"Telegram {method} request failed") from None
        if not payload.get("ok"):
            description = str(payload.get("description", "API error"))
            raise TelegramError(f"Telegram {method}: {description[:160]}")
        return payload.get("result")

    def get_updates(self, offset: int, timeout: int = 30) -> list[dict[str, Any]]:
        return self._call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=timeout + 5,
        )

    def send(self, chat_id: int, text: str, keyboard: dict[str, Any] | None = None) -> Any:
        data: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if keyboard:
            data["reply_markup"] = keyboard
        return self._call("sendMessage", data)

    def edit(self, chat_id: int, message_id: int, text: str, keyboard: dict[str, Any]) -> Any:
        return self._call(
            "editMessageText",
            {"chat_id": chat_id, "message_id": message_id, "text": text, "reply_markup": keyboard},
        )

    def answer_callback(self, callback_id: str) -> Any:
        return self._call("answerCallbackQuery", {"callback_query_id": callback_id})
