#!/usr/bin/env bash
# OpenDubs kiosk autostart — runs inside the kiosk user's normal GNOME (Xorg)
# session via ~/.config/autostart. GNOME reliably drives this forced-EDID panel;
# this script adds rotation, touch transform, no-blank, and the dashboard.
# Logs to ~/opendubs-kiosk.log (kiosk-owned).
set -u
exec >>"$HOME/opendubs-kiosk.log" 2>&1
echo "=== kiosk autostart $(date) ==="
export DISPLAY="${DISPLAY:-:0}"

URL="http://localhost:8080"

# Rotate to 1920x480 landscape + match touch coordinates (xrandr "left" = 90 CCW).
xrandr --output HDMI-0 --rotate left || echo "rotate failed"
xinput set-prop "QDtech MPI7019" "Coordinate Transformation Matrix" \
  0 -1 1 1 0 0 0 0 1 2>/dev/null || echo "touch matrix failed"

# Kill all blanking (GNOME + X).
gsettings set org.gnome.desktop.session idle-delay 0 || true
gsettings set org.gnome.desktop.screensaver idle-activation-enabled false || true
gsettings set org.gnome.desktop.screensaver lock-enabled false || true
xset s off || true; xset s noblank || true; xset -dpms 2>/dev/null || true

# Wait for the dashboard backend.
for i in $(seq 1 60); do
  curl -sf "$URL/api/health" >/dev/null 2>&1 && { echo "backend up"; break; }
  sleep 1
done

# Launch Chromium kiosk, restart on crash. (snap chromium uses its own profile.)
while true; do
  echo "launching chromium $(date)"
  chromium --kiosk --app="$URL" \
    --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
    --disable-features=TranslateUI --no-first-run \
    --check-for-update-interval=31536000 \
    --overscroll-history-navigation=0 --disable-pinch \
    --autoplay-policy=no-user-gesture-required || echo "chromium exited $?"
  sleep 3
done
