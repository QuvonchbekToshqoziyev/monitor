from __future__ import annotations

import subprocess


def _failed_units(user: bool) -> list[str] | None:
    command = ["systemctl"]
    if user:
        command.append("--user")
    command.extend(["list-units", "--failed", "--no-legend", "--plain"])
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode not in (0, 1):
        return None
    prefix = "user:" if user else ""
    return sorted(
        prefix + line.split()[0]
        for line in result.stdout.splitlines()
        if line.strip() and len(line.split()) >= 1
    )


def failed_units() -> list[str]:
    system = _failed_units(user=False)
    if system is None:
        raise RuntimeError("systemctl failed")
    return sorted(system + (_failed_units(user=True) or []))
