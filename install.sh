#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/home/biqu/tool_pickup_counter"
UNIT_NAME="tool-pickup-counter.service"
UNIT_LINK="/etc/systemd/system/${UNIT_NAME}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$(id -un)" == "root" ]]; then
  echo "Run ./install.sh as biqu; the script will use sudo where needed."
  exit 1
fi
for file in tool_pickup_counter.py "$UNIT_NAME" uninstall.sh README.md; do
  if [[ ! -f "$SRC_DIR/$file" ]]; then
    echo "ERROR: Missing $SRC_DIR/$file"
    exit 1
  fi
done
command -v sudo >/dev/null || { echo "ERROR: sudo is required."; exit 1; }

printf 'Installing KTC-Easy Tool Pickup Counter...\n'
mkdir -p "$APP_DIR"
# Install the complete runtime package together in one directory.
install -m 0755 "$SRC_DIR/tool_pickup_counter.py" "$APP_DIR/tool_pickup_counter.py"
install -m 0644 "$SRC_DIR/$UNIT_NAME" "$APP_DIR/$UNIT_NAME"
install -m 0755 "$SRC_DIR/uninstall.sh" "$APP_DIR/uninstall.sh"
if [[ "$SRC_DIR/install.sh" != "$APP_DIR/install.sh" ]]; then
  install -m 0755 "$SRC_DIR/install.sh" "$APP_DIR/install.sh"
fi
install -m 0644 "$SRC_DIR/README.md" "$APP_DIR/README.md"

# Use the app-folder service file as the single source of truth.
sudo systemctl stop "$UNIT_NAME" >/dev/null 2>&1 || true
sudo rm -f "$UNIT_LINK"
sudo ln -s "$APP_DIR/$UNIT_NAME" "$UNIT_LINK"
sudo systemctl daemon-reload
sudo systemctl enable "$UNIT_NAME"
sudo systemctl restart "$UNIT_NAME" || sudo systemctl start "$UNIT_NAME"

printf '\nInstall complete. Runtime files:\n'
ls -l "$APP_DIR"
printf '\nSystemd unit link:\n'
ls -l "$UNIT_LINK"
printf '\nService status:\n'
sudo systemctl --no-pager --full status "$UNIT_NAME" || true
printf '\nLogs: journalctl -u %s -f\n' "$UNIT_NAME"
printf 'Counters remain in Klipper\x27s existing variables.cfg; this installer does not edit it.\n'
printf 'Installer source folder is not needed after install and can be deleted when you are satisfied.\n'
