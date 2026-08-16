from __future__ import annotations

import json
import os
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any


class StateStore:
    """Small, crash-safe JSON state store used for deduplication and the outbox."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {"version": 1, "outbox": []}
        self._load()

    def _load(self) -> None:
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self._data.update(loaded)
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError):
            # A damaged state file must not prevent monitoring. The next save replaces it.
            return

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return deepcopy(self._data.get(key, default))

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = deepcopy(value)
            self._save()

    def update(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._data.update(deepcopy(values))
            self._save()

    def enqueue(self, event_id: str, text: str) -> bool:
        with self._lock:
            outbox = self._data.setdefault("outbox", [])
            if any(item.get("id") == event_id for item in outbox):
                return False
            outbox.append({"id": event_id, "text": text, "created": int(time.time())})
            del outbox[:-100]
            self._save()
            return True

    def pending(self) -> list[dict[str, Any]]:
        return self.get("outbox", [])

    def delivered(self, event_id: str) -> None:
        with self._lock:
            self._data["outbox"] = [
                item for item in self._data.get("outbox", []) if item.get("id") != event_id
            ]
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = json.dumps(self._data, ensure_ascii=False, indent=2, sort_keys=True)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

