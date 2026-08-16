from __future__ import annotations

from typing import Any

from .users import UserManager


_SENDER_LOCATIONS = (
    ("callback_query", "from"),
    ("message", "from"),
    ("edited_message", "from"),
    ("channel_post", "from"),
    ("edited_channel_post", "from"),
    ("business_connection", "user"),
    ("business_message", "from"),
    ("edited_business_message", "from"),
    ("inline_query", "from"),
    ("chosen_inline_result", "from"),
    ("shipping_query", "from"),
    ("pre_checkout_query", "from"),
    ("poll_answer", "user"),
    ("my_chat_member", "from"),
    ("chat_member", "from"),
    ("chat_join_request", "from"),
    ("message_reaction", "user"),
    ("purchased_paid_media", "from"),
)


def extract_sender_user_id(update: dict[str, Any]) -> int | None:
    """Extract only Telegram's immutable numeric sender identity."""
    if not isinstance(update, dict):
        return None
    for field, sender_field in _SENDER_LOCATIONS:
        if field not in update:
            continue
        payload = update.get(field)
        if not isinstance(payload, dict):
            return None
        sender = payload.get(sender_field)
        if not isinstance(sender, dict):
            return None
        user_id = sender.get("id")
        if isinstance(user_id, int) and not isinstance(user_id, bool) and user_id > 0:
            return user_id
        return None
    return None


class AuthorizationGate:
    """Mandatory entry boundary before any Telegram update routing."""

    def __init__(self, users: UserManager):
        self.users = users

    def allows(self, update: dict[str, Any]) -> bool:
        user_id = extract_sender_user_id(update)
        return user_id is not None and self.users.is_allowed(user_id)
