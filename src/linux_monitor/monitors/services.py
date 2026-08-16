from __future__ import annotations

import subprocess


def service_state(name: str) -> str:
    user_scope = name.startswith("user:")
    unit = name.removeprefix("user:")
    command = ["systemctl"]
    if user_scope:
        command.append("--user")
    command.extend(["is-active", unit])
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    state = result.stdout.strip()
    return state or ("unknown" if result.returncode == 4 else "inactive")


def service_states(names: tuple[str, ...]) -> dict[str, str]:
    return {name: service_state(name) for name in names}
