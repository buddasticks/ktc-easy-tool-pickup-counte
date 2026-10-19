# KTC-Easy Tool Pickup Counter

Version 2.0 — standalone Moonraker monitor for the Sovol SV08 / KTC-Easy four-tool setup (T0–T3).

## What it does

- Counts observed toolchange attempts, including failed attempts.
- Counts verified successful pickups for T0–T3.
- Counts only clearly identified per-tool pickup failures.
- Persists totals through Klipper's `SAVE_VARIABLE` in the file configured by Klipper's `[save_variables]` section (for example, `saved_variables.cfg`).
- Uses Python's standard library; no pip packages or Klipper macro edits required.

## Important architecture note

This is a **standalone Moonraker monitor managed by systemd**, not a `klippy/extras` Python extension and not a Mainsail frontend panel. That is intentional: it avoids modifying Klipper's source tree or KTC-Easy's toolchanger macros. The package is organised like a small installable plugin, with its runtime, service unit, installer, uninstaller, and documentation together.

## Install

Copy the extracted package folder to the printer (or copy its contents to a folder), then run `install.sh` as user `biqu`, not with `sudo`:

```bash
cd /path/to/ktc_easy_tool_pickup_counter_plugin
chmod +x install.sh uninstall.sh
./install.sh
```

The installer puts runtime files in `/home/biqu/tool_pickup_counter/`. The systemd unit at `/etc/systemd/system/tool-pickup-counter.service` is a symlink to the service file inside that runtime folder, so there is one maintained copy of the service definition. The installer does not edit `printer.cfg`, KTC-Easy macros, or `variables.cfg`.

## Check status and logs

```bash
sudo systemctl status tool-pickup-counter.service --no-pager
journalctl -u tool-pickup-counter.service -n 30 --no-pager
journalctl -u tool-pickup-counter.service -f
```

## Dry run

Stop the service first to avoid running two monitors at once:

```bash
sudo systemctl stop tool-pickup-counter.service
python3 /home/biqu/tool_pickup_counter/tool_pickup_counter.py --dry-run
```

Stop with `Ctrl+C`, then restart normal monitoring:

```bash
sudo systemctl start tool-pickup-counter.service
```

## Uninstall

```bash
cd /home/biqu/tool_pickup_counter
./uninstall.sh
```

This removes the service and its systemd link but preserves the runtime folder and saved counters. To delete the program files manually after uninstalling, run `rm -rf /home/biqu/tool_pickup_counter`. The saved counters are in the file configured by Klipper's `[save_variables]` section and are not removed by this package.

## Limitations

The monitor reads Klipper objects through Moonraker's GET `/printer/objects/query` endpoint and polls every 0.25 seconds. Brief transitions or events during a monitor outage may be missed. Failed `SAVE_VARIABLE` requests are queued and retried every two seconds while the monitor remains running; a power loss before a retry succeeds can still lose that pending update. Ambiguous errors are not assigned to a specific tool rather than guessed. Verify counting on the live printer before treating the totals as authoritative.
