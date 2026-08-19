#!/usr/bin/env bash
# OpenDubs touch bridge wrapper. Runs inside the kiosk user's GNOME session
# (launched via ~/.config/autostart), so DISPLAY/XAUTHORITY are inherited.
# Restarts the bridge if it ever exits. Logs to ~/touch-bridge.log.
set -u
exec >>"$HOME/touch-bridge.log" 2>&1

# Single-instance guard: only one bridge may hold the device grab. Duplicates
# fighting over the grab was the cause of erratic ("misbehaving") touch.
exec 9>"$HOME/.touch-bridge.lock"
if ! flock -n 9; then
  echo "=== another bridge instance already running, exiting $(date) ==="
  exit 0
fi

echo "=== touch-bridge wrapper start $(date) ==="
export DISPLAY="${DISPLAY:-:0}"
if [ -z "${XAUTHORITY:-}" ]; then
  export XAUTHORITY="$(ls /run/user/$(id -u)/gdm/Xauthority 2>/dev/null || echo "$HOME/.Xauthority")"
fi
echo "DISPLAY=$DISPLAY XAUTHORITY=$XAUTHORITY"

while true; do
  python3 -u /opt/opendubs/kiosk-dashboard/tools/touch_bridge.py || echo "bridge exited $? at $(date)"
  sleep 2
done
