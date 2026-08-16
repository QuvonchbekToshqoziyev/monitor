# Linux Laptop Monitor

A small, deterministic Linux monitor with an authorized Telegram dashboard. It
uses Python's standard library plus Linux `/proc`, `/sys`, `systemctl`, and
`journalctl`; there are no runtime Python dependencies and no remote service
other than Telegram.

It uses a hybrid model: CPU, RAM/swap, temperature, battery, storage, power,
configured services, failed systemd units, and OOM activity are sampled
continuously into one shared current-state hub. The event engine emits only
state transitions, and a separate notification policy decides which events go
to Telegram. Dashboard buttons refresh through the same monitor hub, so there
is no second implementation of monitoring logic.

Normal statistics are never pushed automatically. Actionable transitions such
as a battery threshold crossing, sustained critical RAM, critical/recovered
temperature, low/recovered storage, service failure/recovery, new failed unit,
OOM activity, or a power event are deduplicated. Event state and undelivered
alerts survive restarts in a private local JSON file.

## Requirements and installation

- Linux with systemd and Python 3.11+
- A Telegram bot token from `@BotFather`
- The numeric private chat ID for automatic alerts
- Each immutable numeric Telegram user ID that may control the bot

```bash
cd ~/monitor
python3 -m venv .venv
cp config.example.toml config.toml
```

There are no Python packages to install and no `pip` step. Edit `config.toml`,
replacing `chat_id` and `allowed_user_ids`. `chat_id` is the destination for
automatic alerts; it is not used as authorization identity. Authorization uses
only immutable numeric sender user IDs, even when a private-chat ID happens to
have the same value. Send the bot a message after creating it so Telegram has a
chat to poll.

Never put the bot token in Git or `config.toml`:

```bash
read -rsp 'Telegram bot token: ' TELEGRAM_BOT_TOKEN && echo
export TELEGRAM_BOT_TOKEN
PYTHONPATH=src .venv/bin/python -m linux_monitor --config config.toml
```

Commands are `/start`, `/status`, and `/help`. Authorization is strict and
default-deny. An update with a missing, malformed, or unlisted numeric sender ID
is discarded before routing and receives no response. Usernames and other
profile fields never grant access. Interactive monitoring is limited to private
chats. There is no shell-command endpoint.

### Manage authorized users

On the first run, `telegram.allowed_user_ids` seeds the private JSON file at
`telegram.allowlist_path`. After initialization, manage that persistent file
only through the local CLI abstraction:

```bash
PYTHONPATH=src .venv/bin/python -m linux_monitor --config config.toml --list-users
PYTHONPATH=src .venv/bin/python -m linux_monitor --config config.toml --allow-user 123456789
PYTHONPATH=src .venv/bin/python -m linux_monitor --config config.toml --remove-user 123456789
```

IDs must be positive integers. Duplicate additions are harmless. Restart the
running service after a local add or removal so it reloads the allowlist:

```bash
systemctl --user restart linux-monitor.service
```

There is no `/start` enrollment, pending-user list, approval workflow, or
username-based authorization. Unauthorized users receive no message and are
not reported to the owner.

## Configuration

`config.example.toml` documents every option. Important settings are:

- `poll_interval_seconds`: background event interval, minimum 15 seconds
- `battery_thresholds`: one alert per discharge crossing (defaults: 20/10/5)
- `storage_critical_free_percent` / `storage_recovery_free_percent`: low-space
  and recovery boundaries with hysteresis
- `temperature_critical_celsius` / `temperature_recovery_celsius`: thermal
  transition boundaries with hysteresis
- `memory_critical_percent` / `memory_recovery_percent`: RAM transition
  boundaries; `memory_sustained_polls` controls how long critical usage must last
- `notify_charger_events`: enable charger connected/disconnected notifications
- `notify_storage_recovery` and `notify_resume`: optional recovery/resume alerts
- `storage_paths`: `/` plus any other explicit mount/directory paths
- `services`: exact systemd unit names to monitor; prefix user units with
  `user:` (for example `user:linux-monitor.service`)
- `state_path`: persistent deduplication and pending-alert state
- `log_path`: optional rotating local log; otherwise use the systemd journal
- `telegram.allowed_user_ids`: first-run seed of numeric authorized sender IDs
- `telegram.allowlist_path`: separate persistent allowlist file

`TELEGRAM_CHAT_ID` may override the automatic-alert destination. Set
`LINUX_MONITOR_CONFIG` to change the default `config.toml` path.

## Install as a user service

The supplied unit assumes the repository is `~/monitor` and writes state below
`~/.local/state/linux-monitor`. Keep the environment file private:

```bash
mkdir -p ~/.config/linux-monitor ~/.config/systemd/user ~/.local/state/linux-monitor
cp config.toml ~/.config/linux-monitor/config.toml
cp systemd/linux-monitor.service ~/.config/systemd/user/linux-monitor.service
read -rsp 'Telegram bot token: ' MONITOR_BOT_TOKEN && echo
printf 'TELEGRAM_BOT_TOKEN=%s\n' "$MONITOR_BOT_TOKEN" > ~/.config/linux-monitor/env
unset MONITOR_BOT_TOKEN
chmod 600 ~/.config/linux-monitor/env ~/.config/linux-monitor/config.toml
systemctl --user daemon-reload
systemctl --user enable --now linux-monitor.service
systemctl --user status linux-monitor.service
```

To keep the user service running after logout:

```bash
loginctl enable-linger "$USER"
```

View logs with `journalctl --user -u linux-monitor.service -f`. After changing
services or thresholds, run `systemctl --user restart linux-monitor.service`.

## Tests

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

## Known limits

- Unexpected shutdown detection is best-effort: it compares kernel boot IDs
  and whether the monitor received a clean stop signal.
- A suspend alert can only be delivered after resume; the laptop cannot send
  while asleep.
- Temperature, battery-health, journal, and systemd details depend on the
  permissions and interfaces exposed by the machine.
- OOM journal messages are deliberately never sent to Telegram; only a generic
  event alert is sent to avoid leaking process arguments or personal data.
