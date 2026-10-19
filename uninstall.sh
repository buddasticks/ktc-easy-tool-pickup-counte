#!/usr/bin/env bash
set -euo pipefail

UNIT_NAME="tool-pickup-counter.service"
UNIT_LINK="/etc/systemd/system/${UNIT_NAME}"
APP_DIR="/home/biqu/tool_pickup_counter"

if [[ "$(id -un)" == "root" ]]; then
  echo "Run ./uninstall.sh as biqu; the script will use sudo where needed."
  exit 1
fi
sudo systemctl disable --now "$UNIT_NAME" 2>/dev/null || true
sudo rm -f "$UNIT_LINK"
sudo systemctl daemon-reload
sudo systemctl reset-failed "$UNIT_NAME" 2>/dev/null || true

echo "Service stopped, disabled, and systemd unit link removed."
echo "Preserved: $APP_DIR and Klipper's variables.cfg (including all saved counters)."
echo "To remove program files too, run: rm -rf $APP_DIR"
