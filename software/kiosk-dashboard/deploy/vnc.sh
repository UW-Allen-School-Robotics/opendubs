#!/usr/bin/env bash
# OpenDubs VNC — mirror the kiosk :0 session over VNC (x11vnc). Runs in the
# kiosk user's GNOME session via ~/.config/autostart, so DISPLAY/XAUTHORITY are
# inherited. Restarts on exit. Connect from a Mac: `open vnc://opendubs2.local`
# (password below). Logs to ~/vnc.log.
set -u
exec >>"$HOME/vnc.log" 2>&1
echo "=== vnc start $(date) ==="

export DISPLAY="${DISPLAY:-:0}"
if [ -z "${XAUTHORITY:-}" ]; then
  export XAUTHORITY="$(ls /run/user/$(id -u)/gdm/Xauthority 2>/dev/null || echo "$HOME/.Xauthority")"
fi

# ensure a VNC password exists. Set per-unit via OPENDUBS_VNC_PASS at provision time;
# falls back to a placeholder that MUST be changed (never commit a real password here).
if [ ! -f "$HOME/.vnc/passwd" ]; then
  mkdir -p "$HOME/.vnc"
  x11vnc -storepasswd "${OPENDUBS_VNC_PASS:-CHANGE-ME}" "$HOME/.vnc/passwd" >/dev/null 2>&1
fi

# The dashboard's VNC start/stop button toggles ~/.vnc/disabled. When present,
# VNC stays off; absent (default) = on.
while true; do
  if [ ! -f "$HOME/.vnc/disabled" ]; then
    x11vnc -display "$DISPLAY" -auth "$XAUTHORITY" -rfbauth "$HOME/.vnc/passwd" \
      -forever -shared -noxdamage -xrandr resize 2>&1 || echo "x11vnc exited $? $(date)"
  fi
  sleep 2
done
