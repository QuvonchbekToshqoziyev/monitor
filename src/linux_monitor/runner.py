from __future__ import annotations

import logging
from collections.abc import Callable, Mapping


class SafeRunner:
    """Runs independent checks without letting one broken sensor stop the others."""

    def __init__(self, checks: Mapping[str, Callable[[], None]], logger: logging.Logger | None = None):
        self.checks = checks
        self.logger = logger or logging.getLogger(__name__)
        self._failed: set[str] = set()

    def run_once(self) -> list[str]:
        failed: list[str] = []
        for name, check in self.checks.items():
            try:
                check()
                if name in self._failed:
                    self.logger.info("monitor check recovered: %s", name)
                    self._failed.remove(name)
            except Exception:
                failed.append(name)
                if name not in self._failed:
                    self.logger.exception("monitor check failed: %s", name)
                    self._failed.add(name)
        return failed
