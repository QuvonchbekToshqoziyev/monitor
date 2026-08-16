from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path


def validate_user_id(user_id: object) -> int:
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("Telegram user ID must be a positive integer")
    return user_id


class UserManager:
    """Owns the persistent, default-deny Telegram user allowlist."""

    def __init__(self, path: Path, initial_users: tuple[int, ...] = ()):
        self.path = path
        self._lock = threading.RLock()
        seeds = {validate_user_id(user_id) for user_id in initial_users}
        if path.exists():
            self._users = self._load()
        else:
            self._users = seeds
            self._save()

    def is_allowed(self, user_id: object) -> bool:
        try:
            validated = validate_user_id(user_id)
        except ValueError:
            return False
        with self._lock:
            return validated in self._users

    def add_user(self, user_id: object) -> bool:
        validated = validate_user_id(user_id)
        with self._lock:
            if validated in self._users:
                return False
            self._users.add(validated)
            self._save()
            return True

    def remove_user(self, user_id: object) -> bool:
        validated = validate_user_id(user_id)
        with self._lock:
            if validated not in self._users:
                return False
            self._users.remove(validated)
            self._save()
            return True

    def list_users(self) -> tuple[int, ...]:
        with self._lock:
            return tuple(sorted(self._users))

    def _load(self) -> set[int]:
        try:
            details = self.path.lstat()
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
                raise ValueError
            if stat.S_IMODE(details.st_mode) & 0o077:
                raise ValueError
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            values = payload["allowed_user_ids"]
            if not isinstance(payload, dict) or not isinstance(values, list):
                raise ValueError
            return {validate_user_id(user_id) for user_id in values}
        except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"cannot safely load Telegram allowlist: {self.path}") from exc

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            payload = json.dumps(
                {"version": 1, "allowed_user_ids": sorted(self._users)},
                indent=2,
            )
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(temporary, flags, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                if temporary.exists():
                    temporary.unlink()
        except OSError as exc:
            raise ValueError(f"cannot safely persist Telegram allowlist: {self.path}") from exc
