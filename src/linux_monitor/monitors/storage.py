from __future__ import annotations

import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class StorageInfo:
    path: str
    total: int
    used: int
    free: int

    @property
    def used_percent(self) -> float:
        return round(100 * self.used / self.total, 1) if self.total else 0.0

    @property
    def free_percent(self) -> float:
        return round(100 * self.free / self.total, 1) if self.total else 0.0


def storage_info(path: str) -> StorageInfo:
    usage = shutil.disk_usage(path)
    return StorageInfo(path, usage.total, usage.used, usage.free)

